"""TASK-005: degradation episodes and the "baseline vs incident" comparison."""

from __future__ import annotations

import pytest
from acc_support import ATTACHED_LOG, at, read_log_lines, stage_timeline, text_line

from test_task.contracts.common import AnalysisConfig, MetricStats


def normal(start, end, step=30, service="svc-n"):
    return [text_line(s, "INFO", service, "heartbeat") for s in range(start, end, step)]


def errors_per_minute(minute, count, service="svc-e", offset=0):
    """``count`` ERROR heads in minute ``minute`` (from T0); the first one is exactly on the bucket boundary."""
    return [
        text_line(minute * 60 + offset + i * (50 // max(count - 1, 1)), "ERROR", service, "charge rejected")
        for i in range(count)
    ]


def by_ts(lines):
    return sorted(lines, key=lambda x: x.split(" ")[0])


def check_episode_shape(ep):
    assert ep.onset <= ep.peak.start <= ep.last_anomaly
    assert ep.peak.start <= ep.peak.end
    assert ep.window.start == ep.onset
    assert ep.recovered == (ep.recovery is not None)
    if ep.recovered:
        assert ep.recovery >= ep.last_anomaly
        assert ep.window.end == ep.recovery
    else:
        assert ep.window.end == ep.last_anomaly


@pytest.mark.criterion("TASK-005:C1")
@pytest.mark.req("FR-07")
def test_single_episode_with_recovery():
    errs = errors_per_minute(5, 5) + errors_per_minute(6, 5) + errors_per_minute(7, 5)
    ns = stage_timeline(by_ts(normal(0, 780) + errs))
    tl = ns.timeline
    assert len(tl.episodes) == 1
    ep = tl.episodes[0]
    assert ep.id == "E1"
    assert ep.onset == at(300)
    assert ep.peak.start == at(300)
    assert at(300) < ep.peak.end <= at(360)
    assert (ep.peak_anomalous, ep.peak_errors) == (5, 5)
    assert ep.last_anomaly == at(7 * 60 + 48)
    assert ep.recovered is True and ep.recovery is not None
    check_episode_shape(ep)
    assert ep.anomalous_events == 15 and ep.error_events == 15


@pytest.mark.criterion("TASK-005:C1")
@pytest.mark.req("FR-07")
def test_peak_is_bucket_with_most_anomalies_and_errors():
    errs = errors_per_minute(5, 3) + errors_per_minute(6, 6) + errors_per_minute(7, 4)
    ns = stage_timeline(by_ts(normal(0, 780) + errs))
    (ep,) = ns.timeline.episodes
    assert ep.peak.start == at(360)
    assert (ep.peak_anomalous, ep.peak_errors) == (6, 6)
    check_episode_shape(ep)


@pytest.mark.criterion("TASK-005:C2")
@pytest.mark.req("FR-07")
def test_log_ends_during_errors():
    errs = errors_per_minute(5, 5) + errors_per_minute(6, 5)
    ns = stage_timeline(by_ts(normal(0, 300) + errs + [text_line(6 * 60 + 55, "INFO", "svc-n", "heartbeat")]))
    (ep,) = ns.timeline.episodes
    assert ep.recovered is False
    assert ep.recovery is None
    assert ep.window.end == ep.last_anomaly == at(6 * 60 + 48)
    check_episode_shape(ep)


@pytest.mark.criterion("TASK-005:C3")
@pytest.mark.req("FR-07", "CTX-01")
def test_two_bursts_two_episodes():
    errs = errors_per_minute(5, 5) + errors_per_minute(16, 5)
    ns = stage_timeline(by_ts(normal(0, 1500) + errs))
    eps = ns.timeline.episodes
    assert [e.id for e in eps] == ["E1", "E2"]
    assert eps[0].onset == at(300) and eps[1].onset == at(960)
    for ep in eps:
        check_episode_shape(ep)
    assert ns.timeline.residual_anomalies == 0


@pytest.mark.criterion("TASK-005:C3")
@pytest.mark.req("FR-07", "CTX-01")
def test_close_active_buckets_merge():
    errs = errors_per_minute(5, 3) + errors_per_minute(7, 3)
    ns = stage_timeline(by_ts(normal(0, 900) + errs))
    (ep,) = ns.timeline.episodes
    assert ep.onset == at(300)
    assert ep.last_anomaly == at(7 * 60 + 50)
    assert ep.anomalous_events == 6


@pytest.mark.criterion("TASK-005:C3")
@pytest.mark.req("FR-07", "CTX-01")
@pytest.mark.parametrize(("second_minute", "episodes"), [(8, 1), (9, 2)], ids=["gap-120s-merge", "gap-180s-split"])
def test_merge_gap_boundary(second_minute, episodes):
    """Active buckets at minutes 5 and 8: gap 120 s (<= 120), one episode; minutes 5 and 9: 180 s, two."""
    errs = errors_per_minute(5, 3) + errors_per_minute(second_minute, 3)
    ns = stage_timeline(by_ts(normal(0, 1200) + errs))
    eps = ns.timeline.episodes
    assert len(eps) == episodes
    assert eps[0].onset == at(300)
    assert eps[-1].last_anomaly == at(second_minute * 60 + 50)
    if episodes == 2:
        assert eps[1].onset == at(second_minute * 60)
        assert eps[0].last_anomaly == at(5 * 60 + 50)


@pytest.mark.criterion("TASK-005:C3")
@pytest.mark.req("FR-07", "CTX-01")
def test_sparse_warnings_are_residual():
    warns = [text_line(300 + 180 * k, "WARN", "svc-w", "config drift noticed") for k in range(4)]
    ns = stage_timeline(by_ts(normal(0, 1200) + warns))
    assert ns.timeline.episodes == ()
    assert ns.timeline.residual_anomalies == 4


@pytest.mark.criterion("TASK-005:C3")
@pytest.mark.req("FR-07")
def test_buckets_aligned_to_epoch():
    split = [text_line(59, "ERROR", "svc-e", "charge rejected"), text_line(61, "ERROR", "svc-e", "charge rejected")]
    ns = stage_timeline(by_ts(normal(0, 600) + split))
    assert ns.timeline.episodes == ()
    assert ns.timeline.residual_anomalies == 2

    same = [text_line(61, "ERROR", "svc-e", "charge rejected"), text_line(119, "ERROR", "svc-e", "charge rejected")]
    ns2 = stage_timeline(by_ts(normal(0, 600) + same))
    assert len(ns2.timeline.episodes) == 1
    assert ns2.timeline.episodes[0].onset == at(61)


@pytest.mark.criterion("TASK-005:C4")
@pytest.mark.req("FR-07")
def test_log_window_and_baseline_from_profile():
    errs = errors_per_minute(5, 5)
    ns = stage_timeline(by_ts(normal(15, 700) + errs))
    tl = ns.timeline
    assert (tl.log_window.start, tl.log_window.end) == (at(15), at(675))
    assert tl.baseline == ns.baseline.window
    assert tl.baseline_is_fallback == ns.baseline.is_fallback is False

    early = by_ts(normal(0, 600) + [text_line(10, "WARN", "svc-w", "config drift noticed")])
    ns2 = stage_timeline(early)
    assert ns2.timeline.baseline_is_fallback is True
    assert ns2.timeline.baseline == ns2.baseline.window


@pytest.mark.criterion("TASK-005:C4")
@pytest.mark.req("FR-07")
def test_empty_input(impl):
    cfg = AnalysisConfig()
    profile = impl("baseline", "build_baseline")((), cfg)
    tl = impl("timeline", "build_timeline")((), profile, cfg)
    assert tl.log_window is None
    assert tl.episodes == ()
    assert tl.residual_anomalies == 0
    cmp = impl("timeline", "compare_windows")((), tl, cfg)
    assert cmp.metrics == () and cmp.error_rates == ()


def comparison_scenario():
    """Baseline 0-300 s, episode in minutes 5-6 (svc-e errors), normal traffic until 900 s."""
    lines = normal(0, 900)
    lines += [text_line(5 + 60 * k, "INFO", "svc-b", "request served latency_ms=30") for k in range(5)]
    lines += [text_line(7 + 60 * k, "INFO", "svc-z", "request served latency_ms=0") for k in range(3)]
    lines += [text_line(9 + 60 * k, "INFO", "svc-old", "request served latency_ms=20") for k in range(3)]
    lines += errors_per_minute(5, 5) + errors_per_minute(6, 5)
    values = [7, 3, 19, 1, 12, 20, 5, 16, 9, 14, 2, 18, 11, 6, 15, 8, 4, 13, 17, 10]
    lines += [text_line(302 + 2 * i, "INFO", "svc-m", f"request served latency_ms={v}") for i, v in enumerate(values)]
    lines += [text_line(345 + i, "INFO", "svc-b", "request served latency_ms=100") for i in range(3)]
    lines += [text_line(350 + i, "INFO", "svc-z", "request served latency_ms=5") for i in range(2)]
    lines += [
        text_line(380, "ERROR", "svc-r", "refund rejected"),
        text_line(381, "INFO", "svc-r", "refund queued"),
        text_line(382, "INFO", "svc-r", "refund queued"),
    ]
    return stage_timeline(by_ts(lines))


def metric_row(cmp, service, metric="latency_ms"):
    rows = [m for m in cmp.metrics if (m.service, m.metric) == (service, metric)]
    assert len(rows) == 1, f"no row {service}/{metric}"
    return rows[0]


def rate_row(cmp, service):
    rows = [r for r in cmp.error_rates if r.service == service]
    assert len(rows) == 1, f"no error rate row for {service}"
    return rows[0]


@pytest.mark.criterion("TASK-005:C5")
@pytest.mark.req("FR-08")
def test_metric_comparison():
    ns = comparison_scenario()
    (ep,) = ns.timeline.episodes
    assert ep.onset == at(300)
    cmp = ns.comparison
    m = metric_row(cmp, "svc-m")
    assert m.baseline is None
    assert m.incident == MetricStats(count=20, p50=10, p95=19, max=20)
    assert m.p95_ratio is None
    b = metric_row(cmp, "svc-b")
    assert b.baseline == MetricStats(count=5, p50=30, p95=30, max=30)
    assert b.incident == MetricStats(count=3, p50=100, p95=100, max=100)
    assert b.p95_ratio == 3.33
    z = metric_row(cmp, "svc-z")
    assert z.baseline is not None and z.baseline.p95 == 0
    assert z.incident is not None and z.p95_ratio is None
    old = metric_row(cmp, "svc-old")
    assert old.baseline is not None and old.incident is None and old.p95_ratio is None
    keys = [(r.service, r.metric) for r in cmp.metrics]
    assert keys == sorted(keys)


@pytest.mark.criterion("TASK-005:C6")
@pytest.mark.req("FR-08")
def test_error_rates():
    ns = comparison_scenario()
    cmp = ns.comparison
    e = rate_row(cmp, "svc-e")
    assert (e.incident_errors, e.incident_total, e.incident_rate) == (10, 10, 1.0)
    assert (e.baseline_total, e.baseline_rate) == (0, None)
    r = rate_row(cmp, "svc-r")
    assert (r.incident_errors, r.incident_total, r.incident_rate) == (1, 3, 0.3333)
    old = rate_row(cmp, "svc-old")
    assert old.baseline_total == 3 and old.baseline_errors == 0 and old.baseline_rate == 0.0
    assert old.incident_total == 0 and old.incident_rate is None
    assert [x.service for x in cmp.error_rates] == sorted(x.service for x in cmp.error_rates)


def union_scenario():
    """Two svc-e error bursts (minutes 5 and 16) with a quiet pause between them (11:00-15:00).

    svc-m: baseline 30 ms; 40 inside E1, 45 inside E2; 77 in the pause (below the 3×p95 deviation threshold,
    so the pause stays quiet). svc-q: baseline 20, values only in the pause. svc-p: a head in the baseline,
    a single ERROR and an INFO in the pause. The incident window is the union of E1 and E2, excluding the pause.
    """
    lines = normal(0, 1500)
    lines += errors_per_minute(5, 5) + errors_per_minute(16, 5)
    lines += [text_line(20 + 60 * k, "INFO", "svc-m", "request served latency_ms=30") for k in range(5)]
    lines += [text_line(25 + 60 * k, "INFO", "svc-q", "request served latency_ms=20") for k in range(3)]
    lines += [text_line(100, "INFO", "svc-p", "refund queued")]
    lines += [text_line(s, "INFO", "svc-m", "request served latency_ms=40") for s in (305, 315, 325)]
    lines += [text_line(s, "INFO", "svc-m", "request served latency_ms=45") for s in (965, 975)]
    lines += [text_line(s, "INFO", "svc-m", "request served latency_ms=77") for s in (660, 720, 780, 840, 900)]
    lines += [text_line(s, "INFO", "svc-q", "request served latency_ms=25") for s in (690, 750, 810)]
    lines += [
        text_line(700, "ERROR", "svc-p", "refund rejected"),
        text_line(705, "INFO", "svc-p", "refund queued"),
        text_line(710, "INFO", "svc-p", "refund queued"),
    ]
    return stage_timeline(by_ts(lines))


@pytest.mark.criterion("TASK-005:C5")
@pytest.mark.req("FR-08")
def test_incident_window_is_union_of_episodes():
    ns = union_scenario()
    eps = ns.timeline.episodes
    assert [e.id for e in eps] == ["E1", "E2"]
    assert (eps[0].onset, eps[1].onset) == (at(300), at(960))
    assert eps[0].window.end < at(660) and eps[1].window.start > at(900)
    assert ns.timeline.residual_anomalies == 1
    m = metric_row(ns.comparison, "svc-m")
    assert m.baseline == MetricStats(count=5, p50=30, p95=30, max=30)
    assert m.incident == MetricStats(count=5, p50=40, p95=45, max=45)
    assert m.p95_ratio == 1.5
    q = metric_row(ns.comparison, "svc-q")
    assert q.baseline == MetricStats(count=3, p50=20, p95=20, max=20)
    assert q.incident is None and q.p95_ratio is None


@pytest.mark.criterion("TASK-005:C6")
@pytest.mark.req("FR-08")
def test_error_rates_ignore_pause_between_episodes():
    ns = union_scenario()
    p = rate_row(ns.comparison, "svc-p")
    assert (p.baseline_errors, p.baseline_total, p.baseline_rate) == (0, 1, 0.0)
    assert (p.incident_errors, p.incident_total, p.incident_rate) == (0, 0, None)
    e = rate_row(ns.comparison, "svc-e")
    assert (e.incident_errors, e.incident_total, e.incident_rate) == (10, 10, 1.0)


@pytest.mark.criterion("TASK-005:C6")
@pytest.mark.req("FR-08")
def test_no_episodes_no_incident_rates():
    warns = [text_line(300 + 180 * k, "WARN", "svc-w", "config drift noticed") for k in range(3)]
    ns = stage_timeline(by_ts(normal(0, 900) + warns))
    assert ns.timeline.episodes == ()
    assert ns.comparison.error_rates
    for r in ns.comparison.error_rates:
        assert r.incident_total == 0 and r.incident_errors == 0 and r.incident_rate is None
    for m in ns.comparison.metrics:
        assert m.incident is None


@pytest.mark.criterion("TASK-005:C7")
@pytest.mark.req("FR-07", "FR-08", "CTX-01")
def test_smoke_attached_log():
    ns = stage_timeline(read_log_lines(ATTACHED_LOG))
    tl = ns.timeline
    assert tl.log_window is not None
    assert len(tl.episodes) >= 1
    assert [e.id for e in tl.episodes] == [f"E{i}" for i in range(1, len(tl.episodes) + 1)]
    for ep in tl.episodes:
        assert tl.log_window.start <= ep.onset and ep.window.end <= tl.log_window.end
        assert ep.onset <= ep.peak.start <= ep.last_anomaly
    assert any(m.service == "api-gateway" and m.metric == "latency_ms" for m in ns.comparison.metrics)
    assert any(m.metric == "db_latency_ms" for m in ns.comparison.metrics)
