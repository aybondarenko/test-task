"""TASK-006: service graph, findings, severity and cause/symptom roles."""

from __future__ import annotations

import re

import pytest
from acc_support import (
    ATTACHED_LOG,
    CHAIN_LOG,
    DB,
    GW,
    NOISE,
    SVC,
    at,
    chain_lines,
    episode_client_first,
    episode_db_first,
    findings_of_service,
    json_line,
    normal_block,
    overconfident,
    read_log_lines,
    stage_analysis,
    stage_patterns,
    text_line,
)

from test_task.contracts.causality import EdgeSource, ServiceClass
from test_task.contracts.common import SIGNAL_PRIORITY, Role, Severity, SignalKind

RESIDUAL = "residual deviation outside detected episodes"


def finding_with(analysis, kind, service):
    """The single finding with service ``service`` whose primary ``kind`` or one of ``kinds`` matches.

    Since 2.0 a finding merges groups of different kinds and services of the same layer within an episode
    (``Finding.kinds``), so ``kind`` is also looked up in ``kinds``; in 1.0 ``kinds`` is empty and this checks ``kind``.
    """
    found = [f for f in analysis.findings if service in f.services and kind in (f.kinds or (f.kind,))]
    assert len(found) == 1, (
        f"expected exactly one {kind.value} finding with {service}, got: "
        f"{[(f.id, f.kind.value, f.kinds, f.services) for f in analysis.findings]}"
    )
    return found[0]


def noise(start=0, end=300, step=30):
    return [text_line(s, "INFO", "svc-noise", "heartbeat") for s in range(start, end, step)]


def graph_of(lines, impl):
    ns = stage_patterns(lines)
    return impl("causality", "build_service_graph")(ns.scored, ns.patterns)


def edge_set(graph):
    return {(e.dependent, e.dependency, e.source) for e in graph.edges}


@pytest.mark.criterion("TASK-006:C1")
@pytest.mark.req("FR-04", "CTX-02")
def test_service_graph_gateway_service_db(impl):
    g = graph_of(normal_block(0, 300), impl)
    assert [s.service for s in g.services] == sorted([GW, SVC, DB, NOISE])
    classes = {s.service: s.service_class for s in g.services}
    assert classes[DB] == ServiceClass.DB_SERVER
    assert classes[GW] == ServiceClass.ENTRY_POINT
    assert classes[SVC] == ServiceClass.INTERNAL
    assert classes[NOISE] == ServiceClass.INTERNAL
    assert edge_set(g) == {(GW, SVC, EdgeSource.PEER_FIELD), (SVC, DB, EdgeSource.DB_ACCESS)}
    assert all(e.dependent != e.dependency for e in g.edges)
    assert all(e.count >= 1 for e in g.edges)
    pairs = [(e.dependent, e.dependency) for e in g.edges]
    assert pairs == sorted(pairs)


@pytest.mark.criterion("TASK-006:C2")
@pytest.mark.req("FR-04", "CTX-02")
def test_trace_order_edge_added(impl):
    lines = noise() + [
        text_line(310, "INFO", "alpha-service", "step one done request_id=t-1"),
        text_line(320, "ERROR", "beta-service", "step two rejected request_id=t-1"),
    ]
    edges = edge_set(graph_of(lines, impl))
    assert ("beta-service", "alpha-service", EdgeSource.TRACE_ORDER) in edges
    assert not any(d == "alpha-service" and t == "beta-service" for d, t, _ in edges)


@pytest.mark.criterion("TASK-006:C2")
@pytest.mark.req("FR-04", "CTX-02")
def test_trace_order_suppressed_by_explicit_reverse_edge(impl):
    lines = noise() + [
        text_line(310, "INFO", "alpha-service", "step one done request_id=t-1 upstream=beta-service"),
        text_line(320, "ERROR", "beta-service", "step two rejected request_id=t-1"),
    ]
    edges = edge_set(graph_of(lines, impl))
    assert ("alpha-service", "beta-service", EdgeSource.PEER_FIELD) in edges
    assert ("beta-service", "alpha-service", EdgeSource.TRACE_ORDER) not in edges


