"""Root-cause hypotheses with confidence and evidence for/against."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from math import ceil

from .contracts.baseline import ScoredEvent
from .contracts.causality import (
    Finding,
    Hypothesis,
    HypothesisKind,
    MetricShift,
    ProbableCauses,
    ServiceClass,
    ServiceGraph,
    Verdict,
)
from .contracts.common import (
    AnalysisConfig,
    Confidence,
    DbCategory,
    EvidenceLine,
    MetricUnit,
    Role,
    SignalKind,
    TimeWindow,
)
from .contracts.timeline import Episode, Timeline

_RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
_KIND_ORDER = {k: i for i, k in enumerate(HypothesisKind)}
_MAX_ARGS = 3
_MAX_TEMPLATE_CHARS = 80

_Baselines = dict[tuple[str, str], tuple[float, float]]
_Limits = dict[tuple[str, str], tuple[str, float]]


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.4g}"


def _join(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _hhmmss(ts: datetime) -> str:
    return ts.strftime("%H:%M:%S")


def _clock(f: Finding) -> str:
    return _hhmmss(f.window.start)


def _kind(f: Finding) -> str:
    return f.kind.value.replace("_", " ")


def _numbers(f: Finding) -> str:
    """Short phrase with the finding's numbers: event count and the most notable metric."""
    noun = "event" if f.count == 1 else "events"
    text = f"{f.id} records {f.count} {_kind(f)} {noun} in {_join(f.services)} from {_clock(f)}"
    if f.evidence.metrics:
        m = max(f.evidence.metrics, key=lambda x: (x.max, x.metric))
        text += f", with {m.metric} up to {m.max:g}"
        if m.baseline_p95 is not None:
            text += f" against a baseline p95 of {m.baseline_p95:g}"
    return text


def _fid_key(fid: str) -> int:
    digits = "".join(c for c in fid if c.isdigit())
    return int(digits) if digits else 0


def _reach(graph: ServiceGraph) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in graph.edges:
        adj.setdefault(e.dependent, set()).add(e.dependency)
    reach: dict[str, set[str]] = {}
    for start, first_hops in adj.items():
        seen: set[str] = set()
        stack = list(first_hops)
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(adj.get(node, ()))
        reach[start] = seen
    return reach


def _depends_on(
    dependent: Finding, dependency: Finding, reach: dict[str, set[str]]
) -> bool:
    return any(
        d in reach.get(s, ()) for s in dependent.services for d in dependency.services
    )


def _confidence(supporting: int, contradicting: int, high_ok: bool) -> Confidence:
    if high_ok and contradicting == 0:
        return Confidence.HIGH
    if supporting >= 1 and contradicting <= supporting:
        return Confidence.MEDIUM
    return Confidence.LOW


def _categories(findings: Sequence[Finding]) -> tuple[DbCategory, ...]:
    cats = {c for f in findings for c in f.db_categories}
    return tuple(c for c in DbCategory if c in cats)


def _ids(findings: Sequence[Finding]) -> tuple[str, ...]:
    return tuple(sorted({f.id for f in findings}, key=_fid_key))


def _shift_key(s: MetricShift) -> tuple[bool, float, str, str]:
    return (not s.limit_reached, -s.relative_deviation, s.metric, s.service)


def _shift_phrase(s: MetricShift) -> str:
    if s.limit_reached and s.limit_metric is not None and s.limit is not None:
        return f"{s.metric} {_fmt(s.worst)} at {s.limit_metric}={_fmt(s.limit)}"
    if s.higher_is_worse:
        return f"{s.metric} up to {_fmt(s.worst)} vs baseline {_fmt(s.baseline_p50)}"
    return f"{s.metric} down to {_fmt(s.worst)} vs baseline {_fmt(s.baseline_p50)}"


# ---------- SOURCE ----------


