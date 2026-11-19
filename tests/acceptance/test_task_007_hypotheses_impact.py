"""TASK-007: hypotheses with confidence, and impact/propagation."""

from __future__ import annotations

import pytest
from acc_support import (
    ATTACHED_LOG,
    CHAIN_LOG,
    DB,
    GW,
    SVC,
    all_hypotheses,
    chain_lines,
    episode_client_first,
    episode_db_first,
    finding_with,
    findings_of_service,
    has_hedge,
    normal_block,
    overconfident,
    read_log_lines,
    stage_analysis,
    text_line,
)

from test_task.contracts.causality import HypothesisKind, Verdict
from test_task.contracts.common import LEVEL_RANK, Confidence, Level, Role, SignalKind

RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}

SOURCE_LIKE = (HypothesisKind.SOURCE, HypothesisKind.CLIENT_SIDE)
"""Hypothesis kinds for which the 1.0 for/against confidence rule still holds in 2.0; in 1.0 all hypotheses
have the default kind SOURCE. EPISODE_LINK, LOAD_SPIKE and PRE_ONSET_TRIGGER (2.0) have their own rules."""


def hid(h):
    return int(h.id[1:])


def check_hypotheses(a):
    """Invariants of hypotheses and Probable Cause."""
    hyps = all_hypotheses(a)
    assert [h.id for h in hyps] == [f"H{i}" for i in range(1, len(hyps) + 1)]
    fids = {f.id for f in a.findings}
    for h in hyps:
        assert isinstance(h.confidence, Confidence)
        assert h.supporting or h.contradicting, h.id
        # 2.0: a LOAD_SPIKE not confirmed by the log may have no supporting evidence
        if not (h.kind == HypothesisKind.LOAD_SPIKE and h.verdict != Verdict.SUPPORTED):
            assert h.supporting, h.id
        assert h.finding_ids and set(h.finding_ids) <= fids, h.id
        assert has_hedge(h.statement), h.statement
        for text in (h.statement, *h.supporting, *h.contradicting):
            assert not overconfident(text), text
        if h.confidence == Confidence.HIGH:
            assert h.contradicting == ()
        elif h.kind in SOURCE_LIKE:
            # Hypothesis contract: not HIGH -> MEDIUM if there is no more evidence against than for, else LOW
            expected = Confidence.MEDIUM if len(h.contradicting) <= len(h.supporting) else Confidence.LOW
            assert h.confidence == expected, (h.id, h.confidence, h.supporting, h.contradicting)
    covered = {fid for h in hyps for fid in h.finding_ids}
    causes = [f for f in a.findings if f.role == Role.CAUSE]
    assert {f.id for f in causes} <= covered
    if causes:
        assert a.causes.primary
    # alternatives in descending confidence (2.0: within the hypothesis's first episode)
    alt_by_episode = {}
    for h in a.causes.alternatives:
        alt_by_episode.setdefault(h.episode_ids[:1], []).append(RANK[h.confidence])
    for ranks in alt_by_episode.values():
        assert ranks == sorted(ranks), ranks
    # primary = best by confidence in the episode; since 2.0 only among SOURCE (in 1.0 all hypotheses are SOURCE)
    sources = [h for h in hyps if h.kind == HypothesisKind.SOURCE]
    episodes = {e for h in sources for e in h.episode_ids}
    for e in episodes:
        in_ep = [h for h in sources if e in h.episode_ids]
        best = min(RANK[h.confidence] for h in in_ep)
        for h in in_ep:
            if RANK[h.confidence] == best:
                assert h in a.causes.primary, f"{h.id} is best in {e} but not in primary"
    for h in a.causes.primary:
        assert any(
            RANK[h.confidence] == min(RANK[x.confidence] for x in sources if e in x.episode_ids)
            for e in h.episode_ids
        ), f"{h.id} is in primary but is not the best in any episode"
    for f in a.findings:
        assert not overconfident(f.title) and not overconfident(f.role_rationale)


@pytest.mark.criterion("TASK-007:C1")
@pytest.mark.req("OUT-03", "OUT-11")
def test_hypotheses_cover_causes():
    a = stage_analysis(chain_lines(episode_db_first())).analysis
    assert any(f.role == Role.CAUSE for f in a.findings)
    assert a.causes.primary
    check_hypotheses(a)


