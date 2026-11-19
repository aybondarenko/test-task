"""TASK-002: events with signal kinds, metrics, DB markers and request ids."""

from __future__ import annotations

import pytest
from acc_support import ATTACHED_LOG, json_line, read_log_lines, stage_events, text_line

from test_task.contracts.common import SIGNAL_PRIORITY, MetricUnit, SignalKind


def events(lines):
    return stage_events(lines).events


def check_kinds_canonical(kinds):
    assert len(set(kinds)) == len(kinds), f"duplicates in kinds: {kinds}"
    idx = [SIGNAL_PRIORITY.index(k) for k in kinds]
    assert idx == sorted(idx), f"kinds not in SIGNAL_PRIORITY order: {kinds}"


def metric(event, name):
    found = [m for m in event.metrics if m.name == name]
    assert len(found) == 1, f"metric {name} not found: {[m.name for m in event.metrics]}"
    return found[0]


@pytest.mark.criterion("TASK-002:C1")
@pytest.mark.req("FR-02", "FR-06")
def test_pool_warning_event():
    (ev,) = events(
        [text_line(0, "WARN", "order-service", "db acquire connection slow wait_ms=418 pool_in_use=29 pool_idle=1")]
    )
    assert SignalKind.POOL_SATURATION in ev.kinds
    assert SignalKind.WARNING in ev.kinds
    check_kinds_canonical(ev.kinds)
    assert ev.db_related is True
    m = metric(ev, "wait_ms")
    assert m.value == 418
    assert m.unit == MetricUnit.MS


@pytest.mark.criterion("TASK-002:C2")
@pytest.mark.req("FR-02", "FR-09")
def test_gateway_timeout_json_event():
    line = json_line(
        0,
        "ERROR",
        "api-gateway",
        "upstream timeout",
        status=504,
        upstream="order-service",
        request_id="r-1",
        order_id="o-9",
    )
    (ev,) = events([line])
    assert {SignalKind.TIMEOUT, SignalKind.SERVER_ERROR_STATUS, SignalKind.ERROR} <= set(ev.kinds)
    check_kinds_canonical(ev.kinds)
    assert ev.peers == ("order-service",)
    assert ev.request_id == "r-1"
    assert ev.order_id == "o-9"


@pytest.mark.criterion("TASK-002:C2")
@pytest.mark.req("FR-02")
@pytest.mark.parametrize(
    ("status", "is_server_error"), [(503, True), (500, True), (599, True), (404, False), (200, False), (600, False)]
)
def test_status_5xx_boundaries(status, is_server_error):
    (ev,) = events([text_line(0, "INFO", "api-gateway", f"request served status={status}")])
    assert (SignalKind.SERVER_ERROR_STATUS in ev.kinds) is is_server_error


@pytest.mark.criterion("TASK-002:C3")
@pytest.mark.req("FR-02", "FR-06")
def test_metrics_units_and_direction():
    (ev,) = events(
        [
            text_line(
                0,
                "INFO",
                "svc-a",
                "step done latency_ms=42 db_latency_ms=9 lag_ms=25 timeout_ms=3000 wait_ms=fast req_per_s=12.5",
            )
        ]
    )
    assert [m.name for m in ev.metrics] == ["latency_ms", "db_latency_ms", "lag_ms", "req_per_s"]
    for name in ("latency_ms", "db_latency_ms", "lag_ms"):
        m = metric(ev, name)
        assert m.unit == MetricUnit.MS
        assert m.higher_is_worse is True
        assert m.kind == SignalKind.LATENCY
    assert metric(ev, "latency_ms").value == 42
    assert metric(ev, "db_latency_ms").db_related is True
    rate = metric(ev, "req_per_s")
    assert rate.unit == MetricUnit.PER_SECOND
    assert rate.higher_is_worse is False
    assert rate.value == 12.5


@pytest.mark.criterion("TASK-002:C3")
@pytest.mark.req("FR-02")
def test_non_db_metric_not_db_related():
    (ev,) = events([text_line(0, "INFO", "svc-a", "step done latency_ms=42")])
    assert ev.db_related is False
    assert metric(ev, "latency_ms").db_related is False


@pytest.mark.criterion("TASK-002:C4")
@pytest.mark.req("FR-02")
def test_restart_count_per_pod():
    evs = events(
        [
            text_line(0, "INFO", "kubelet", "pod=p-1 probe=liveness ready=true restart_count=0"),
            text_line(10, "INFO", "kubelet", "pod=p-2 probe=liveness ready=true restart_count=3"),
            text_line(20, "INFO", "kubelet", "pod=p-1 probe=liveness ready=true restart_count=1"),
            text_line(30, "INFO", "kubelet", "pod=p-1 probe=liveness ready=true restart_count=1"),
            text_line(40, "INFO", "kubelet", "pod=p-3 probe=liveness ready=true restart_count=0"),
            text_line(50, "INFO", "kubelet", "pod=p-3 probe=liveness ready=true restart_count=0"),
        ]
    )
    restarts = [SignalKind.RESTART in e.kinds for e in evs]
    assert restarts == [False, False, True, False, False, False]


