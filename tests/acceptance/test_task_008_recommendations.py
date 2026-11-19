"""TASK-008: DB signals, uncertainty and next steps."""

from __future__ import annotations

import dataclasses
import re

import pytest
from acc_support import (
    ATTACHED_LOG,
    CHAIN_LOG,
    CYRILLIC,
    DB,
    SVC,
    all_hypotheses,
    chain_lines,
    episode_db_first,
    overconfident,
    read_log_lines,
    stage_recs,
    text_line,
)

from test_task.contracts.common import DbCategory, TimeWindow
from test_task.contracts.parser import ParseFailure, ParseFailureReason
from test_task.contracts.recommendations import (
    DB_CATEGORY_TITLES,
    MAX_NEXT_STEPS,
    NEXT_STEP_HINTS,
)
from test_task.contracts.timeline import RecoveryStatus

ORDER = list(DbCategory)


def known_ids(analysis):
    return {f.id for f in analysis.findings} | {h.id for h in all_hypotheses(analysis)}


def check_db_signals(ns):
    a, recs = ns.analysis, ns.recs
    cats = [s.category for s in recs.db_signals]
    assert len(cats) == len(set(cats))
    assert [ORDER.index(c) for c in cats] == sorted(ORDER.index(c) for c in cats)
    present = {c for f in a.findings for c in f.db_categories}
    assert set(cats) == present
    fids = {f.id for f in a.findings}
    hids = {h.id for h in all_hypotheses(a)}
    for s in recs.db_signals:
        assert s.title == DB_CATEGORY_TITLES[s.category]
        assert s.services and s.count >= 1
        assert s.window.start <= s.window.end
        assert 1 <= len(s.samples) <= 3
        assert all(x.line_no >= 1 for x in s.samples)
        assert s.finding_ids and set(s.finding_ids) <= fids
        assert set(s.hypothesis_ids) <= hids
        assert set(s.finding_ids) == {f.id for f in a.findings if s.category in f.db_categories}


def check_next_steps(ns):
    a, recs = ns.analysis, ns.recs
    steps = recs.next_steps
    if a.findings:
        assert steps
    assert [n.id for n in steps] == [f"N{i}" for i in range(1, len(steps) + 1)]
    prios = [n.priority for n in steps]
    assert prios == sorted(prios)
    ids = known_ids(a)
    for n in steps:
        assert n.action.strip()
        assert n.related_ids and set(n.related_ids) <= ids, n
    covered = [
        s.category for s in recs.db_signals if any(h in n.action for n in steps for h in NEXT_STEP_HINTS[s.category])
    ]
    if len(steps) < MAX_NEXT_STEPS:
        assert covered == [s.category for s in recs.db_signals], f"some categories have no steps: {covered}"
    elif recs.db_signals:
        # 2.0: at most MAX_NEXT_STEPS steps; the least significant categories are cut off
        assert covered, "no DB category got a step"


def check_texts(recs):
    texts = [s.title for s in recs.db_signals]
    texts += [u.statement for u in recs.uncertainty]
    texts += [t for n in recs.next_steps for t in (n.action, n.rationale)]
    for t in texts:
        assert not CYRILLIC.search(t), t
        assert not overconfident(t), t


@pytest.mark.criterion("TASK-008:C1")
@pytest.mark.req("FR-06", "OUT-07")
def test_db_signals_from_findings():
    ns = stage_recs(read_log_lines(CHAIN_LOG))
    check_db_signals(ns)
    by_cat = {s.category: s for s in ns.recs.db_signals}
    assert {DbCategory.POOL_EXHAUSTION, DbCategory.SERVER_SATURATION} <= set(by_cat)
    pool = by_cat[DbCategory.POOL_EXHAUSTION]
    assert SVC in pool.services
    assert pool.metrics
    assert list(pool.finding_ids) == sorted(pool.finding_ids)
    assert list(pool.hypothesis_ids) == sorted(pool.hypothesis_ids)
    assert DB in by_cat[DbCategory.SERVER_SATURATION].services


@pytest.mark.criterion("TASK-008:C1", "TASK-008:C4")
@pytest.mark.req("FR-06", "OUT-05", "QA-01")
def test_no_db_findings_no_db_signals(impl):
    lines = [text_line(s, "INFO", "alpha-service", "heartbeat") for s in range(0, 300, 30)]
    lines += [text_line(300 + 5 * i, "ERROR", "alpha-service", "charge rejected") for i in range(6)]
    lines += [text_line(s, "INFO", "alpha-service", "heartbeat") for s in range(420, 800, 30)]
    ns = stage_recs(lines)
    assert ns.analysis.findings
    assert ns.recs.db_signals == ()
    assert ns.recs.uncertainty
    check_next_steps(ns)
    check_texts(ns.recs)
    again = impl("recommendations", "recommend")(ns.parse, ns.patterns, ns.timeline, ns.analysis, ns.config)
    assert again == ns.recs


@pytest.mark.criterion("TASK-008:C2")
@pytest.mark.req("OUT-06")
def test_next_steps_cover_db_categories():
    ns = stage_recs(read_log_lines(CHAIN_LOG))
    check_next_steps(ns)
    assert any("pg_stat_activity" in n.action for n in ns.recs.next_steps)


def statements(recs):
    return [u.statement for u in recs.uncertainty]


def extra_items(base, other):
    return set(statements(other)) - set(statements(base))