def _source_hypothesis(
    causes: Sequence[Finding],
    findings: Sequence[Finding],
    classes: dict[str, ServiceClass],
    reach: dict[str, set[str]],
    db_servers: set[str],
    config: AnalysisConfig,
) -> Hypothesis:
    episode = causes[0].episode_id or ""
    sources = causes[0].services
    first_start = min(f.window.start for f in causes)
    downstream = [
        f
        for f in findings
        if f.episode_id == causes[0].episode_id
        and f.role is Role.SYMPTOM
        and any(_depends_on(f, c, reach) for c in causes)
    ]
    later = [f for f in downstream if f.window.start >= first_start]
    earlier = [f for f in downstream if f.window.start < first_start]

    unique: dict[tuple[str, str], MetricShift] = {}
    for f in sorted(causes, key=lambda x: _fid_key(x.id)):
        for s in f.evidence.shifts:
            unique.setdefault((s.service, s.metric), s)
    ranked = sorted(unique.values(), key=_shift_key)
    shown = ranked[: max(1, config.max_evidence_metrics)]

    supporting: list[str] = []
    if shown:
        supporting.append(
            f"Strongest deviations in {_join(sources)}: "
            + ", ".join(_shift_phrase(s) for s in shown)
            + "."
        )
    else:
        supporting.append("; ".join(_numbers(f) for f in causes) + ".")
    composites: dict[tuple[str, str], str] = {}
    shift_set = list(shown)
    for f in causes:
        for c in f.evidence.composites:
            key = (c.service, c.kind.value)
            if key not in composites:
                composites[key] = c.interpretation
                for s in (c.rising, c.falling):
                    if s not in shift_set:
                        shift_set.append(s)
    supporting.extend(composites[k] for k in sorted(composites))
    if later:
        parts = ", ".join(
            f"{f.id} ({f.count} {_kind(f)}, from {_clock(f)})" for f in later
        )
        supporting.append(
            f"Later deviations in {_join(sorted({s for f in later for s in f.services}))} "
            f"follow: {parts}, after the deviation in {_join(sources)} began at "
            f"{_hhmmss(first_start)}."
        )
    contradicting: list[str] = []
    if earlier:
        parts = ", ".join(f"{f.id} at {_clock(f)}" for f in earlier)
        contradicting.append(
            f"Dependent services show deviations earlier: {parts}, before "
            f"{_join(sources)} at {_hhmmss(first_start)}."
        )
    is_db_server = any(classes.get(s) is ServiceClass.DB_SERVER for s in sources)
    if any(f.db_related for f in causes) and not is_db_server:
        silent = sorted(
            s
            for s in db_servers
            if not any(
                s in f.services and f.episode_id == causes[0].episode_id
                for f in findings
            )
        )
        if silent:
            contradicting.append(
                f"No anomalous events are recorded for the database server {_join(silent)} in {episode}."
            )
    supporting = supporting[:_MAX_ARGS]
    contradicting = contradicting[:_MAX_ARGS]

    supp_services = set(sources)
    for f in later:
        supp_services.update(f.services)
    supp_classes = {classes.get(s, ServiceClass.INTERNAL) for s in supp_services}
    high_ok = len(supp_services) >= 2 and (
        (
            ServiceClass.DB_SERVER in supp_classes
            and bool(supp_classes - {ServiceClass.DB_SERVER})
        )
        or (
            ServiceClass.ENTRY_POINT in supp_classes
            and bool(supp_classes - {ServiceClass.ENTRY_POINT})
        )
    )
    members = [*causes, *later]
    kinds = sorted({_kind(f) for f in causes})
    effects = (
        f", with downstream effects possible in {_join(sorted({s for f in later for s in f.services}))}"
        if later
        else ""
    )
    statement = (
        f"The {_join(kinds)} in {_join(sources)} appears to be a probable origin "
        f"of the degradation in {episode}{effects}."
    )
    return Hypothesis(
        id="",
        statement=statement,
        confidence=_confidence(len(supporting), len(contradicting), high_ok),
        episode_ids=(episode,) if episode else (),
        supporting=tuple(supporting),
        contradicting=tuple(contradicting),
        finding_ids=_ids(members),
        db_categories=_categories(members),
        kind=HypothesisKind.SOURCE,
        cause_finding_ids=_ids(causes),
        source_services=tuple(sorted(sources)),
        shifts=tuple(shift_set),
    )


