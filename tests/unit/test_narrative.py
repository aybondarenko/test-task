import dataclasses

from test_hypotheses import chain
from test_recommendations import run

from test_task.contracts.common import Role
from test_task.contracts.narrative import NarrativeFacts
from test_task.contracts.timeline import RecoveryStatus
from test_task.narrative import validate_summary, write_summary
from test_task.recommendations import recommend


def make_facts(specs):
    parse, pats, tl, an, cfg = run(specs)
    return NarrativeFacts(tl, an, recommend(parse, pats, tl, an, cfg))


def test_summary_valid_and_deterministic():
    facts = make_facts(chain())
    text = write_summary(facts)
    assert text == write_summary(facts)
    assert validate_summary(text, facts) == ()


def test_summary_variants():
    facts = make_facts(chain())
    unrec = dataclasses.replace(
        facts.timeline,
        episodes=tuple(
            dataclasses.replace(
                e,
                recovered=False,
                recovery=None,
                recovery_status=RecoveryStatus.NOT_OBSERVED,
            )
            for e in facts.timeline.episodes
        ),
    )
    f2 = dataclasses.replace(facts, timeline=unrec)
    assert "not observed" in write_summary(f2)
    f3 = dataclasses.replace(
        facts, timeline=dataclasses.replace(facts.timeline, episodes=())
    )
    assert validate_summary(write_summary(f3), f3) == ()
    an = dataclasses.replace(
        facts.analysis,
        findings=tuple(
            dataclasses.replace(f, role=Role.SYMPTOM) for f in facts.analysis.findings
        ),
    )
    f4 = dataclasses.replace(facts, analysis=an)
    assert validate_summary(write_summary(f4), f4) == ()


def test_no_findings():
    facts = make_facts(chain())
    empty = dataclasses.replace(
        facts, analysis=dataclasses.replace(facts.analysis, findings=())
    )
    text = write_summary(empty)
    assert validate_summary(text, empty) == ()


def test_validator_rejects():
    facts = make_facts(chain())
    bad = "The root cause is billing-service (F99). Done."
    assert len(validate_summary(bad, facts)) >= 4
    assert validate_summary("One. Two likely. Three. Four. Five.", facts)


def test_build_llm_paths():
    import test_task.narrative as mod

    for name in ("build_narrative", "NarrativeResult", "LlmClient", "SummarySource"):
        assert not hasattr(mod, name)
    facts = make_facts(chain())
    assert validate_summary(write_summary(facts), facts) == ()


def test_summary_names_each_episode_cause_and_recovery_states():
    facts = make_facts(chain())
    text = write_summary(facts)
    tops = {}
    for f in facts.analysis.findings:
        if f.role == Role.CAUSE and f.episode_id:
            tops.setdefault(f.episode_id, []).append(f)
    for fs in tops.values():
        assert any(f.id in text for f in fs)
    assert validate_summary("Only likely. Two.", facts)
    for status, word in (
        (RecoveryStatus.OBSERVED, "was observed in"),
        (RecoveryStatus.OBSERVED_SHORT_WINDOW, "short"),
    ):
        eps = tuple(
            dataclasses.replace(e, recovery_status=status)
            for e in facts.timeline.episodes
        )
        f2 = dataclasses.replace(
            facts, timeline=dataclasses.replace(facts.timeline, episodes=eps)
        )
        out = write_summary(f2)
        assert word in out and "not observed" not in out
        assert validate_summary(out, f2) == ()
