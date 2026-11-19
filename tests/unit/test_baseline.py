from test_task.baseline import build_baseline, metric_stats, nearest_rank, score_events
from test_task.contracts.baseline import AnomalyBasis
from test_task.contracts.common import (
    AnalysisConfig,
    DbCategory,
    MetricStats,
    SignalKind,
)
from test_task.parser import parse_line
from test_task.signals import extract_events


def ts(sec):
    return f"2026-03-27T14:{int(sec) // 60:02d}:{sec % 60:06.3f}Z"


def run(*specs):
    recs = [parse_line(f"{ts(s)} {rest}", i) for i, (s, rest) in enumerate(specs, 1)]
    cfg = AnalysisConfig()
    evs = extract_events(recs)  # type: ignore[arg-type]
    base = build_baseline(evs, cfg)
    return base, score_events(evs, base, cfg)


def test_nearest_rank():
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert nearest_rank([1.0], 95) == 1.0
    assert metric_stats([3, 1, 2]) == MetricStats(3, 2, 3, 3)


def test_empty():
    base, scored = run()
    assert base.window is None and base.metrics == () and scored == ()


def test_baseline_and_scoring():
    specs = [(i * 10, f"INFO svc request latency_ms={40 + i}") for i in range(10)]
    specs += [(120, "WARN svc slow"), (130, "INFO svc request latency_ms=900")]
    specs += [(140, "INFO pg db_cluster=c waiting_clients=3")]
    specs += [(150, "WARN svc db timeout acquiring connection pool_idle=0")]
    specs += [(160, "ERROR pg db_cluster=c connection refused")]
    base, scored = run(*specs)
    assert not base.is_fallback
    assert base.first_anomaly_ts is not None
    spike = scored[11]
    assert spike.reasons[0].basis == AnomalyBasis.BASELINE_DEVIATION
    assert spike.reasons[0].baseline_p95 == 49
    assert scored[0].anomalous is False
    assert scored[12].db_categories == (DbCategory.SERVER_SATURATION,)
    assert scored[13].db_categories == (DbCategory.POOL_EXHAUSTION,)
    assert scored[14].db_categories == (DbCategory.CONNECTION_ERRORS,)
    assert scored[10].primary_kind == SignalKind.WARNING


def test_fallback_when_anomaly_early():
    specs = [(0, "INFO s a_ms=1"), (5, "WARN s x"), (100, "INFO s a_ms=1")]
    base, _ = run(*specs)
    assert base.is_fallback and base.window is not None
    assert base.window.end > base.window.start