@pytest.mark.criterion("TASK-002:C4")
@pytest.mark.req("FR-02")
def test_probe_attempt_and_neutral_info():
    evs = events(
        [
            text_line(0, "INFO", "kubelet", "pod=p-1 probe=readiness ready=false"),
            text_line(1, "INFO", "kubelet", "pod=p-1 probe=readiness ready=true"),
            text_line(2, "INFO", "billing-job", "invoice sent attempt=2"),
            text_line(3, "INFO", "billing-job", "invoice sent attempt=1"),
            text_line(4, "INFO", "svc-a", "request served items=3"),
        ]
    )
    assert SignalKind.PROBE_FAILURE in evs[0].kinds
    assert SignalKind.PROBE_FAILURE not in evs[1].kinds
    assert SignalKind.RETRY in evs[2].kinds
    assert SignalKind.RETRY not in evs[3].kinds
    assert evs[4].kinds == ()


@pytest.mark.criterion("TASK-002:C5")
@pytest.mark.req("FR-02")
def test_stack_trace_attached_to_head():
    evs = events(
        [
            text_line(10.0, "ERROR", "svc-a", "request_id=r-5 payment call failed"),
            text_line(10.1, "ERROR", "svc-a", "request_id=r-5 \tat a.B.c(B.java:1)"),
            text_line(
                10.2, "ERROR", "svc-a", "request_id=r-5 Caused by: java.net.SocketTimeoutException: Read timed out"
            ),
        ]
    )
    head = evs[0]
    assert head.continuation_of is None
    assert SignalKind.TIMEOUT in head.kinds
    assert SignalKind.ERROR in head.kinds
    check_kinds_canonical(head.kinds)
    for cont in evs[1:]:
        assert cont.kinds == ()
        assert cont.continuation_of == head.record.line_no == 1


@pytest.mark.criterion("TASK-002:C5")
@pytest.mark.req("FR-02")
@pytest.mark.parametrize(
    "second",
    [
        text_line(11.5, "ERROR", "svc-a", "request_id=r-5 \tat a.B.c(B.java:1)"),
        text_line(10.1, "ERROR", "svc-b", "request_id=r-5 \tat a.B.c(B.java:1)"),
        text_line(10.1, "ERROR", "svc-a", "request_id=r-6 \tat a.B.c(B.java:1)"),
    ],
    ids=["gap-over-1000ms", "other-service", "other-request"],
)
def test_orphan_continuation_becomes_head(second):
    evs = events([text_line(10.0, "ERROR", "svc-a", "request_id=r-5 payment call failed"), second])
    assert evs[1].continuation_of is None


@pytest.mark.criterion("TASK-002:C6")
@pytest.mark.req("FR-06")
def test_db_server_flag_by_service():
    evs = events(
        [
            text_line(0, "INFO", "pg-x", "db_cluster=c1 active_connections=10"),
            text_line(1, "INFO", "pg-x", "stats tick"),
            text_line(2, "INFO", "svc-a", "request served items=1"),
        ]
    )
    assert [e.db_server for e in evs] == [True, True, False]


@pytest.mark.criterion("TASK-002:C6")
@pytest.mark.req("FR-06")
def test_db_keywords_in_message_and_values_not_keys():
    evs = events(
        [
            text_line(0, "INFO", "svc-a", "call to Postgres finished"),
            text_line(1, "INFO", "svc-a", "call finished backend=PostgreSQL"),
            text_line(2, "INFO", "svc-a", "call finished retry_policy=none"),
            text_line(3, "INFO", "svc-a", "call finished"),
        ]
    )
    assert evs[0].db_related is True
    assert evs[1].db_related is True
    assert evs[2].kinds == ()
    assert evs[2].db_related is False
    assert evs[3].db_related is False


@pytest.mark.criterion("TASK-002:C6")
@pytest.mark.req("FR-06")
def test_signal_keyword_in_field_value():
    # not `reason=`: since 2.0, values of RELAYED_FIELDS (a relayed upstream error) are excluded from matching
    (ev,) = events([text_line(0, "INFO", "svc-a", 'call finished error="connection reset by peer"')])
    assert SignalKind.CONNECTION_ERROR in ev.kinds


