"""TASK-020: merged findings, strict DB_ACCESS edges, metric shifts and propagation along the graph."""

from __future__ import annotations

import pytest
from acc_support import (
    APP_NAMES_OF_ATTACHED_LOG,
    ATTACHED_LOG,
    DB,
    GW,
    MAILER,
    PROJECT_ROOT,
    SVC,
    SVC_B,
    calm,
    json_line,
    read_log_lines,
    saturation_burst,
    scan_for,
    scenario,
    stage_analysis,
    text_line,
)

from test_task.contracts.causality import CompositeKind, EdgeSource, ServiceClass
from test_task.contracts.common import Role, Severity, SignalKind

K = SignalKind
PRICING = "pricing-service"
BATCH = "batch-worker"


def edges(graph, source=None):
    return {(e.dependent, e.dependency) for e in graph.edges if source is None or e.source == source}


def episode_findings(analysis, episode_id):
    return [f for f in analysis.findings if f.episode_id == episode_id]


def db_finding(analysis):
    found = [f for f in analysis.findings if f.service_class == ServiceClass.DB_SERVER and DB in f.services]
    assert len(found) == 1, [(f.id, f.service_class.value, f.services) for f in analysis.findings]
    return found[0]


def gw_findings(analysis):
    return [f for f in analysis.findings if GW in f.services]


@pytest.fixture(scope="module")
def saturation():
    return stage_analysis(scenario(calm(0, 300), saturation_burst(300), calm(420, 900, first_index=100)))


@pytest.mark.criterion("TASK-020:C1")
@pytest.mark.req("FR-15")
def test_db_access_edges_only_for_own_db_events():
    extra = []
    for k in range(6):
        t = 125 + 20 * k
        extra.append(text_line(t, "WARN", BATCH, f'retrying attempt=2 job=j{k} reason="db timeout acquiring connection"'))
        extra.append(
            json_line(t + 2, "ERROR", GW, "upstream call failed", upstream=PRICING, status=503, error="database unavailable")
        )
        extra.append(text_line(t + 4, "WARN", PRICING, f"pq: canceling statement due to user request job=j{k}"))
    ns = stage_analysis(scenario(calm(0, 400), extra))
    db_access = edges(ns.graph, EdgeSource.DB_ACCESS)
    assert not [e for e in db_access if e[0] in (BATCH, GW)], db_access
    assert {(SVC, DB), (SVC_B, DB), (PRICING, DB)} <= db_access, db_access
    assert (GW, SVC) in edges(ns.graph, EdgeSource.PEER_FIELD)


@pytest.mark.criterion("TASK-020:C2")
@pytest.mark.req("OUT-14", "FR-14")
def test_one_entry_point_finding_and_separate_mailer():
    ep = []
    for k in range(6):
        t = 300 + 10 * k
        rid = f"g-{k}"
        ep.append(json_line(t, "ERROR", GW, "upstream timeout", request_id=rid, upstream=SVC, status=504, latency_ms=3000))
        ep.append(text_line(t + 1, "WARN", SVC, f"db acquire connection slow request_id={rid} wait_ms=900 pool_in_use=20 pool_idle=0"))
        ep.append(json_line(t + 3, "ERROR", GW, "upstream error", request_id=f"{rid}b", upstream=SVC_B, status=503))
        ep.append(text_line(t + 6, "INFO", GW, f"request served request_id={rid}c status=200 upstream={SVC} latency_ms=2500"))
        if k % 2 == 0:
            ep.append(text_line(t + 8, "WARN", MAILER, 'delivery attempt failed provider=smtp reason="connection reset by peer"'))
    ns = stage_analysis(scenario(calm(0, 300), ep, calm(360, 900, first_index=100)))
    assert len(ns.timeline.episodes) == 1
    eid = ns.timeline.episodes[0].id
    entry = [f for f in episode_findings(ns.analysis, eid) if f.service_class == ServiceClass.ENTRY_POINT]
    assert len(entry) == 1, [(f.id, f.services, f.kinds) for f in ns.analysis.findings]
    assert {K.TIMEOUT, K.SERVER_ERROR_STATUS, K.LATENCY} <= set(entry[0].kinds), entry[0].kinds
    mailer = [f for f in ns.analysis.findings if MAILER in f.services]
    client = [f for f in ns.analysis.findings if SVC in f.services and f.db_categories]
    assert mailer and client
    assert not {f.id for f in mailer} & {f.id for f in client}
    for f in mailer:
        assert f.db_related is False and f.db_categories == ()


@pytest.mark.criterion("TASK-020:C3")
@pytest.mark.req("QA-08", "QA-09")
def test_connections_held_and_strongest_shifts(saturation):
    f = db_finding(saturation.analysis)
    held = [c for c in f.evidence.composites if c.kind == CompositeKind.CONNECTIONS_HELD]
    assert len(held) == 1, f.evidence.composites
    c = held[0]
    assert c.rising.metric == "active_connections" and c.falling.metric == "txn_per_s"
    assert "connections appear to be held rather than serving load" in c.interpretation
    shifts = f.evidence.shifts
    assert len(shifts) >= 2
    assert shifts[0].metric == "active_connections"
    assert shifts[0].limit_reached is True and shifts[0].limit == 100
    assert shifts[1].metric == "waiting_clients"


