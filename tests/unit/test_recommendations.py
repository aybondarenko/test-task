import dataclasses

from test_hypotheses import chain, noise, ts

from test_task.baseline import build_baseline, score_events
from test_task.causality import assess, build_service_graph
from test_task.contracts.common import AnalysisConfig, DbCategory
from test_task.contracts.parser import ParseFailure, ParseFailureReason
from test_task.contracts.recommendations import SHORT_WINDOW_UNCERTAINTY
from test_task.contracts.timeline import RecoveryStatus
from test_task.parser import parse_lines
from test_task.patterns import find_patterns
from test_task.recommendations import recommend
from test_task.signals import extract_events
from test_task.timeline import build_timeline, compare_windows


def run(specs):
    cfg = AnalysisConfig()
    lines = [f"{ts(s)} {r}\n" for s, r in sorted(specs)]
    parse = parse_lines(lines)
    evs = extract_events(parse.records)
    base = build_baseline(evs, cfg)
    scored = score_events(evs, base, cfg)
    pats = find_patterns(scored, base, cfg)
    tl = build_timeline(scored, base, cfg)
    an = assess(
        scored,
        pats,
        tl,
        compare_windows(scored, tl, cfg),
        build_service_graph(scored, pats),
        cfg,
    )
    return parse, pats, tl, an, cfg


def test_db_signals_and_steps():
    parse, pats, tl, an, cfg = run(chain())
    r = recommend(parse, pats, tl, an, cfg)
    cats = [s.category for s in r.db_signals]
    assert DbCategory.POOL_EXHAUSTION in cats
    assert cats == sorted(cats, key=list(DbCategory).index)
    assert any("pg_stat_activity" in n.action for n in r.next_steps)
    assert [n.id for n in r.next_steps] == [
        f"N{i}" for i in range(1, len(r.next_steps) + 1)
    ]
    assert r == recommend(parse, pats, tl, an, cfg)
    texts = [u.statement for u in r.uncertainty]
    assert len(texts) == len(set(texts))
    assert not any("host" in t.lower() for t in texts)
    assert len(r.next_steps) <= 7


def test_conditional_uncertainty():
    parse, pats, tl, an, cfg = run(chain())
    parse = dataclasses.replace(
        parse, failures=(ParseFailure(9, "x", ParseFailureReason.UNRECOGNIZED),)
    )
    tl = dataclasses.replace(
        tl,
        baseline_is_fallback=True,
        episodes=tuple(
            dataclasses.replace(
                e,
                recovered=False,
                recovery=None,
                recovery_status=RecoveryStatus.NOT_OBSERVED,
            )
            for e in tl.episodes
        ),
    )
    r = recommend(parse, pats, tl, an, cfg)
    texts = [u.statement for u in r.uncertainty]
    assert any("not observed" in t for t in texts)
    assert any("fallback" in t for t in texts)
    assert any("1 log line " in u.statement for u in r.uncertainty)


def test_no_db_findings():
    specs = noise()
    specs = [(s, x.replace("INFO", "INFO")) for s, x in specs if "svc-n" in x]
    specs += [(300 + 5 * i, "ERROR svc-n charge rejected") for i in range(6)]
    specs += [(s, "INFO svc-n heartbeat latency_ms=10") for s in range(420, 800, 30)]
    parse, pats, tl, an, cfg = run(specs)
    assert an.findings
    r = recommend(parse, pats, tl, an, cfg)
    assert r.db_signals == ()
    assert r.uncertainty
    assert r.next_steps
    assert all(n.related_ids for n in r.next_steps)


def test_short_window_item():
    parse, pats, tl, an, cfg = run(chain())
    tl = dataclasses.replace(
        tl,
        episodes=tuple(
            dataclasses.replace(e, recovery_status=RecoveryStatus.OBSERVED_SHORT_WINDOW)
            for e in tl.episodes
        ),
    )
    r = recommend(parse, pats, tl, an, cfg)
    eid = tl.episodes[0].id
    assert any(
        u.statement == SHORT_WINDOW_UNCERTAINTY.format(episode=eid)
        and eid in u.related_ids
        for u in r.uncertainty
    )