@pytest.mark.criterion("TASK-007:C2")
@pytest.mark.req("OUT-11")
def test_db_hypothesis_high_with_client_and_server_evidence():
    ns = stage_analysis(chain_lines(episode_db_first()))
    # scenario preconditions (criterion C2): the DB server has no errors or restarts, waiting_clients > 0,
    # and active_connections is below the 0.9×max_connections saturation threshold, which is not evidence against
    db_heads = [s for s in ns.scored if s.event.record.service == DB and s.event.continuation_of is None]
    assert db_heads
    assert all(LEVEL_RANK[s.event.record.level] < LEVEL_RANK[Level.ERROR] for s in db_heads)
    assert all(SignalKind.RESTART not in s.event.kinds and s.primary_kind != SignalKind.RESTART for s in db_heads)
    stats = [s.event.record.fields for s in db_heads if "active_connections" in s.event.record.fields]
    assert any(f["waiting_clients"] > 0 for f in stats)
    assert all(f["active_connections"] < 0.9 * f["max_connections"] for f in stats)
    assert findings_of_service(ns.analysis, SVC), "no client-side evidence"
    a = ns.analysis
    db = finding_with(a, SignalKind.POOL_SATURATION, DB)
    assert db.role == Role.CAUSE
    db_hyps = [h for h in all_hypotheses(a) if db.id in h.finding_ids]
    assert db_hyps
    assert any(h.confidence == Confidence.HIGH and h.contradicting == () for h in db_hyps), [
        (h.id, h.confidence, h.contradicting) for h in db_hyps
    ]
    check_hypotheses(a)


@pytest.mark.criterion("TASK-007:C2")
@pytest.mark.req("OUT-11")
def test_db_hypothesis_not_high_without_server_side():
    def client(sec, service, i):
        return text_line(sec, "INFO", service, f"order stored request_id=c-{i} db_latency_ms={10 + i % 3}")

    lines = [client(10 * i, SVC if i % 2 else "stock-service", i) for i in range(30)]
    for k, sec in enumerate((300, 310, 320, 330)):
        lines.append(
            text_line(sec, "WARN", SVC, f"db acquire connection slow wait_ms={800 + k} pool_in_use=20 pool_idle=0")
        )
        lines.append(
            text_line(
                sec + 5,
                "WARN",
                "stock-service",
                f"db acquire connection slow wait_ms={700 + k} pool_in_use=10 pool_idle=0",
            )
        )
    lines += [client(420 + 10 * i, SVC if i % 2 else "stock-service", i) for i in range(30)]
    a = stage_analysis(lines).analysis
    assert not any(s.service_class.value == "db_server" for s in a.graph.services)
    db_findings = {f.id for f in a.findings if f.db_categories}
    assert db_findings
    db_hyps = [h for h in all_hypotheses(a) if set(h.finding_ids) & db_findings]
    assert db_hyps
    assert all(h.confidence != Confidence.HIGH for h in db_hyps)
    # there is supporting evidence (pool exhausted on two clients), the DB server side is absent from the log,
    # and nothing in the log refutes the hypothesis, so MEDIUM rather than LOW
    assert any(h.confidence == Confidence.MEDIUM for h in db_hyps), [
        (h.id, h.confidence, h.supporting, h.contradicting) for h in db_hyps
    ]
    check_hypotheses(a)


@pytest.mark.criterion("TASK-007:C3")
@pytest.mark.req("OUT-03")
def test_two_independent_causes_both_covered():
    extra = [
        text_line(303, "ERROR", "batch-worker", "export job rejected"),
        text_line(308, "ERROR", "batch-worker", "export job rejected"),
    ]
    a = stage_analysis(chain_lines(episode_db_first(), extra)).analysis
    db = finding_with(a, SignalKind.POOL_SATURATION, DB)
    batch = finding_with(a, SignalKind.ERROR, "batch-worker")
    assert db.role == Role.CAUSE and batch.role == Role.CAUSE
    covered = {fid for h in all_hypotheses(a) for fid in h.finding_ids}
    assert {db.id, batch.id} <= covered
    check_hypotheses(a)


@pytest.mark.criterion("TASK-007:C4")
@pytest.mark.req("QA-02", "NEG-04", "CTX-03")
@pytest.mark.parametrize("scenario", ["db_first", "client_first", "fixture", "attached"])
def test_wording_hedged_and_not_overconfident(scenario):
    lines = {
        "db_first": lambda: chain_lines(episode_db_first()),
        "client_first": lambda: chain_lines(episode_client_first()),
        "fixture": lambda: read_log_lines(CHAIN_LOG),
        "attached": lambda: read_log_lines(ATTACHED_LOG),
    }[scenario]()
    a = stage_analysis(lines).analysis
    assert all_hypotheses(a)
    check_hypotheses(a)