@pytest.mark.criterion("TASK-006:C3")
@pytest.mark.req("FR-04", "OUT-02")
def test_roles_db_is_cause_when_client_shows_first():
    ns = stage_analysis(chain_lines(episode_client_first()))
    assert len(ns.timeline.episodes) == 1
    a = ns.analysis
    db = finding_with(a, SignalKind.POOL_SATURATION, DB)
    assert db.role == Role.CAUSE
    assert db.service_class == ServiceClass.DB_SERVER
    for f in findings_of_service(a, SVC) + findings_of_service(a, GW):
        assert f.role == Role.SYMPTOM, f"{f.id} {f.kind} {f.services}"
    gw = finding_with(a, SignalKind.TIMEOUT, GW)
    assert gw.severity == Severity.CRITICAL
    assert gw.service_class == ServiceClass.ENTRY_POINT
    for f in a.findings:
        assert f.role_rationale.strip()
        assert f.episode_id == "E1"
        # Finding contract: the rationale mentions the start time (all findings of the episode start at 14:05)
        assert re.search(r"(?<!\d)14:05", f.role_rationale), f.role_rationale
    # ...and the dependency: the DB client symptom points to the DB server, the gateway symptom to the chain
    svc = finding_with(a, SignalKind.POOL_SATURATION, SVC)
    assert DB in svc.role_rationale, svc.role_rationale
    assert SVC in gw.role_rationale or DB in gw.role_rationale, gw.role_rationale


@pytest.mark.criterion("TASK-006:C4")
@pytest.mark.req("FR-04")
def test_each_episode_has_cause_and_residual_is_symptom():
    ns = stage_analysis(read_log_lines(CHAIN_LOG))
    a = ns.analysis
    for ep in ns.timeline.episodes:
        in_ep = [f for f in a.findings if f.episode_id == ep.id]
        if in_ep:
            assert any(f.role == Role.CAUSE for f in in_ep)
    residual = [f for f in a.findings if f.episode_id is None]
    assert [f.services for f in residual] == [(NOISE,)]
    assert residual[0].role == Role.SYMPTOM
    assert RESIDUAL in residual[0].role_rationale


@pytest.mark.criterion("TASK-006:C4")
@pytest.mark.req("FR-04")
@pytest.mark.parametrize(("offset", "role"), [(30, Role.CAUSE), (45, Role.SYMPTOM)], ids=["onset+30s", "onset+45s"])
def test_cause_candidates_limited_by_cause_window(offset, role):
    """An independent service with no edges starts failing ``offset`` s after onset (14:05:00) of the same episode.

    Cause candidates are findings with ``window.start <= onset + cause_window_seconds`` (30 s): at the boundary
    the finding is a non-dominated candidate (CAUSE), past the window it is a SYMPTOM; CAUSE stays with the DB server.
    """
    extra = [
        text_line(300 + offset, "ERROR", "batch-worker", "export job rejected"),
        text_line(300 + offset + 7, "ERROR", "batch-worker", "export job rejected"),
    ]
    ns = stage_analysis(chain_lines(episode_db_first(), extra))
    (ep,) = ns.timeline.episodes
    assert ep.onset == at(300)
    a = ns.analysis
    assert not any("batch-worker" in (e.dependent, e.dependency) for e in a.graph.edges)
    batch = finding_with(a, SignalKind.ERROR, "batch-worker")
    assert batch.episode_id == ep.id
    assert batch.window.start == at(300 + offset)
    assert batch.role == role
    db = finding_with(a, SignalKind.POOL_SATURATION, DB)
    assert db.role == Role.CAUSE
    for f in findings_of_service(a, SVC) + findings_of_service(a, GW):
        assert f.role == Role.SYMPTOM, f"{f.id} {f.kind} {f.services}"


@pytest.mark.criterion("TASK-006:C4")
@pytest.mark.req("FR-04")
def test_dependency_cycle_earliest_is_cause():
    prelude = []
    for k in range(4):
        prelude.append(text_line(5 + 60 * k, "INFO", "alpha-service", "call sent upstream=beta-service"))
        prelude.append(text_line(10 + 60 * k, "INFO", "beta-service", "call sent upstream=alpha-service"))
    incident = [
        text_line(310, "ERROR", "alpha-service", "charge rejected"),
        text_line(315, "ERROR", "alpha-service", "charge rejected"),
        text_line(320, "WARN", "beta-service", "upstream timeout"),
        text_line(325, "WARN", "beta-service", "upstream timeout"),
    ]
    lines = sorted(noise() + prelude, key=lambda x: x.split(" ")[0]) + incident + noise(400, 800)
    a = stage_analysis(lines).analysis
    alpha = finding_with(a, SignalKind.ERROR, "alpha-service")
    beta = finding_with(a, SignalKind.TIMEOUT, "beta-service")
    assert alpha.episode_id == beta.episode_id is not None
    assert alpha.role == Role.CAUSE
    # since 2.0 both INTERNAL services without DB markers in the episode form a single finding
    assert beta.id == alpha.id or beta.role == Role.SYMPTOM


