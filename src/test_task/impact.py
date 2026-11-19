"""Impact section: affected services and problem propagation checked against dependency edges."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .contracts.baseline import ScoredEvent
from .contracts.causality import (
    EdgeSource,
    Finding,
    Impact,
    PropagationStep,
    ServiceClass,
    ServiceGraph,
    ServiceImpact,
)
from .contracts.common import LEVEL_RANK, AnalysisConfig, EvidenceLine, Level, Role
from .contracts.patterns import PatternResult
from .contracts.timeline import Episode, Timeline

_ERROR_RANK = LEVEL_RANK[Level.ERROR]
_VIA_ORDER = (EdgeSource.PEER_FIELD, EdgeSource.DB_ACCESS, EdgeSource.TRACE_ORDER)


def _heads(scored: Sequence[ScoredEvent]) -> list[ScoredEvent]:
    return [s for s in scored if s.event.continuation_of is None]


def _is_error(item: ScoredEvent) -> bool:
    return LEVEL_RANK[item.event.record.level] >= _ERROR_RANK


def _is_failed(item: ScoredEvent) -> bool:
    status = item.event.record.fields.get("status")
    is_5xx = (
        isinstance(status, int)
        and not isinstance(status, bool)
        and 500 <= status <= 599
    )
    return _is_error(item) or is_5xx


def _line(item: ScoredEvent, config: AnalysisConfig) -> EvidenceLine:
    rec = item.event.record
    text = rec.raw
    limit = config.max_evidence_chars
    if len(text) > limit:
        text = text[: max(limit - 3, 0)] + "..."
    return EvidenceLine(
        line_no=rec.line_no,
        ts=rec.ts,
        service=rec.service,
        level=rec.level,
        text=text,
    )


def build_services(
    scored: Sequence[ScoredEvent],
    graph: ServiceGraph,
    findings: Sequence[Finding],
) -> tuple[ServiceImpact, ...]:
    classes = {s.service: s.service_class for s in graph.services}
    first: dict[str, datetime] = {}
    anomalous: dict[str, int] = {}
    errors: dict[str, int] = {}
    failed: dict[str, set[str]] = {}
    for item in _heads(scored):
        rec = item.event.record
        svc = rec.service
        if item.anomalous:
            anomalous[svc] = anomalous.get(svc, 0) + 1
            if svc not in first or rec.ts < first[svc]:
                first[svc] = rec.ts
        if _is_error(item):
            errors[svc] = errors.get(svc, 0) + 1
        if _is_failed(item) and item.event.request_id:
            failed.setdefault(svc, set()).add(item.event.request_id)
    out = [
        ServiceImpact(
            service=svc,
            service_class=classes.get(svc, ServiceClass.INTERNAL),
            first_affected=first[svc],
            anomalous_events=anomalous[svc],
            error_events=errors.get(svc, 0),
            failed_requests=len(failed.get(svc, ())),
            finding_ids=tuple(f.id for f in findings if svc in f.services),
        )
        for svc in anomalous
    ]
    out.sort(key=lambda s: (s.first_affected, s.service))
    return tuple(out)


def count_failed_requests(scored: Sequence[ScoredEvent]) -> int:
    return len(
        {
            item.event.request_id
            for item in _heads(scored)
            if _is_failed(item) and item.event.request_id
        }
    )


def _episode_steps(
    episode: Episode,
    scored: Sequence[ScoredEvent],
    graph: ServiceGraph,
    findings: Sequence[Finding],
    config: AnalysisConfig,
) -> list[PropagationStep]:
    first: dict[str, ScoredEvent] = {}
    for item in _heads(scored):
        ts = item.event.record.ts
        if item.anomalous and episode.window.start <= ts <= episode.window.end:
            svc = item.event.record.service
            if svc not in first or ts < first[svc].event.record.ts:
                first[svc] = item
    causes = {
        s
        for f in findings
        if f.episode_id == episode.id and f.role is Role.CAUSE
        for s in f.services
    }
    via: dict[tuple[str, str], EdgeSource] = {}
    for e in graph.edges:
        key = (e.dependency, e.dependent)
        if key[0] not in first or key[1] not in first:
            continue
        if key not in via or _VIA_ORDER.index(e.source) < _VIA_ORDER.index(via[key]):
            via[key] = e.source

    explicit: dict[str, list[str]] = {}
    traced: dict[str, list[str]] = {}
    for (src, dst), source in sorted(via.items()):
        target = traced if source is EdgeSource.TRACE_ORDER else explicit
        target.setdefault(src, []).append(dst)

    steps: list[PropagationStep] = []

    def add(src: str, dst: str) -> None:
        steps.append(
            PropagationStep(
                episode_id=episode.id,
                from_service=src,
                to_service=dst,
                via=via[(src, dst)],
                first_seen=first[dst].event.record.ts,
                evidence=(_line(first[src], config), _line(first[dst], config)),
            )
        )

    level = {s: 0 for s in sorted(causes) if s in first}
    frontier = sorted(level)
    while frontier:
        found: dict[str, int] = {}
        for src in frontier:
            for dst in explicit.get(src, ()):
                if dst not in level:
                    add(src, dst)
                    found[dst] = level[src] + 1
        if not found:
            best: dict[str, str] = {}
            for src in sorted(level, key=lambda x: (level[x], x)):
                for dst in traced.get(src, ()):
                    if dst not in level and dst not in best:
                        best[dst] = src
            for dst in sorted(best):
                add(best[dst], dst)
                found[dst] = level[best[dst]] + 1
        level.update(found)
        frontier = sorted(found)
    return steps


def build_impact(
    scored: Sequence[ScoredEvent],
    patterns: PatternResult,
    timeline: Timeline,
    graph: ServiceGraph,
    findings: Sequence[Finding],
    config: AnalysisConfig,
) -> Impact:
    steps: list[PropagationStep] = []
    for episode in timeline.episodes:
        steps.extend(_episode_steps(episode, scored, graph, findings, config))
    steps.sort(key=lambda p: (p.episode_id, p.first_seen, p.from_service, p.to_service))
    return Impact(
        services=build_services(scored, graph, findings),
        propagation=tuple(steps),
        failed_requests=count_failed_requests(scored),
        example_trace_ids=patterns.example_trace_ids,
    )
