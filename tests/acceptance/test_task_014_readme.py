"""TASK-014: README: how to run, approach, severity scale."""

from __future__ import annotations

import os
import re
import subprocess

import pytest
from acc_support import NETWORK_GUARD, PROJECT_ROOT, copy_project, md_section

from test_task.contracts.common import SEVERITY_CRITERIA


def readme():
    return (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")


def section(title):
    body = md_section(readme(), title)
    assert body is not None, f"no ## {title} section"
    assert body.strip(), f"section ## {title} is empty"
    return body


def first_command(body):
    """First command of the first code block: without the ``$`` prompt, with ``\\`` continuations joined."""
    m = re.search(r"```[^\n]*\n(.*?)```", body, re.DOTALL)
    assert m, "no code block in the Quick start section"
    cmd, cont = [], False
    for raw in m.group(1).split("\n"):
        line = raw.strip()
        if not cont and (not line or line.startswith("#")):
            continue
        line = line.removeprefix("$ ")
        cont = line.endswith("\\")
        cmd.append(line[:-1] if cont else line)
        if not cont:
            break
    return " ".join(cmd).strip()


@pytest.mark.criterion("TASK-014:C1")
@pytest.mark.req("DEL-02", "DEL-07")
def test_readme_sections_and_usage():
    text = readme()
    for title in ("Quick start", "Usage", "Output", "Approach", "Severity scale"):
        section(title)
    assert re.search(r"3\.9\+?|>=\s*3\.9", text), "Python 3.9+ version not stated"
    assert re.search(r"standard library|stdlib|no (third-party|external) (dependencies|packages)", text, re.IGNORECASE)
    usage = section("Usage")
    for opt in ("--out-dir", "--quiet"):  # --llm was removed in 2.0 (see test_task_017)
        assert opt in usage, opt
    assert re.search(r"LOG_PATH|path/to|<path|\.log", usage), "no instructions for passing your own log path"
    assert "out/report.md" in text and "out/report.json" in text


@pytest.mark.criterion("TASK-014:C2")
@pytest.mark.req("DEL-02", "DEL-04")
def test_quick_start_command_runs_offline(tmp_path):
    cmd = first_command(section("Quick start"))
    assert cmd
    repo = copy_project(tmp_path / "repo")
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    (repo / "src" / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    env.update({"PYTHONPATH": str(guard), "UV_OFFLINE": "1", "PIP_NO_INDEX": "1"})
    proc = subprocess.run(["bash", "-c", cmd], cwd=repo, env=env, capture_output=True, text=True, timeout=600, check=False)
    assert proc.returncode == 0, f"{cmd}\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    assert (repo / "out" / "report.md").is_file()
    assert (repo / "out" / "report.json").is_file()


@pytest.mark.criterion("TASK-014:C3")
@pytest.mark.req("OUT-13")
def test_severity_scale_section():
    body = section("Severity scale")
    flat = " ".join(body.split())
    for sev, criteria in SEVERITY_CRITERIA:
        assert re.search(rf"\b{sev.value}\b", body, re.IGNORECASE), sev.value
        assert " ".join(criteria.split()) in flat, criteria


@pytest.mark.criterion("TASK-014:C4")
@pytest.mark.req("QA-05")
def test_approach_section_explains_heuristics():
    body = section("Approach").lower()
    for word in ("baseline", "episode", "cause", "symptom", "confidence", "uncertainty"):
        assert word in body, word