@pytest.mark.criterion("TASK-006:C4")
@pytest.mark.req("FR-04")
def test_unmergeable_cycle_earliest_is_only_cause():
    """Mutual upstream edges (a cycle) and findings that do not merge even in 2.0 (DB group alpha with ``pq:``
    and non-DB group beta): only the earliest is CAUSE (in a cycle, the earliest ones win)."""
    prelude = []
    for k in range(4):
        prelude.append(text_line(5 + 60 * k, "INFO", "alpha-service", "call sent upstream=beta-service"))
        prelude.append(text_line(10 + 60 * k, "INFO", "beta-service", "call sent upstream=alpha-service"))
    incident = [
        text_line(310, "ERROR", "alpha-service", "pq: charge insert rejected upstream=beta-service"),
        text_line(315, "ERROR", "alpha-service", "pq: charge insert rejected upstream=beta-service"),
        text_line(320, "WARN", "beta-service", "upstream timeout upstream=alpha-service"),
        text_line(325, "WARN", "beta-service", "upstream timeout upstream=alpha-service"),
    ]
    lines = sorted(noise() + prelude, key=lambda x: x.split(" ")[0]) + incident + noise(400, 800)
    a = stage_analysis(lines).analysis
    alpha = [f for f in a.findings if "alpha-service" in f.services]
    beta = [f for f in a.findings if "beta-service" in f.services]
    assert len(alpha) == 1 and len(beta) == 1 and alpha[0].id != beta[0].id, [(f.id, f.services) for f in a.findings]
    assert alpha[0].episode_id == beta[0].episode_id is not None
    assert alpha[0].role == Role.CAUSE
    assert beta[0].role == Role.SYMPTOM


@pytest.mark.criterion("TASK-006:C4")
@pytest.mark.req("FR-04")
def test_no_anomalies_no_findings():
    ns = stage_analysis(normal_block(0, 600))
    assert ns.analysis.findings == ()


def severity_lines():
    base = [text_line(15 + 60 * k, "INFO", "stats-service", "report built latency_ms=50") for k in range(5)]
    incident = [
        json_line(300, "ERROR", GW, "upstream timeout", upstream=SVC, status=504, request_id="r-7001"),
        text_line(302, "INFO", GW, f"request rejected status=503 upstream={SVC}"),
        text_line(304, "WARN", GW, f"upstream connection refused upstream={SVC}"),
        text_line(306, "ERROR", DB, "db_cluster=main server process was terminated by signal 9: Killed"),
        text_line(308, "ERROR", SVC, "order write failed"),
        text_line(310, "WARN", SVC, "lock wait on orders table"),
        text_line(312, "WARN", SVC, "db acquire connection slow wait_ms=900 pool_in_use=20 pool_idle=0"),
    ]
    # Since 2.0, groups of INTERNAL services without DB markers in one episode form a single finding with the
    # maximum severity. To test MEDIUM and LOW separately, these signals go into separate episodes.
    later = [
        text_line(600, "INFO", "stats-service", "report built latency_ms=900"),
        text_line(606, "INFO", "stats-service", "report built latency_ms=900"),
        text_line(900, "WARN", "notify-worker", "template cache stale"),
        text_line(906, "WARN", "notify-worker", "template cache stale"),
    ]
    head = sorted(normal_block(0, 300) + base, key=lambda x: x.split(" ")[0])
    tail = sorted(normal_block(420, 1200, first_index=200) + later, key=lambda x: x.split(" ")[0])
    return head + incident + tail