def _client_hypotheses(
    cause: Finding,
    findings: Sequence[Finding],
    classes: dict[str, ServiceClass],
    reach: dict[str, set[str]],
) -> list[Hypothesis]:
    """Alternative: pool exhaustion is a local client problem rather than a consequence of the DB server."""
    if not any(classes.get(s) is ServiceClass.DB_SERVER for s in cause.services):
        return []
    out = []
    for f in findings:
        if (
            f.episode_id != cause.episode_id
            or f.role is not Role.SYMPTOM
            or DbCategory.POOL_EXHAUSTION not in f.db_categories
            or f.kind is not SignalKind.POOL_SATURATION
            or not _depends_on(f, cause, reach)
        ):
            continue
        supporting = [f"{_numbers(f)}."]
        pressure = (
            f"Pressure on the database server is suggested by {cause.id}: {cause.count} "
            f"{_kind(cause)} at {_join(cause.services)} since {_clock(cause)}."
        )
        contradicting = [pressure]
        if f.window.start >= cause.window.start:
            contradicting.append(
                f"The deviation in {_join(f.services)} began at {_clock(f)}, after {_join(cause.services)} at {_clock(cause)}."
            )
        else:
            supporting.append(
                f"The deviation in {_join(f.services)} began at {_clock(f)}, before {_join(cause.services)} at {_clock(cause)}."
            )
        episode = f.episode_id or ""
        out.append(
            Hypothesis(
                id="",
                statement=(
                    f"The connection pool exhaustion in {_join(f.services)} might be a client-side "
                    f"problem rather than a consequence of {_join(cause.services)} in {episode}."
                ),
                confidence=_confidence(len(supporting), len(contradicting), False),
                episode_ids=(episode,) if episode else (),
                supporting=tuple(supporting),
                contradicting=tuple(contradicting),
                finding_ids=(f.id,),
                db_categories=_categories([f]),
                kind=HypothesisKind.CLIENT_SIDE,
                source_services=tuple(sorted(f.services)),
            )
        )
    return out


# ---------- LOAD_SPIKE ----------


def _load_spike(
    episode: Episode,
    causes: Sequence[Finding],
    entries: Sequence[str],
    heads: dict[str, list[ScoredEvent]],
    timeline: Timeline,
    config: AnalysisConfig,
) -> Hypothesis:
    base = timeline.baseline
    ep_s = (episode.window.end - episode.window.start).total_seconds()
    base_s = (base.end - base.start).total_seconds() if base is not None else 0.0
    ep_n = sum(
        1
        for s in entries
        for e in heads.get(s, ())
        if episode.window.start <= e.event.record.ts <= episode.window.end
    )
    base_n = (
        sum(
            1
            for s in entries
            for e in heads.get(s, ())
            if base.start <= e.event.record.ts <= base.end
        )
        if base is not None
        else 0
    )
    verdict = Verdict.INCONCLUSIVE
    ep_rate = base_rate = 0.0
    if ep_s > 0 and base_s > 0 and base_n > 0:
        ep_rate = ep_n / ep_s * 60
        base_rate = base_n / base_s * 60
        verdict = (
            Verdict.SUPPORTED
            if ep_rate / base_rate >= config.load_spike_ratio
            else Verdict.NOT_SUPPORTED
        )
    names = _join(sorted(entries))
    supporting: list[str] = []
    contradicting: list[str] = []
    if verdict is Verdict.INCONCLUSIVE:
        contradicting.append(
            f"The log has too few requests at {names} in the baseline window to compare request rates."
        )
    else:
        factor = ep_rate / base_rate if base_rate else 0.0
        sentence = (
            f"Requests at {names} arrive at {ep_rate:.1f} per minute during {episode.id} "
            f"against {base_rate:.1f} per minute in the baseline window (x{factor:.2f})"
        )
        if verdict is Verdict.SUPPORTED:
            supporting.append(f"{sentence}: a load spike is supported by the log.")
        else:
            contradicting.append(
                f"{sentence}: a load spike is not supported by the log."
            )
    return Hypothesis(
        id="",
        statement=(
            f"A surge of incoming load at {names} might have contributed to the "
            f"degradation in {episode.id}."
        ),
        confidence=(
            Confidence.MEDIUM if verdict is Verdict.SUPPORTED else Confidence.LOW
        ),
        episode_ids=(episode.id,),
        supporting=tuple(supporting),
        contradicting=tuple(contradicting),
        finding_ids=_ids(causes),
        db_categories=_categories(causes),
        kind=HypothesisKind.LOAD_SPIKE,
        verdict=verdict,
        source_services=tuple(sorted(entries)),
    )


