"""Service dependency graph and Key Findings with cause/symptom roles."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from .contracts.baseline import ScoredEvent
from .contracts.causality import (
    CausalAnalysis,
    CompositeKind,
    CompositeSignal,
    EdgeSource,
    Evidence,
    Finding,
    MetricShift,
    ServiceClass,
    ServiceEdge,
    ServiceGraph,
    ServiceInfo,
)
from .contracts.common import (
    LEVEL_RANK,
    SIGNAL_PRIORITY,
    AnalysisConfig,
    DbCategory,
    EvidenceLine,
    Level,
    MetricSummary,
    MetricUnit,
    Role,
    Severity,
    SignalKind,
    TimeWindow,
)
from .contracts.patterns import PatternGroup, PatternResult, TemplateCount
from .contracts.signals import CONNECTION_COUNT_METRICS, METRIC_LIMITS
from .contracts.timeline import Episode, Timeline, WindowComparison
from .hypotheses import build_causes
from .impact import build_impact

_CLASS_ORDER = (ServiceClass.DB_SERVER, ServiceClass.ENTRY_POINT, ServiceClass.INTERNAL)
_MAX_TEMPLATES = 5
_MAX_REQUEST_IDS = 5
_ERROR_RANK = LEVEL_RANK[Level.ERROR]

_SEVERITY_ORDER = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW)

_ENTRY_CRITICAL = {
    SignalKind.DEPENDENCY_UNAVAILABLE,
    SignalKind.SERVER_ERROR_STATUS,
    SignalKind.TIMEOUT,
    SignalKind.CONNECTION_ERROR,
}
_HIGH_KINDS = {
    SignalKind.RESTART,
    SignalKind.DEPENDENCY_UNAVAILABLE,
    SignalKind.POOL_SATURATION,
    SignalKind.CONNECTION_ERROR,
    SignalKind.LOCK,
}
_MEDIUM_KINDS = {
    SignalKind.TIMEOUT,
    SignalKind.LATENCY,
    SignalKind.RETRY,
    SignalKind.QUEUE_BACKLOG,
    SignalKind.REPLICATION_LAG,
    SignalKind.SERVER_ERROR_STATUS,
    SignalKind.PROBE_FAILURE,
}

_KIND_LABELS = {
    SignalKind.RESTART: "restart",
    SignalKind.DEPENDENCY_UNAVAILABLE: "dependency unavailable",
    SignalKind.CONNECTION_ERROR: "connection error",
    SignalKind.TIMEOUT: "timeout",
    SignalKind.SERVER_ERROR_STATUS: "server error status",
    SignalKind.POOL_SATURATION: "connection pool saturation",
    SignalKind.LOCK: "lock contention",
    SignalKind.ERROR: "error",
    SignalKind.RETRY: "retry",
    SignalKind.REPLICATION_LAG: "replication lag",
    SignalKind.QUEUE_BACKLOG: "queue backlog",
    SignalKind.LATENCY: "latency deviation",
    SignalKind.PROBE_FAILURE: "probe failure",
    SignalKind.WARNING: "warning",
}

_RESIDUAL = "residual deviation outside detected episodes"


# ---------- graph ----------


def build_service_graph(
    scored: Sequence[ScoredEvent], patterns: PatternResult
) -> ServiceGraph:
    """Dependency graph from PEER fields, DB access and order of appearance in traces."""
    services = sorted({s.event.record.service for s in scored})
    known = set(services)
    db_servers = sorted({s.event.record.service for s in scored if s.event.db_server})
    db_set = set(db_servers)

    peer_counts: Counter[tuple[str, str]] = Counter()
    db_counts: Counter[tuple[str, str]] = Counter()
    for item in scored:
        ev = item.event
        service = ev.record.service
        for peer in ev.peers:
            if peer != service and peer in known:
                peer_counts[(service, peer)] += 1
        if ev.db_related and service not in db_set:
            for server in db_servers:
                db_counts[(service, server)] += 1

    explicit = set(peer_counts) | set(db_counts)

    order_counts: Counter[tuple[str, str]] = Counter()
    for trace in patterns.traces:
        seen = [s for s in trace.services if s in known]
        for i, earlier in enumerate(seen):
            for later in seen[i + 1 :]:
                if later != earlier and (earlier, later) not in explicit:
                    order_counts[(later, earlier)] += 1
    dropped = [
        pair
        for pair, n in order_counts.items()
        if order_counts.get((pair[1], pair[0]), 0) >= n
        and (pair[1], pair[0]) in order_counts
    ]
    for pair in dropped:
        del order_counts[pair]

    edges = [
        ServiceEdge(d, t, EdgeSource.PEER_FIELD, n) for (d, t), n in peer_counts.items()
    ]
    edges += [
        ServiceEdge(d, t, EdgeSource.DB_ACCESS, n) for (d, t), n in db_counts.items()
    ]
    edges += [
        ServiceEdge(d, t, EdgeSource.TRACE_ORDER, n)
        for (d, t), n in order_counts.items()
    ]
    edges.sort(key=lambda e: (e.dependent, e.dependency, e.source.value))

    has_incoming_peer = {t for (_, t) in peer_counts}
    has_outgoing_peer = {d for (d, _) in peer_counts}
    infos = []
    for name in services:
        if name in db_set:
            cls = ServiceClass.DB_SERVER
        elif name in has_outgoing_peer and name not in has_incoming_peer:
            cls = ServiceClass.ENTRY_POINT
        else:
            cls = ServiceClass.INTERNAL
        infos.append(ServiceInfo(service=name, service_class=cls))
    return ServiceGraph(services=tuple(infos), edges=tuple(edges))


# ---------- findings ----------


@dataclass(frozen=True)
class _Draft:
    service_class: ServiceClass
    episode_id: str | None
    groups: tuple[PatternGroup, ...]
    kind: SignalKind
    kinds: tuple[SignalKind, ...]
    severity: Severity

    @property
    def start(self) -> datetime:
        return min(g.window.start for g in self.groups)


def _episode_of(group: PatternGroup, episodes: Sequence[Episode]) -> str | None:
    for ep in episodes:
        if ep.window.start <= group.window.start <= ep.window.end:
            return ep.id
    return None


def _severity(kind: SignalKind, cls: ServiceClass, max_level: Level) -> Severity:
    if kind is SignalKind.RESTART and cls is ServiceClass.DB_SERVER:
        return Severity.CRITICAL
    if cls is ServiceClass.ENTRY_POINT and kind in _ENTRY_CRITICAL:
        return Severity.CRITICAL
    if LEVEL_RANK[max_level] >= _ERROR_RANK or kind in _HIGH_KINDS:
        return Severity.HIGH
    if kind in _MEDIUM_KINDS:
        return Severity.MEDIUM
    return Severity.LOW


def _join(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _title(kinds: Sequence[SignalKind], services: Sequence[str], count: int) -> str:
    noun = "event" if count == 1 else "events"
    labels = _join([_KIND_LABELS[k] for k in kinds[:3]])
    return f"{count} {labels} {noun} recorded in {_join(services)}."


def _make_draft(
    cls: ServiceClass, episode_id: str | None, groups: Sequence[PatternGroup]
) -> _Draft:
    sev = {g.id: _severity(g.kind, cls, g.max_level) for g in groups}
    best = min(
        groups,
        key=lambda g: (
            _SEVERITY_ORDER.index(sev[g.id]),
            -g.count,
            SIGNAL_PRIORITY.index(g.kind),
        ),
    )
    present = {g.kind for g in groups}
    return _Draft(
        service_class=cls,
        episode_id=episode_id,
        groups=tuple(groups),
        kind=best.kind,
        kinds=tuple(k for k in SIGNAL_PRIORITY if k in present),
        severity=min((sev[g.id] for g in groups), key=_SEVERITY_ORDER.index),
    )


_WEAK = (Severity.MEDIUM, Severity.LOW)


def _merge_weak(items: Sequence[tuple[_Draft, bool]]) -> list[_Draft]:
    """Weak findings of an episode with a single db_related (no DB server) are merged into one."""
    out: list[_Draft] = []
    pool: dict[tuple[str, bool], list[_Draft]] = {}
    for d, db in items:
        weak = (
            d.episode_id is not None
            and d.service_class is not ServiceClass.DB_SERVER
            and d.severity in _WEAK
        )
        if weak:
            pool.setdefault((str(d.episode_id), db), []).append(d)
        else:
            out.append(d)
    for (ep, _), ds in pool.items():
        if len(ds) == 1:
            out.append(ds[0])
        else:
            groups = [g for d in ds for g in d.groups]
            out.append(_make_draft(ServiceClass.INTERNAL, ep, groups))
    return out


def _merge_metrics(groups: Sequence[PatternGroup]) -> tuple[MetricSummary, ...]:
    by_name: dict[str, list[MetricSummary]] = {}
    for g in groups:
        for m in g.metrics:
            by_name.setdefault(m.metric, []).append(m)
    out = []
    for name in sorted(by_name):
        items = by_name[name]
        if len(items) == 1:
            out.append(items[0])
            continue
        weighted = sorted(m.p50 for m in items for _ in range(m.count))
        baselines = [m.baseline_p95 for m in items if m.baseline_p95 is not None]
        out.append(
            MetricSummary(
                metric=name,
                unit=items[0].unit,
                count=sum(m.count for m in items),
                min=min(m.min for m in items),
                p50=weighted[(len(weighted) - 1) // 2],
                max=max(m.max for m in items),
                baseline_p95=max(baselines) if baselines else None,
            )
        )
    return tuple(out)


def _clip(line: EvidenceLine, limit: int) -> EvidenceLine:
    if len(line.text) <= limit:
        return line
    return replace(line, text=line.text[: max(limit - 3, 0)] + "...")


def _evidence(groups: Sequence[PatternGroup], config: AnalysisConfig) -> Evidence:
    limit = max(1, config.max_samples_per_finding)
    chosen: dict[int, EvidenceLine] = {}
    for g in groups:
        first = g.samples[0]
        if len(chosen) < limit:
            chosen.setdefault(first.line_no, first)
    for g in groups:
        for s in g.samples:
            if len(chosen) < limit:
                chosen.setdefault(s.line_no, s)
    samples = tuple(_clip(chosen[n], config.max_evidence_chars) for n in sorted(chosen))
    counts: Counter[str] = Counter()
    for g in groups:
        for t in g.templates:
            counts[t.template] += t.count
    templates = tuple(
        TemplateCount(template=t, count=c)
        for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[
            :_MAX_TEMPLATES
        ]
    )
    request_ids = tuple(sorted({r for g in groups for r in g.request_ids}))[
        :_MAX_REQUEST_IDS
    ]
    return Evidence(
        metrics=_merge_metrics(groups),
        samples=samples,
        templates=templates,
        request_ids=request_ids,
    )


def _reachability(graph: ServiceGraph) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in graph.edges:
        adj.setdefault(e.dependent, set()).add(e.dependency)
    reach: dict[str, set[str]] = {}
    for start, first_hops in adj.items():
        seen: set[str] = set()
        stack = list(first_hops)
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adj.get(node, ()))
        reach[start] = seen
    return reach


def _clock(ts: datetime) -> str:
    return ts.strftime("%H:%M:%S")


@dataclass(frozen=True)
class _Built:
    draft: _Draft
    services: tuple[str, ...]
    window: TimeWindow
    count: int
    max_level: Level


def _roles(
    items: Sequence[_Built],
    episode: Episode | None,
    reach: dict[str, set[str]],
    graph: ServiceGraph,
    config: AnalysisConfig,
) -> list[tuple[Role, str]]:
    if episode is None:
        return [
            (
                Role.SYMPTOM,
                f"Starts at {_clock(b.window.start)}; {_RESIDUAL}.",
            )
            for b in items
        ]
    deadline_s = config.cause_window_seconds
    candidates = [
        i
        for i, b in enumerate(items)
        if (b.window.start - episode.onset).total_seconds() <= deadline_s
    ]
    if not candidates:
        first = min(b.window.start for b in items)
        candidates = [i for i, b in enumerate(items) if b.window.start == first]

    def reaches(src: Sequence[str], dst: Sequence[str]) -> bool:
        return any(t in reach.get(x, ()) for x in src for t in dst)

    def dominators(idx: int) -> list[int]:
        f = items[idx]
        return [
            j
            for j in candidates
            if j != idx
            and reaches(f.services, items[j].services)
            and not reaches(items[j].services, f.services)
        ]

    doms = {i: dominators(i) for i in candidates}
    causes = {i for i in candidates if not doms[i]}
    cyclic: set[int] = set()
    for i in sorted(causes):
        fi = items[i]
        for j in sorted(causes):
            gj = items[j]
            if (
                j != i
                and not set(fi.services) & set(gj.services)
                and reaches(fi.services, gj.services)
                and reaches(gj.services, fi.services)
                and (gj.window.start, j) < (fi.window.start, i)
            ):
                cyclic.add(i)
                break
    cycle_members = {
        i
        for i in causes
        for j in causes
        if i != j
        and not set(items[i].services) & set(items[j].services)
        and reaches(items[i].services, items[j].services)
        and reaches(items[j].services, items[i].services)
    }
    causes -= cyclic
    if not causes:
        causes = {min(candidates, key=lambda i: (items[i].window.start, i))}

    direct: dict[str, set[str]] = {}
    for e in graph.edges:
        direct.setdefault(e.dependent, set()).add(e.dependency)

    result: list[tuple[Role, str]] = []
    for i, b in enumerate(items):
        start = _clock(b.window.start)
        if i in causes:
            deps = sorted({d for s in b.services for d in direct.get(s, ())})
            text = f"Starts at {start}, within {deadline_s}s of episode onset {_clock(episode.onset)}"
            if i in cycle_members:
                text += "; dependencies form a cycle, so the earliest deviation is treated as the cause"
            elif deps:
                text += f"; no dependency among {_join(deps)} shows a deviation that explains it"
            else:
                text += "; it has no dependencies in the service graph"
            result.append((Role.CAUSE, text + "."))
        elif i in cyclic:
            result.append(
                (
                    Role.SYMPTOM,
                    f"Starts at {start}; dependencies form a cycle with an earlier deviation in the same episode, so it is treated as a downstream effect.",
                )
            )
        elif i in candidates:
            names = sorted({s for j in doms[i] for s in items[j].services})
            result.append(
                (
                    Role.SYMPTOM,
                    f"Starts at {start}; depends on {_join(names)}, which show an upstream deviation in the same episode.",
                )
            )
        else:
            deps = sorted({d for s in b.services for d in reach.get(s, ())})
            tail = f"; depends on {_join(deps)}" if deps else ""
            result.append(
                (
                    Role.SYMPTOM,
                    f"Starts at {start}, more than {deadline_s}s after episode onset {_clock(episode.onset)}{tail}.",
                )
            )
    return result


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.4g}"


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _service_limits(
    scored: Sequence[ScoredEvent],
) -> dict[tuple[str, str], tuple[str, float]]:
    """(service, metric) -> (limit field, the service's maximum value across the whole log)."""
    maxima: dict[tuple[str, str], float] = {}
    for item in scored:
        rec = item.event.record
        for name, value in rec.fields.items():
            num = _numeric(value)
            if num is None:
                continue
            key = (rec.service, name)
            maxima[key] = num if key not in maxima else max(maxima[key], num)
    out: dict[tuple[str, str], tuple[str, float]] = {}
    for (service, field), top in maxima.items():
        for metric, limit_field in METRIC_LIMITS:
            if field == limit_field and (service, metric) not in out:
                out[(service, metric)] = (limit_field, top)
    return out


_Row = tuple[float, int, MetricUnit, bool]


def _service_shifts(
    service: str,
    window: TimeWindow,
    samples: dict[str, list[_Row]],
    baselines: dict[tuple[str, str], tuple[float, float]],
    limits: dict[tuple[str, str], tuple[str, float]],
    config: AnalysisConfig,
) -> list[MetricShift]:
    out = []
    for metric in sorted(samples):
        base = baselines.get((service, metric))
        if base is None:
            continue
        p50, p95 = base
        rows = samples[metric]
        higher = rows[0][3]
        values = [r[0] for r in rows]
        low, high = min(values), max(values)
        worst = high if higher else low
        if higher:
            deviated = worst > p95
        else:
            deviated = worst <= config.composite_drop_ratio * p50
        if not deviated:
            continue
        line_no = min(r[1] for r in rows if r[0] == worst)
        limit = limits.get((service, metric))
        reached = limit is not None and worst >= config.pool_saturation_ratio * limit[1]
        out.append(
            MetricShift(
                service=service,
                metric=metric,
                unit=rows[0][2],
                higher_is_worse=higher,
                window=window,
                baseline_p50=p50,
                baseline_p95=p95,
                low=low,
                high=high,
                worst=worst,
                worst_line_no=line_no,
                limit_metric=limit[0] if limit else None,
                limit=limit[1] if limit else None,
                limit_reached=reached,
                relative_deviation=round(abs(worst - p50) / max(abs(p50), 1.0), 3),
            )
        )
    return out


def _composites(
    shifts: Sequence[MetricShift], config: AnalysisConfig
) -> tuple[CompositeSignal, ...]:
    out = []
    for service in sorted({s.service for s in shifts}):
        mine = [s for s in shifts if s.service == service]
        rising = [
            s
            for s in mine
            if s.metric in CONNECTION_COUNT_METRICS
            and s.higher_is_worse
            and s.worst >= config.composite_rise_ratio * s.baseline_p50
        ]
        falling = [
            s
            for s in mine
            if s.unit is MetricUnit.PER_SECOND
            and not s.higher_is_worse
            and s.worst <= config.composite_drop_ratio * s.baseline_p50
        ]
        if rising and falling:
            r, f = rising[0], falling[0]
            text = (
                f"{r.metric} rose {_fmt(r.baseline_p50)} → {_fmt(r.worst)} while "
                f"{f.metric} fell {_fmt(f.baseline_p50)} → {_fmt(f.worst)}: "
                "connections appear to be held rather than serving load."
            )
            out.append(
                CompositeSignal(
                    kind=CompositeKind.CONNECTIONS_HELD,
                    service=service,
                    rising=r,
                    falling=f,
                    interpretation=text,
                )
            )
    return tuple(out)


def _finding_shifts(
    services: Sequence[str],
    window: TimeWindow,
    heads: dict[str, list[tuple[datetime, int, str, float, MetricUnit, bool]]],
    baselines: dict[tuple[str, str], tuple[float, float]],
    limits: dict[tuple[str, str], tuple[str, float]],
    config: AnalysisConfig,
) -> list[MetricShift]:
    shifts: list[MetricShift] = []
    for service in services:
        per_metric: dict[str, list[_Row]] = {}
        for ts, line_no, name, value, unit, higher in heads.get(service, ()):
            if window.start <= ts <= window.end:
                per_metric.setdefault(name, []).append((value, line_no, unit, higher))
        shifts.extend(
            _service_shifts(service, window, per_metric, baselines, limits, config)
        )
    shifts.sort(
        key=lambda x: (not x.limit_reached, -x.relative_deviation, x.metric, x.service)
    )
    return shifts


def assess(
    scored: Sequence[ScoredEvent],
    patterns: PatternResult,
    timeline: Timeline,
    comparison: WindowComparison,
    graph: ServiceGraph,
    config: AnalysisConfig,
) -> CausalAnalysis:
    """Key Findings with severity, evidence and cause/symptom roles."""
    classes = {s.service: s.service_class for s in graph.services}
    buckets: dict[tuple[ServiceClass, str | None, bool], list[PatternGroup]] = {}
    for g in patterns.groups:
        cls = classes.get(g.service, ServiceClass.INTERNAL)
        db_flag = g.db_related if cls is ServiceClass.INTERNAL else False
        key = (cls, _episode_of(g, timeline.episodes), db_flag)
        buckets.setdefault(key, []).append(g)
    drafts = _merge_weak(
        [(_make_draft(c, e, gs), db) for (c, e, db), gs in buckets.items()]
    )
    drafts.sort(
        key=lambda d: (
            d.start,
            SIGNAL_PRIORITY.index(d.kind),
            _CLASS_ORDER.index(d.service_class),
            d.episode_id or "",
        )
    )

    built = []
    for d in drafts:
        built.append(
            _Built(
                draft=d,
                services=tuple(sorted({g.service for g in d.groups})),
                window=TimeWindow(
                    start=min(g.window.start for g in d.groups),
                    end=max(g.window.end for g in d.groups),
                ),
                count=sum(g.count for g in d.groups),
                max_level=max(
                    (g.max_level for g in d.groups), key=lambda lv: LEVEL_RANK[lv]
                ),
            )
        )

    reach = _reachability(graph)
    episodes = {e.id: e for e in timeline.episodes}
    role_of: dict[int, tuple[Role, str]] = {}
    by_episode: dict[str | None, list[int]] = {}
    for idx, b in enumerate(built):
        by_episode.setdefault(b.draft.episode_id, []).append(idx)
    for ep_id, idxs in by_episode.items():
        roles = _roles(
            [built[i] for i in idxs],
            episodes.get(ep_id) if ep_id else None,
            reach,
            graph,
            config,
        )
        for i, r in zip(idxs, roles):
            role_of[i] = r

    baselines = {
        (m.service, m.metric): (m.baseline.p50, m.baseline.p95)
        for m in comparison.metrics
        if m.baseline is not None
    }
    limits = _service_limits(scored)
    heads: dict[str, list[tuple[datetime, int, str, float, MetricUnit, bool]]] = {}
    for item in scored:
        ev = item.event
        if ev.continuation_of is not None:
            continue
        for m in ev.metrics:
            heads.setdefault(ev.record.service, []).append(
                (
                    ev.record.ts,
                    ev.record.line_no,
                    m.name,
                    m.value,
                    m.unit,
                    m.higher_is_worse,
                )
            )

    group_order = {g.id: n for n, g in enumerate(patterns.groups)}
    findings = []
    for idx, b in enumerate(built):
        d = b.draft
        cats = {c for g in d.groups for c in g.db_categories}
        role, rationale = role_of[idx]
        shifts = _finding_shifts(b.services, b.window, heads, baselines, limits, config)
        evidence = replace(
            _evidence(d.groups, config),
            shifts=tuple(shifts),
            composites=_composites(shifts, config),
        )
        findings.append(
            Finding(
                id=f"F{idx + 1}",
                title=_title(d.kinds, b.services, b.count),
                kind=d.kind,
                service_class=d.service_class,
                services=b.services,
                severity=d.severity,
                role=role,
                role_rationale=rationale,
                episode_id=d.episode_id,
                window=b.window,
                count=b.count,
                db_related=any(g.db_related for g in d.groups),
                db_categories=tuple(c for c in DbCategory if c in cats),
                group_ids=tuple(
                    sorted((g.id for g in d.groups), key=lambda x: group_order[x])
                ),
                evidence=evidence,
                kinds=d.kinds,
            )
        )
    return CausalAnalysis(
        graph=graph,
        findings=tuple(findings),
        causes=build_causes(
            findings, graph, timeline, scored, baselines, limits, config
        ),
        impact=build_impact(scored, patterns, timeline, graph, findings, config),
    )
