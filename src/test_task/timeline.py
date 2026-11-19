"""Incident timeline and "baseline vs incident" comparison."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from .baseline import metric_stats
from .contracts.baseline import BaselineProfile, ScoredEvent
from .contracts.common import (
    LEVEL_RANK,
    AnalysisConfig,
    EvidenceLine,
    Level,
    MetricStats,
    MetricUnit,
    TimeWindow,
)
from .contracts.timeline import (
    Episode,
    ErrorRateComparison,
    MetricComparison,
    RecoveryStatus,
    Timeline,
    WindowComparison,
)

_ERROR_RANK = LEVEL_RANK[Level.ERROR]


def _is_error(item: ScoredEvent) -> bool:
    return LEVEL_RANK[item.event.record.level] >= _ERROR_RANK


def _order(item: ScoredEvent) -> tuple[datetime, int]:
    return (item.event.record.ts, item.event.record.line_no)


def _from_epoch(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _evidence(item: ScoredEvent, config: AnalysisConfig) -> EvidenceLine:
    rec = item.event.record
    text = rec.raw
    limit = config.max_evidence_chars
    if len(text) > limit:
        text = text[: max(limit - 3, 0)] + "..."
    return EvidenceLine(
        line_no=rec.line_no, ts=rec.ts, service=rec.service, level=rec.level, text=text
    )


def _group_buckets(keys: list[int], config: AnalysisConfig) -> list[list[int]]:
    groups: list[list[int]] = []
    width = config.bucket_seconds
    for key in keys:
        gap = (key - groups[-1][-1] - 1) * width if groups else 0
        if groups and gap <= config.episode_merge_gap_seconds:
            groups[-1].append(key)
        else:
            groups.append([key])
    return groups


def build_timeline(
    scored: Sequence[ScoredEvent], baseline: BaselineProfile, config: AnalysisConfig
) -> Timeline:
    """Baseline window and degradation episodes over time buckets."""
    if not scored:
        return Timeline(
            log_window=None,
            baseline=baseline.window,
            baseline_is_fallback=baseline.is_fallback,
            episodes=(),
            residual_anomalies=0,
        )
    times = [s.event.record.ts for s in scored]
    log_window = TimeWindow(start=min(times), end=max(times))
    width = config.bucket_seconds
    heads = [s for s in scored if s.event.continuation_of is None]

    anomalous: dict[int, list[ScoredEvent]] = {}
    errors: dict[int, int] = {}
    for s in heads:
        key = int(s.event.record.ts.timestamp() // width)
        if s.anomalous:
            anomalous.setdefault(key, []).append(s)
        if _is_error(s):
            errors[key] = errors.get(key, 0) + 1
    active = sorted(
        k for k, v in anomalous.items() if len(v) >= config.active_bucket_min_events
    )
    groups = _group_buckets(active, config)

    episodes: list[Episode] = []
    for idx, group in enumerate(groups):
        first, last = group[0], group[-1]
        onset = min(s.event.record.ts for s in anomalous[first])
        last_anomaly = max(s.event.record.ts for s in anomalous[last])
        peak_key = max(group, key=lambda k: (len(anomalous[k]) + errors.get(k, 0), -k))
        peak = TimeWindow(
            start=max(_from_epoch(peak_key * width), onset),
            end=_from_epoch((peak_key + 1) * width),
        )
        bucket_end = (last + 1) * width
        if idx + 1 < len(groups):
            next_onset: datetime | None = min(
                s.event.record.ts for s in anomalous[groups[idx + 1][0]]
            )
            limit: float = groups[idx + 1][0] * width
        else:
            next_onset = None
            limit = log_window.end.timestamp()
        services = {
            s.event.record.service
            for k in group
            for s in anomalous[k]
            if onset <= s.event.record.ts <= last_anomaly
        }
        markers = sorted(
            (
                s
                for s in heads
                if s.event.recovery_marker
                and s.event.record.service in services
                and s.event.record.ts >= peak.start
                and (next_onset is None or s.event.record.ts < next_onset)
            ),
            key=_order,
        )
        recovery: datetime | None
        if markers:
            recovery = markers[0].event.record.ts
            tail = (log_window.end - markers[-1].event.record.ts).total_seconds()
            status = (
                RecoveryStatus.OBSERVED
                if tail >= config.recovery_quiet_seconds
                else RecoveryStatus.OBSERVED_SHORT_WINDOW
            )
        elif limit - bucket_end >= config.recovery_quiet_seconds:
            recovery = _from_epoch(bucket_end)
            status = RecoveryStatus.OBSERVED
        else:
            recovery = None
            status = RecoveryStatus.NOT_OBSERVED
        evidence = tuple(
            _evidence(s, config)
            for s in markers[: max(1, config.max_samples_per_finding)]
        )
        window = TimeWindow(
            start=onset,
            end=max(last_anomaly, recovery) if recovery is not None else last_anomaly,
        )
        inside = [s for s in heads if window.start <= s.event.record.ts <= window.end]
        episodes.append(
            Episode(
                id=f"E{idx + 1}",
                onset=onset,
                peak=peak,
                peak_anomalous=len(anomalous[peak_key]),
                peak_errors=errors.get(peak_key, 0),
                last_anomaly=last_anomaly,
                recovered=recovery is not None,
                recovery=recovery,
                window=window,
                anomalous_events=sum(1 for s in inside if s.anomalous),
                error_events=sum(1 for s in inside if _is_error(s)),
                recovery_status=status,
                recovery_evidence=evidence,
            )
        )

    residual = sum(
        1
        for s in heads
        if s.anomalous
        and not any(
            e.window.start <= s.event.record.ts <= e.window.end for e in episodes
        )
    )
    return Timeline(
        log_window=log_window,
        baseline=baseline.window,
        baseline_is_fallback=baseline.is_fallback,
        episodes=tuple(episodes),
        residual_anomalies=residual,
    )


def _collect(
    heads: Sequence[ScoredEvent],
) -> tuple[
    dict[tuple[str, str], list[float]],
    dict[tuple[str, str], MetricUnit],
    dict[str, list[int]],
]:
    values: dict[tuple[str, str], list[float]] = {}
    units: dict[tuple[str, str], MetricUnit] = {}
    rates: dict[str, list[int]] = {}
    for s in heads:
        service = s.event.record.service
        counts = rates.setdefault(service, [0, 0])
        counts[1] += 1
        if _is_error(s):
            counts[0] += 1
        for m in s.event.metrics:
            key = (service, m.name)
            values.setdefault(key, []).append(m.value)
            units.setdefault(key, m.unit)
    return values, units, rates


def _rate(errors: int, total: int) -> float | None:
    return round(errors / total, 4) if total else None


def compare_windows(
    scored: Sequence[ScoredEvent], timeline: Timeline, config: AnalysisConfig
) -> WindowComparison:
    """p50/p95/max and error-rate tables: baseline window vs the union of episode windows."""
    heads = [s for s in scored if s.event.continuation_of is None]
    base_window = timeline.baseline
    base_heads = (
        [s for s in heads if base_window.start <= s.event.record.ts <= base_window.end]
        if base_window is not None
        else []
    )
    windows = [e.window for e in timeline.episodes]
    inc_heads = [
        s for s in heads if any(w.start <= s.event.record.ts <= w.end for w in windows)
    ]
    b_values, b_units, b_rates = _collect(base_heads)
    i_values, i_units, i_rates = _collect(inc_heads)

    metrics: list[MetricComparison] = []
    for key in sorted(set(b_values) | set(i_values)):
        b_stats: MetricStats | None = (
            metric_stats(b_values[key]) if key in b_values else None
        )
        i_stats: MetricStats | None = (
            metric_stats(i_values[key]) if key in i_values else None
        )
        ratio = (
            round(i_stats.p95 / b_stats.p95, 2)
            if b_stats is not None and i_stats is not None and b_stats.p95 != 0
            else None
        )
        metrics.append(
            MetricComparison(
                service=key[0],
                metric=key[1],
                unit=b_units.get(key) or i_units[key],
                baseline=b_stats,
                incident=i_stats,
                p95_ratio=ratio,
            )
        )

    rows: list[ErrorRateComparison] = []
    for service in sorted(set(b_rates) | set(i_rates)):
        b_err, b_tot = b_rates.get(service, [0, 0])
        i_err, i_tot = i_rates.get(service, [0, 0])
        rows.append(
            ErrorRateComparison(
                service=service,
                baseline_errors=b_err,
                baseline_total=b_tot,
                baseline_rate=_rate(b_err, b_tot),
                incident_errors=i_err,
                incident_total=i_tot,
                incident_rate=_rate(i_err, i_tot),
            )
        )
    return WindowComparison(metrics=tuple(metrics), error_rates=tuple(rows))