@pytest.mark.criterion("TASK-006:C5")
@pytest.mark.req("OUT-02", "OUT-13")
@pytest.mark.parametrize(
    ("kind", "service", "severity"),
    [
        (SignalKind.TIMEOUT, GW, Severity.CRITICAL),
        (SignalKind.SERVER_ERROR_STATUS, GW, Severity.CRITICAL),
        (SignalKind.CONNECTION_ERROR, GW, Severity.CRITICAL),
        (SignalKind.RESTART, DB, Severity.CRITICAL),
        (SignalKind.ERROR, SVC, Severity.HIGH),
        (SignalKind.LOCK, SVC, Severity.HIGH),
        (SignalKind.POOL_SATURATION, SVC, Severity.HIGH),
        (SignalKind.LATENCY, "stats-service", Severity.MEDIUM),
        (SignalKind.WARNING, "notify-worker", Severity.LOW),
    ],
)
def test_severity_rules(kind, service, severity):
    a = stage_analysis(severity_lines()).analysis
    f = finding_with(a, kind, service)
    assert f.severity == severity
    assert all(isinstance(x.severity, Severity) for x in a.findings)


INTERNAL_SIGNALS = {
    # kind: (service without peer fields or DB server fields, rest of the line)
    SignalKind.TIMEOUT: ("quote-service", "quote fetch timed out"),
    SignalKind.SERVER_ERROR_STATUS: ("price-service", "quote rejected status=503"),
    SignalKind.CONNECTION_ERROR: ("cache-service", "cache connection reset by peer"),
    SignalKind.RESTART: ("cron-service", "worker restarted after fault"),
    SignalKind.RETRY: ("pay-service", "retrying charge call attempt=2"),
    SignalKind.QUEUE_BACKLOG: ("feed-service", "backlog growing queue_depth=500"),
    SignalKind.PROBE_FAILURE: ("probe-service", "readiness check probe=readiness ready=false"),
}


def internal_severity_lines(level, kind):
    """Signal ``kind`` on an INTERNAL service (not a gateway or DB server), two ``level`` heads in one episode.

    One signal per run: since 2.0, groups of INTERNAL services without DB markers in one episode
    are merged into a single finding with the maximum severity.
    """
    service, rest = INTERNAL_SIGNALS[kind]
    incident = [text_line(300, level, service, rest), text_line(306, level, service, rest)]
    return noise() + incident + noise(420, 800)


@pytest.mark.criterion("TASK-006:C5")
@pytest.mark.req("OUT-02", "OUT-13")
@pytest.mark.parametrize(
    ("level", "kind", "severity"),
    [
        # CRITICAL rules require ENTRY_POINT/DB_SERVER, so they do not fire for INTERNAL
        ("WARN", SignalKind.TIMEOUT, Severity.MEDIUM),
        ("WARN", SignalKind.SERVER_ERROR_STATUS, Severity.MEDIUM),
        ("ERROR", SignalKind.TIMEOUT, Severity.HIGH),
        ("ERROR", SignalKind.SERVER_ERROR_STATUS, Severity.HIGH),
        ("WARN", SignalKind.CONNECTION_ERROR, Severity.HIGH),
        ("WARN", SignalKind.RESTART, Severity.HIGH),
        ("WARN", SignalKind.RETRY, Severity.MEDIUM),
        ("WARN", SignalKind.QUEUE_BACKLOG, Severity.MEDIUM),
        ("WARN", SignalKind.PROBE_FAILURE, Severity.MEDIUM),
    ],
)
def test_severity_depends_on_service_layer(level, kind, severity):
    a = stage_analysis(internal_severity_lines(level, kind)).analysis
    service = INTERNAL_SIGNALS[kind][0]
    classes = {s.service: s.service_class for s in a.graph.services}
    assert classes[service] == ServiceClass.INTERNAL
    f = finding_with(a, kind, service)
    assert f.service_class == ServiceClass.INTERNAL
    assert f.severity == severity


