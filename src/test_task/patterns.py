"""Grouping anomalous heads into patterns, request traces and noise counters."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from .baseline import metric_stats
from .contracts.baseline import BaselineProfile, ScoredEvent
from .contracts.common import (
    LEVEL_RANK,
    SIGNAL_PRIORITY,
    AnalysisConfig,
    DbCategory,
    EvidenceLine,
    Level,
    MetricSummary,
    MetricUnit,
    SignalKind,
    TimeWindow,
)
from .contracts.patterns import (
    PatternGroup,
    PatternResult,
    RequestTrace,
    TemplateCount,
    TraceHop,
)


def _order(item: ScoredEvent) -> tuple[datetime, int]:
    return (item.event.record.ts, item.event.record.line_no)


def _evidence(item: ScoredEvent, config: AnalysisConfig) -> EvidenceLine:
    rec = item.event.record
    text = rec.raw
    limit = config.max_evidence_chars
    if len(text) > limit:
        text = text[: max(limit - 3, 0)] + "..."
    return EvidenceLine(
        line_no=rec.line_no, ts=rec.ts, service=rec.service, level=rec.level, text=text
    )


def _pick_samples(
    members: Sequence[ScoredEvent], config: AnalysisConfig
) -> tuple[EvidenceLine, ...]:
    limit = max(1, config.max_samples_per_finding)
    chosen = [members[0]]
    seen = {members[0].event.template}
    for item in members[1:]:
        if len(chosen) >= limit:
            break
        if item.event.template not in seen:
            seen.add(item.event.template)
            chosen.append(item)
    for item in members[1:]:
        if len(chosen) >= limit:
            break
        if item not in chosen:
            chosen.append(item)
    chosen.sort(key=lambda s: s.event.record.line_no)
    return tuple(_evidence(s, config) for s in chosen)


def _summaries(
    service: str, members: Sequence[ScoredEvent], baseline: BaselineProfile
) -> tuple[MetricSummary, ...]:
    values: dict[str, list[float]] = {}
    units: dict[str, MetricUnit] = {}
    for item in members:
        for sample in item.event.metrics:
            values.setdefault(sample.name, []).append(sample.value)
            units.setdefault(sample.name, sample.unit)
    base = {(m.service, m.metric): m.stats.p95 for m in baseline.metrics}
    out = []
    for name in sorted(values):
        stats = metric_stats(values[name])
        out.append(
            MetricSummary(
                metric=name,
                unit=units[name],
                count=stats.count,
                min=min(values[name]),
                p50=stats.p50,
                max=stats.max,
                baseline_p95=base.get((service, name)),
            )
        )
    return tuple(out)


def _build_group(
    kind: SignalKind,
    service: str,
    members: Sequence[ScoredEvent],
    baseline: BaselineProfile,
    config: AnalysisConfig,
) -> PatternGroup:
    cats = {c for m in members for c in m.db_categories}
    templates = Counter(m.event.template for m in members)
    max_level: Level = max(
        (m.event.record.level for m in members), key=lambda lv: LEVEL_RANK[lv]
    )
    return PatternGroup(
        id="",
        kind=kind,
        service=service,
        window=TimeWindow(
            start=members[0].event.record.ts, end=members[-1].event.record.ts
        ),
        count=len(members),
        max_level=max_level,
        db_related=any(m.event.db_related or m.db_categories for m in members),
        db_categories=tuple(c for c in DbCategory if c in cats),
        templates=tuple(
            TemplateCount(template=t, count=c)
            for t, c in sorted(templates.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        metrics=_summaries(service, members, baseline),
        samples=_pick_samples(members, config),
        request_ids=tuple(
            sorted({m.event.request_id for m in members if m.event.request_id})
        ),
        peers=tuple(sorted({p for m in members for p in m.event.peers})),
        line_nos=tuple(m.event.record.line_no for m in members),
    )


def _groups(
    heads: Sequence[ScoredEvent], baseline: BaselineProfile, config: AnalysisConfig
) -> tuple[PatternGroup, ...]:
    by_key: dict[tuple[SignalKind, str], list[ScoredEvent]] = {}
    for item in heads:
        assert item.primary_kind is not None
        by_key.setdefault((item.primary_kind, item.event.record.service), []).append(
            item
        )
    groups: list[PatternGroup] = []
    for (kind, service), items in by_key.items():
        run: list[ScoredEvent] = []
        for item in items:
            if run:
                gap = (item.event.record.ts - run[-1].event.record.ts).total_seconds()
                if gap > config.group_gap_seconds:
                    groups.append(_build_group(kind, service, run, baseline, config))
                    run = []
            run.append(item)
        groups.append(_build_group(kind, service, run, baseline, config))
    groups.sort(
        key=lambda g: (g.window.start, SIGNAL_PRIORITY.index(g.kind), g.service)
    )
    return tuple(replace(g, id="P" + str(i)) for i, g in enumerate(groups, 1))


def _traces(
    heads: Sequence[ScoredEvent], config: AnalysisConfig
) -> tuple[tuple[RequestTrace, ...], tuple[str, ...]]:
    order_owner: dict[str, str] = {}
    for item in heads:
        ev = item.event
        if ev.request_id and ev.order_id and ev.order_id not in order_owner:
            order_owner[ev.order_id] = ev.request_id
    by_req: dict[str, list[ScoredEvent]] = {}
    for item in heads:
        ev = item.event
        rid = ev.request_id or (order_owner.get(ev.order_id) if ev.order_id else None)
        if rid:
            by_req.setdefault(rid, []).append(item)
    traces: list[RequestTrace] = []
    for rid, items in by_req.items():
        services: list[str] = []
        for item in items:
            if item.event.record.service not in services:
                services.append(item.event.record.service)
        anomalous = sum(1 for i in items if i.anomalous)
        if len(services) < 2 or anomalous < 1:
            continue
        hops = tuple(
            TraceHop(
                line_no=i.event.record.line_no,
                ts=i.event.record.ts,
                service=i.event.record.service,
                level=i.event.record.level,
                anomalous=i.anomalous,
                primary_kind=i.primary_kind,
                summary=i.event.template,
            )
            for i in items
        )
        traces.append(
            RequestTrace(
                request_id=rid,
                order_ids=tuple(
                    sorted({i.event.order_id for i in items if i.event.order_id})
                ),
                services=tuple(services),
                hops=hops,
                anomalous_hops=anomalous,
                window=TimeWindow(start=hops[0].ts, end=hops[-1].ts),
            )
        )
    traces.sort(key=lambda t: (t.window.start, t.request_id))
    best = sorted(
        traces,
        key=lambda t: (
            -t.anomalous_hops,
            -len(t.services),
            t.window.start,
            t.request_id,
        ),
    )[: max(0, config.max_example_traces)]
    return tuple(traces), tuple(t.request_id for t in best)


def find_patterns(
    scored: Sequence[ScoredEvent],
    baseline: BaselineProfile,
    config: AnalysisConfig,
) -> PatternResult:
    """Group anomalies, collect request traces and count noise."""
    continuation = sum(1 for s in scored if s.event.continuation_of is not None)
    all_heads = sorted(
        (s for s in scored if s.event.continuation_of is None), key=_order
    )
    anomalous = [s for s in all_heads if s.anomalous]
    traces, examples = _traces(all_heads, config)
    return PatternResult(
        groups=_groups(anomalous, baseline, config),
        traces=traces,
        example_trace_ids=examples,
        noise_lines=len(all_heads) - len(anomalous),
        anomalous_lines=len(anomalous),
        continuation_lines=continuation,
    )
