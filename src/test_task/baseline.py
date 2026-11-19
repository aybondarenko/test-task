"""Baseline of normal behavior and anomaly scoring."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta

from .contracts.baseline import (
    AnomalyBasis,
    AnomalyReason,
    BaselineProfile,
    ScoredEvent,
    ServiceMetricStats,
)
from .contracts.common import (
    LEVEL_RANK,
    SIGNAL_PRIORITY,
    AnalysisConfig,
    DbCategory,
    Level,
    MetricStats,
    MetricUnit,
    SignalKind,
    TimeWindow,
)
from .contracts.signals import Event, MetricSample

_WARN_RANK = LEVEL_RANK[Level.WARN]


def nearest_rank(sorted_values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile over sorted (non-empty) values."""
    k = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[min(k, len(sorted_values)) - 1]


def metric_stats(values: Sequence[float]) -> MetricStats:
    """Nearest-rank statistics over a non-empty set of values."""
    ordered = sorted(values)
    return MetricStats(
        count=len(ordered),
        p50=nearest_rank(ordered, 50),
        p95=nearest_rank(ordered, 95),
        max=ordered[-1],
    )


def build_baseline(events: Sequence[Event], config: AnalysisConfig) -> BaselineProfile:
    """Baseline profile: the window before the first anomalous head and metric statistics within it."""
    heads = [e for e in events if e.continuation_of is None]
    if not heads:
        return BaselineProfile(
            window=None, is_fallback=False, first_anomaly_ts=None, metrics=()
        )
    start = min(e.record.ts for e in heads)
    last = max(e.record.ts for e in heads)
    anomaly_ts = min((e.record.ts for e in heads if e.kinds), default=None)
    fallback = False
    if anomaly_ts is None:
        end = last
        members = heads
    elif (anomaly_ts - start).total_seconds() >= config.min_baseline_seconds:
        end = max(e.record.ts for e in heads if e.record.ts < anomaly_ts)
        members = [e for e in heads if e.record.ts <= end]
    else:
        fallback = True
        end = start + timedelta(
            seconds=(last - start).total_seconds() * config.baseline_fallback_fraction
        )
        members = [e for e in heads if e.record.ts <= end]

    values: dict[tuple[str, str], list[float]] = {}
    units: dict[tuple[str, str], MetricUnit] = {}
    for e in members:
        for m in e.metrics:
            key = (e.record.service, m.name)
            values.setdefault(key, []).append(m.value)
            units.setdefault(key, m.unit)
    metrics = tuple(
        ServiceMetricStats(
            service=key[0], metric=key[1], unit=units[key], stats=metric_stats(vals)
        )
        for key, vals in sorted(values.items())
    )
    return BaselineProfile(
        window=TimeWindow(start=start, end=end),
        is_fallback=fallback,
        first_anomaly_ts=anomaly_ts,
        metrics=metrics,
    )


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _threshold_reasons(
    event: Event, config: AnalysisConfig
) -> list[tuple[AnomalyReason, MetricSample | None]]:
    out: list[tuple[AnomalyReason, MetricSample | None]] = []
    fields = event.record.fields
    by_name = {m.name: m for m in event.metrics}

    def add(name: str, detail: str) -> None:
        sample = by_name.get(name)
        value = _num(fields.get(name))
        out.append(
            (
                AnomalyReason(
                    kind=SignalKind.POOL_SATURATION,
                    basis=AnomalyBasis.THRESHOLD,
                    metric=name,
                    value=value,
                    baseline_p95=None,
                    detail=detail,
                ),
                sample,
            )
        )

    idle = _num(fields.get("pool_idle"))
    if idle is not None and idle == 0:
        add("pool_idle", "pool_idle is 0: no idle connections left")
    waiting = _num(fields.get("waiting_clients"))
    if waiting is not None and waiting > 0:
        add("waiting_clients", "clients are waiting for connections")
    active = _num(fields.get("active_connections"))
    max_conn = _num(fields.get("max_connections"))
    if (
        active is not None
        and max_conn is not None
        and active >= config.pool_saturation_ratio * max_conn
    ):
        add("active_connections", "active connections near max_connections")
    return out


