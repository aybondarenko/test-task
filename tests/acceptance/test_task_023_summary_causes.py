"""TASK-023: the Incident Summary names the cause of every episode, starting with the highest severity."""

from __future__ import annotations

import re

import pytest
from acc_support import (
    ATTACHED_LOG,
    DB,
    GW,
    all_hypotheses,
    calm,
    episode_top_causes,
    has_hedge,
    id_pos,
    read_log_lines,
    restart_burst,
    saturation_burst,
    scenario,
    sentence_count,
    stage_facts,
    text_line,
)

from test_task.contracts.causality import HypothesisKind
from test_task.contracts.common import Severity
from test_task.contracts.timeline import RecoveryStatus

HK = HypothesisKind
RS = RecoveryStatus


def write(facts):
    from acc_support import impl

    return impl("narrative", "write_summary")(facts)


def validate(text, facts):
    from acc_support import impl

    return impl("narrative", "validate_summary")(text, facts)


@pytest.fixture(scope="module")
def two():
    """E1: DB server saturation (CAUSE HIGH); E2: DB server restart (CAUSE CRITICAL)."""
    return stage_facts(
        scenario(
            calm(0, 300),
            saturation_burst(300),
            calm(420, 900, first_index=100),
            restart_burst(900, clients=False),
            calm(960, 1500, first_index=300),
        )
    )


def tops(ns):
    top = episode_top_causes(ns.analysis)
    e1, e2 = (e.id for e in ns.timeline.episodes[:2])
    return top[e1], top[e2]


@pytest.mark.criterion("TASK-023:C1")
@pytest.mark.req("QA-06", "OUT-01")
def test_both_causes_highest_severity_first(two):
    f1, f2 = tops(two)
    assert f1.severity == Severity.HIGH and f2.severity == Severity.CRITICAL
    text = write(two.facts)
    p1, p2 = id_pos(text, f1.id), id_pos(text, f2.id)
    assert p1 >= 0 and p2 >= 0, text
    assert p2 < p1, text
    assert f2.window.start.strftime("%H:%M:%S") in text, text
    assert any(s in text for s in f2.services), text
    assert 2 <= sentence_count(text) <= 4, text
    assert validate(text, two.facts) == ()


