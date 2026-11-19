from test_task.contracts.common import Level, MetricUnit, SignalKind
from test_task.parser import parse_line
from test_task.signals import extract_events, message_template


def recs(*lines):
    out = []
    for i, line in enumerate(lines, 1):
        rec = parse_line(line, i)
        assert rec is not None and not hasattr(rec, "reason")
        out.append(rec)
    return out


def ts(sec):
    return f"2026-03-27T14:00:{sec:06.3f}Z"


def test_pool_warning_and_metrics():
    (ev,) = extract_events(
        recs(
            f"{ts(0)} WARN svc db acquire connection slow wait_ms=418 pool_in_use=29 pool_idle=0 hit_rate=0.5"
        )
    )
    assert ev.kinds == (SignalKind.POOL_SATURATION, SignalKind.WARNING)
    assert ev.db_related
    by = {m.name: m for m in ev.metrics}
    assert by["wait_ms"].value == 418 and by["wait_ms"].unit is MetricUnit.MS
    assert by["pool_idle"].higher_is_worse is False
    assert by["pool_in_use"].unit is MetricUnit.COUNT
    assert by["hit_rate"].unit is MetricUnit.RATIO


def test_fields_rules():
    evs = extract_events(
        recs(
            f"{ts(0)} INFO k pod=a restart_count=0 probe=x ready=false timeout_ms=5",
            f"{ts(1)} INFO k pod=a restart_count=1 attempt=3 status=502 waiting_clients=2",
            f"{ts(2)} ERROR k timeout_ms=5 note=ok",
            f"{ts(3)} INFO k restart_count=1",
            f"{ts(4)} INFO k restart_count=2 peer_service=x target=x",
            f"{ts(5)} FATAL k hello",
        )
    )
    assert evs[0].kinds == (SignalKind.PROBE_FAILURE,)
    assert evs[0].metrics == ()
    assert SignalKind.RESTART in evs[1].kinds
    assert (
        SignalKind.RETRY in evs[1].kinds
        and SignalKind.SERVER_ERROR_STATUS in evs[1].kinds
    )
    assert SignalKind.POOL_SATURATION in evs[1].kinds
    assert SignalKind.TIMEOUT in evs[2].kinds
    assert SignalKind.RESTART not in evs[3].kinds
    assert SignalKind.RESTART in evs[4].kinds and evs[4].peers == ("x",)
    assert evs[5].kinds == (SignalKind.ERROR,)


def test_continuation_and_db_server():
    evs = extract_events(
        recs(
            f"{ts(0)} ERROR s request_id=r1 boom db_cluster=c",
            f"{ts(0.1)} ERROR s request_id=r1 Caused by: Read timed out",
            f"{ts(5)} ERROR s request_id=r1 \tat a.B.c(B.java:1)",
            f"{ts(5.1)} INFO t other",
        )
    )
    assert evs[1].continuation_of == 1 and evs[1].kinds == ()
    assert SignalKind.TIMEOUT in evs[0].kinds
    assert evs[2].continuation_of is None and evs[2].kinds == (SignalKind.ERROR,)
    assert [e.db_server for e in evs] == [True, True, True, False]
    assert evs[0].request_id == "r1"


def test_templates():
    r = recs(
        f"{ts(0)} INFO s hit 10.0.0.1:80 id 4bf92f3577b34da6 n 12 x=1",
        f"{ts(0)} INFO s hit 10.0.0.2:81 id 5bf92f3577b34da7 n 99",
        f"{ts(0)} INFO s a=1 b=2 u_id=3",
    )
    assert message_template(r[0]) == message_template(r[1])
    assert message_template(r[2]) == "{a,b}"
    assert r[0].level is Level.INFO
