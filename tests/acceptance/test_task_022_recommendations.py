"""TASK-022: DB signals by group, per-category uncertainty, at most 7 steps ordered by significance."""

from __future__ import annotations

import dataclasses
import re

import pytest
from acc_support import (
    ATTACHED_LOG,
    DB,
    MAILER,
    SVC,
    all_hypotheses,
    calm,
    read_log_lines,
    restart_burst,
    saturation_burst,
    scenario,
    stage_recs,
    text_line,
)

from test_task.contracts.causality import HypothesisKind, Verdict
from test_task.contracts.common import DbCategory
from test_task.contracts.recommendations import (
    DB_CATEGORY_TITLES,
    FORBIDDEN_STEP_PHRASES,
    LOAD_UNCERTAINTY,
    MAX_NEXT_STEPS,
    NEXT_STEP_HINTS,
    SHORT_WINDOW_UNCERTAINTY,
    UNCERTAINTY_BY_CATEGORY,
)
from test_task.contracts.timeline import RecoveryStatus

C = DbCategory
HK = HypothesisKind
RS = RecoveryStatus
NOT_OBSERVED_TEXT = re.compile(r"recover\w*\b.*\bnot\b|\bnot\b.*\brecover|\bno\b.*\brecover", re.IGNORECASE)


def recommend(ns, analysis=None, timeline=None, parse=None):
    from acc_support import impl

    return impl("recommendations", "recommend")(
        parse or ns.parse, ns.patterns, timeline or ns.timeline, analysis or ns.analysis, ns.config
    )


def all_ids(ns, recs=None):
    a = ns.analysis
    return {f.id for f in a.findings} | {h.id for h in all_hypotheses(a)} | {e.id for e in ns.timeline.episodes}


def statements(recs):
    return [u.statement for u in recs.uncertainty]


@pytest.fixture(scope="module")
def two_episodes():
    """E1: DB server saturation (HIGH); E2: DB server crash and restart (CRITICAL)."""
    return stage_recs(
        scenario(
            calm(0, 300),
            saturation_burst(300),
            calm(420, 900, first_index=100),
            restart_burst(900, clients=False),
            calm(960, 1500, first_index=300),
        )
    )


@pytest.mark.criterion("TASK-022:C1")
@pytest.mark.req("FR-14", "OUT-07")
def test_db_signal_built_from_groups_not_whole_findings():
    ep = []
    for k in range(8):
        t = 300 + 10 * k
        ep.append(text_line(t, "ERROR", SVC, f"pq: connection reset by peer request_id=c-{k}"))
        ep.append(text_line(t + 4, "WARN", MAILER, "smtp delivery failed: connection reset by peer provider=smtp"))
    ns = stage_recs(scenario(calm(0, 300), ep, calm(380, 900, first_index=100)))
    client_f = [f for f in ns.analysis.findings if SVC in f.services and C.CONNECTION_ERRORS in f.db_categories]
    mailer_f = [f for f in ns.analysis.findings if MAILER in f.services]
    assert client_f and mailer_f
    mailer_groups = [g for g in ns.patterns.groups if g.service == MAILER]
    assert mailer_groups and all(not g.db_categories for g in mailer_groups)
    # a finding that merges the client's DB group and the notifier's non-DB group
    target = client_f[0]
    merged = dataclasses.replace(
        target,
        services=tuple(sorted(set(target.services) | {MAILER})),
        group_ids=tuple(sorted(set(target.group_ids) | {g.id for g in mailer_groups}, key=lambda x: int(x[1:]))),
        count=target.count + sum(g.count for g in mailer_groups),
    )
    findings = tuple(merged if f.id == target.id else f for f in ns.analysis.findings)
    analysis = dataclasses.replace(ns.analysis, findings=findings)
    recs = recommend(ns, analysis=analysis)
    sig = [s for s in recs.db_signals if s.category == C.CONNECTION_ERRORS]
    assert len(sig) == 1
    s = sig[0]
    assert s.services == (SVC,)
    db_groups = [g for g in ns.patterns.groups if C.CONNECTION_ERRORS in g.db_categories]
    assert s.count == sum(g.count for g in db_groups)
    assert {x.service for x in s.samples} == {SVC}
    order = list(DbCategory)
    cats = [x.category for x in recs.db_signals]
    assert cats == sorted(set(cats), key=order.index)
    for x in recs.db_signals:
        assert 1 <= len(x.samples) <= 3 and x.title == DB_CATEGORY_TITLES[x.category]


def check_steps(ns, recs):
    steps = recs.next_steps
    assert 1 <= len(steps) <= MAX_NEXT_STEPS
    assert len({s.action for s in steps}) == len(steps)
    assert [s.priority for s in steps] == list(range(1, len(steps) + 1))
    ids = all_ids(ns)
    for s in steps:
        low = (s.action + " " + s.rationale).lower()
        assert not [p for p in FORBIDDEN_STEP_PHRASES if p in low], s
        assert s.related_ids and set(s.related_ids) <= ids, s


@pytest.mark.criterion("TASK-022:C2")
@pytest.mark.req("OUT-17", "OUT-06")
def test_steps_start_with_restart_hint(two_episodes):
    ns = two_episodes
    cats = {c for f in ns.analysis.findings for c in f.db_categories}
    assert {C.SERVER_RESTART, C.SERVER_SATURATION} <= cats
    check_steps(ns, ns.recs)
    assert ns.recs.next_steps[0].action == NEXT_STEP_HINTS[C.SERVER_RESTART][0]


