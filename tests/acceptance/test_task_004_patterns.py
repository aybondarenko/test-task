"""TASK-004: anomaly groups, request traces and noise counters."""

from __future__ import annotations

import pytest
from acc_support import ATTACHED_LOG, at, read_log_lines, stage_patterns, text_line

from test_task.contracts.common import (
    SIGNAL_PRIORITY,
    AnalysisConfig,
    DbCategory,
    Level,
    MetricUnit,
    SignalKind,
)


def noise(start=0, end=300, step=30, service="svc-noise"):
    return [text_line(s, "INFO", service, "heartbeat") for s in range(start, end, step)]


def line_nos(ns, needle):
    return [s.event.record.line_no for s in ns.scored if needle in s.event.record.raw]


def check_result_invariants(ns):
    pr = ns.patterns
    assert pr.noise_lines + pr.anomalous_lines + pr.continuation_lines == len(ns.scored)
    assert sum(g.count for g in pr.groups) == pr.anomalous_lines
    assert [g.id for g in pr.groups] == [f"P{i}" for i in range(1, len(pr.groups) + 1)]
    keys = [(g.window.start, SIGNAL_PRIORITY.index(g.kind), g.service) for g in pr.groups]
    assert keys == sorted(keys)
    for g in pr.groups:
        assert g.count >= 1
        assert g.window.start <= g.window.end
        assert 1 <= len(g.samples) <= 3
        assert g.line_nos[0] in [x.line_no for x in g.samples]
        assert [x.line_no for x in g.samples] == sorted({x.line_no for x in g.samples})
        assert list(g.line_nos) == sorted(g.line_nos) and len(g.line_nos) == g.count


@pytest.mark.criterion("TASK-004:C1")
@pytest.mark.req("FR-03", "OUT-09")
def test_five_identical_warnings_one_group():
    warns = [text_line(310 + 10 * i, "WARN", "svc-q", "consumer lagging") for i in range(5)]
    ns = stage_patterns(noise() + warns)
    pr = ns.patterns
    assert len(pr.groups) == 1
    g = pr.groups[0]
    assert g.id == "P1"
    assert g.service == "svc-q"
    assert g.count == 5
    assert (g.window.start, g.window.end) == (at(310), at(350))
    assert list(g.line_nos) == line_nos(ns, "consumer lagging")
    assert 1 <= len(g.samples) <= 3
    assert g.samples[0].line_no == g.line_nos[0]
    assert len(g.templates) == 1 and g.templates[0].count == 5
    assert pr.anomalous_lines == 5
    assert pr.noise_lines == 10
    check_result_invariants(ns)


@pytest.mark.criterion("TASK-004:C2")
@pytest.mark.req("FR-03")
def test_gap_over_group_gap_splits_group():
    warns = [text_line(310 + 10 * i, "WARN", "svc-q", "consumer lagging") for i in range(3)]
    warns += [text_line(930 + 10 * i, "WARN", "svc-q", "consumer lagging") for i in range(2)]
    ns = stage_patterns(noise() + warns + noise(1000, 1100))
    groups = [g for g in ns.patterns.groups if g.service == "svc-q"]
    assert [g.count for g in groups] == [3, 2]
    assert groups[0].window.end == at(330) and groups[1].window.start == at(930)
    check_result_invariants(ns)


@pytest.mark.criterion("TASK-004:C2")
@pytest.mark.req("FR-03")
@pytest.mark.parametrize("second", [590, 610], ids=["gap-280s", "gap-300s-boundary"])
def test_gap_within_group_gap_keeps_group(second):
    """A group is split only on a gap strictly greater than group_gap_seconds (300 s)."""
    warns = [text_line(310, "WARN", "svc-q", "consumer lagging"), text_line(second, "WARN", "svc-q", "consumer lagging")]
    ns = stage_patterns(noise() + warns)
    assert [g.count for g in ns.patterns.groups] == [2]
    assert ns.patterns.groups[0].window.end == at(second)


@pytest.mark.criterion("TASK-004:C2")
@pytest.mark.req("FR-03")
def test_groups_split_by_service_and_kind_and_ordered():
    lines = noise() + [
        text_line(330, "WARN", "svc-c", "consumer lagging"),
        text_line(360, "WARN", "svc-b", "consumer lagging"),
        text_line(360, "WARN", "svc-a", "consumer lagging"),
        text_line(360, "ERROR", "svc-z", "charge rejected"),
        text_line(370, "ERROR", "svc-a", "charge rejected"),
    ]
    ns = stage_patterns(lines)
    got = [(g.id, g.kind, g.service) for g in ns.patterns.groups]
    assert got == [
        ("P1", SignalKind.WARNING, "svc-c"),
        ("P2", SignalKind.ERROR, "svc-z"),
        ("P3", SignalKind.WARNING, "svc-a"),
        ("P4", SignalKind.WARNING, "svc-b"),
        ("P5", SignalKind.ERROR, "svc-a"),
    ]
    check_result_invariants(ns)