@pytest.mark.criterion("TASK-007:C5")
@pytest.mark.req("OUT-04", "FR-09")
def test_impact_services_counts_and_failed_requests():
    ns = stage_analysis(read_log_lines(CHAIN_LOG))
    imp = ns.analysis.impact
    heads = [s for s in ns.scored if s.event.continuation_of is None]
    expected = {}
    for s in heads:
        rec = s.event.record
        e = expected.setdefault(rec.service, {"first": None, "anom": 0, "err": 0, "failed": set()})
        if s.anomalous:
            e["anom"] += 1
            e["first"] = rec.ts if e["first"] is None else min(e["first"], rec.ts)
        status = rec.fields.get("status")
        is_err = LEVEL_RANK[rec.level] >= LEVEL_RANK[Level.ERROR]
        is_5xx = isinstance(status, int) and not isinstance(status, bool) and 500 <= status <= 599
        if is_err:
            e["err"] += 1
        if (is_err or is_5xx) and s.event.request_id:
            e["failed"].add(s.event.request_id)
    affected = {k: v for k, v in expected.items() if v["anom"]}
    assert {s.service for s in imp.services} == set(affected) == {DB, SVC, GW, "audit-worker"}
    keys = [(s.first_affected, s.service) for s in imp.services]
    assert keys == sorted(keys)
    fids = {f.id for f in ns.analysis.findings}
    for s in imp.services:
        e = affected[s.service]
        assert s.first_affected == e["first"]
        assert s.anomalous_events == e["anom"]
        assert s.error_events == e["err"]
        assert s.failed_requests == len(e["failed"])
        assert set(s.finding_ids) <= fids
    assert affected[GW]["failed"] == {"r-1001", "r-1002", "r-1003", "r-1004", "r-1005"}
    all_failed = set().union(*(v["failed"] for v in expected.values()))
    assert imp.failed_requests == len(all_failed) == 5
    assert imp.example_trace_ids == ns.patterns.example_trace_ids


@pytest.mark.criterion("TASK-007:C6")
@pytest.mark.req("OUT-04", "FR-09")
def test_propagation_against_dependency_edges():
    ns = stage_analysis(chain_lines(episode_db_first()))
    a = ns.analysis
    steps = a.impact.propagation
    pairs = {(p.from_service, p.to_service) for p in steps}
    assert {(DB, SVC), (SVC, GW)} <= pairs
    keys = [(p.episode_id, p.first_seen, p.from_service, p.to_service) for p in steps]
    assert keys == sorted(keys)
    edges = {(e.dependent, e.dependency, e.source) for e in a.graph.edges}
    first_anomaly = {}
    for s in ns.scored:
        if s.anomalous:
            first_anomaly.setdefault(s.event.record.service, s.event.record.ts)
    for p in steps:
        assert (p.to_service, p.from_service, p.via) in edges
        assert p.first_seen >= first_anomaly[p.from_service]
        assert 1 <= len(p.evidence) <= 2
    for ep in {p.episode_id for p in steps}:
        first = next(p for p in steps if p.episode_id == ep)
        causes = {svc for f in a.findings if f.episode_id == ep and f.role == Role.CAUSE for svc in f.services}
        assert first.from_service in causes
    assert next(p for p in steps if p.episode_id == "E1").from_service == DB


@pytest.mark.criterion("TASK-007:C7")
@pytest.mark.req("OUT-03", "OUT-04")
def test_no_anomalies_empty_sections():
    a = stage_analysis(normal_block(0, 600)).analysis
    assert a.causes.primary == () and a.causes.alternatives == ()
    assert a.impact.services == () and a.impact.propagation == ()
    assert a.impact.failed_requests == 0


@pytest.mark.criterion("TASK-007:C7")
@pytest.mark.req("OUT-03", "OUT-04", "CTX-03")
def test_smoke_attached_log_and_determinism(impl):
    ns = stage_analysis(read_log_lines(ATTACHED_LOG))
    a = ns.analysis
    assert a.causes.primary
    assert any(h.db_categories for h in all_hypotheses(a))
    assert len(a.impact.services) >= 2
    assert a.impact.propagation
    again = impl("causality", "assess")(ns.scored, ns.patterns, ns.timeline, ns.comparison, ns.graph, ns.config)
    assert again == a
    assert stage_analysis(read_log_lines(ATTACHED_LOG)).analysis == a
