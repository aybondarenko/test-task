"""TASK-021: one SOURCE per origin, EPISODE_LINK, LOAD_SPIKE, PRE_ONSET_TRIGGER, evidence without repetition."""

from __future__ import annotations

import re

import pytest
from acc_support import (
    APP_NAMES_OF_ATTACHED_LOG,
    ATTACHED_LOG,
    DB,
    GW,
    PROJECT_ROOT,
    calm,
    has_hedge,
    hyp_texts,
    hyps_of_kind,
    json_line,
    overconfident,
    read_log_lines,
    saturation_burst,
    scan_for,
    scenario,
    six_word_repeats,
    stage_analysis,
    text_line,
)

from test_task.contracts.causality import (
    MAX_HYPOTHESIS_ARGUMENTS,
    HypothesisKind,
    Verdict,
)
from test_task.contracts.common import Confidence, Role

HK = HypothesisKind
PRICING = "pricing-service"
NOT_ABOVE_MEDIUM = (Confidence.MEDIUM, Confidence.LOW)


def saturation_lines(gw_extra=0, between=None, second=False, triggers=()):
    parts = [calm(0, 300), saturation_burst(300, gw_extra=gw_extra), list(triggers)]
    if second:
        parts += [calm(420, 900, active=between or 30, first_index=100), saturation_burst(900), calm(1020, 1500, first_index=300)]
    else:
        parts += [calm(420, 900, first_index=100)]
    return scenario(*parts)


def source_of(analysis, service, episode_id=None):
    found = [
        h
        for h in hyps_of_kind(analysis, HK.SOURCE)
        if service in h.source_services and (episode_id is None or episode_id in h.episode_ids)
    ]
    assert len(found) == 1, [(h.id, h.source_services, h.episode_ids) for h in hyps_of_kind(analysis, HK.SOURCE)]
    return found[0]


def check_hypotheses(analysis):
    finding_ids = {f.id for f in analysis.findings}
    for h in list(analysis.causes.primary) + list(analysis.causes.alternatives):
        assert len(h.supporting) <= MAX_HYPOTHESIS_ARGUMENTS and len(h.contradicting) <= MAX_HYPOTHESIS_ARGUMENTS, h.id
        assert six_word_repeats(list(h.supporting) + list(h.contradicting)) == [], h.id
        assert has_hedge(h.statement), h.statement
        assert overconfident(hyp_texts(h)) == [], h.id
        assert h.finding_ids and set(h.finding_ids) <= finding_ids, h.id


@pytest.fixture(scope="module")
def saturation():
    return stage_analysis(saturation_lines())


@pytest.mark.criterion("TASK-021:C1")
@pytest.mark.req("QA-10")
def test_one_source_hypothesis_per_source():
    lines = []
    for i in range(30):
        t = 10 * i
        lines.append(text_line(t, "INFO", GW, f"request served request_id=p-{i} status=200 upstream={PRICING} latency_ms=41"))
        lines.append(text_line(t + 1, "INFO", PRICING, f"price computed request_id=p-{i} items=3"))
    for k in range(10):
        t = 300 + 10 * k
        lines.append(text_line(t, "ERROR", PRICING, f"pq: could not serialize access request_id=e-{k}"))
        lines.append(text_line(t + 2, "WARN", PRICING, "price cache refresh retrying attempt=2"))
        lines.append(json_line(t + 5, "ERROR", GW, "upstream error", request_id=f"e-{k}", upstream=PRICING, status=503))
    for i in range(40, 80):
        t = 10 * i
        lines.append(text_line(t, "INFO", GW, f"request served request_id=p-{i} status=200 upstream={PRICING} latency_ms=42"))
        lines.append(text_line(t + 1, "INFO", PRICING, f"price computed request_id=p-{i} items=2"))
    ns = stage_analysis(scenario(lines))
    a = ns.analysis
    pricing_causes = [f for f in a.findings if f.role == Role.CAUSE and f.services == (PRICING,)]
    assert len(pricing_causes) == 2, [(f.id, f.services, f.role.value, f.db_related) for f in a.findings]
    eid = pricing_causes[0].episode_id
    src = [h for h in hyps_of_kind(a, HK.SOURCE) if eid in h.episode_ids and h.source_services == (PRICING,)]
    assert len(src) == 1, [(h.id, h.source_services) for h in hyps_of_kind(a, HK.SOURCE)]
    assert {f.id for f in pricing_causes} <= set(src[0].cause_finding_ids)
    covered = {fid for h in hyps_of_kind(a, HK.SOURCE) for fid in h.cause_finding_ids}
    assert {f.id for f in a.findings if f.role == Role.CAUSE} <= covered
    assert a.causes.primary and all(h.kind == HK.SOURCE for h in a.causes.primary)


@pytest.mark.criterion("TASK-021:C2")
@pytest.mark.req("QA-08", "QA-09")
def test_source_arguments_strongest_shifts_and_composite(saturation):
    h = source_of(saturation.analysis, DB)
    assert h.supporting
    first = h.supporting[0]
    assert "active_connections 100 at max_connections=100" in first, first
    assert "waiting_clients" in first, first
    joined = " ".join(h.supporting)
    assert "connections appear to be held rather than serving load" in joined
    assert "30 → 100" in joined and "107 → 51" in joined, joined
    assert h.shifts and h.shifts[0].metric == "active_connections"


def link_args(h):
    return [a for a in list(h.supporting) + list(h.contradicting)]


