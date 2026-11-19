"""Grouping anomalies into patterns and request traces (``patterns`` contract).

Implementation: ``test_task.patterns.find_patterns`` (``FindPatterns``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .baseline import BaselineProfile, ScoredEvent
from .common import (
    AnalysisConfig,
    DbCategory,
    EvidenceLine,
    Level,
    MetricSummary,
    SignalKind,
    TimeWindow,
)


@dataclass(frozen=True)
class TemplateCount:
    """How many events of the group have this message template."""

    template: str
    count: int


@dataclass(frozen=True)
class PatternGroup:
    """A group of repeated anomalous events of one kind and service within one window.

    * ``id`` — ``"P<n>"``, n from 1 in the order of groups in ``PatternResult.groups``.
    * group key — (``kind`` = ``ScoredEvent.primary_kind``, ``service``); the key's events in
      (ts, line_no) order are split into windows at gaps > ``group_gap_seconds``.
    * ``count`` — number of anomalous heads (stack-trace continuations are not counted), >= 1.
    * ``window`` — from the first to the last event of the group.
    * ``db_related`` — at least one event is DB-related; ``db_categories`` — union of
      ``ScoredEvent.db_categories`` in ``DbCategory`` declaration order.
    * ``templates`` — by descending count, then by template.
    * ``metrics`` — one per metric present in the group's events, by name.
    * ``samples`` — 1..``max_samples_per_finding`` distinct lines, by ``line_no``; the first
      event of the group is always included.
    * ``request_ids`` — unique, sorted; ``peers`` — union of ``Event.peers``,
      sorted; ``line_nos`` — all heads of the group in ascending order.
    """

    id: str
    kind: SignalKind
    service: str
    window: TimeWindow
    count: int
    max_level: Level
    db_related: bool
    db_categories: tuple[DbCategory, ...]
    templates: tuple[TemplateCount, ...]
    metrics: tuple[MetricSummary, ...]
    samples: tuple[EvidenceLine, ...]
    request_ids: tuple[str, ...]
    peers: tuple[str, ...]
    line_nos: tuple[int, ...]


@dataclass(frozen=True)
class TraceHop:
    """A request hop: one event head with this request. ``summary`` is the message template."""

    line_no: int
    ts: datetime
    service: str
    level: Level
    anomalous: bool
    primary_kind: SignalKind | None
    summary: str


@dataclass(frozen=True)
class RequestTrace:
    """Events of a single request across the chain of services.

    Events are linked by ``request_id``; an event with only ``order_id`` is attached to the
    request in which that ``order_id`` appeared first. ``hops`` — by (ts, line_no);
    ``services`` — unique, in order of first appearance; ``order_ids`` — sorted;
    ``window`` — from the first to the last hop.
    """

    request_id: str
    order_ids: tuple[str, ...]
    services: tuple[str, ...]
    hops: tuple[TraceHop, ...]
    anomalous_hops: int
    window: TimeWindow


@dataclass(frozen=True)
class PatternResult:
    """Grouping result.

    * ``groups`` — by (window.start, index of kind in ``SIGNAL_PRIORITY``, service).
    * ``traces`` — all requests with >= 2 distinct services and >= 1 anomalous hop, by
      (window.start, request_id).
    * ``example_trace_ids`` — up to ``max_example_traces`` ids from ``traces``, chosen by
      (more anomalous hops, more services, earlier start, request_id).
    * ``noise_lines`` — non-anomalous heads (discarded noise); ``anomalous_lines`` —
      anomalous heads; ``continuation_lines`` — stack-trace continuation lines.
    Invariants: ``noise_lines + anomalous_lines + continuation_lines`` = number of events;
    ``sum(g.count for g in groups) == anomalous_lines``.
    """

    groups: tuple[PatternGroup, ...]
    traces: tuple[RequestTrace, ...]
    example_trace_ids: tuple[str, ...]
    noise_lines: int
    anomalous_lines: int
    continuation_lines: int


class FindPatterns(Protocol):
    """``test_task.patterns.find_patterns``: group anomalies and collect traces.

    ``baseline`` is needed for ``MetricSummary.baseline_p95``. Pure deterministic function.
    """

    def __call__(
        self,
        scored: Sequence[ScoredEvent],
        baseline: BaselineProfile,
        config: AnalysisConfig,
    ) -> PatternResult: ...
