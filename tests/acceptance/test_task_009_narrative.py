"""TASK-009: Incident Summary from facts, and the validator.

Since 2.0 the LLM path is removed and criteria C5-C7 were dropped (see test_task_016). Valid text for
the validator names the CAUSE finding id of every episode (the 2.0 ``validate_summary`` check).
"""

from __future__ import annotations

import re

import pytest
from acc_support import (
    ATTACHED_LOG,
    CHAIN_LOG,
    CYRILLIC,
    DB,
    GW,
    ID_TOKEN,
    SERVICE_TOKEN,
    episode_top_causes,
    facts_ids,
    facts_services,
    has_hedge,
    normal_block,
    read_log_lines,
    sentence_count,
    stage_facts,
)

from test_task.contracts.common import Role
from test_task.contracts.narrative import SERVICE_SUFFIXES


def cause_refs(facts):
    """Ids of the highest-severity CAUSE findings of all episodes, comma-separated."""
    return ", ".join(f.id for f in episode_top_causes(facts.analysis).values())


def valid_text(facts):
    return (
        f"The degradation likely started in {DB} ({cause_refs(facts)}). "
        f"Requests to {GW} probably failed as a downstream effect."
    )


@pytest.fixture(scope="module")
def chain():
    return stage_facts(read_log_lines(CHAIN_LOG))


def check_grounded(text, facts):
    services = facts_services(facts)
    for tok in SERVICE_TOKEN.findall(text):
        if tok.endswith(SERVICE_SUFFIXES):
            assert tok in services, f"service {tok} is not from the facts"
    ids = facts_ids(facts)
    for tok in ID_TOKEN.findall(text):
        assert tok in ids, f"id {tok} is not from the facts"


@pytest.mark.criterion("TASK-009:C1")
@pytest.mark.req("OUT-01", "QA-02")
def test_write_summary_with_cause(impl, chain):
    text = impl("narrative", "write_summary")(chain.facts)
    assert 2 <= sentence_count(text) <= 4, text
    assert has_hedge(text)
    cause_services = {s for f in chain.analysis.findings if f.role == Role.CAUSE for s in f.services}
    assert cause_services and any(s in text for s in cause_services)
    assert impl("narrative", "validate_summary")(text, chain.facts) == ()


@pytest.mark.criterion("TASK-009:C1")
@pytest.mark.req("OUT-01", "QA-02")
def test_write_summary_without_findings(impl):
    ns = stage_facts(normal_block(0, 600))
    assert ns.analysis.findings == ()
    text = impl("narrative", "write_summary")(ns.facts)
    assert sentence_count(text) == 2, text
    assert has_hedge(text)
    # the point of the criterion: no clear signs of an incident were found (negation + what it refers to)
    assert re.search(r"\bno\b|\bnot\b|\bnone\b|\bnothing\b|\bwithout\b|n't\b", text, re.IGNORECASE), text
    assert re.search(
        r"incident|anomal|degradation|sign|evidence|issue|problem|outage|failure|error", text, re.IGNORECASE
    ), text
    assert impl("narrative", "validate_summary")(text, ns.facts) == ()


@pytest.mark.criterion("TASK-009:C2")
@pytest.mark.req("AI-05", "QA-02", "NEG-03")
def test_validator_accepts_grounded_text(impl, chain):
    assert cause_refs(chain.facts), "the fixture must have an episode with a CAUSE finding"
    assert impl("narrative", "validate_summary")(valid_text(chain.facts), chain.facts) == ()


@pytest.mark.criterion("TASK-009:C2")
@pytest.mark.req("AI-05", "QA-02", "NEG-03")
@pytest.mark.parametrize(
    "template",
    [
        "The root cause is likely saturation of {db} ({ids}). Requests to {gw} probably failed.",
        "The degradation started in {db} ({ids}). Requests to {gw} failed as a downstream effect.",
        "The degradation likely started in billing-service ({ids}). Requests to {gw} probably failed.",
        "The degradation likely started in {db} ({ids}, F99). Requests to {gw} probably failed.",
        "The degradation likely started in {db} ({ids}).",
        "Step 1 likely involved {db} ({ids}). " + " ".join(f"Step {i} likely involved {{db}}." for i in range(2, 7)),
    ],
    ids=["overconfident", "no-hedge", "unknown-service", "unknown-id", "one-sentence", "six-sentences"],
)
def test_validator_rejects(impl, chain, template):
    text = template.format(db=DB, gw=GW, ids=cause_refs(chain.facts))
    violations = impl("narrative", "validate_summary")(text, chain.facts)
    assert isinstance(violations, tuple) and violations
    for v in violations:
        assert isinstance(v, str) and v.strip() and not CYRILLIC.search(v)


@pytest.mark.criterion("TASK-009:C3")
@pytest.mark.req("AI-05", "NEG-03", "OUT-01")
@pytest.mark.parametrize("source", ["fixture", "attached"])
def test_summary_grounded_in_facts(impl, chain, source):
    ns = chain if source == "fixture" else stage_facts(read_log_lines(ATTACHED_LOG))
    text = impl("narrative", "write_summary")(ns.facts)
    check_grounded(text, ns.facts)
    assert impl("narrative", "validate_summary")(text, ns.facts) == ()


@pytest.mark.criterion("TASK-009:C4")
@pytest.mark.req("DEL-08", "Q-02")
def test_offline_and_deterministic(impl, network_blocked):
    write = impl("narrative", "write_summary")
    validate = impl("narrative", "validate_summary")
    a = stage_facts(read_log_lines(CHAIN_LOG))
    b = stage_facts(read_log_lines(CHAIN_LOG))
    assert a.facts == b.facts
    assert write(a.facts) == write(a.facts) == write(b.facts)
    assert validate(valid_text(a.facts), a.facts) == validate(valid_text(b.facts), b.facts) == ()
    assert network_blocked == []
