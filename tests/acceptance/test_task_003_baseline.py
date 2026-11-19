"""TASK-003: baseline window, metric statistics and anomaly labeling."""

from __future__ import annotations

import pytest
from acc_support import ATTACHED_LOG, at, read_log_lines, stage_scored, text_line

from test_task.contracts.baseline import AnomalyBasis
from test_task.contracts.common import DbCategory, MetricStats, MetricUnit, SignalKind

BASE_VALUES = [40, 45, 50, 55, 60, 42, 47, 52, 57, 44]
"""10 values 40-60: nearest-rank p50=47, p95=60, max=60."""


def base_block(service="svc-a"):
    return [text_line(i * 30, "INFO", service, f"request served latency_ms={v}") for i, v in enumerate(BASE_VALUES)]


def cut_and_prelude(extra_base=()):
    """Neutral normal traffic 0-270 s, then a WARN at 300 s that ends the baseline window."""
    lines = [text_line(i * 30, "INFO", "svc-base", "heartbeat") for i in range(10)]
    lines += list(extra_base)
    lines.append(text_line(300, "WARN", "svc-cut", "config drift noticed"))
    return lines


def scored_by_line(ns):
    return {s.event.record.line_no: s for s in ns.scored}


def reasons_with(s, basis):
    return [r for r in s.reasons if r.basis == basis]


@pytest.mark.criterion("TASK-003:C1")
@pytest.mark.req("FR-08")
def test_baseline_window_until_first_signal():
    lines = base_block() + [text_line(300, "WARN", "svc-a", "response slow")]
    ns = stage_scored(lines)
    b = ns.baseline
    assert b.is_fallback is False
    assert b.window is not None
    assert b.window.start == at(0)
    assert at(270) <= b.window.end < at(300)
    assert b.first_anomaly_ts == at(300)
    stats = [m for m in b.metrics if (m.service, m.metric) == ("svc-a", "latency_ms")]
    assert len(stats) == 1
    assert stats[0].unit == MetricUnit.MS
    assert stats[0].stats == MetricStats(count=10, p50=47, p95=60, max=60)
    keys = [(m.service, m.metric) for m in b.metrics]
    assert keys == sorted(keys)


@pytest.mark.criterion("TASK-003:C1")
@pytest.mark.req("FR-08")
def test_baseline_whole_log_without_signals():
    ns = stage_scored(base_block())
    b = ns.baseline
    assert b.window is not None
    assert (b.window.start, b.window.end) == (at(0), at(270))
    assert b.is_fallback is False
    assert b.first_anomaly_ts is None


@pytest.mark.criterion("TASK-003:C1")
@pytest.mark.req("FR-08")
def test_baseline_empty_input(impl):
    from test_task.contracts.common import AnalysisConfig

    b = impl("baseline", "build_baseline")((), AnalysisConfig())
    assert b.window is None
    assert b.metrics == ()
    assert impl("baseline", "score_events")((), b, AnalysisConfig()) == ()


@pytest.mark.criterion("TASK-003:C2")
@pytest.mark.req("FR-08")
def test_fallback_baseline_first_tenth_of_log():
    vals = {0: 11, 10: 12, 20: 13, 40: 14, 50: 15}
    lines = [text_line(s, "INFO", "svc-a", f"request served latency_ms={v}") for s, v in vals.items() if s < 30]
    lines.append(text_line(30, "WARN", "svc-a", "response slow"))
    lines += [text_line(s, "INFO", "svc-a", f"request served latency_ms={v}") for s, v in vals.items() if s > 30]
    lines += [text_line(s, "INFO", "svc-a", "request served latency_ms=16") for s in range(90, 601, 30)]
    b = stage_scored(lines).baseline
    assert b.is_fallback is True
    assert b.window.start == at(0)
    assert at(50) <= b.window.end <= at(60)
    stats = [m for m in b.metrics if (m.service, m.metric) == ("svc-a", "latency_ms")]
    assert len(stats) == 1 and stats[0].stats.count == 5
    assert b.first_anomaly_ts == at(30)


def deviation_scenario():
    extra = []
    extra += [
        text_line(5 + 10 * k, "INFO", "svc-f", f"request served latency_ms={3 if k == 0 else 1}") for k in range(20)
    ]
    extra += [text_line(s, "INFO", "svc-g", "request served latency_ms=10") for s in (7, 17)]
    extra += [text_line(s, "INFO", "svc-h", "rate sample req_per_s=100") for s in (3, 63, 123, 183, 243)]
    lines = base_block() + sorted(extra) + [text_line(300, "WARN", "svc-cut", "config drift noticed")]
    probes = {
        "a400": text_line(310, "INFO", "svc-a", "request served latency_ms=400"),
        "a120": text_line(320, "INFO", "svc-a", "request served latency_ms=120"),
        "a180": text_line(325, "INFO", "svc-a", "request served latency_ms=180"),
        "a181": text_line(326, "INFO", "svc-a", "request served latency_ms=181"),
        "f4": text_line(330, "INFO", "svc-f", "request served latency_ms=4"),
        "f50": text_line(331, "INFO", "svc-f", "request served latency_ms=50"),
        "f51": text_line(332, "INFO", "svc-f", "request served latency_ms=51"),
        "g5000": text_line(340, "INFO", "svc-g", "request served latency_ms=5000"),
        "h40": text_line(350, "INFO", "svc-h", "rate sample req_per_s=40"),
        "h50": text_line(355, "INFO", "svc-h", "rate sample req_per_s=50"),
        "h60": text_line(360, "INFO", "svc-h", "rate sample req_per_s=60"),
    }
    lines += list(probes.values())
    ns = stage_scored(sorted(lines, key=lambda x: x.split(" ")[0]))
    by_raw = {s.event.record.raw: s for s in ns.scored}
    return ns, {k: by_raw[v] for k, v in probes.items()}