def elevated(i):
    return 53 + 2 * ((i // 2) % 5)


def linked_lines(between):
    """Two saturation episodes of the same DB server; connections between them are ``between`` (number or function)."""
    return scenario(
        calm(0, 300),
        saturation_burst(300),
        calm(420, 900, active=between, first_index=100),
        saturation_burst(900),
        calm(1020, 1500, first_index=300),
    )


@pytest.mark.criterion("TASK-023:C2")
@pytest.mark.req("QA-07", "QA-02")
def test_episode_link_mentioned():
    ns = stage_facts(linked_lines(elevated))
    links = [h for h in all_hypotheses(ns.analysis) if h.kind == HK.EPISODE_LINK]
    assert len(links) == 1, [(h.id, h.kind.value) for h in all_hypotheses(ns.analysis)]
    text = write(ns.facts)
    assert id_pos(text, links[0].id) >= 0, text
    assert has_hedge(text)
    assert validate(text, ns.facts) == ()


@pytest.mark.criterion("TASK-023:C2")
@pytest.mark.req("QA-07", "QA-02")
def test_no_episode_link_no_link_id():
    ns = stage_facts(linked_lines(30))
    hyps = all_hypotheses(ns.analysis)
    assert not [h for h in hyps if h.kind == HK.EPISODE_LINK]
    text = write(ns.facts)
    assert has_hedge(text)
    known = {h.id for h in hyps}
    assert set(re.findall(r"\bH\d+\b", text)) <= known, text
    assert validate(text, ns.facts) == ()


def recovery_lines(status):
    """One DB server saturation episode; the log tail sets the recovery state (real pipeline)."""
    head = [calm(0, 300), saturation_burst(300)]
    if status == RS.NOT_OBSERVED:
        return scenario(*head)  # the log ends while errors are ongoing
    if status == RS.OBSERVED:
        return scenario(*head, calm(420, 900, first_index=100))
    marker = [text_line(425, "INFO", DB, "database system is ready to accept connections")]
    return scenario(*head, marker, calm(430, 480, first_index=100))


@pytest.mark.criterion("TASK-023:C3")
@pytest.mark.req("QA-13")
@pytest.mark.parametrize("status", [RS.NOT_OBSERVED, RS.OBSERVED, RS.OBSERVED_SHORT_WINDOW])
def test_recovery_state_of_last_episode(status):
    ns = stage_facts(recovery_lines(status))
    assert ns.timeline.episodes and ns.timeline.episodes[-1].recovery_status == status
    text = write(ns.facts)
    low = text.lower()
    if status == RS.NOT_OBSERVED:
        assert "not observed" in low, text
    else:
        assert "recover" in low and "not observed" not in low, text
    if status == RS.OBSERVED_SHORT_WINDOW:
        assert "short" in low, text
    assert validate(text, ns.facts) == ()


@pytest.mark.criterion("TASK-023:C4")
@pytest.mark.req("QA-06", "AI-05", "NEG-03", "QA-02")
def test_validator(two):
    f1, f2 = tops(two)
    both = f"{f2.id} and {f1.id}"
    good = f"The {DB} restart ({f2.id}) likely followed its earlier saturation ({f1.id}). Requests to {GW} probably failed."
    assert validate(good, two.facts) == ()
    bad = [
        f"The root cause is likely {DB} ({both}). Requests to {GW} probably failed.",
        f"The degradation started in {DB} ({both}). Requests to {GW} failed.",
        f"The degradation likely started in billing-service ({both}). Requests to {GW} probably failed.",
        f"The degradation likely started in {DB} ({both}, F99). Requests to {GW} probably failed.",
        f"The degradation likely started in {DB} ({both}).",
        f"Step one likely involved {DB} ({both}). " + " ".join(f"Step {i} probably followed." for i in range(2, 7)),
        f"The {DB} restart ({f2.id}) likely caused the incident. Requests to {GW} probably failed.",
    ]
    for text in bad:
        v = validate(text, two.facts)
        assert isinstance(v, tuple) and v, text


def five_episode_lines():
    services = ("alpha-service", "beta-worker", "gamma-db", "delta-api", "omega-service")
    lines = [text_line(10 * i, "INFO", services[i % 5], f"heartbeat ok seq={i}") for i in range(30)]
    for n, svc in enumerate(services):
        start = 300 + 600 * n
        for k in range(6):
            lines.append(text_line(start + 10 * k, "ERROR", svc, f"write failed on shard {k}"))
        for i in range(start + 60, start + 600, 20):
            lines.append(text_line(i, "INFO", services[(i // 20) % 5], f"heartbeat ok seq={i}"))
    return scenario(lines)


@pytest.mark.criterion("TASK-023:C5")
@pytest.mark.req("QA-06", "OUT-01")
def test_many_episodes_fit_four_sentences():
    ns = stage_facts(five_episode_lines())
    top = episode_top_causes(ns.analysis)
    assert len(top) == 5, [(f.id, f.episode_id, f.role.value) for f in ns.analysis.findings]
    text = write(ns.facts)
    assert 2 <= sentence_count(text) <= 4, text
    for f in top.values():
        assert id_pos(text, f.id) >= 0, (f.id, text)
    assert text == write(ns.facts)
    assert validate(text, ns.facts) == ()


@pytest.mark.criterion("TASK-023:C5")
@pytest.mark.req("QA-06", "OUT-01")
def test_no_findings_two_sentences():
    ns = stage_facts(calm(0, 900))
    assert ns.analysis.findings == ()
    text = write(ns.facts)
    assert sentence_count(text) == 2 and has_hedge(text), text
    assert text == write(ns.facts)
    assert validate(text, ns.facts) == ()


@pytest.mark.criterion("TASK-023:C6")
@pytest.mark.req("QA-06", "OUT-01")
def test_attached_log_summary():
    ns = stage_facts(read_log_lines(ATTACHED_LOG))
    text = write(ns.facts)
    assert "postgres-primary" in text and "14:29:23" in text, text
    e1, e2 = (e.id for e in ns.timeline.episodes[:2])
    top = episode_top_causes(ns.analysis)
    restart = top[e2]
    assert "restart" in [k.value for k in (restart.kinds or (restart.kind,))]
    assert 0 <= id_pos(text, restart.id) < id_pos(text, top[e1].id), text
    assert validate(text, ns.facts) == ()
    assert re.search(r"\S", text)