# ---------- PRE_ONSET_TRIGGER ----------


def _evidence_line(e: ScoredEvent, limit: int) -> EvidenceLine:
    rec = e.event.record
    text = rec.raw
    if len(text) > limit:
        text = text[: max(limit - 3, 0)] + "..."
    return EvidenceLine(
        line_no=rec.line_no, ts=rec.ts, service=rec.service, level=rec.level, text=text
    )


def _pre_onset(
    episode: Episode,
    causes: Sequence[Finding],
    sources: Sequence[str],
    heads: dict[str, list[ScoredEvent]],
    timeline: Timeline,
    config: AnalysisConfig,
) -> Hypothesis | None:
    base = timeline.baseline
    if base is None:
        return None
    width = config.pre_onset_window_seconds
    pre_start = episode.onset - timedelta(seconds=width)
    overlap = (
        min(base.end, episode.onset) - max(base.start, pre_start)
    ).total_seconds()
    rest_s = (base.end - base.start).total_seconds() - max(overlap, 0.0)
    groups: list[tuple[str, str, list[ScoredEvent]]] = []
    for service in sources:
        mine = heads.get(service, ())
        rest = [
            e
            for e in mine
            if base.start <= e.event.record.ts <= base.end
            and not (pre_start <= e.event.record.ts < episode.onset)
        ]
        if not rest:
            continue
        rest_counts = Counter(e.event.template for e in rest)
        window = [e for e in mine if pre_start <= e.event.record.ts < episode.onset]
        by_template: dict[str, list[ScoredEvent]] = {}
        for e in window:
            by_template.setdefault(e.event.template, []).append(e)
        for template, items in by_template.items():
            n = len(items)
            rest_n = rest_counts.get(template, 0)
            unusual = rest_n == 0 or (
                n >= 2
                and rest_s > 0
                and n / width > config.deviation_factor * rest_n / rest_s
            )
            if unusual:
                groups.append((service, template, items))
    if not groups:
        return None
    groups.sort(key=lambda g: (-len(g[2]), g[2][0].event.record.ts, g[0], g[1]))
    limit = max(1, config.max_samples_per_finding)
    chosen: dict[int, ScoredEvent] = {}
    for _, _, items in groups:
        for e in items:
            if len(chosen) < limit:
                chosen.setdefault(e.event.record.line_no, e)
    samples = tuple(
        _evidence_line(chosen[n], config.max_evidence_chars) for n in sorted(chosen)
    )
    parts = []
    for service, template, items in groups[:_MAX_ARGS]:
        text = template
        if len(text) > _MAX_TEMPLATE_CHARS:
            text = text[: _MAX_TEMPLATE_CHARS - 3] + "..."
        first = _hhmmss(items[0].event.record.ts)
        last = _hhmmss(items[-1].event.record.ts)
        parts.append(f'{len(items)} x "{text}" in {service} ({first} to {last})')
    support = (
        f"Events not seen in the rest of the baseline occur within {width}s before the "
        f"onset at {_hhmmss(episode.onset)}: " + "; ".join(parts) + "."
    )
    names = sorted({g[0] for g in groups})
    return Hypothesis(
        id="",
        statement=(
            f"Unusual events in {_join(names)} shortly before the onset of "
            f"{episode.id} might have triggered the degradation."
        ),
        confidence=Confidence.LOW,
        episode_ids=(episode.id,),
        supporting=(support,),
        contradicting=(),
        finding_ids=_ids(causes),
        db_categories=_categories(causes),
        kind=HypothesisKind.PRE_ONSET_TRIGGER,
        source_services=tuple(names),
        samples=samples,
    )


# ---------- EPISODE_LINK ----------


def _percentile50(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(ceil(0.5 * len(ordered)), 1) - 1]