@pytest.mark.criterion("TASK-003:C3")
@pytest.mark.req("FR-10", "FR-08")
def test_deviation_reason_for_latency_spike():
    _, p = deviation_scenario()
    s = p["a400"]
    assert s.anomalous is True
    assert s.primary_kind == SignalKind.LATENCY
    dev = reasons_with(s, AnomalyBasis.BASELINE_DEVIATION)
    assert len(dev) >= 1
    r = dev[0]
    assert r.kind == SignalKind.LATENCY
    assert r.metric == "latency_ms"
    assert r.value == 400
    assert r.baseline_p95 == 60


@pytest.mark.criterion("TASK-003:C3")
@pytest.mark.req("FR-10", "FR-08")
@pytest.mark.parametrize(
    ("probe", "anomalous"),
    [
        ("a120", False),
        ("a180", False),
        ("a181", True),
        ("f4", False),
        ("f50", False),
        ("f51", True),
        ("g5000", False),
        ("h40", True),
        ("h50", False),
        ("h60", False),
    ],
)
def test_deviation_thresholds(probe, anomalous):
    _, p = deviation_scenario()
    s = p[probe]
    assert s.anomalous is anomalous
    assert bool(reasons_with(s, AnomalyBasis.BASELINE_DEVIATION)) is anomalous
    if not anomalous:
        assert s.reasons == () and s.primary_kind is None


@pytest.mark.criterion("TASK-003:C3")
@pytest.mark.req("FR-10")
def test_lower_is_worse_reason_names_metric():
    _, p = deviation_scenario()
    dev = reasons_with(p["h40"], AnomalyBasis.BASELINE_DEVIATION)
    assert [r.metric for r in dev] == ["req_per_s"]
    assert dev[0].value == 40


def threshold_scenario():
    probes = {
        "waiting8": text_line(310, "INFO", "pg-a", "db_cluster=c1 waiting_clients=8"),
        "idle0": text_line(320, "INFO", "svc-p", "pool stats pool_idle=0 pool_in_use=10"),
        "active95": text_line(330, "INFO", "pg-b", "db_cluster=c2 active_connections=95 max_connections=100"),
        "active50": text_line(340, "INFO", "pg-b", "db_cluster=c2 active_connections=50 max_connections=100"),
        "waiting0": text_line(350, "INFO", "pg-a", "db_cluster=c1 waiting_clients=0"),
        "active91": text_line(360, "INFO", "pg-c", "db_cluster=c3 active_connections=91 max_connections=100"),
        "active89": text_line(370, "INFO", "pg-c", "db_cluster=c3 active_connections=89 max_connections=100"),
        "active90": text_line(380, "INFO", "pg-c", "db_cluster=c3 active_connections=90 max_connections=100"),
    }
    ns = stage_scored(cut_and_prelude() + list(probes.values()))
    by_raw = {s.event.record.raw: s for s in ns.scored}
    return {k: by_raw[v] for k, v in probes.items()}


@pytest.mark.criterion("TASK-003:C4")
@pytest.mark.req("FR-06")
@pytest.mark.parametrize("probe", ["waiting8", "idle0", "active95", "active91", "active90"])
def test_absolute_thresholds_trigger(probe):
    s = threshold_scenario()[probe]
    assert s.anomalous is True
    assert s.primary_kind == SignalKind.POOL_SATURATION
    assert reasons_with(s, AnomalyBasis.THRESHOLD)


@pytest.mark.criterion("TASK-003:C4")
@pytest.mark.req("FR-06")
@pytest.mark.parametrize("probe", ["active50", "waiting0", "active89"])
def test_below_thresholds_is_normal(probe):
    s = threshold_scenario()[probe]
    assert s.anomalous is False
    assert s.reasons == () and s.primary_kind is None