@pytest.mark.criterion("TASK-004:C3")
@pytest.mark.req("FR-03", "FR-10")
def test_stack_trace_counts_once():
    trace = [
        text_line(310.0, "ERROR", "svc-p", "request_id=r-7 charge failed"),
        text_line(310.1, "ERROR", "svc-p", "request_id=r-7 \tat a.B.c(B.java:1)"),
        text_line(310.2, "ERROR", "svc-p", "request_id=r-7 \tat a.B.d(B.java:2)"),
        text_line(310.3, "ERROR", "svc-p", "request_id=r-7 \tat a.C.e(C.java:3)"),
        text_line(310.4, "ERROR", "svc-p", "request_id=r-7 Caused by: java.lang.IllegalStateException: bad state"),
        text_line(310.5, "ERROR", "svc-p", "request_id=r-7 ... 4 more"),
    ]
    ns = stage_patterns(noise() + trace)
    pr = ns.patterns
    assert len(pr.groups) == 1
    assert pr.groups[0].count == 1
    assert pr.continuation_lines == 5
    assert pr.anomalous_lines == 1
    assert pr.noise_lines == 10
    check_result_invariants(ns)


@pytest.mark.criterion("TASK-004:C3")
@pytest.mark.req("FR-03", "FR-10")
def test_invariants_on_attached_log():
    ns = stage_patterns(read_log_lines(ATTACHED_LOG))
    assert ns.patterns.anomalous_lines > 0 and ns.patterns.noise_lines > 0
    check_result_invariants(ns)


@pytest.mark.criterion("TASK-004:C4")
@pytest.mark.req("FR-03")
def test_group_metrics_peers_db_and_level():
    base = [text_line(15 + 60 * k, "INFO", "svc-m", "response sent latency_ms=50") for k in range(5)]
    lines = sorted(noise() + base) + [
        text_line(310, "WARN", "svc-m", "response slow upstream=svc-y latency_ms=300"),
        text_line(320, "WARN", "svc-m", "response slow upstream=svc-x latency_ms=700"),
        text_line(330, "WARN", "svc-m", "response slow upstream=svc-y latency_ms=500"),
        text_line(340, "WARN", "svc-t", "upstream timeout latency_ms=3000"),
        text_line(345, "ERROR", "svc-t", "upstream timeout latency_ms=3100"),
        text_line(350, "WARN", "svc-d", "pool exhausted pool_in_use=10 pool_idle=0"),
        text_line(355, "WARN", "svc-d", "pool exhausted pool_in_use=10 pool_idle=0"),
    ]
    ns = stage_patterns(lines)
    by = {(g.kind, g.service): g for g in ns.patterns.groups}

    lat = by[(SignalKind.LATENCY, "svc-m")]
    assert lat.count == 3
    assert lat.peers == ("svc-x", "svc-y")
    assert lat.max_level == Level.WARN
    assert lat.db_related is False and lat.db_categories == ()
    (ms,) = [m for m in lat.metrics if m.metric == "latency_ms"]
    assert ms.unit == MetricUnit.MS
    assert (ms.count, ms.min, ms.p50, ms.max) == (3, 300, 500, 700)
    assert ms.baseline_p95 == 50

    tmo = by[(SignalKind.TIMEOUT, "svc-t")]
    assert tmo.count == 2
    assert tmo.max_level == Level.ERROR
    (tms,) = [m for m in tmo.metrics if m.metric == "latency_ms"]
    assert tms.baseline_p95 is None

    pool = by[(SignalKind.POOL_SATURATION, "svc-d")]
    assert pool.db_related is True
    assert pool.db_categories == (DbCategory.POOL_EXHAUSTION,)
    assert [m.metric for m in pool.metrics] == sorted(m.metric for m in pool.metrics)
    assert {"pool_idle", "pool_in_use"} <= {m.metric for m in pool.metrics}


