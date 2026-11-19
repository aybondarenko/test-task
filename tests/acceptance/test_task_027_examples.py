"""TASK-027: sample output in examples/, links from README, no links to internal development artifacts."""

from __future__ import annotations

import re

import pytest
from acc_support import ATTACHED_LOG, PROJECT_ROOT, impl

EXAMPLES = PROJECT_ROOT / "examples"
NAMES = ("report.md", "report-appendix.md", "report.json")
ARTIFACT_REFS = ("backlog/", "verification/", ".harness", "state/progress", "`state/`", "traceability.")


@pytest.mark.criterion("TASK-027:C1")
@pytest.mark.req("DEL-13")
def test_examples_equal_fresh_output(tmp_path, monkeypatch, capsys):
    for name in NAMES:
        assert (EXAMPLES / name).is_file(), name
    monkeypatch.chdir(PROJECT_ROOT)
    out_dir = tmp_path / "fresh"
    code = impl("cli", "main")([ATTACHED_LOG.name, "--out-dir", str(out_dir), "--quiet"])
    capsys.readouterr()
    assert code == 0
    for name in NAMES:
        assert (EXAMPLES / name).read_bytes() == (out_dir / name).read_bytes(), name


def md_links(text):
    return re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text)


@pytest.mark.criterion("TASK-027:C2")
@pytest.mark.req("DEL-13", "DEL-02")
def test_readme_links_to_examples():
    links = md_links((PROJECT_ROOT / "README.md").read_text(encoding="utf-8"))
    for name in NAMES:
        target = f"examples/{name}"
        found = [x for x in links if x.lstrip("./") == target]
        assert found, f"no link to {target}: {links}"
        for x in found:
            assert not re.match(r"^[a-z]+://|^/", x), x
            assert (PROJECT_ROOT / x).resolve().is_file(), x


@pytest.mark.criterion("TASK-027:C3")
@pytest.mark.req("DEL-14")
def test_no_references_to_harness_artifacts():
    files = [PROJECT_ROOT / "README.md", PROJECT_ROOT / "pyproject.toml"]
    files += [EXAMPLES / n for n in NAMES]
    files += sorted((PROJECT_ROOT / "src" / "test_task").rglob("*.py"))
    hits = []
    for p in files:
        text = p.read_text(encoding="utf-8")
        hits += [f"{p.relative_to(PROJECT_ROOT)}: {n}" for n in ARTIFACT_REFS if n in text]
    assert hits == [], hits
