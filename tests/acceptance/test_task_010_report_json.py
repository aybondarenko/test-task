"""TASK-010: the Report model and deterministic report.json.

Since 2.0: schema 2.0, 15 top-level keys, ``summary_source`` removed.
"""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime

import pytest
from acc_support import (
    CHAIN_LOG,
    PROJECT_ROOT,
    assert_same_json,
    read_log_lines,
    ref_json,
    stage_report,
)

from test_task.contracts.common import SEVERITY_CRITERIA
from test_task.contracts.parser import ParseFailureReason
from test_task.contracts.report import REPORT_SCHEMA_VERSION, SeverityCriterion

TOP_KEYS = [
    "schema_version",
    "source",
    "parse_failures",
    "incident_summary",
    "key_findings",
    "probable_causes",
    "impact",
    "uncertainty",
    "next_steps",
    "db_signals",
    "timeline",
    "metric_comparison",
    "error_rates",
    "request_traces",
    "severity_scale",
]
TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


@pytest.fixture(scope="module")
def chain():
    return stage_report(read_log_lines(CHAIN_LOG), file_name="incident_chain.log")


@pytest.mark.criterion("TASK-010:C1")
@pytest.mark.req("FR-05", "OUT-12")
def test_build_report_is_pure_composition(chain):
    r, ns = chain.report, chain
    assert r.schema_version == REPORT_SCHEMA_VERSION
    assert r.key_findings == ns.analysis.findings
    assert r.probable_causes == ns.analysis.causes
    assert r.impact == ns.analysis.impact
    assert r.uncertainty == ns.recs.uncertainty
    assert r.next_steps == ns.recs.next_steps
    assert r.db_signals == ns.recs.db_signals
    assert r.timeline == ns.timeline
    assert r.metric_comparison == ns.comparison.metrics
    assert r.error_rates == ns.comparison.error_rates
    assert r.incident_summary == ns.inputs.incident_summary
    assert not hasattr(r, "summary_source")
    traces = {t.request_id: t for t in ns.patterns.traces}
    assert ns.patterns.example_trace_ids
    assert r.request_traces == tuple(traces[i] for i in ns.patterns.example_trace_ids)


@pytest.mark.criterion("TASK-010:C1")
@pytest.mark.req("FR-05", "OUT-12")
def test_top_level_keys_in_order(impl, chain):
    d = impl("report", "to_json_dict")(chain.report)
    assert list(d) == TOP_KEYS
    assert len(d) == 15 and "summary_source" not in d
    assert d["schema_version"] == REPORT_SCHEMA_VERSION


@pytest.mark.criterion("TASK-010:C2")
@pytest.mark.req("OUT-12")
def test_serialization_rule(impl, chain):
    d = impl("report", "to_json_dict")(chain.report)
    assert_same_json(d, ref_json(chain.report))
    f0 = d["key_findings"][0]
    assert TS_RE.match(f0["window"]["start"]) and TS_RE.match(f0["window"]["end"])
    assert isinstance(f0["severity"], str) and isinstance(f0["role"], str)
    gw_lat = [
        m
        for f in chain.report.key_findings
        for m in f.evidence.metrics
        if m.metric == "latency_ms" and m.max == 3000.12345
    ]
    assert gw_lat, "findings must include a metric with max=3000.12345 (fixture)"
    maxima = [m["max"] for f in d["key_findings"] for m in f["evidence"]["metrics"] if m["metric"] == "latency_ms"]
    assert 3000.123 in maxima and 3000.12345 not in maxima


@pytest.mark.criterion("TASK-010:C2")
@pytest.mark.req("OUT-12")
def test_render_json_text(impl, chain):
    to_dict = impl("report", "to_json_dict")
    text = impl("report", "render_json")(chain.report)
    assert text == json.dumps(to_dict(chain.report), ensure_ascii=False, indent=2) + "\n"
    assert json.loads(text) == to_dict(chain.report)


@pytest.mark.criterion("TASK-010:C3")
@pytest.mark.req("OUT-10")
def test_findings_and_hypotheses_carry_evidence(impl, chain):
    d = impl("report", "to_json_dict")(chain.report)
    assert d["key_findings"]
    for f in d["key_findings"]:
        for key in ("severity", "role", "count", "window"):
            assert key in f
        samples = f["evidence"]["samples"]
        assert 1 <= len(samples) <= 3
        for s in samples:
            assert isinstance(s["line_no"], int) and isinstance(s["text"], str)
    hyps = d["probable_causes"]["primary"] + d["probable_causes"]["alternatives"]
    assert hyps
    for h in hyps:
        assert h["confidence"] in ("high", "medium", "low")
        assert "supporting" in h and "contradicting" in h
        # 2.0: an unconfirmed LOAD_SPIKE may have only evidence against
        assert h["supporting"] or h["contradicting"]


@pytest.mark.criterion("TASK-010:C4")
@pytest.mark.req("OUT-13")
def test_severity_scale_matches_criteria(impl, chain):
    assert chain.report.severity_scale == tuple(SeverityCriterion(s, t) for s, t in SEVERITY_CRITERIA)
    d = impl("report", "to_json_dict")(chain.report)
    assert d["severity_scale"] == [{"severity": s.value, "criteria": t} for s, t in SEVERITY_CRITERIA]


@pytest.mark.criterion("TASK-010:C5")
@pytest.mark.req("FR-01", "FR-05")
def test_source_stats_and_parse_failures():
    lines = read_log_lines(CHAIN_LOG)
    garbage = [f"#corrupt-{i:02d}# " + ("x" * 300 if i % 3 == 0 else "partial") for i in range(25)]
    mixed = []
    for i, line in enumerate(lines):
        mixed.append(line)
        if i % 3 == 0 and garbage:
            mixed.append(garbage.pop(0))
    mixed += garbage
    ns = stage_report(mixed)
    src = ns.report.source
    assert src.total_lines == len(mixed)
    assert src.failed_lines == 26
    assert src.total_lines == src.blank_lines + src.parsed_lines + src.failed_lines
    assert src.parsed_lines == src.noise_lines + src.anomalous_lines + src.continuation_lines
    assert src.parsed_lines == len(ns.parse.records)
    assert list(src.services) == sorted({r.service for r in ns.parse.records})
    expected = sorted(f.line_no for f in ns.parse.failures)[:20]
    views = ns.report.parse_failures
    assert [v.line_no for v in views] == expected
    for v in views:
        assert isinstance(v.reason, ParseFailureReason)
        assert len(v.excerpt) <= 240
    assert any(len(mixed[v.line_no - 1]) > 240 for v in views)


@pytest.mark.criterion("TASK-010:C6")
@pytest.mark.req("DEL-09")
def test_render_json_deterministic_and_clean(impl, tmp_path):
    lines = read_log_lines(CHAIN_LOG)
    a = stage_report(lines, file_name="incident_chain.log")
    b = stage_report(lines, file_name="incident_chain.log")
    ja = impl("report", "render_json")(a.report)
    jb = impl("report", "render_json")(b.report)
    assert ja == jb
    assert ja == impl("report", "render_json")(a.report)
    assert datetime.now().strftime("%Y-%m-%d") not in ja
    for path in {str(PROJECT_ROOT), str(tmp_path), str(CHAIN_LOG.parent)}:
        assert path not in ja
    host = socket.gethostname()
    if len(host) >= 6:
        assert host not in ja
    assert json.loads(ja)["source"]["file_name"] == "incident_chain.log"
