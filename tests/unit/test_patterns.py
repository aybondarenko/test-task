from test_task.baseline import build_baseline, score_events
from test_task.contracts.common import AnalysisConfig, SignalKind
from test_task.parser import parse_line
from test_task.patterns import find_patterns
from test_task.signals import extract_events


def ts(sec):
    return f"2026-03-27T14:{int(sec) // 60:02d}:{sec % 60:06.3f}Z"


def run(specs, cfg=None):
    cfg = cfg or AnalysisConfig()
    recs = [parse_line(f"{ts(s)} {rest}", i) for i, (s, rest) in enumerate(specs, 1)]
    evs = extract_events(recs)  # type: ignore[arg-type]
    base = build_baseline(evs, cfg)
    scored = score_events(evs, base, cfg)
    return scored, find_patterns(scored, base, cfg)


def test_group_trace_and_noise():
    specs = [(0, "INFO gw ok"), (1, "INFO gw ok")]
    specs += [(310, "INFO gw req request_id=r1")]
    specs += [(311, "ERROR svc-a failed request_id=r1 latency_ms=5")]
    specs += [(312, "ERROR svc-a request_id=r1 \tat a.B.c(B.java:1)")]
    specs += [(313, "WARN svc-b slow request_id=r1")]
    scored, res = run(specs)
    assert res.noise_lines + res.anomalous_lines + res.continuation_lines == len(scored)
    assert sum(g.count for g in res.groups) == res.anomalous_lines
    assert res.continuation_lines == 1
    assert [g.id for g in res.groups] == ["P1", "P2"]
    assert res.groups[0].kind == SignalKind.ERROR
    assert len(res.traces) == 1 and res.traces[0].anomalous_hops == 2
    assert res.example_trace_ids == ("r1",)


def test_samples_truncate_and_limit():
    long = "x" * 400
    specs = [(0, "INFO gw ok")]
    specs += [(310 + i, "WARN s msg" + chr(97 + i) + " " + long) for i in range(6)]
    _, res = run(specs, AnalysisConfig(max_evidence_chars=50))
    (g,) = res.groups
    assert g.count == 6 and len(g.samples) == 3
    assert all(len(s.text) <= 50 and s.text.endswith("...") for s in g.samples)
    assert g.samples[0].line_no == g.line_nos[0]


def test_empty():
    _, res = run([])
    assert res.groups == () and res.traces == () and res.noise_lines == 0