@pytest.mark.criterion("TASK-002:C7")
@pytest.mark.req("FR-02")
def test_template_masks_variable_parts(impl):
    # hex means only 0x-prefixed literals: the signals spec ("masks numbers, hex/uuid, IP:port")
    # does not say whether bare strings like 4bf92f3577b34da6 count as hex tokens.
    parse_line = impl("parser", "parse_line")
    template = impl("signals", "message_template")
    a = parse_line(
        text_line(
            0,
            "INFO",
            "svc-a",
            "user 1234 paid from 10.0.0.5:8080 span 0x7ffe3a9c10 "
            "key 123e4567-e89b-12d3-a456-426614174000 amount=5 user_id=u1",
        ),
        1,
    )
    b = parse_line(
        text_line(
            5,
            "INFO",
            "svc-a",
            "user 98 paid from 192.168.1.20:5432 span 0x00b1d4ef22 "
            "key 9b2c1f0e-8d7a-4c3b-9a1e-0f5d6c7b8a90 amount=999 user_id=u7",
        ),
        2,
    )
    c = parse_line(
        text_line(
            9,
            "INFO",
            "svc-a",
            "user 1234 refunded from 10.0.0.5:8080 span 0x7ffe3a9c10 "
            "key 123e4567-e89b-12d3-a456-426614174000 amount=5",
        ),
        3,
    )
    ta, tb, tc = template(a), template(b), template(c)
    assert ta == tb
    assert ta != tc
    for t in (ta, tb, tc):
        assert "\n" not in t and len(t) <= 160


@pytest.mark.criterion("TASK-002:C7")
@pytest.mark.req("FR-02")
def test_template_length_newlines_and_empty_message(impl):
    parse_line = impl("parser", "parse_line")
    template = impl("signals", "message_template")
    long_rec = parse_line(text_line(0, "INFO", "svc-a", " ".join(f"word{i}x" for i in range(120))), 1)
    nl_rec = parse_line(json_line(0, "INFO", "svc-a", "first line\nsecond line\r\nthird"), 2)
    empty1 = parse_line(
        text_line(
            0,
            "INFO",
            "svc-a",
            "request_id=r-1 order_id=o-1 user_id=u-1 stage=validate items=1 db_latency_ms=9 cache_hit=false",
        ),
        3,
    )
    empty2 = parse_line(
        text_line(
            1,
            "INFO",
            "svc-a",
            "request_id=r-2 order_id=o-7 user_id=u-9 stage=confirm items=4 db_latency_ms=13 cache_hit=true",
        ),
        4,
    )
    t_long, t_nl = template(long_rec), template(nl_rec)
    assert len(t_long) <= 160 and "\n" not in t_long
    assert "\n" not in t_nl and "\r" not in t_nl and len(t_nl) <= 160
    assert template(empty1) == "{cache_hit,db_latency_ms,items,stage}"
    assert template(empty2) == template(empty1)


@pytest.mark.criterion("TASK-002:C7")
@pytest.mark.req("FR-02")
def test_event_template_is_message_template(impl):
    template = impl("signals", "message_template")
    ns = stage_events(
        [
            text_line(0, "WARN", "svc-a", "queue 17 is slow depth=5"),
            text_line(1, "INFO", "svc-a", "stage=validate items=2"),
        ]
    )
    for ev in ns.events:
        assert ev.template == template(ev.record)


@pytest.mark.criterion("TASK-002:C8")
@pytest.mark.req("FR-02", "FR-11")
def test_smoke_one_event_per_record():
    ns = stage_events(read_log_lines(ATTACHED_LOG))
    assert len(ns.events) == len(ns.parse.records) > 0
    for ev, rec in zip(ns.events, ns.parse.records):
        assert ev.record == rec
        check_kinds_canonical(ev.kinds)


@pytest.mark.criterion("TASK-002:C8")
@pytest.mark.req("FR-11", "NEG-06")
def test_rules_do_not_depend_on_service_name():
    def scenario(svc):
        return [
            text_line(0, "WARN", svc, "db acquire connection slow wait_ms=418 pool_in_use=29 pool_idle=1"),
            json_line(1, "ERROR", svc, "upstream timeout", status=504, upstream="peer-node", request_id="r-1"),
            text_line(2, "INFO", svc, "step done latency_ms=42 req_per_s=3.5"),
            text_line(3, "ERROR", svc, 'write failed error="connection refused"'),
            text_line(4, "INFO", svc, "invoice sent attempt=2"),
        ]

    a, b = events(scenario("alpha-node")), events(scenario("beta-node"))
    assert len(a) == len(b) == 5
    for x, y in zip(a, b):
        assert x.kinds == y.kinds
        assert x.metrics == y.metrics
        assert x.db_related == y.db_related
    assert any(e.kinds for e in a)