def _deviation_reason(
    event: Event,
    sample: MetricSample,
    lookup: dict[tuple[str, str], MetricStats],
    config: AnalysisConfig,
) -> AnomalyReason | None:
    base = lookup.get((event.record.service, sample.name))
    if base is None or base.count < config.min_baseline_samples:
        return None
    value = sample.value
    if sample.higher_is_worse:
        if sample.unit == MetricUnit.MS:
            min_delta = config.min_abs_delta_ms
        elif sample.unit == MetricUnit.COUNT:
            min_delta = config.min_abs_delta_count
        else:
            min_delta = 0.0
        if not (
            value > config.deviation_factor * base.p95 and value - base.p95 >= min_delta
        ):
            return None
        detail = f"{sample.name} is well above baseline p95"
    else:
        if not value < config.drop_ratio * base.p50:
            return None
        detail = f"{sample.name} dropped below baseline p50"
    return AnomalyReason(
        kind=sample.kind,
        basis=AnomalyBasis.BASELINE_DEVIATION,
        metric=sample.name,
        value=value,
        baseline_p95=base.p95,
        detail=detail,
    )


def _db_categories(
    event: Event, kinds: set[SignalKind], latency_hit: bool
) -> tuple[DbCategory, ...]:
    if not event.db_related:
        return ()
    cats: set[DbCategory] = set()
    if SignalKind.RESTART in kinds:
        cats.add(DbCategory.SERVER_RESTART)
    if (
        SignalKind.CONNECTION_ERROR in kinds
        or SignalKind.DEPENDENCY_UNAVAILABLE in kinds
    ):
        cats.add(DbCategory.CONNECTION_ERRORS)
    if event.aborted_transaction:
        cats.add(DbCategory.ABORTED_TRANSACTIONS)
    if SignalKind.POOL_SATURATION in kinds:
        cats.add(
            DbCategory.SERVER_SATURATION
            if event.db_server
            else DbCategory.POOL_EXHAUSTION
        )
    elif SignalKind.TIMEOUT in kinds and SignalKind.CONNECTION_ERROR not in kinds:
        cats.add(DbCategory.QUERY_TIMEOUT)
    if SignalKind.LOCK in kinds:
        cats.add(DbCategory.LOCKS)
    if SignalKind.REPLICATION_LAG in kinds:
        cats.add(DbCategory.REPLICATION_LAG)
    if latency_hit:
        cats.add(DbCategory.LATENCY)
    return tuple(c for c in DbCategory if c in cats)


def _score_one(
    event: Event,
    lookup: dict[tuple[str, str], MetricStats],
    config: AnalysisConfig,
) -> ScoredEvent:
    if event.continuation_of is not None:
        return ScoredEvent(event, False, None, (), ())
    reasons: list[AnomalyReason] = []
    kinds: set[SignalKind] = set(event.kinds)
    for kind in event.kinds:
        basis = (
            AnomalyBasis.LEVEL
            if kind in (SignalKind.WARNING, SignalKind.ERROR)
            else AnomalyBasis.KEYWORD
        )
        reasons.append(
            AnomalyReason(kind, basis, None, None, None, f"{kind.value} signal")
        )
    for reason, _sample in _threshold_reasons(event, config):
        reasons.append(reason)
        kinds.add(reason.kind)
    latency_hit = False
    for sample in event.metrics:
        dev = _deviation_reason(event, sample, lookup, config)
        if dev is not None:
            reasons.append(dev)
            kinds.add(dev.kind)
            if dev.kind == SignalKind.LATENCY and sample.db_related:
                latency_hit = True
    if not reasons:
        return ScoredEvent(event, False, None, (), ())
    if LEVEL_RANK[event.record.level] >= _WARN_RANK:
        kinds.update(m.kind for m in event.metrics)
    primary = next(k for k in SIGNAL_PRIORITY if k in kinds)
    return ScoredEvent(
        event=event,
        anomalous=True,
        primary_kind=primary,
        reasons=tuple(reasons),
        db_categories=_db_categories(event, kinds, latency_hit),
    )


def score_events(
    events: Sequence[Event], baseline: BaselineProfile, config: AnalysisConfig
) -> tuple[ScoredEvent, ...]:
    """Mark each event as an anomaly or noise; order is preserved."""
    lookup = {(m.service, m.metric): m.stats for m in baseline.metrics}
    return tuple(_score_one(e, lookup, config) for e in events)