@pytest.mark.criterion("TASK-022:C3")
@pytest.mark.req("QA-12", "QA-01", "OUT-05")
def test_category_uncertainty(two_episodes):
    texts = statements(two_episodes.recs)
    first = texts[0].lower()
    assert re.search(r"\blogs?\b", first) and "cause" in first and re.search(r"\bnot\b|\bno\b|n't", first), texts[0]
    assert UNCERTAINTY_BY_CATEGORY[C.SERVER_RESTART] in texts
    assert UNCERTAINTY_BY_CATEGORY[C.SERVER_SATURATION] in texts
    assert len(texts) == len(set(texts))
    for t in texts:
        assert not re.search(r"\b(host|cpu)\b", t, re.IGNORECASE), t


@pytest.mark.criterion("TASK-022:C3")
@pytest.mark.req("QA-12")
@pytest.mark.parametrize("gw_extra", [0, 2], ids=["baseline-rate", "triple-rate"])
def test_load_uncertainty_depends_on_verdict(gw_extra):
    ns = stage_recs(scenario(calm(0, 300), saturation_burst(300, gw_extra=gw_extra), calm(420, 900, first_index=100)))
    spikes = [h for h in all_hypotheses(ns.analysis) if h.kind == HK.LOAD_SPIKE]
    assert spikes
    supported = any(h.verdict == Verdict.SUPPORTED for h in spikes)
    assert supported == (gw_extra > 0)
    assert (LOAD_UNCERTAINTY in statements(ns.recs)) == (not supported)


@pytest.mark.criterion("TASK-022:C3")
@pytest.mark.req("QA-01", "OUT-05")
def test_no_findings_still_uncertain():
    ns = stage_recs(calm(0, 900))
    assert ns.analysis.findings == ()
    assert ns.recs.db_signals == ()
    assert ns.recs.uncertainty


def recovery_lines(status):
    """One DB server saturation episode; the log tail sets the recovery state (real pipeline)."""
    head = [calm(0, 300), saturation_burst(300)]
    if status == RS.NOT_OBSERVED:
        return scenario(*head)
    if status == RS.OBSERVED:
        return scenario(*head, calm(420, 900, first_index=100))
    marker = [text_line(425, "INFO", DB, "database system is ready to accept connections")]
    return scenario(*head, marker, calm(430, 480, first_index=100))


@pytest.mark.criterion("TASK-022:C4")
@pytest.mark.req("QA-13", "QA-01")
@pytest.mark.parametrize("status", [RS.OBSERVED_SHORT_WINDOW, RS.NOT_OBSERVED, RS.OBSERVED])
def test_recovery_uncertainty(status):
    ns = stage_recs(recovery_lines(status))
    ep = ns.timeline.episodes[-1]
    assert ep.recovery_status == status
    recs = ns.recs
    eid = ep.id
    short = SHORT_WINDOW_UNCERTAINTY.format(episode=eid)
    short_items = [u for u in recs.uncertainty if u.statement == short]
    not_obs = [u for u in recs.uncertainty if eid in u.related_ids and NOT_OBSERVED_TEXT.search(u.statement)]
    if status == RS.OBSERVED_SHORT_WINDOW:
        assert len(short_items) == 1 and eid in short_items[0].related_ids
        assert not_obs == []
    elif status == RS.NOT_OBSERVED:
        assert not_obs, statements(recs)
        assert short_items == []
    else:
        assert short_items == [] and not_obs == []


@pytest.mark.criterion("TASK-022:C4")
@pytest.mark.req("QA-01")
def test_parse_failures_counted():
    lines = scenario(calm(0, 300), saturation_burst(300), calm(420, 900, first_index=100))
    ns = stage_recs(lines[:10] + ["%% not a log line %%", "<<garbage>>"] + lines[10:])
    assert len(ns.parse.failures) == 2
    assert [u for u in ns.recs.uncertainty if re.search(r"\b2\b", u.statement)], statements(ns.recs)


@pytest.mark.criterion("TASK-022:C5")
@pytest.mark.req("QA-12", "OUT-17", "FR-14", "QA-13")
def test_attached_log_recommendations():
    ns = stage_recs(read_log_lines(ATTACHED_LOG))
    recs = ns.recs
    check_steps(ns, recs)
    assert recs.next_steps[0].action == NEXT_STEP_HINTS[C.SERVER_RESTART][0]
    assert "OOM" in recs.next_steps[0].action
    texts = statements(recs)
    assert UNCERTAINTY_BY_CATEGORY[C.SERVER_RESTART] in texts
    assert any("held" in t and "connections" in t for t in texts)
    assert LOAD_UNCERTAINTY in texts
    e2 = ns.timeline.episodes[1].id
    assert SHORT_WINDOW_UNCERTAINTY.format(episode=e2) in texts
    assert not [u for u in recs.uncertainty if e2 in u.related_ids and NOT_OBSERVED_TEXT.search(u.statement)]
    for s in recs.db_signals:
        assert "notification-service" not in s.services, s.category
    pool = [s for s in recs.db_signals if s.category == C.POOL_EXHAUSTION]
    for s in pool:
        assert not {"billing-worker", "inventory-service"} & set(s.services)
    assert any(s.category == C.ABORTED_TRANSACTIONS for s in recs.db_signals)
    for s in recs.db_signals:
        if s.category == C.QUERY_TIMEOUT:
            for x in s.samples:
                for bad in ("could not receive data", "Broken pipe", "unexpected EOF"):
                    assert bad not in x.text, x.text
