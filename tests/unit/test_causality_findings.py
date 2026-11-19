from test_task.baseline import build_baseline, score_events
from test_task.causality import assess, build_service_graph
from test_task.contracts.causality import EdgeSource, ServiceClass
from test_task.contracts.common import AnalysisConfig, Role, Severity, SignalKind
from test_task.parser import parse_line
from test_task.patterns import find_patterns
from test_task.signals import extract_events
from test_task.timeline import build_timeline, compare_windows


def ts(sec):
    return f"2026-03-27T14:{int(sec) // 60:02d}:{sec % 60:06.3f}Z"


def run(specs, cfg=None):
    cfg = cfg or AnalysisConfig()
    recs = [
        parse_line(f"{ts(s)} {rest}", i) for i, (s, rest) in enumerate(sorted(specs), 1)
    ]
    evs = extract_events(recs)  # type: ignore[arg-type]
    base = build_baseline(evs, cfg)
    scored = score_events(evs, base, cfg)
    pats = find_patterns(scored, base, cfg)
    tl = build_timeline(scored, base, cfg)
    graph = build_service_graph(scored, pats)
    return assess(scored, pats, tl, compare_windows(scored, tl, cfg), graph, cfg)


def noise(end=300, start=0):
    return [(s, "INFO svc-n heartbeat latency_ms=10") for s in range(start, end, 30)]


def test_no_anomalies():
    out = run(noise(600))
    assert out.findings == ()
    assert out.causes.primary == () and out.impact.services == ()


def test_empty_log():
    assert run([]).findings == ()


def test_opposite_trace_orders_cancel_on_tie():
    specs = noise() + [
        (310, "INFO a-svc step request_id=x1"),
        (311, "ERROR b-svc step bad request_id=x1"),
        (320, "INFO b-svc step request_id=x2"),
        (321, "ERROR a-svc step bad request_id=x2"),
    ]
    out = run(specs)
    assert not [e for e in out.graph.edges if e.source is EdgeSource.TRACE_ORDER]


def test_merged_metrics_and_samples():
    specs = noise() + [(300, "INFO a-svc ok latency_ms=10")] * 0
    base = [
        (15 + 60 * k, f"INFO {s} done latency_ms=50")
        for k in range(5)
        for s in ("a-svc", "b-svc")
    ]
    inc = [
        (310 + i, f"WARN {s} slow latency_ms={900 + i}")
        for i, s in enumerate(["a-svc", "b-svc", "a-svc", "b-svc", "a-svc"])
    ]
    out = run(sorted(specs + base + inc) + noise(800, 420))
    (f,) = [f for f in out.findings if f.kind is SignalKind.LATENCY]
    assert f.services == ("a-svc", "b-svc")
    (m,) = [m for m in f.evidence.metrics if m.metric == "latency_ms"]
    assert m.count == 5 and m.max == 904 and m.min == 900
    assert m.baseline_p95 == 50
    assert f.severity is Severity.MEDIUM
    assert len(f.evidence.samples) <= 3


def test_residual_symptom_and_late_candidate():
    specs = (
        noise()
        + [(300 + i, "ERROR a-svc boom") for i in (0, 1)]
        + [(340 + i, "WARN b-svc upstream timeout upstream=a-svc") for i in (0, 1)]
        + noise(800, 420)
        + [(700, "WARN lone-svc odd thing")]
    )
    out = run(specs)
    by = {f.services: f for f in out.findings}
    assert by[("a-svc",)].role is Role.CAUSE
    assert by[("b-svc",)].role is Role.SYMPTOM
    assert "after episode onset" in by[("b-svc",)].role_rationale
    lone = by[("lone-svc",)]
    assert lone.episode_id is None and lone.role is Role.SYMPTOM
    assert "residual deviation outside detected episodes" in lone.role_rationale


def test_group_before_episode_still_yields_cause():
    # anomaly before the episode, then continuation: the episode has no findings in the candidate window
    specs = (
        noise(900)
        + [(10, "ERROR a-svc boom")]
        + [(100 + 5 * i, "ERROR a-svc boom") for i in range(4)]
        + [(150, "WARN b-svc odd thing"), (151, "WARN b-svc odd thing")]
    )
    out = run(specs)
    in_episode = [f for f in out.findings if f.episode_id is not None]
    assert [f.services for f in in_episode] == [("b-svc",)]
    assert any(f.role is Role.CAUSE for f in in_episode)