def _min_delta(unit: MetricUnit, config: AnalysisConfig) -> float:
    if unit is MetricUnit.MS:
        return config.min_abs_delta_ms
    if unit is MetricUnit.COUNT:
        return config.min_abs_delta_count
    return 0.0


def _gap_shifts(
    sources: Sequence[str],
    start: datetime,
    end: datetime,
    scored: Sequence[ScoredEvent],
    baselines: _Baselines,
    limits: _Limits,
    config: AnalysisConfig,
) -> list[MetricShift]:
    rows: dict[tuple[str, str], list[tuple[float, int, MetricUnit, bool]]] = {}
    for item in scored:
        ev = item.event
        rec = ev.record
        if ev.continuation_of is not None or rec.service not in sources:
            continue
        if not start <= rec.ts < end:
            continue
        for m in ev.metrics:
            rows.setdefault((rec.service, m.name), []).append(
                (m.value, rec.line_no, m.unit, m.higher_is_worse)
            )
    out = []
    for (service, metric), items in sorted(rows.items()):
        base = baselines.get((service, metric))
        if base is None:
            continue
        p50_base, p95_base = base
        unit, higher = items[0][2], items[0][3]
        values = [r[0] for r in items]
        mid = _percentile50(values)
        if higher:
            deviated = mid > p95_base and mid - p95_base >= _min_delta(unit, config)
        else:
            deviated = p50_base > 0 and mid <= config.composite_drop_ratio * p50_base
        if not deviated:
            continue
        low, high = min(values), max(values)
        worst = high if higher else low
        limit = limits.get((service, metric))
        out.append(
            MetricShift(
                service=service,
                metric=metric,
                unit=unit,
                higher_is_worse=higher,
                window=TimeWindow(start=start, end=end),
                baseline_p50=p50_base,
                baseline_p95=p95_base,
                low=low,
                high=high,
                worst=worst,
                worst_line_no=min(r[1] for r in items if r[0] == worst),
                limit_metric=limit[0] if limit else None,
                limit=limit[1] if limit else None,
                limit_reached=limit is not None
                and worst >= config.pool_saturation_ratio * limit[1],
                relative_deviation=round(
                    abs(worst - p50_base) / max(abs(p50_base), 1.0), 3
                ),
            )
        )
    out.sort(key=_shift_key)
    return out


def _range(s: MetricShift) -> str:
    if s.low == s.high:
        return _fmt(s.low)
    return f"{_fmt(s.low)}–{_fmt(s.high)}"


def _episode_link(
    first: Episode,
    second: Episode,
    causes_first: Sequence[Finding],
    causes_second: Sequence[Finding],
    sources: Sequence[str],
    scored: Sequence[ScoredEvent],
    baselines: _Baselines,
    limits: _Limits,
    config: AnalysisConfig,
) -> Hypothesis | None:
    start = first.recovery if first.recovery is not None else first.window.end
    if start >= second.onset:
        return None
    shifts = _gap_shifts(
        sources, start, second.onset, scored, baselines, limits, config
    )
    if not shifts:
        return None
    shown = shifts[:_MAX_ARGS]
    listed = ", ".join(
        f"{s.metric} {_range(s)} vs baseline {_fmt(s.baseline_p50)}" for s in shown
    )
    gap_s = int((second.onset - start).total_seconds())
    timing = (
        f"{second.id} starts at {_hhmmss(second.onset)}, {gap_s} s after {first.id} "
        f"ended at {_hhmmss(start)}."
    )
    supporting = [
        f"Between {first.id} and {second.id} the source stayed off baseline: {listed}.",
        timing,
    ]
    contradicting: list[str] = []
    second_sources = sorted({s for f in causes_second for s in f.services})
    if second_sources and not set(second_sources) & set(sources):
        contradicting.append(
            f"The degradation of {second.id} originates in {_join(second_sources)}, "
            f"not in {_join(sorted(sources))}."
        )
    members = [*causes_first, *causes_second]
    return Hypothesis(
        id="",
        statement=(
            f"The degradation in {second.id} may be a consequence of {first.id}, "
            f"because {_join(sorted(sources))} did not return to its baseline in between."
        ),
        confidence=Confidence.LOW if contradicting else Confidence.MEDIUM,
        episode_ids=(first.id, second.id),
        supporting=tuple(supporting),
        contradicting=tuple(contradicting),
        finding_ids=_ids(members),
        db_categories=_categories(members),
        kind=HypothesisKind.EPISODE_LINK,
        cause_finding_ids=_ids(members),
        source_services=tuple(sorted(sources)),
        shifts=tuple(shifts),
    )