@pytest.mark.criterion("TASK-021:C3")
@pytest.mark.req("QA-07")
def test_episode_link_when_source_stays_elevated():
    def elevated(i):
        return 53 + 2 * ((i // 2) % 5)

    ns = stage_analysis(saturation_lines(second=True, between=elevated))
    assert [e.id for e in ns.timeline.episodes] == ["E1", "E2"]
    links = hyps_of_kind(ns.analysis, HK.EPISODE_LINK)
    assert len(links) == 1, [(h.id, h.episode_ids) for h in links]
    h = links[0]
    assert h.episode_ids == ("E1", "E2")
    assert h.confidence in NOT_ABOVE_MEDIUM
    assert any(
        "active_connections" in a and re.search(r"\b53\b", a) and re.search(r"\b61\b", a) and re.search(r"\b30\b", a)
        for a in link_args(h)
    ), link_args(h)
    check_hypotheses(ns.analysis)


@pytest.mark.criterion("TASK-021:C3")
@pytest.mark.req("QA-07")
def test_no_episode_link_when_source_returns_to_baseline():
    ns = stage_analysis(saturation_lines(second=True))
    assert len(ns.timeline.episodes) == 2
    assert hyps_of_kind(ns.analysis, HK.EPISODE_LINK) == []


@pytest.mark.criterion("TASK-021:C4")
@pytest.mark.req("QA-11")
def test_load_spike_not_supported_at_baseline_rate(saturation):
    spikes = hyps_of_kind(saturation.analysis, HK.LOAD_SPIKE)
    assert len(spikes) == 1, spikes
    h = spikes[0]
    assert h.verdict == Verdict.NOT_SUPPORTED
    assert h.confidence == Confidence.LOW
    assert "not supported by the log" in hyp_texts(h)


@pytest.mark.criterion("TASK-021:C4")
@pytest.mark.req("QA-11")
def test_load_spike_supported_at_triple_rate():
    ns = stage_analysis(saturation_lines(gw_extra=2))
    spikes = hyps_of_kind(ns.analysis, HK.LOAD_SPIKE)
    assert len(spikes) == 1, spikes
    h = spikes[0]
    assert h.verdict == Verdict.SUPPORTED
    text = hyp_texts(h)
    assert "supported by the log" in text and "not supported by the log" not in text


@pytest.mark.criterion("TASK-021:C5")
@pytest.mark.req("QA-11")
def test_pre_onset_trigger_found_and_absent(saturation):
    triggers = [text_line(t, "INFO", DB, "autovacuum launched on table orders_archive") for t in (291, 294, 297)]
    lines = saturation_lines(triggers=triggers)
    ns = stage_analysis(lines)
    numbers = {lines.index(t) + 1 for t in triggers}
    e1 = ns.timeline.episodes[0].id
    pre = [h for h in hyps_of_kind(ns.analysis, HK.PRE_ONSET_TRIGGER) if e1 in h.episode_ids]
    assert len(pre) == 1, hyps_of_kind(ns.analysis, HK.PRE_ONSET_TRIGGER)
    assert numbers <= {s.line_no for s in pre[0].samples}
    assert hyps_of_kind(saturation.analysis, HK.PRE_ONSET_TRIGGER) == []


@pytest.mark.criterion("TASK-021:C6")
@pytest.mark.req("OUT-16", "QA-02")
@pytest.mark.parametrize("which", ["saturation", "spike", "attached"])
def test_hypothesis_texts(saturation, which):
    if which == "saturation":
        a = saturation.analysis
    elif which == "spike":
        a = stage_analysis(saturation_lines(gw_extra=2)).analysis
    else:
        a = stage_analysis(read_log_lines(ATTACHED_LOG)).analysis
    assert a.causes.primary
    check_hypotheses(a)


@pytest.mark.criterion("TASK-021:C7")
@pytest.mark.req("QA-07", "QA-08", "QA-09", "QA-10", "QA-11")
def test_attached_log_hypotheses():
    ns = stage_analysis(read_log_lines(ATTACHED_LOG))
    a = ns.analysis
    e1, e2 = ns.timeline.episodes[0].id, ns.timeline.episodes[1].id
    sources = [h for h in hyps_of_kind(a, HK.SOURCE) if e1 in h.episode_ids]
    assert len(sources) == 1, [(h.id, h.source_services) for h in sources]
    h = sources[0]
    assert h.source_services == ("postgres-primary",)
    assert "active_connections 100 at max_connections=100" in h.supporting[0]
    assert "waiting_clients up to 31" in h.supporting[0]
    joined = " ".join(h.supporting)
    assert "30 → 100" in joined and "107 → 51" in joined
    links = [x for x in hyps_of_kind(a, HK.EPISODE_LINK) if x.episode_ids == (e1, e2)]
    assert len(links) == 1
    assert links[0].confidence in NOT_ABOVE_MEDIUM
    assert any(re.search(r"\b53\b", t) and re.search(r"\b61\b", t) and re.search(r"\b30\b", t) for t in link_args(links[0]))
    spikes = hyps_of_kind(a, HK.LOAD_SPIKE)
    assert spikes and all(x.verdict is not None for x in spikes)
    pre = [x for x in hyps_of_kind(a, HK.PRE_ONSET_TRIGGER) if e1 in x.episode_ids]
    assert pre and {136, 139, 142} <= {s.line_no for p in pre for s in p.samples}


@pytest.mark.criterion("TASK-021:C8")
@pytest.mark.req("NEG-07")
def test_no_attached_log_specifics_in_rules():
    files = [PROJECT_ROOT / "src" / "test_task" / n for n in ("hypotheses.py", "causality.py")]
    assert scan_for(files, list(APP_NAMES_OF_ATTACHED_LOG) + ["2026-03-27"]) == []
