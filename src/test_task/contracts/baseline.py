"""Baseline of normal behavior and anomaly scoring (``baseline`` contract).

Implementation: ``test_task.baseline.build_baseline`` (``BuildBaseline``) and
``test_task.baseline.score_events`` (``ScoreEvents``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from .common import (
    AnalysisConfig,
    DbCategory,
    MetricStats,
    MetricUnit,
    SignalKind,
    TimeWindow,
)
from .signals import Event


@dataclass(frozen=True)
class ServiceMetricStats:
    """Statistics of metric ``metric`` of service ``service`` in the baseline window."""

    service: str
    metric: str
    unit: MetricUnit
    stats: MetricStats


@dataclass(frozen=True)
class BaselineProfile:
    """Baseline profile.

    * ``window`` — the baseline window: from the time of the first "head" (non-continuation) to a moment strictly
      before the first head with non-empty ``Event.kinds``; if there is none, the whole log.
      ``None`` only for empty input.
    * ``is_fallback`` — the baseline window is shorter than ``min_baseline_seconds``, so the first
      ``baseline_fallback_fraction`` of the log duration is used. The report reflects this in Uncertainty.
    * ``first_anomaly_ts`` — time of the first head with non-empty ``kinds``, ``None`` if there is none.
    * ``metrics`` — for each (service, metric) pair with at least one value in the window; ordered
      by (service, metric).
    """

    window: TimeWindow | None
    is_fallback: bool
    first_anomaly_ts: datetime | None
    metrics: tuple[ServiceMetricStats, ...]


class AnomalyBasis(str, Enum):
    """Basis on which an event is deemed anomalous."""

    LEVEL = "level"
    """Record level is WARN or higher."""
    KEYWORD = "keyword"
    """Keyword or field (5xx status, attempt>1, restart_count increased, ready=false)."""
    THRESHOLD = "threshold"
    """Absolute threshold: pool_idle == 0, waiting_clients > 0, active >= ratio * max_connections."""
    BASELINE_DEVIATION = "baseline_deviation"
    """Metric deviated from the baseline (``deviation_factor``, ``drop_ratio``)."""


@dataclass(frozen=True)
class AnomalyReason:
    """A single anomaly reason. For metrics, ``metric``/``value`` are set (and ``baseline_p95``
    for BASELINE_DEVIATION); ``detail`` is a short English text for the report."""

    kind: SignalKind
    basis: AnomalyBasis
    metric: str | None
    value: float | None
    baseline_p95: float | None
    detail: str


@dataclass(frozen=True)
class ScoredEvent:
    """An event with an "anomalous/normal" decision.

    Invariants:
    * stack-trace continuations (``event.continuation_of is not None``): ``anomalous=False``,
      ``primary_kind=None``, ``reasons=()``, ``db_categories=()`` — they are accounted for via the head;
    * ``anomalous`` ⇔ ``reasons`` is non-empty ⇔ ``primary_kind is not None``;
    * ``primary_kind`` — the first by ``SIGNAL_PRIORITY`` among: ``event.kinds``; kinds of metrics
      triggered by THRESHOLD/BASELINE_DEVIATION; and for level >= WARN, kinds of all the event's metrics;
    * ``db_categories`` — only for anomalous events with ``event.db_related`` (a DB sign in the
      event itself), in ``DbCategory`` declaration order, by these rules: RESTART → SERVER_RESTART;
      CONNECTION_ERROR or DEPENDENCY_UNAVAILABLE → CONNECTION_ERRORS; ``event.aborted_transaction``
      → ABORTED_TRANSACTIONS; TIMEOUT without POOL_SATURATION and without CONNECTION_ERROR → QUERY_TIMEOUT
      (a dropped connection is not a query timeout); POOL_SATURATION → SERVER_SATURATION if
      ``event.db_server``, otherwise POOL_EXHAUSTION; LOCK → LOCKS; REPLICATION_LAG → REPLICATION_LAG;
      a triggered LATENCY metric with ``db_related`` → LATENCY.
    """

    event: Event
    anomalous: bool
    primary_kind: SignalKind | None
    reasons: tuple[AnomalyReason, ...]
    db_categories: tuple[DbCategory, ...]


class BuildBaseline(Protocol):
    """``test_task.baseline.build_baseline``: baseline profile from events. Pure function."""

    def __call__(
        self, events: Sequence[Event], config: AnalysisConfig
    ) -> BaselineProfile: ...


class ScoreEvents(Protocol):
    """``test_task.baseline.score_events``: score each event, preserving order.

    A head is anomalous if any of these rules holds:
    1. non-empty ``event.kinds`` (basis LEVEL for WARNING/ERROR, KEYWORD otherwise);
    2. THRESHOLD: ``pool_idle == 0``; ``waiting_clients > 0``;
       ``active_connections >= pool_saturation_ratio * max_connections`` in the same record;
    3. BASELINE_DEVIATION: the profile has at least ``min_baseline_samples`` values
       for the (service, metric) pair, and
       * higher-is-worse: ``value > deviation_factor * p95`` and ``value - p95 >= min_abs_delta_ms``
         (unit ms) / ``min_abs_delta_count`` (unit count) / ``0`` (other units);
       * lower-is-worse: ``value < drop_ratio * p50``.
    A metric without a baseline is not evaluated by rule 3. An INFO record with no triggers is normal (noise).
    """

    def __call__(
        self, events: Sequence[Event], baseline: BaselineProfile, config: AnalysisConfig
    ) -> tuple[ScoredEvent, ...]: ...
