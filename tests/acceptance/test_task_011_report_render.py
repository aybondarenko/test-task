"""TASK-011: report.md with sections in the required order, and the short stdout.

Since 2.0 the detailed sections moved to report-appendix.md (see test_task_024). Tests of this completed task
check what holds both before and after the split: required sections in order, Timeline and
Severity Scale in the main report, Input Statistics in report.md or in the appendix. The strict structure
of both files is covered by test_task_024.
"""

from __future__ import annotations

import re

import pytest
from acc_support import (
    CHAIN_LOG,
    CYRILLIC,
    h2_titles,
    is_subsequence,
    markdown_with_appendix,
    md_section,
    normal_block,
    overconfident,
    read_log_lines,
    stage_report,
)

from test_task.contracts.common import SEVERITY_CRITERIA
from test_task.contracts.report import MD_MAIN_EXTRA_TITLES, MD_SECTION_TITLES

MAIN_TITLES = list(MD_SECTION_TITLES) + list(MD_MAIN_EXTRA_TITLES)
PATHS = ["out/report.md", "out/report.json"]


@pytest.fixture(scope="module")
def chain():
    return stage_report(read_log_lines(CHAIN_LOG), file_name="incident_chain.log")


@pytest.fixture(scope="module")
def quiet():
    return stage_report(normal_block(0, 600), file_name="quiet.log")


def finding_block(md, header):
    lines = md.split("\n")
    start = lines.index(header) + 1
    body = []
    for line in lines[start:]:
        if re.match(r"^#{1,3} ", line):
            break
        body.append(line)
    return "\n".join(body)


@pytest.mark.criterion("TASK-011:C1")
@pytest.mark.req("FR-05", "OUT-08")
@pytest.mark.parametrize("which", ["chain", "quiet"])
def test_markdown_skeleton(impl, chain, quiet, which):
    ns = chain if which == "chain" else quiet
    md = impl("report", "render_markdown")(ns.report)
    assert md.split("\n", 1)[0] == f"# Incident Report: {ns.report.source.file_name}"
    h2 = h2_titles(md)
    assert h2[: len(MD_SECTION_TITLES)] == list(MD_SECTION_TITLES), h2
    assert is_subsequence(MD_MAIN_EXTRA_TITLES, h2[len(MD_SECTION_TITLES) :]), h2
    assert len(h2) == len(set(h2)), h2
    assert "\r" not in md
    assert md.endswith("\n") and not md.endswith("\n\n")


@pytest.mark.criterion("TASK-011:C2")
@pytest.mark.req("OUT-09", "NEG-05", "OUT-08")
def test_findings_rendered_with_evidence(impl, chain):
    md = impl("report", "render_markdown")(chain.report)
    findings = chain.report.key_findings
    assert findings
    assert len([line for line in md.split("\n") if line.startswith("### F")]) == len(findings)
    for f in findings:
        header = f"### {f.id} [{f.severity.value}] ({f.role.value}) {f.title}"
        assert header in md.split("\n"), header
        block = finding_block(md, header)
        assert re.search(rf"\b{f.count}\b", block), f"{f.id}: no event count"
        assert f.window.start.strftime("%H:%M") in block, f"{f.id}: no time window"
        # 2.0: report.md shows up to MD_MAX_SAMPLES_PER_FINDING samples; at least one has a line number
        assert any(re.search(rf"\b{s.line_no}\b", block) for s in f.evidence.samples), f"{f.id}: no line number"


@pytest.mark.criterion("TASK-011:C2")
@pytest.mark.req("NEG-05")
def test_empty_sections_have_explicit_text(impl, quiet):
    assert quiet.report.key_findings == () and quiet.report.db_signals == ()
    assert quiet.report.request_traces == ()
    md = impl("report", "render_markdown")(quiet.report)
    for title in MAIN_TITLES:
        body = md_section(md, title)
        assert body is not None and body.strip(), f"section {title} is empty"


@pytest.mark.criterion("TASK-011:C3")
@pytest.mark.req("OUT-13", "QA-04")
def test_severity_scale_and_input_statistics(impl, chain):
    md = impl("report", "render_markdown")(chain.report)
    scale = md_section(md, "Severity Scale")
    flat = " ".join(scale.split())
    for sev, text in SEVERITY_CRITERIA:
        assert sev.value in scale.lower()
        assert " ".join(text.split()) in flat
    stats = md_section(markdown_with_appendix(chain.report), "Input Statistics")
    assert stats is not None, "no Input Statistics section in report.md or in the appendix"
    src = chain.report.source
    for value in (src.total_lines, src.parsed_lines, src.failed_lines, src.noise_lines, src.anomalous_lines):
        assert re.search(rf"\b{value}\b", stats), value
    for label in ("total", "noise", "anomal"):
        assert label in stats.lower()


@pytest.mark.criterion("TASK-011:C4")
@pytest.mark.req("Q-03")
def test_stdout_short_version(impl, chain):
    out = impl("report", "render_stdout")(chain.report, PATHS)
    md = impl("report", "render_markdown")(chain.report)
    assert chain.report.incident_summary in out
    lines = out.split("\n")
    for f in chain.report.key_findings:
        hits = [x for x in lines if f.id in x and f.severity.value in x and f.title in x]
        assert len(hits) == 1, f.id
    for p in PATHS:
        assert p in out
    assert out.index(PATHS[0]) < out.index(PATHS[1])
    assert len(out.splitlines()) < len(md.splitlines())


@pytest.mark.criterion("TASK-011:C5")
@pytest.mark.req("Q-05", "QA-04")
@pytest.mark.parametrize("which", ["chain", "quiet"])
def test_english_no_overconfidence_deterministic(impl, chain, quiet, which):
    r = (chain if which == "chain" else quiet).report
    renders = {
        "md": lambda: impl("report", "render_markdown")(r),
        "json": lambda: impl("report", "render_json")(r),
        "stdout": lambda: impl("report", "render_stdout")(r, PATHS),
    }
    for name, render in renders.items():
        text = render()
        assert text == render(), name
        assert not CYRILLIC.search(text), name
        assert not overconfident(text), (name, overconfident(text))
