"""TASK-013: the test-task CLI writes report.md/report.json and prints a short version.

Since 2.0 there is no ``--llm`` flag (checked by test_task_016, C4); the CLI also writes
report-appendix.md (see test_task_024). The checks for this completed task hold in both states: stdout
is compared with ``render_stdout`` for the report files actually written.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
from acc_support import (
    ATTACHED_LOG,
    CHAIN_LOG,
    PROJECT_ROOT,
    impl,
    read_log_lines,
    sha256_hex,
)

from test_task.contracts.pipeline import SourceRef

PIPELINE_MODULES = [
    "cli",
    "pipeline",
    "parser",
    "signals",
    "baseline",
    "patterns",
    "timeline",
    "causality",
    "recommendations",
    "narrative",
    "report",
]


def run_main(argv, capsys):
    main = impl("cli", "main")
    try:
        code = main(argv)
    except SystemExit as e:  # argparse may exit via SystemExit
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def log_copy(tmp_path):
    dst = tmp_path / "input" / "incident_chain.log"
    dst.parent.mkdir()
    shutil.copy2(CHAIN_LOG, dst)
    return dst


@pytest.mark.criterion("TASK-013:C1")
@pytest.mark.req("DEL-01", "NEG-01", "Q-03")
def test_main_writes_reports_and_prints_summary(log_copy, tmp_path, capsys):
    out_dir = tmp_path / "reports" / "nested"
    code, out, _ = run_main([str(log_copy), "--out-dir", str(out_dir)], capsys)
    assert code == 0
    md_path, json_path = out_dir / "report.md", out_dir / "report.json"
    assert md_path.is_file() and json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["source"]["file_name"] == "incident_chain.log"
    assert data["source"]["sha256"] == sha256_hex(log_copy.read_bytes())
    report = impl("pipeline", "analyze")(
        read_log_lines(log_copy), SourceRef("incident_chain.log", sha256_hex(log_copy.read_bytes()))
    )
    assert json_path.read_text(encoding="utf-8") == impl("report", "render_json")(report)
    assert md_path.read_text(encoding="utf-8") == impl("report", "render_markdown")(report)
    # The path form (absolute/relative/resolved) is not specified: compare stdout with
    # render_stdout, substituting the paths the CLI printed, and check that they are the same files.
    # File order per the cli spec: report.md, [report-appendix.md (2.0)], report.json.
    names = [n for n in ("report.md", "report-appendix.md", "report.json") if (out_dir / n).is_file()]
    marks = [f"ZZPATH{i}ZZ" for i in range(len(names))]
    template = impl("report", "render_stdout")(report, marks).rstrip("\n")
    assert all(mk in template for mk in marks)
    pattern = ""
    seen = set()
    for part in re.split("(" + "|".join(marks) + ")", template):
        if part in marks:
            name = "p" + str(marks.index(part))
            pattern += f"(?P={name})" if name in seen else f"(?P<{name}>.+?)"
            seen.add(name)
        else:
            pattern += re.escape(part)
    m = re.fullmatch(pattern, out.rstrip("\n"), re.DOTALL)
    assert m, f"stdout does not match render_stdout:\n{out}"
    for i, n in enumerate(names):
        assert Path(m.group(f"p{i}")).resolve() == (out_dir / n).resolve()
    assert report.incident_summary in out


@pytest.mark.criterion("TASK-013:C2")
@pytest.mark.req("DEL-06")
def test_default_log_and_out_dir(tmp_path, monkeypatch, capsys):
    shutil.copy2(ATTACHED_LOG, tmp_path / ATTACHED_LOG.name)
    monkeypatch.chdir(tmp_path)
    code, _, _ = run_main([], capsys)
    assert code == 0
    data = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert (tmp_path / "out" / "report.md").is_file()
    assert data["source"]["file_name"] == "it_task_log (2).log"
    assert data["source"]["total_lines"] == 521
    ignored = {ln.strip() for ln in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert ignored & {"out/", "out", "/out/", "/out"}


@pytest.mark.criterion("TASK-013:C3")
@pytest.mark.req("DEL-04")
@pytest.mark.parametrize("case", ["missing-log", "unknown-option", "out-dir-is-file"])
def test_usage_errors_exit_2(log_copy, tmp_path, capsys, case):
    blocker = tmp_path / "occupied"
    blocker.write_text("not a directory", encoding="utf-8")
    argv = {
        "missing-log": [str(tmp_path / "nope" / "missing.log"), "--out-dir", str(tmp_path / "o1")],
        "unknown-option": [str(log_copy), "--bogus-option"],
        "out-dir-is-file": [str(log_copy), "--out-dir", str(blocker)],
    }[case]
    code, _, err = run_main(argv, capsys)
    assert code == 2
    assert err.strip()
    assert "Traceback" not in err


@pytest.mark.criterion("TASK-013:C4")
@pytest.mark.req("DEL-09")
def test_repeatable_output_and_quiet(log_copy, tmp_path, capsys):
    out_dir = tmp_path / "o"
    argv = [str(log_copy), "--out-dir", str(out_dir)]
    code1, out1, _ = run_main(argv, capsys)
    first = {n: (out_dir / n).read_bytes() for n in ("report.md", "report.json")}
    code2, out2, _ = run_main(argv, capsys)
    second = {n: (out_dir / n).read_bytes() for n in ("report.md", "report.json")}
    assert code1 == code2 == 0
    assert first == second
    assert out1 == out2 and out1.strip()
    for blob in first.values():
        text = blob.decode("utf-8")
        assert "\r" not in text and text.endswith("\n")
    quiet_dir = tmp_path / "q"
    # argument order is not fixed by the usage line: options before LOG_PATH are also allowed
    code3, out3, _ = run_main(["--quiet", "--out-dir", str(quiet_dir), str(log_copy)], capsys)
    assert code3 == 0
    assert out3 == ""
    assert (quiet_dir / "report.md").read_bytes() == first["report.md"]
    assert (quiet_dir / "report.json").read_bytes() == first["report.json"]


@pytest.mark.criterion("TASK-013:C5")
@pytest.mark.req("DEL-08")
def test_offline_without_llm_flag(log_copy, tmp_path, capsys, network_blocked):
    code, _, _ = run_main([str(log_copy), "--out-dir", str(tmp_path / "off")], capsys)
    assert code == 0
    assert network_blocked == []
    assert (tmp_path / "off" / "report.json").is_file()


@pytest.mark.criterion("TASK-013:C6")
@pytest.mark.req("DEL-01", "DEL-04", "NEG-01")
def test_python_dash_m_entry_point(log_copy, tmp_path):
    out_dir = tmp_path / "pm"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "test_task", str(log_copy), "--out-dir", str(out_dir)],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert (out_dir / "report.md").is_file() and (out_dir / "report.json").is_file()


@pytest.mark.criterion("TASK-013:C6")
@pytest.mark.req("DEL-01", "NEG-01")
def test_package_main_reexports_cli_main(log_copy, tmp_path, capsys):
    pkg = importlib.import_module("test_task")
    cli = importlib.import_module("test_task.cli")
    assert pkg.main is cli.main
    code = pkg.main([str(log_copy), "--out-dir", str(tmp_path / "pkg"), "--quiet"])
    assert isinstance(code, int) and code == 0


@pytest.mark.criterion("TASK-013:C7")
@pytest.mark.req("Q-01")
def test_stdlib_only_imports():
    allowed = {"test_task", "__future__"}
    files = []
    for name in PIPELINE_MODULES:
        mod = importlib.import_module(f"test_task.{name}")
        files.append(Path(mod.__file__))
    pkg_dir = files[0].parent
    files += [p for p in pkg_dir.rglob("*.py") if p not in files]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops = [node.module.split(".")[0]]
            else:
                continue
            for top in tops:
                assert top in allowed or is_stdlib(top), f"{path.name}: non-stdlib import: {top}"
    assert project_dependencies(PROJECT_ROOT / "pyproject.toml") == []


def is_stdlib(name):
    """Standard library module: ``sys.stdlib_module_names`` (3.10+), otherwise (3.9) built in
    or located in the stdlib directory but not in site-packages."""
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return name in names
    if name in sys.builtin_module_names:
        return True
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        return False
    if spec.origin in ("built-in", "frozen"):
        return True
    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    origin = Path(spec.origin).resolve()
    return stdlib in origin.parents and "site-packages" not in origin.parts


def project_dependencies(path):
    """``[project].dependencies`` from pyproject via simple section parsing (no tomllib on Python 3.9)."""
    text = path.read_text(encoding="utf-8")
    section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    assert section, "no [project] section"
    deps = re.search(r"^dependencies\s*=\s*\[(.*?)\]", section.group(1), re.MULTILINE | re.DOTALL)
    if deps is None:
        return []
    body = re.sub(r"#[^\n]*", "", deps.group(1))
    return re.findall(r"[\"']([^\"']+)[\"']", body)
