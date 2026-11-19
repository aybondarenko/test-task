import dataclasses

from test_hypotheses import chain, noise
from test_recommendations import run

from test_task.contracts.common import SEVERITY_CRITERIA
from test_task.contracts.parser import ParseFailure, ParseFailureReason
from test_task.contracts.report import (
    MD_APPENDIX_TITLES,
    MD_MAIN_EXTRA_TITLES,
    MD_SECTION_TITLES,
    Report,
    ReportInputs,
)
from test_task.recommendations import recommend
from test_task.report import (
    build_report,
    render_appendix_markdown,
    render_json,
    render_markdown,
    render_stdout,
)
from test_task.timeline import compare_windows


def make(specs, extra_failure: bool = False) -> Report:
    parse, pats, tl, an, cfg = run(specs)
    if extra_failure:
        failure = ParseFailure(
            line_no=999,
            raw="bad | line\twith   spaces",
            reason=ParseFailureReason.BAD_TIMESTAMP,
        )
        parse = dataclasses.replace(parse, failures=(failure,))
    recs = recommend(parse, pats, tl, an, cfg)
    inputs = ReportInputs(
        file_name="dir/x.log",
        sha256="0" * 64,
        parse=parse,
        patterns=pats,
        timeline=tl,
        metric_comparison=(),
        error_rates=(),
        findings=an.findings,
        probable_causes=an.causes,
        impact=an.impact,
        recommendations=recs,
        incident_summary="Summary text.",
    )
    return build_report(inputs, cfg)


def test_markdown_full_chain() -> None:
    report = make(chain(), extra_failure=True)
    md = render_markdown(report)
    h2 = [x[3:] for x in md.split("\n") if x.startswith("## ")]
    assert h2 == list(MD_SECTION_TITLES) + list(MD_MAIN_EXTRA_TITLES)
    assert "report-appendix.md" in md.splitlines()[-1]
    app = render_appendix_markdown(report)
    assert app.startswith("# Incident Report Appendix: x.log\n")
    assert [x[3:] for x in app.split("\n") if x.startswith("## ")] == list(
        MD_APPENDIX_TITLES
    )
    assert app.endswith("\n") and not app.endswith("\n\n")
    assert app == render_appendix_markdown(report)
    assert md.startswith("# Incident Report: x.log\n")
    assert md.endswith("\n") and not md.endswith("\n\n")
    assert "bad \\| line" not in app  # excerpt is not a table cell
    assert "bad | line with spaces" in app
    for _, text in SEVERITY_CRITERIA:
        assert " ".join(text.split()) in md
    assert "Primary candidates:" in md
    assert "Propagation:" in md


def test_markdown_empty_report() -> None:
    report = make(noise(600))
    md = render_markdown(report)
    assert "No key findings" in md
    assert "No database signals detected." in md
    assert "No probable cause candidates" in md
    assert "No service impact" in md
    assert "No request traces" in render_appendix_markdown(report)
    assert "No incident episodes were detected." in md
    assert render_markdown(report) == md


def test_markdown_empty_collections() -> None:
    report = make(chain())
    empty = dataclasses.replace(
        report,
        uncertainty=(),
        next_steps=(),
        timeline=dataclasses.replace(report.timeline, log_window=None, baseline=None),
    )
    md = render_markdown(empty)
    assert "No uncertainty items" in md
    assert "No next steps" in md
    assert "Log window: empty input." in md
    assert "Baseline window: not available." in md


def test_baseline_tables() -> None:
    parse, _pats, tl, _an, cfg = run(chain())
    from test_task.baseline import build_baseline, score_events
    from test_task.signals import extract_events

    evs = extract_events(parse.records)
    scored = score_events(evs, build_baseline(evs, cfg), cfg)
    cmp = compare_windows(scored, tl, cfg)
    report = dataclasses.replace(
        make(chain()), metric_comparison=cmp.metrics, error_rates=cmp.error_rates
    )
    md = render_appendix_markdown(report)
    assert "| Service | Metric |" in md
    assert "| Service | Baseline errors |" in md
    assert "omitted" in md
    unchanged = tuple(dataclasses.replace(m, incident=None) for m in cmp.metrics)
    md2 = render_appendix_markdown(
        dataclasses.replace(report, metric_comparison=unchanged, error_rates=())
    )
    assert "No metric has incident values that differ from baseline." in md2
    assert f"{len(unchanged)} of {len(unchanged)}" in md2


def test_stdout_short() -> None:
    report = make(chain())
    out = render_stdout(report, ["a/report.md", "a/report.json"])
    assert len(out.splitlines()) < len(render_markdown(report).splitlines())
    assert "DB signals: " in out
    assert out.index("a/report.md") < out.index("a/report.json")
    empty = render_stdout(make(noise(600)), [])
    assert "No key findings." in empty
    assert "No probable cause candidates." in empty


def test_render_json_still_works() -> None:
    assert render_json(make(chain())).endswith("\n")


def test_long_sample_truncated_and_hypothesis_limits() -> None:
    from test_task.contracts.report import MD_EVIDENCE_MAX_CHARS

    report = make(chain())
    f0 = report.key_findings[0]
    long = dataclasses.replace(f0.evidence.samples[0], text="x" * 400)
    f0 = dataclasses.replace(
        f0, evidence=dataclasses.replace(f0.evidence, samples=(long,) * 4)
    )
    h = dataclasses.replace(
        report.probable_causes.primary[0],
        supporting=("a",) * 5,
        contradicting=("b",) * 5,
    )
    causes = dataclasses.replace(report.probable_causes, primary=(h,))
    r = dataclasses.replace(
        report, key_findings=(f0,) + report.key_findings[1:], probable_causes=causes
    )
    md = render_markdown(r)
    assert "x" * 400 not in md
    lines = [ln for ln in md.split("\n") if "xxxx" in ln]
    assert len(lines) == 2
    assert all(ln.endswith("...") for ln in lines)
    assert all(len(ln.split(": ", 1)[1]) <= MD_EVIDENCE_MAX_CHARS for ln in lines)
    assert md.count("  - for: a") == 3 and md.count("  - against: b") == 3