@pytest.mark.criterion("TASK-020:C4")
@pytest.mark.req("FR-04", "FR-15")
def test_roles_and_propagation(saturation):
    a = saturation.analysis
    assert db_finding(a).role == Role.CAUSE
    gws = gw_findings(a)
    assert len(gws) == 1, [(f.id, f.services) for f in gws]
    assert gws[0].role == Role.SYMPTOM
    assert gws[0].severity == Severity.CRITICAL
    steps = {(s.from_service, s.to_service) for s in a.impact.propagation}
    assert (DB, SVC) in steps and (DB, SVC_B) in steps, steps
    assert (SVC, GW) in steps or (SVC_B, GW) in steps, steps
    assert (DB, GW) not in steps
    graph_edges = {(e.dependent, e.dependency, e.source) for e in a.graph.edges}
    for s in a.impact.propagation:
        assert (s.to_service, s.from_service, s.via) in graph_edges, s
    cause_services = {svc for f in a.findings if f.role == Role.CAUSE for svc in f.services}
    for ep in saturation.timeline.episodes:
        ep_steps = [s for s in a.impact.propagation if s.episode_id == ep.id]
        if ep_steps:
            assert any(s.from_service in cause_services for s in ep_steps)


@pytest.mark.criterion("TASK-020:C5")
@pytest.mark.req("OUT-10", "FR-04")
def test_evidence_and_cause_per_episode(saturation):
    a = saturation.analysis
    assert a.findings
    for f in a.findings:
        assert 1 <= len(f.evidence.samples) <= 3, f.id
        assert all(s.line_no >= 1 for s in f.evidence.samples)
        assert f.count >= 1
    for ep in saturation.timeline.episodes:
        in_ep = episode_findings(a, ep.id)
        if in_ep:
            assert any(f.role == Role.CAUSE for f in in_ep), ep.id


@pytest.mark.criterion("TASK-020:C5")
@pytest.mark.req("OUT-10", "FR-04")
def test_no_anomalies_no_findings():
    ns = stage_analysis(calm(0, 900))
    assert ns.analysis.findings == ()
    assert ns.analysis.causes.primary == () and ns.analysis.causes.alternatives == ()
    assert ns.analysis.impact.services == () and ns.analysis.impact.propagation == ()


@pytest.fixture(scope="module")
def attached():
    return stage_analysis(read_log_lines(ATTACHED_LOG))


@pytest.mark.criterion("TASK-020:C6")
@pytest.mark.req("OUT-14", "FR-14", "FR-15")
def test_attached_findings_and_edges(attached):
    a = attached.analysis
    assert 5 <= len(a.findings) <= 8, [(f.id, f.services) for f in a.findings]
    for ep in attached.timeline.episodes:
        entry = [f for f in episode_findings(a, ep.id) if f.service_class == ServiceClass.ENTRY_POINT]
        assert len(entry) <= 1, ep.id
    db_access = edges(a.graph, EdgeSource.DB_ACCESS)
    assert not [e for e in db_access if e[0] == "api-gateway"]
    assert {("order-service", "postgres-primary"), ("payment-service", "postgres-primary")} <= db_access
    for f in a.findings:
        if f.db_categories:
            assert "notification-service" not in f.services, f.id
            assert 367 not in {s.line_no for s in f.evidence.samples}, f.id
    e1 = attached.timeline.episodes[0].id
    assert ("postgres-primary", "api-gateway") not in {
        (s.from_service, s.to_service) for s in a.impact.propagation if s.episode_id == e1
    }


@pytest.mark.criterion("TASK-020:C7")
@pytest.mark.req("QA-08", "QA-09")
def test_attached_connections_held(attached):
    a = attached.analysis
    e1 = attached.timeline.episodes[0].id
    causes = [
        f for f in a.findings if f.episode_id == e1 and f.role == Role.CAUSE and "postgres-primary" in f.services
    ]
    assert len(causes) == 1, [(f.id, f.services, f.role.value) for f in a.findings]
    f = causes[0]
    held = [c for c in f.evidence.composites if c.kind == CompositeKind.CONNECTIONS_HELD]
    assert held
    c = held[0]
    assert (c.rising.metric, c.rising.baseline_p50, c.rising.worst) == ("active_connections", 30, 100)
    assert (c.falling.metric, c.falling.baseline_p50, c.falling.worst) == ("txn_per_s", 107, 51)
    s0 = f.evidence.shifts[0]
    assert (s0.metric, s0.limit_reached, s0.limit) == ("active_connections", True, 100)
    assert any(s.metric == "waiting_clients" and s.worst == 31 for s in f.evidence.shifts)


@pytest.mark.criterion("TASK-020:C8")
@pytest.mark.req("NEG-07")
def test_no_attached_log_specifics_in_rules():
    files = [PROJECT_ROOT / "src" / "test_task" / n for n in ("causality.py", "impact.py")]
    assert scan_for(files, list(APP_NAMES_OF_ATTACHED_LOG) + ["2026-03-27"]) == []
