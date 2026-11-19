"""Incident timeline and "baseline vs incident" comparison (``timeline`` contract).

Implementation: ``test_task.timeline.build_timeline`` (``BuildTimeline``) and
``test_task.timeline.compare_windows`` (``CompareWindows``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from .baseline import BaselineProfile, ScoredEvent
from .common import AnalysisConfig, EvidenceLine, MetricStats, MetricUnit, TimeWindow


class RecoveryStatus(str, Enum):
    """Whether the episode's recovery is observed in the log."""

    OBSERVED = "observed"
    """Recovery is visible, and the log continues for at least ``recovery_quiet_seconds`` after it."""
    OBSERVED_SHORT_WINDOW = "observed_short_window"
    """There are recovery signs (``Event.recovery_marker``), but the log ends less than
    ``recovery_quiet_seconds`` after them: "recovery is observed, but the observation window is short"."""
    NOT_OBSERVED = "not_observed"
    """Neither recovery signs nor quiet after the episode."""


@dataclass(frozen=True)
class Episode:
    """A degradation episode: onset → peak → recovery.

    Buckets are intervals ``[k*bucket_seconds, (k+1)*bucket_seconds)`` from the Unix epoch (UTC).
    A bucket is active if it holds >= ``active_bucket_min_events`` anomalous heads. An episode is
    a maximal run of active buckets where the gap between the end of one and the start of the next
    is <= ``episode_merge_gap_seconds``.

    * ``id`` — ``"E<n>"`` in time order, from 1.
    * ``onset`` — time of the first anomalous head in the first active bucket.
    * ``peak`` — the bucket with the maximum ``anomalous + errors`` (errors are heads with level >= ERROR;
      ties go to the earlier one); ``peak_anomalous``/``peak_errors`` are its counters.
    * ``last_anomaly`` — time of the last anomalous head in the last active bucket.
    * ``recovery_evidence`` — heads with ``event.recovery_marker`` from services anomalous in the episode,
      timed in [``peak.start``, onset of the next episode or end of log), ordered by (ts, line_no), at
      most ``max_samples_per_finding`` (the first ones).
    * ``recovery_status``/``recovery``, first matching rule:
      1. ``recovery_evidence`` is non-empty → ``recovery`` = time of the first of them; status OBSERVED if
         the log continues >= ``recovery_quiet_seconds`` after the last of them, otherwise
         OBSERVED_SHORT_WINDOW;
      2. after the end of the last active bucket the log has another >= ``recovery_quiet_seconds`` without
         active buckets → OBSERVED, ``recovery`` = end of that bucket;
      3. otherwise NOT_OBSERVED, ``recovery = None``.
      Sign-based recovery may come before ``last_anomaly``: residual deviations after it stay
      in the episode, and "between episodes" metrics are counted from ``recovery``.
    * ``recovered`` ⇔ ``recovery_status != NOT_OBSERVED`` ⇔ ``recovery is not None``.
    * ``window`` — from ``onset`` to ``max(last_anomaly, recovery)`` (``last_anomaly`` if
      ``recovery is None``).
    * ``anomalous_events``/``error_events`` — anomalous heads and heads >= ERROR within ``window``.
    """

    id: str
    onset: datetime
    peak: TimeWindow
    peak_anomalous: int
    peak_errors: int
    last_anomaly: datetime
    recovered: bool
    recovery: datetime | None
    window: TimeWindow
    anomalous_events: int
    error_events: int
    recovery_status: RecoveryStatus = RecoveryStatus.NOT_OBSERVED
    recovery_evidence: tuple[EvidenceLine, ...] = ()


@dataclass(frozen=True)
class Timeline:
    """Timeline: baseline and episodes.

    ``log_window`` — from the first to the last record (``None`` for empty input).
    ``residual_anomalies`` — anomalous heads outside all episode windows (scattered deviations).
    There may be several episodes or none (no incident detected).
    """

    log_window: TimeWindow | None
    baseline: TimeWindow | None
    baseline_is_fallback: bool
    episodes: tuple[Episode, ...]
    residual_anomalies: int


@dataclass(frozen=True)
class MetricComparison:
    """A service metric: baseline vs incident.

    The incident is the union of all episode windows. ``p95_ratio`` = incident.p95 / baseline.p95,
    rounded to 2 decimals; ``None`` if either side is missing or baseline.p95 == 0.
    """

    service: str
    metric: str
    unit: MetricUnit
    baseline: MetricStats | None
    incident: MetricStats | None
    p95_ratio: float | None


@dataclass(frozen=True)
class ErrorRateComparison:
    """Share of heads with level >= ERROR among all heads of a service: baseline vs incident.

    ``*_rate`` = errors / total, rounded to 4 decimals; ``None`` if total == 0.
    """

    service: str
    baseline_errors: int
    baseline_total: int
    baseline_rate: float | None
    incident_errors: int
    incident_total: int
    incident_rate: float | None


@dataclass(frozen=True)
class WindowComparison:
    """Comparison tables: ``metrics`` by (service, metric), ``error_rates`` by service.

    A row is included if there is at least one value/head in the baseline or in the incident.
    """

    metrics: tuple[MetricComparison, ...]
    error_rates: tuple[ErrorRateComparison, ...]


class BuildTimeline(Protocol):
    """``test_task.timeline.build_timeline``: baseline window and episodes. Pure function."""

    def __call__(
        self,
        scored: Sequence[ScoredEvent],
        baseline: BaselineProfile,
        config: AnalysisConfig,
    ) -> Timeline: ...


class CompareWindows(Protocol):
    """``test_task.timeline.compare_windows``: per-service baseline vs incident comparison.

    If there are no episodes, ``incident`` is empty in all rows (``None``/zeros).
    """

    def __call__(
        self, scored: Sequence[ScoredEvent], timeline: Timeline, config: AnalysisConfig
    ) -> WindowComparison: ...
