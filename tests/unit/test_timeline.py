from test_task.baseline import build_baseline, score_events
from test_task.contracts.common import AnalysisConfig
from test_task.parser import parse_line
from test_task.signals import extract_events
from test_task.timeline import build_timeline, compare_windows


def ts(sec):
    return f"2026-03-27T14:{int(sec) // 60:02d}:{sec % 60:06.3f}Z"


def run(specs):
    cfg = AnalysisConfig()
    recs = [
        parse_line(f"{ts(s)} {rest}", i) for i, (s, rest) in enumerate(sorted(specs), 1)
    ]
    evs = extract_events(recs)  # type: ignore[arg-type]
    base = build_baseline(evs, cfg)
    scored = score_events(evs, base, cfg)
    tl = build_timeline(scored, base, cfg)
    return tl, compare_windows(scored, tl, cfg), base


def normal(end):
    return [(s, "INFO svc-n heartbeat latency_ms=10") for s in range(0, end, 30)]


def errs(minute, n=3):
    return [(minute * 60 + 5 * i, "ERROR svc-e boom") for i in range(n)]


def test_empty():
    tl, cmp, _ = run([])
    assert tl.log_window is None and tl.episodes == ()
    assert cmp.metrics == () and cmp.error_rates == ()


def test_recovered_episode_and_comparison():
    tl, cmp, base = run(normal(780) + errs(5) + errs(6))
    (ep,) = tl.episodes
    assert ep.recovered and ep.recovery is not None and ep.recovery >= ep.last_anomaly
    assert ep.window.end == ep.recovery
    assert tl.baseline == base.window
    row = next(m for m in cmp.metrics if m.service == "svc-n")
    assert row.baseline is not None
    e = next(r for r in cmp.error_rates if r.service == "svc-e")
    assert (e.incident_errors, e.incident_total, e.incident_rate) == (6, 6, 1.0)
    assert e.baseline_rate is None


def test_not_recovered_and_split():
    tl, _, _ = run(normal(300) + errs(5) + errs(6) + [(415, "INFO svc-n x")])
    (ep,) = tl.episodes
    assert not ep.recovered and ep.recovery is None and ep.window.end == ep.last_anomaly
    tl2, _, _ = run(normal(1500) + errs(5) + errs(16))
    assert [e.id for e in tl2.episodes] == ["E1", "E2"]
    assert tl2.episodes[0].recovered


def test_no_episodes():
    warns = [(300 + 180 * k, "WARN svc-w drift") for k in range(3)]
    tl, cmp, _ = run(normal(900) + warns)
    assert tl.episodes == () and tl.residual_anomalies == 3
    assert all(r.incident_rate is None for r in cmp.error_rates)
    assert all(m.incident is None for m in cmp.metrics)


def test_ratio_zero_baseline():
    specs = [(s, "INFO svc-z a latency_ms=0") for s in (1, 2, 3)]
    specs += normal(300) + errs(5) + [(310, "INFO svc-z a latency_ms=5")]
    _, cmp, _ = run(specs)
    z = next(m for m in cmp.metrics if m.service == "svc-z")
    assert z.p95_ratio is None


def test_marker_short_window_and_not_observed():
    specs = (
        normal(300)
        + errs(5)
        + errs(6)
        + [(370, "INFO svc-e ready to accept connections")]
    )
    tl, _, _ = run(specs)
    (ep,) = tl.episodes
    assert ep.recovery_status.value == "observed_short_window"
    assert ep.recovery is not None and ep.recovered
    assert [e.line_no for e in ep.recovery_evidence]
    tl2, _, _ = run(normal(300) + errs(5))
    assert tl2.episodes[0].recovery is None
    assert tl2.episodes[0].recovery_status.value == "not_observed"
