from test_task.baseline import build_baseline, score_events
from test_task.causality import assess, build_service_graph
from test_task.contracts.causality import HypothesisKind, Verdict
from test_task.contracts.common import HEDGE_WORDS, AnalysisConfig, Confidence, Role
from test_task.parser import parse_line
from test_task.patterns import find_patterns
from test_task.signals import extract_events
from test_task.timeline import build_timeline, compare_windows


def ts(sec):
    return f"2026-03-27T14:{int(sec) // 60:02d}:{sec % 60:06.3f}Z"


def run(specs):
    cfg = AnalysisConfig()
    recs = [parse_line(f"{ts(s)} {r}", i) for i, (s, r) in enumerate(sorted(specs), 1)]
    evs = extract_events(recs)  # type: ignore[arg-type]
    base = build_baseline(evs, cfg)
    scored = score_events(evs, base, cfg)
    pats = find_patterns(scored, base, cfg)
    tl = build_timeline(scored, base, cfg)
    graph = build_service_graph(scored, pats)
    return assess(scored, pats, tl, compare_windows(scored, tl, cfg), graph, cfg)


def noise(end=300):
    out = [(s, "INFO svc-n heartbeat latency_ms=10") for s in range(0, end, 30)]
    out += [
        (
            s,
            "INFO db-x stats active_connections=10 max_connections=100 waiting_clients=0",
        )
        for s in range(0, end, 30)
    ]
    out += [(s, "INFO svc-a query ok db_latency_ms=10") for s in range(0, end, 30)]
    return out


def chain(client_first=False):
    db = [
        (
            t,
            "WARN db-x stats active_connections=88 max_connections=100 waiting_clients=6",
        )
        for t in (300 + (10 if client_first else 0), 320)
    ]
    cl = [
        (
            t,
            "WARN svc-a db acquire connection slow wait_ms=900 pool_in_use=20 pool_idle=0",
        )
        for t in (300 + (0 if client_first else 5), 330)
    ]
    return noise() + db + cl + noise(600)[10:]


def test_hypotheses_hedged_and_numbered():
    out = run(chain())
    hyps = list(out.causes.primary) + list(out.causes.alternatives)
    assert hyps
    assert [h.id for h in hyps] == [f"H{i}" for i in range(1, len(hyps) + 1)]
    for h in hyps:
        assert any(w in h.statement.lower() for w in HEDGE_WORDS)
        assert h.supporting and h.finding_ids
    causes = {f.id for f in out.findings if f.role is Role.CAUSE}
    assert causes <= {i for h in hyps for i in h.finding_ids}


def test_db_first_high_and_client_alternative():
    out = run(chain())
    top = out.causes.primary[0]
    assert top.confidence is Confidence.HIGH and top.contradicting == ()
    assert any("client-side" in h.statement for h in out.causes.alternatives)


def test_client_first_has_contradiction_and_not_high():
    out = run(chain(client_first=True))
    top = out.causes.primary[0]
    assert top.confidence is not Confidence.HIGH
    assert top.contradicting
    alt = [
        h
        for h in (*out.causes.primary, *out.causes.alternatives)
        if "client-side" in h.statement
    ]
    assert alt and alt[0].supporting and len(alt[0].supporting) == 2


def test_missing_db_server_activity_contradicts_client_cause():
    specs = (
        noise()
        + [
            (
                300,
                "WARN svc-a db acquire connection slow wait_ms=900 pool_in_use=20 pool_idle=0",
            ),
            (
                310,
                "WARN svc-a db acquire connection slow wait_ms=900 pool_in_use=20 pool_idle=0",
            ),
        ]
        + noise(600)[10:]
    )
    out = run(specs)
    hyps = list(out.causes.primary) + list(out.causes.alternatives)
    assert any("No anomalous events" in c for h in hyps for c in h.contradicting)
    assert all(h.confidence is not Confidence.HIGH for h in hyps)


def test_no_anomalies_no_hypotheses():
    out = run(noise(600))
    assert out.causes.primary == () and out.causes.alternatives == ()


def kinds_of(out, kind):
    return [
        h for h in (*out.causes.primary, *out.causes.alternatives) if h.kind is kind
    ]


def test_primary_only_source_and_one_per_source():
    out = run(chain())
    assert out.causes.primary
    assert all(h.kind is HypothesisKind.SOURCE for h in out.causes.primary)
    sources = kinds_of(out, HypothesisKind.SOURCE)
    keys = [(h.episode_ids, h.source_services) for h in sources]
    assert len(keys) == len(set(keys))
    cause_ids = {f.id for f in out.findings if f.role is Role.CAUSE}
    assert cause_ids <= {i for h in sources for i in h.cause_finding_ids}


def test_load_spike_inconclusive_without_entry_requests_in_baseline():
    specs = (
        noise()
        + [
            (300, "ERROR gw upstream failed upstream=svc-a status=503 request_id=r1"),
            (305, "ERROR gw upstream failed upstream=svc-a status=503 request_id=r2"),
            (
                300,
                "WARN svc-a db acquire connection slow wait_ms=900 pool_in_use=20 pool_idle=0",
            ),
            (
                306,
                "WARN svc-a db acquire connection slow wait_ms=900 pool_in_use=20 pool_idle=0",
            ),
        ]
        + noise(600)[10:]
    )
    out = run(specs)
    spikes = kinds_of(out, HypothesisKind.LOAD_SPIKE)
    assert spikes
    for h in spikes:
        assert h.verdict is Verdict.INCONCLUSIVE
        assert h.confidence is Confidence.LOW
        assert h.contradicting and not h.supporting


def test_pre_onset_trigger_lists_unusual_events():
    triggers = [
        (s, "INFO db-x autovacuum launched on table archive") for s in (285, 290)
    ]
    out = run(chain() + triggers)
    pre = kinds_of(out, HypothesisKind.PRE_ONSET_TRIGGER)
    assert len(pre) == 1
    assert pre[0].confidence is Confidence.LOW
    assert len(pre[0].samples) == 2
    assert pre[0].source_services == ("db-x",)
    assert not kinds_of(run(chain()), HypothesisKind.PRE_ONSET_TRIGGER)