def test_service_classes_and_single_event_title():
    specs = noise() + [
        (310, "ERROR gw-svc call failed upstream=core-svc"),
        (311, "ERROR gw-svc call failed upstream=core-svc"),
        (312, "ERROR core-svc work failed"),
    ]
    out = run(specs)
    classes = {s.service: s.service_class for s in out.graph.services}
    assert classes["gw-svc"] is ServiceClass.ENTRY_POINT
    assert classes["core-svc"] is ServiceClass.INTERNAL
    core = next(f for f in out.findings if f.services == ("core-svc",))
    assert core.title == "1 error event recorded in core-svc."
    assert core.role is Role.CAUSE


def test_same_class_non_db_groups_merge_into_one_finding():
    specs = noise() + [
        (310, "ERROR a-svc boom"),
        (311, "ERROR a-svc boom"),
        (312, "WARN b-svc upstream timeout"),
        (313, "WARN b-svc upstream timeout"),
    ]
    out = run(specs)
    (f,) = [f for f in out.findings if f.episode_id is not None]
    assert f.services == ("a-svc", "b-svc")
    assert f.kinds == (SignalKind.TIMEOUT, SignalKind.ERROR)
    assert f.kind is SignalKind.ERROR and f.severity is Severity.HIGH
    assert "timeout and error" in f.title


def test_connections_held_composite_and_shifts():
    base = []
    for k in range(6):
        base.append(
            (
                10 + 40 * k,
                "INFO db-x stats active_connections=30 max_connections=100 txn_per_s=107 waiting_clients=0",
            )
        )
    inc = [
        (
            310,
            "WARN db-x stats active_connections=100 max_connections=100 txn_per_s=51 waiting_clients=31",
        ),
        (
            320,
            "WARN db-x stats active_connections=95 max_connections=100 txn_per_s=60 waiting_clients=12",
        ),
    ]
    out = run(base + inc + noise(900, 330))
    (f,) = [f for f in out.findings if "db-x" in f.services]
    shifts = f.evidence.shifts
    assert shifts[0].metric == "active_connections" and shifts[0].limit_reached
    assert shifts[0].limit == 100 and shifts[0].worst_line_no >= 1
    assert shifts[1].metric == "waiting_clients"
    (c,) = f.evidence.composites
    assert (c.rising.worst, c.falling.worst) == (100, 51)
    assert "30 → 100" in c.interpretation and "107 → 51" in c.interpretation
    assert "connections appear to be held rather than serving load" in c.interpretation


def test_propagation_follows_graph_edges():
    specs = noise() + [
        (310, "ERROR core-svc work failed request_id=r1"),
        (311, "ERROR core-svc work failed request_id=r2"),
        (312, "ERROR mid-svc call failed upstream=core-svc request_id=r1"),
        (313, "ERROR mid-svc call failed upstream=core-svc request_id=r2"),
    ]
    out = run(specs)
    steps = {(s.from_service, s.to_service, s.via) for s in out.impact.propagation}
    assert steps == {("core-svc", "mid-svc", EdgeSource.PEER_FIELD)}


def test_merge_weak_keeps_lone_and_strong_drafts() -> None:
    from test_task.causality import _Draft, _merge_weak
    from test_task.contracts.causality import ServiceClass
    from test_task.contracts.common import Severity
    from test_task.contracts.signals import SignalKind

    def draft(sev: Severity, ep: "str | None") -> _Draft:
        return _Draft(
            ServiceClass.INTERNAL,
            ep,
            (),
            SignalKind.LATENCY,
            (SignalKind.LATENCY,),
            sev,
        )

    items = [
        (draft(Severity.MEDIUM, "E1"), False),
        (draft(Severity.HIGH, "E1"), False),
        (draft(Severity.LOW, None), False),
    ]
    assert len(_merge_weak(items)) == 3
    assert _merge_weak([]) == []
