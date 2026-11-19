"""TASK-016: the LLM path is removed from the runtime."""

from __future__ import annotations

import importlib
import inspect
import json
import re
import shutil

import pytest
from acc_support import (
    ATTACHED_LOG,
    CHAIN_LOG,
    PROJECT_ROOT,
    impl,
    read_log_lines,
    sha256_hex,
    stage_facts,
)

from test_task.contracts.pipeline import SourceRef

REMOVED_NARRATIVE_NAMES = (
    "build_narrative",
    "NarrativeResult",
    "SummarySource",
    "LlmClient",
    "LlmFailure",
    "LlmSummaryResponse",
    "LLM_RESPONSE_KEYS",
)
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
PKG_DIR = PROJECT_ROOT / "src" / "test_task"
SRC = SourceRef("it_task_log (2).log", sha256_hex(ATTACHED_LOG.read_bytes()))


def run_main(argv, capsys):
    try:
        code = impl("cli", "main")(argv)
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


def no_llm_text(text):
    low = text.lower()
    return "summary_source" not in low and "summary source" not in low and "llm" not in low


@pytest.mark.criterion("TASK-016:C1")
@pytest.mark.req("DEL-12", "Q-02")
def test_narrative_has_no_llm_names():
    narrative = importlib.import_module("test_task.narrative")
    assert callable(narrative.write_summary) and callable(narrative.validate_summary)
    present = [n for n in REMOVED_NARRATIVE_NAMES if hasattr(narrative, n)]
    assert present == [], present


LLM_WORD = re.compile(r"(?i)(?<![a-z])llm(?![a-z])")


@pytest.mark.criterion("TASK-016:C1")
@pytest.mark.req("DEL-12", "Q-02")
def test_package_sources_have_no_llm():
    # Type checking against the 2.0 interfaces is done by the build_types gate; this test does not spawn a nested uv run.
    importlib.import_module("test_task")
    for name in ("narrative", "pipeline", "report", "cli"):
        importlib.import_module(f"test_task.{name}")
    files = sorted(PKG_DIR.glob("*.py"))
    assert {p.name for p in files} >= {"narrative.py", "pipeline.py", "report.py", "cli.py"}
    hits = []
    for p in files:
        text = p.read_text(encoding="utf-8")
        hits += [f"{p.name}: {m.group(0)}" for m in LLM_WORD.finditer(text)]
        if "summary_source" in text:
            hits.append(f"{p.name}: summary_source")
    assert hits == [], hits


@pytest.mark.criterion("TASK-016:C2")
@pytest.mark.req("DEL-12", "Q-02", "AI-05")
@pytest.mark.parametrize("which", ["attached", "empty"])
def test_summary_is_write_summary(which):
    lines = read_log_lines(ATTACHED_LOG) if which == "attached" else []
    src = SRC if which == "attached" else SourceRef("empty.log", sha256_hex(b""))
    report = impl("pipeline", "analyze")(lines, src)
    expected = impl("narrative", "write_summary")(stage_facts(lines).facts)
    assert report.incident_summary == expected
    assert not hasattr(report, "summary_source")


@pytest.mark.criterion("TASK-016:C2")
@pytest.mark.req("DEL-12", "Q-02")
def test_analyze_rejects_llm_argument():
    analyze = impl("pipeline", "analyze")
    lines = read_log_lines(CHAIN_LOG)
    src = SourceRef("incident_chain.log", sha256_hex(CHAIN_LOG.read_bytes()))
    with pytest.raises(TypeError):
        analyze(lines, src, None, None)
    with pytest.raises(TypeError):
        analyze(lines, src, llm=None)
    assert "llm" not in inspect.signature(analyze).parameters


@pytest.mark.criterion("TASK-016:C3")
@pytest.mark.req("DEL-12")
@pytest.mark.parametrize("which", ["attached", "chain", "empty"])
def test_outputs_have_no_summary_source(which):
    if which == "empty":
        lines, src = [], SourceRef("empty.log", sha256_hex(b""))
    elif which == "chain":
        lines, src = read_log_lines(CHAIN_LOG), SourceRef("incident_chain.log", sha256_hex(CHAIN_LOG.read_bytes()))
    else:
        lines, src = read_log_lines(ATTACHED_LOG), SRC
    report = impl("pipeline", "analyze")(lines, src)
    d = impl("report", "to_json_dict")(report)
    assert "summary_source" not in d
    assert d["schema_version"] == "2.0"
    assert list(d) == TOP_KEYS
    texts = {
        "json": impl("report", "render_json")(report),
        "md": impl("report", "render_markdown")(report),
        "stdout": impl("report", "render_stdout")(report, ["out/report.md", "out/report.json"]),
    }
    for name, text in texts.items():
        assert no_llm_text(text), name
    assert "summary_source" not in json.loads(texts["json"])


@pytest.mark.criterion("TASK-016:C4")
@pytest.mark.req("DEL-12", "Q-02")
def test_cli_rejects_llm_flag(tmp_path, capsys):
    out_dir = tmp_path / "rejected"
    code, _, err = run_main([str(CHAIN_LOG), "--llm", "--out-dir", str(out_dir)], capsys)
    assert code == 2
    assert err.strip() and "Traceback" not in err
    written = [p.name for p in out_dir.glob("*")] if out_dir.exists() else []
    assert not [n for n in written if n.startswith("report")], written


@pytest.mark.criterion("TASK-016:C4")
@pytest.mark.req("DEL-12", "Q-02")
def test_cli_help_and_normal_run(tmp_path, capsys):
    code, out, err = run_main(["--help"], capsys)
    assert code == 0
    assert (out + err).strip()
    assert "llm" not in (out + err).lower()
    assert "--out-dir" in out + err
    out_dir = tmp_path / "ok"
    code, _, _ = run_main([str(CHAIN_LOG), "--out-dir", str(out_dir)], capsys)
    assert code == 0
    assert (out_dir / "report.md").is_file() and (out_dir / "report.json").is_file()


@pytest.mark.criterion("TASK-016:C5")
@pytest.mark.req("DEL-08", "DEL-09")
def test_offline_and_byte_identical(tmp_path, capsys, network_blocked):
    lines = read_log_lines(ATTACHED_LOG)
    a = impl("pipeline", "analyze")(lines, SRC)
    b = impl("pipeline", "analyze")(list(lines), SRC)
    for name in ("render_json", "render_markdown"):
        assert impl("report", name)(a) == impl("report", name)(b), name
    log = tmp_path / ATTACHED_LOG.name
    shutil.copy2(ATTACHED_LOG, log)
    blobs = []
    for run in ("one", "two"):
        d = tmp_path / run
        code, _, _ = run_main([str(log), "--out-dir", str(d), "--quiet"], capsys)
        assert code == 0
        files = sorted(p.name for p in d.iterdir())
        assert "report.md" in files and "report.json" in files
        blobs.append({n: (d / n).read_bytes() for n in files})
    assert blobs[0] == blobs[1]
    assert network_blocked == []
    assert re.search(r"\S", blobs[0]["report.md"].decode("utf-8"))