@pytest.mark.criterion("TASK-008:C3")
@pytest.mark.req("OUT-05", "QA-01")
def test_uncertainty_always_present_items():
    ns = stage_recs(chain_lines(episode_db_first()))
    items = statements(ns.recs)
    assert items
    assert any(
        re.search(r"cause", s, re.IGNORECASE) and re.search(r"\bnot\b|\bno\b|explicit|cannot|can't", s, re.IGNORECASE)
        for s in items
    ), items


PARSE_FAILURE_ITEM = re.compile(r"unpars|not be parsed|n't be parsed|not parsed|failed to parse|parse fail", re.IGNORECASE)
FALLBACK_ITEM = re.compile(r"fallback|fall back", re.IGNORECASE)


def unobserved_recovery_item(s):
    # negation within three words of "recover": does not catch "recovered" in other items with a negation
    near = (
        r"(?:\bnot\b|\bno\b|n't|\bun\w*)\W+(?:\w+\W+){0,3}recover"
        r"|recover\w*\W+(?:\w+\W+){0,3}(?:\bnot\b|n't|unobserved|unconfirmed)"
    )
    return bool(re.search(near, s, re.IGNORECASE))


@pytest.mark.criterion("TASK-008:C3")
@pytest.mark.req("OUT-05", "QA-01")
def test_uncertainty_conditional_items_absent_when_conditions_unmet():
    ns = stage_recs(chain_lines(episode_db_first()))
    assert ns.parse.failures == ()
    assert ns.timeline.baseline_is_fallback is False
    assert ns.timeline.episodes and all(ep.recovered for ep in ns.timeline.episodes)
    items = statements(ns.recs)
    assert not [s for s in items if PARSE_FAILURE_ITEM.search(s)], items
    assert not [s for s in items if FALLBACK_ITEM.search(s)], items
    assert not [s for s in items if unobserved_recovery_item(s)], items


@pytest.mark.criterion("TASK-008:C3")
@pytest.mark.req("OUT-05", "QA-01")
def test_uncertainty_mentions_parse_failures_only_when_present(impl):
    ns = stage_recs(chain_lines(episode_db_first()))
    assert ns.parse.failures == ()
    recommend = impl("recommendations", "recommend")
    failures = (
        ParseFailure(9001, "garbage one", ParseFailureReason.UNRECOGNIZED),
        ParseFailure(9002, "garbage two", ParseFailureReason.BAD_TIMESTAMP),
    )
    parse2 = dataclasses.replace(ns.parse, failures=failures, total_lines=ns.parse.total_lines + 2)
    recs2 = recommend(parse2, ns.patterns, ns.timeline, ns.analysis, ns.config)
    extra = extra_items(ns.recs, recs2)
    assert extra, "no separate item about unparsed lines"
    assert any(re.search(r"\b2\b", s) for s in extra), extra


@pytest.mark.criterion("TASK-008:C3")
@pytest.mark.req("OUT-05", "QA-01")
def test_uncertainty_mentions_fallback_baseline_only_when_fallback(impl):
    ns = stage_recs(chain_lines(episode_db_first()))
    assert ns.timeline.baseline_is_fallback is False
    tl2 = dataclasses.replace(ns.timeline, baseline_is_fallback=True)
    recs2 = impl("recommendations", "recommend")(ns.parse, ns.patterns, tl2, ns.analysis, ns.config)
    extra = extra_items(ns.recs, recs2)
    assert extra, "no separate item about the fallback baseline"
    assert any(re.search(r"baseline|normal", s, re.IGNORECASE) for s in extra), extra


@pytest.mark.criterion("TASK-008:C3")
@pytest.mark.req("OUT-05", "QA-01")
def test_uncertainty_mentions_unobserved_recovery_only_when_unrecovered(impl):
    ns = stage_recs(chain_lines(episode_db_first()))
    (ep,) = ns.timeline.episodes
    assert ep.recovered is True
    ep2 = dataclasses.replace(
        ep,
        recovered=False,
        recovery=None,
        window=TimeWindow(ep.onset, ep.last_anomaly),
        recovery_status=RecoveryStatus.NOT_OBSERVED,
        recovery_evidence=(),
    )
    tl2 = dataclasses.replace(ns.timeline, episodes=(ep2,))
    recs2 = impl("recommendations", "recommend")(ns.parse, ns.patterns, tl2, ns.analysis, ns.config)
    extra = extra_items(ns.recs, recs2)
    assert extra, "no separate item about unobserved recovery"
    assert any(re.search(r"recover", s, re.IGNORECASE) for s in extra), extra


@pytest.mark.criterion("TASK-008:C4")
@pytest.mark.req("OUT-05", "OUT-06", "QA-01")
def test_texts_english_and_deterministic(impl):
    ns = stage_recs(read_log_lines(CHAIN_LOG))
    check_texts(ns.recs)
    again = impl("recommendations", "recommend")(ns.parse, ns.patterns, ns.timeline, ns.analysis, ns.config)
    assert again == ns.recs


@pytest.mark.criterion("TASK-008:C5")
@pytest.mark.req("FR-06", "OUT-06", "OUT-07")
def test_smoke_attached_log():
    ns = stage_recs(read_log_lines(ATTACHED_LOG))
    assert ns.recs.db_signals
    check_db_signals(ns)
    check_next_steps(ns)
    check_texts(ns.recs)
