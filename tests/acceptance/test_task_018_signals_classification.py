"""TASK-018: classification of restarts, dropped connections, DB markers and relayed errors in reason."""

from __future__ import annotations

import pytest
from acc_support import (
    APP_NAMES_OF_ATTACHED_LOG,
    ATTACHED_LOG,
    PROJECT_ROOT,
    json_line,
    read_log_lines,
    scan_for,
    stage_scored,
    text_line,
)

from test_task.contracts.common import DbCategory, SignalKind

K = SignalKind
C = DbCategory
DBX = "db-x"
DB_STATS = "stats db_cluster=main active_connections=12 max_connections=100 waiting_clients=0"


def calm_prefix(services=("svc-a", "front-gateway", "mail-worker", "batch-worker")):
    """Two minutes of normal traffic: DB server stats and neutral INFO lines from other services."""
    out = []
    for i in range(12):
        out.append(text_line(10 * i, "INFO", DBX, DB_STATS))
        out.append(text_line(10 * i + 1, "INFO", services[i % len(services)], f"heartbeat ok seq={i}"))
    return out


def scored_tail(*lines):
    """Normal start + lines under test (from 200 s); returns the labeled events of the lines under test."""
    prefix = calm_prefix()
    tail = [line_at(200 + 5 * i, line) for i, line in enumerate(lines)]
    ns = stage_scored(prefix + tail)
    by_line = {s.event.record.line_no: s for s in ns.scored}
    return [by_line[len(prefix) + i + 1] for i in range(len(tail))]


def line_at(sec, spec):
    level, service, rest = spec
    if isinstance(rest, dict):
        return json_line(sec, level, service, rest.pop("msg"), **rest)
    return text_line(sec, level, service, rest)


@pytest.mark.criterion("TASK-018:C1")
@pytest.mark.req("FR-12")
def test_starting_up_in_field_is_dependency_unavailable():
    (s,) = scored_tail(("ERROR", "svc-a", 'failed to persist error="pq: the database system is starting up"'))
    assert K.DEPENDENCY_UNAVAILABLE in s.event.kinds
    assert K.RESTART not in s.event.kinds
    assert s.event.db_related is True
    assert C.CONNECTION_ERRORS in s.db_categories
    assert C.SERVER_RESTART not in s.db_categories


@pytest.mark.criterion("TASK-018:C1")
@pytest.mark.req("FR-12")
def test_starting_up_in_db_server_message_is_restart():
    (s,) = scored_tail(("ERROR", DBX, "the database system is starting up db_cluster=main"))
    assert K.RESTART in s.event.kinds
    assert s.event.db_server is True
    assert C.SERVER_RESTART in s.db_categories


@pytest.mark.criterion("TASK-018:C2")
@pytest.mark.req("FR-13", "FR-06")
def test_connection_drops_are_not_query_timeouts():
    recv, pipe, eof = scored_tail(
        ("ERROR", DBX, "could not receive data from client: Connection timed out"),
        ("ERROR", DBX, "could not send data to client: Broken pipe"),
        ("ERROR", DBX, "unexpected EOF on client connection with an open transaction"),
    )
    assert K.CONNECTION_ERROR in recv.event.kinds and K.TIMEOUT not in recv.event.kinds
    assert C.CONNECTION_ERRORS in recv.db_categories and C.QUERY_TIMEOUT not in recv.db_categories
    assert K.TIMEOUT not in pipe.event.kinds and C.QUERY_TIMEOUT not in pipe.db_categories
    assert eof.event.aborted_transaction is True
    assert C.ABORTED_TRANSACTIONS in eof.db_categories
    assert recv.event.aborted_transaction is False


@pytest.mark.criterion("TASK-018:C3")
@pytest.mark.req("FR-14")
def test_db_related_only_from_own_markers():
    mailer, gw, lat, pq = scored_tail(
        ("WARN", "mail-worker", 'delivery attempt failed provider=smtp reason="connection reset by peer"'),
        ("ERROR", "front-gateway", {"msg": "upstream call failed", "status": 503, "error": "database unavailable"}),
        ("INFO", "svc-a", "order stored db_latency_ms=14"),
        ("WARN", "svc-a", "pq: canceling statement due to user request"),
    )
    assert mailer.event.db_related is False and mailer.db_categories == ()
    assert gw.event.db_related is False and gw.db_categories == ()
    assert lat.event.db_related is True
    assert pq.event.db_related is True


@pytest.mark.criterion("TASK-018:C4")
@pytest.mark.req("FR-16")
def test_relayed_reason_gives_only_retry():
    (s,) = scored_tail(("WARN", "batch-worker", 'retrying attempt=1 reason="db timeout acquiring connection"'))
    assert K.RETRY in s.event.kinds
    assert K.POOL_SATURATION not in s.event.kinds and K.TIMEOUT not in s.event.kinds
    assert s.event.db_related is False
    assert C.POOL_EXHAUSTION not in s.db_categories


@pytest.mark.criterion("TASK-018:C5")
@pytest.mark.req("QA-13")
def test_recovery_markers():
    ready, normalized, plain = scored_tail(
        ("INFO", DBX, "database system is ready to accept connections"),
        ("INFO", "batch-worker", "queue depth normalized queue_depth=0"),
        ("INFO", "batch-worker", "queue drained items=0"),
    )
    for s in (ready, normalized):
        assert s.event.recovery_marker is True, s.event.record.raw
        assert s.event.kinds == ()
        assert s.anomalous is False
    assert plain.event.recovery_marker is False


@pytest.fixture(scope="module")
def attached():
    return stage_scored(read_log_lines(ATTACHED_LOG))


@pytest.mark.criterion("TASK-018:C6")
@pytest.mark.req("FR-12")
def test_attached_restart_only_on_db_servers(attached):
    restarts = [s for s in attached.scored if K.RESTART in s.event.kinds]
    assert restarts, "the attached log contains a DB server restart"
    bad = [(s.event.record.line_no, s.event.record.service) for s in restarts if not s.event.db_server]
    assert bad == []


@pytest.mark.criterion("TASK-018:C6")
@pytest.mark.req("FR-13")
def test_attached_drops_not_query_timeouts(attached):
    drops = ("could not receive data", "could not send data", "unexpected EOF", "Broken pipe")
    bad = [
        s.event.record.line_no
        for s in attached.scored
        if C.QUERY_TIMEOUT in s.db_categories and any(d in s.event.record.raw for d in drops)
    ]
    assert bad == []
    assert any(C.ABORTED_TRANSACTIONS in s.db_categories for s in attached.scored)


@pytest.mark.criterion("TASK-018:C6")
@pytest.mark.req("FR-14", "FR-16")
def test_attached_non_db_services(attached):
    by = {}
    for s in attached.scored:
        by.setdefault(s.event.record.service, []).append(s)
    assert by.get("notification-service"), "the service is present in the log"
    assert all(s.db_categories == () for s in by["notification-service"])
    for svc in ("billing-worker", "inventory-service"):
        assert by.get(svc), svc
        for s in by[svc]:
            assert K.POOL_SATURATION not in s.event.kinds, (svc, s.event.record.line_no)
            assert C.POOL_EXHAUSTION not in s.db_categories, (svc, s.event.record.line_no)


@pytest.mark.criterion("TASK-018:C7")
@pytest.mark.req("NEG-07")
def test_no_attached_log_specifics_in_rules():
    files = [PROJECT_ROOT / "src" / "test_task" / n for n in ("signals.py", "baseline.py")]
    assert scan_for(files, list(APP_NAMES_OF_ATTACHED_LOG) + ["2026-03-27"]) == []