@pytest.mark.criterion("TASK-006:C6")
@pytest.mark.req("OUT-02")
def test_findings_merge_same_kind_layer_episode():
    lines = (
        noise()
        + [
            text_line(310, "ERROR", "alpha-service", "charge rejected"),
            # DB marker in message: since 2.0, DB and non-DB INTERNAL groups are not merged,
            # so the WARNING stays a separate finding in both 1.0 and 2.0
            text_line(312, "WARN", "gamma-service", "postgres config drift noticed"),
            text_line(315, "ERROR", "beta-service", "stock rejected"),
            text_line(320, "ERROR", "alpha-service", "charge rejected"),
            text_line(325, "ERROR", "beta-service", "stock rejected"),
            text_line(330, "ERROR", "beta-service", "stock rejected"),
        ]
        + noise(420, 800)
    )
    ns = stage_analysis(lines)
    a = ns.analysis
    groups = {g.id: g for g in ns.patterns.groups}
    err = [f for f in a.findings if f.kind == SignalKind.ERROR]
    assert len(err) == 1
    f = err[0]
    assert f.services == ("alpha-service", "beta-service")
    assert f.service_class == ServiceClass.INTERNAL
    assert f.count == 5
    assert len(f.group_ids) == 2 and set(f.group_ids) <= set(groups)
    assert sum(groups[g].count for g in f.group_ids) == f.count
    assert "alpha-service" in f.title and "beta-service" in f.title
    assert re.search(r"\b5\b", f.title)
    assert "\n" not in f.title and not overconfident(f.title)
    assert [x.id for x in a.findings] == [f"F{i}" for i in range(1, len(a.findings) + 1)]
    assert a.findings[0].kind == SignalKind.ERROR
    assert a.findings[1].kind == SignalKind.WARNING
    keys = [(x.window.start, SIGNAL_PRIORITY.index(x.kind)) for x in a.findings]
    assert keys == sorted(keys)
    for x in a.findings:
        assert set(x.group_ids) <= set(groups)
        assert list(x.services) == sorted(x.services)


@pytest.mark.criterion("TASK-006:C7")
@pytest.mark.req("OUT-10")
def test_evidence_limits_and_truncation():
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"]
    # DB marker in message: since 2.0, non-DB INTERNAL groups of an episode are merged into one finding
    long_line = text_line(320, "WARN", "long-service", "postgres config drift noticed note=" + "a" * 300)
    base = [text_line(15 + 60 * k, "INFO", "svc-m", "response sent latency_ms=50") for k in range(5)]
    lines = (
        sorted(noise() + base, key=lambda x: x.split(" ")[0])
        + [
            text_line(310 + i, "ERROR", "wide-service", f"{w} step rejected request_id=w-{i}")
            for i, w in enumerate(words)
        ]
        + [long_line]
        + noise(420, 600)
        # svc-m latency is in a separate episode so its finding does not merge with wide-service (2.0)
        + [text_line(600, "WARN", "svc-m", "response slow latency_ms=900")]
        + [text_line(606, "WARN", "svc-m", "response slow latency_ms=900")]
        + noise(630, 800)
    )
    ns = stage_analysis(lines)
    a = ns.analysis
    groups = {g.id: g for g in ns.patterns.groups}
    raw_by_line = {r.line_no: r.raw for r in ns.parse.records}
    for f in a.findings:
        ev = f.evidence
        assert 1 <= len(ev.samples) <= 3
        for s in ev.samples:
            assert s.line_no >= 1
            assert len(s.text) <= 240
            if len(raw_by_line[s.line_no]) > 240:
                assert s.text.endswith("...")
        assert len(ev.templates) <= 5
        assert len(ev.request_ids) <= 5 and list(ev.request_ids) == sorted(ev.request_ids)
        if any(groups[g].metrics for g in f.group_ids):
            assert ev.metrics
    wide = finding_with(a, SignalKind.ERROR, "wide-service")
    assert 1 <= len(wide.evidence.templates) <= 5
    assert 1 <= len(wide.evidence.request_ids) <= 5
    assert set(wide.evidence.request_ids) <= {f"w-{i}" for i in range(7)}
    long_f = finding_with(a, SignalKind.WARNING, "long-service")
    (sample,) = long_f.evidence.samples
    assert sample.text.endswith("...") and len(sample.text) <= 240
    assert sample.text.startswith(long_line[:150])
    lat = finding_with(a, SignalKind.LATENCY, "svc-m")
    assert "latency_ms" in {m.metric for m in lat.evidence.metrics}


@pytest.mark.criterion("TASK-006:C8")
@pytest.mark.req("FR-04", "CTX-02", "OUT-02")
def test_smoke_attached_log():
    ns = stage_analysis(read_log_lines(ATTACHED_LOG))
    g = ns.analysis.graph
    assert any(s.service_class == ServiceClass.DB_SERVER for s in g.services)
    assert any(e.source == EdgeSource.DB_ACCESS for e in g.edges)
    servers = {s.service for s in g.services if s.service_class == ServiceClass.DB_SERVER}
    assert all(e.dependency in servers for e in g.edges if e.source == EdgeSource.DB_ACCESS)
    assert any(f.role == Role.CAUSE for f in ns.analysis.findings)
    assert any(f.db_categories for f in ns.analysis.findings)