# ---------- assembly ----------


def build_causes(
    findings: Sequence[Finding],
    graph: ServiceGraph,
    timeline: Timeline,
    scored: Sequence[ScoredEvent] = (),
    baselines: _Baselines | None = None,
    limits: _Limits | None = None,
    config: AnalysisConfig | None = None,
) -> ProbableCauses:
    cfg = config or AnalysisConfig()
    baselines = baselines or {}
    limits = limits or {}
    classes = {s.service: s.service_class for s in graph.services}
    db_servers = {s for s, c in classes.items() if c is ServiceClass.DB_SERVER}
    entries = sorted(s for s, c in classes.items() if c is ServiceClass.ENTRY_POINT)
    reach = _reach(graph)
    heads: dict[str, list[ScoredEvent]] = {}
    for item in scored:
        if item.event.continuation_of is None:
            heads.setdefault(item.event.record.service, []).append(item)

    order = {e.id: i for i, e in enumerate(timeline.episodes)}
    causes_by_episode: dict[str, list[Finding]] = {}
    for f in findings:
        if f.role is Role.CAUSE and f.episode_id is not None:
            causes_by_episode.setdefault(f.episode_id, []).append(f)

    sources_h: list[Hypothesis] = []
    others: list[Hypothesis] = []
    sources_of: dict[str, tuple[str, ...]] = {}
    for episode in timeline.episodes:
        causes = causes_by_episode.get(episode.id)
        if not causes:
            continue
        by_services: dict[tuple[str, ...], list[Finding]] = {}
        for f in causes:
            by_services.setdefault(f.services, []).append(f)
        for group in by_services.values():
            sources_h.append(
                _source_hypothesis(group, findings, classes, reach, db_servers, cfg)
            )
        sources_of[episode.id] = tuple(sorted({s for f in causes for s in f.services}))
        for cause in causes:
            for h in _client_hypotheses(cause, findings, classes, reach):
                if not any(
                    h.finding_ids == x.finding_ids and h.statement == x.statement
                    for x in others
                ):
                    others.append(h)
        if entries:
            others.append(_load_spike(episode, causes, entries, heads, timeline, cfg))
        trigger = _pre_onset(
            episode, causes, sources_of[episode.id], heads, timeline, cfg
        )
        if trigger is not None:
            others.append(trigger)
    for first, second in zip(timeline.episodes, timeline.episodes[1:]):
        if first.id not in causes_by_episode or second.id not in causes_by_episode:
            continue
        link = _episode_link(
            first,
            second,
            causes_by_episode[first.id],
            causes_by_episode[second.id],
            sources_of[first.id],
            scored,
            baselines,
            limits,
            cfg,
        )
        if link is not None:
            others.append(link)

    best: dict[str, int] = {}
    for h in sources_h:
        e = h.episode_ids[0]
        best[e] = min(best.get(e, 9), _RANK[h.confidence])
    primary = [h for h in sources_h if _RANK[h.confidence] == best[h.episode_ids[0]]]
    rest = [h for h in sources_h if h not in primary] + others
    primary.sort(key=lambda h: order.get(h.episode_ids[0], 0))

    def alt_key(h: Hypothesis) -> tuple[int, int, int]:
        return (
            order.get(h.episode_ids[0], 0),
            _RANK[h.confidence],
            _KIND_ORDER[h.kind],
        )

    rest.sort(key=alt_key)
    numbered = [replace(h, id=f"H{i}") for i, h in enumerate([*primary, *rest], 1)]
    return ProbableCauses(
        primary=tuple(numbered[: len(primary)]),
        alternatives=tuple(numbered[len(primary) :]),
    )