def trace_lines():
    """r-1: gw->svc-a(ERROR)->svc-b; r-2: single service; r-3: no anomalies; r-4: event with only order_id."""
    return noise() + [
        text_line(400, "INFO", "gw", "request accepted request_id=r-1"),
        text_line(401, "ERROR", "svc-a", "charge rejected request_id=r-1"),
        text_line(402, "INFO", "svc-b", "stock released request_id=r-1"),
        text_line(410, "INFO", "svc-a", "charge accepted request_id=r-2"),
        text_line(411, "ERROR", "svc-a", "charge rejected request_id=r-2"),
        text_line(420, "INFO", "gw", "request accepted request_id=r-3"),
        text_line(421, "INFO", "svc-b", "stock reserved request_id=r-3"),
        text_line(430, "INFO", "gw", "request accepted request_id=r-4 order_id=o-4"),
        text_line(431, "INFO", "svc-a", "charge accepted request_id=r-4 order_id=o-4"),
        text_line(432, "ERROR", "svc-c", "invoice rejected order_id=o-4"),
        text_line(440, "INFO", "gw", "request accepted request_id=r-6 order_id=o-4"),
        text_line(441, "INFO", "svc-a", "charge accepted request_id=r-6 order_id=o-4"),
        # after r-6 appears with the same o-4, an event with only order_id still goes to r-4 (where o-4 was first seen)
        text_line(445, "ERROR", "svc-d", "refund rejected order_id=o-4"),
    ]


@pytest.mark.criterion("TASK-004:C5")
@pytest.mark.req("FR-09")
def test_request_traces():
    ns = stage_patterns(trace_lines())
    traces = {t.request_id: t for t in ns.patterns.traces}
    assert set(traces) == {"r-1", "r-4"}
    t1 = traces["r-1"]
    assert t1.services == ("gw", "svc-a", "svc-b")
    assert [h.service for h in t1.hops] == ["gw", "svc-a", "svc-b"]
    assert [h.ts for h in t1.hops] == [at(400), at(401), at(402)]
    assert t1.anomalous_hops == 1
    assert [h.anomalous for h in t1.hops] == [False, True, False]
    assert (t1.window.start, t1.window.end) == (at(400), at(402))
    t4 = traces["r-4"]
    assert t4.services == ("gw", "svc-a", "svc-c", "svc-d")
    assert t4.order_ids == ("o-4",)
    assert t4.anomalous_hops == 2
    assert t4.window.end == at(445)
    keys = [(t.window.start, t.request_id) for t in ns.patterns.traces]
    assert keys == sorted(keys)


@pytest.mark.criterion("TASK-004:C5")
@pytest.mark.req("FR-09")
@pytest.mark.parametrize(("limit", "expected"), [(3, {"rA", "rB", "rC"}), (2, {"rA", "rB"}), (1, {"rA"})])
def test_example_trace_selection(limit, expected):
    lines = noise() + [
        text_line(500, "INFO", "gw", "request accepted request_id=rD"),
        text_line(501, "ERROR", "svc-a", "charge rejected request_id=rD"),
        text_line(502, "INFO", "gw", "request accepted request_id=rB"),
        text_line(503, "ERROR", "svc-a", "charge rejected request_id=rB"),
        text_line(504, "INFO", "svc-b", "stock released request_id=rB"),
        text_line(510, "INFO", "gw", "request accepted request_id=rA"),
        text_line(511, "ERROR", "svc-a", "charge rejected request_id=rA"),
        text_line(512, "ERROR", "svc-b", "stock rejected request_id=rA"),
        text_line(520, "INFO", "gw", "request accepted request_id=rC"),
        text_line(521, "ERROR", "svc-a", "charge rejected request_id=rC"),
        text_line(522, "INFO", "svc-b", "stock released request_id=rC"),
    ]
    ns = stage_patterns(lines, AnalysisConfig(max_example_traces=limit))
    assert {t.request_id for t in ns.patterns.traces} == {"rA", "rB", "rC", "rD"}
    ids = ns.patterns.example_trace_ids
    assert len(ids) == len(set(ids)) == len(expected)
    assert set(ids) == expected


@pytest.mark.criterion("TASK-004:C6")
@pytest.mark.req("OUT-09", "QA-03", "NEG-02")
def test_smoke_grouped_not_listed():
    ns = stage_patterns(read_log_lines(ATTACHED_LOG))
    pr = ns.patterns
    assert 0 < len(pr.groups) < pr.anomalous_lines
    for g in pr.groups:
        assert g.count >= 1
        assert g.window is not None and g.window.start <= g.window.end
        assert 1 <= len(g.samples) <= 3