@pytest.mark.criterion("TASK-003:C5")
@pytest.mark.req("FR-06")
def test_db_categories_mapping():
    extra = [
        text_line(s, "INFO", "svc-d", f"query done db_latency_ms={v}")
        for s, v in ((15, 10), (75, 12), (135, 11), (195, 10), (255, 12))
    ]
    probes = {
        "server_pool": (
            text_line(310, "INFO", "pg-x", "db_cluster=c1 waiting_clients=8"),
            (DbCategory.SERVER_SATURATION,),
        ),
        "client_pool": (
            text_line(320, "WARN", "svc-c", "pool exhausted pool_in_use=10 pool_idle=0"),
            (DbCategory.POOL_EXHAUSTION,),
        ),
        "restart": (
            text_line(330, "WARN", "pg-x", "db_cluster=c1 postgres restart initiated"),
            (DbCategory.SERVER_RESTART,),
        ),
        "conn": (text_line(340, "ERROR", "svc-c", "pq: connection refused"), (DbCategory.CONNECTION_ERRORS,)),
        # strong DB marker "pq:" (since 2.0 the word "sql" is no longer a DB marker)
        "timeout": (text_line(350, "ERROR", "svc-c", "pq: query timeout"), (DbCategory.QUERY_TIMEOUT,)),
        "latency": (text_line(360, "INFO", "svc-d", "query done db_latency_ms=900"), (DbCategory.LATENCY,)),
        "pool_timeout": (
            text_line(365, "WARN", "svc-c", "timeout acquiring connection pool_idle=0"),
            (DbCategory.POOL_EXHAUSTION,),
        ),
        "non_db": (text_line(370, "ERROR", "svc-n", "payment rejected"), ()),
        "normal": (text_line(380, "INFO", "svc-n", "request served"), ()),
        # multiple categories on one event come in DbCategory declaration order
        "server_conn_crash": (
            text_line(385, "ERROR", "pg-y", "db_cluster=c9 connection terminated after crash"),
            (DbCategory.CONNECTION_ERRORS, DbCategory.SERVER_RESTART),
        ),
        "client_timeout_latency": (
            text_line(390, "WARN", "svc-d", "sql timeout db_latency_ms=900"),
            (DbCategory.LATENCY, DbCategory.QUERY_TIMEOUT),
        ),
    }
    ns = stage_scored(sorted(cut_and_prelude(extra), key=lambda x: x.split(" ")[0]) + [v[0] for v in probes.values()])
    by_raw = {s.event.record.raw: s for s in ns.scored}
    for name, (line, expected) in probes.items():
        s = by_raw[line]
        assert s.db_categories == expected, f"{name}: {s.db_categories} != {expected}"
    assert by_raw[probes["non_db"][0]].anomalous is True
    assert by_raw[probes["normal"][0]].anomalous is False


@pytest.mark.criterion("TASK-003:C6")
@pytest.mark.req("FR-10", "QA-04")
def test_noise_and_continuations_not_anomalous():
    extra = [text_line(s, "INFO", "kube-agent", "probe=readiness ready=true latency_ms=2") for s in (12, 72, 132, 192)]
    tail = [
        text_line(310, "INFO", "kube-agent", "probe=readiness ready=true latency_ms=2"),
        text_line(320, "INFO", "svc-n", "request served"),
        text_line(330.0, "ERROR", "svc-e", "request_id=r-1 charge failed"),
        text_line(330.1, "ERROR", "svc-e", "request_id=r-1 \tat x.Y.z(Y.java:1)"),
    ]
    ns = stage_scored(sorted(cut_and_prelude(extra), key=lambda x: x.split(" ")[0]) + tail)
    assert len(ns.scored) == len(ns.events)
    assert [s.event for s in ns.scored] == list(ns.events)
    by_raw = {s.event.record.raw: s for s in ns.scored}
    for line in tail[:2]:
        s = by_raw[line]
        assert (s.anomalous, s.reasons, s.primary_kind, s.db_categories) == (False, (), None, ())
    assert by_raw[tail[2]].anomalous is True
    cont = by_raw[tail[3]]
    assert cont.event.continuation_of is not None
    assert (cont.anomalous, cont.reasons, cont.primary_kind, cont.db_categories) == (False, (), None, ())


@pytest.mark.criterion("TASK-003:C6")
@pytest.mark.req("FR-10", "QA-04")
def test_invariants_on_attached_log():
    ns = stage_scored(read_log_lines(ATTACHED_LOG))
    assert [s.event for s in ns.scored] == list(ns.events)
    assert any(s.anomalous for s in ns.scored)
    assert any(not s.anomalous for s in ns.scored)
    for s in ns.scored:
        assert s.anomalous == bool(s.reasons) == (s.primary_kind is not None)
        if s.event.continuation_of is not None:
            assert not s.anomalous


@pytest.mark.criterion("TASK-003:C7")
@pytest.mark.req("FR-10")
def test_primary_kind_prefers_metric_type_for_warn():
    line = text_line(310, "WARN", "svc-l", "response slow latency_ms=30")
    ns = stage_scored(cut_and_prelude() + [line])
    s = next(x for x in ns.scored if x.event.record.raw == line)
    assert s.event.kinds == (SignalKind.WARNING,)
    assert s.primary_kind == SignalKind.LATENCY
