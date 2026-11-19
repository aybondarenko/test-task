"""TASK-017: README and pyproject without the LLM runtime, the draft note, or a template description."""

from __future__ import annotations

import re

import pytest
from acc_support import PROJECT_ROOT

USAGE_FLAGS = {"--out-dir", "--quiet"}
PROJECT_REFERENCE = [
    'name = "test-task"',
    'version = "0.1.0"',
    'readme = "README.md"',
    "authors = [",
    '{ name = "Anton Bondarenko", email = "a.bondarenko@unlimit.com" }',
    "]",
    'requires-python = ">=3.9"',
    "dependencies = []",
]
"""The [project] section of pyproject.toml without the description line, as of plan approval (C4: unchanged)."""


def readme():
    return (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")


def without_ai_usage(text):
    """README without the ``## AI usage`` section (up to the next second-level heading)."""
    out, skip, fence = [], False, False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            skip = line.strip().lower() == "## ai usage"
        if not skip:
            out.append(line)
    return "\n".join(out)


def project_section(text):
    m = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    assert m, "no [project] section"
    return m.group(1)


def project_description():
    body = project_section((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    m = re.search(r'^description\s*=\s*(?:"([^"\n]*)"|\'([^\'\n]*)\')\s*$', body, re.MULTILINE)
    assert m, "no description line in [project]"
    return m.group(1) if m.group(1) is not None else m.group(2)


@pytest.mark.criterion("TASK-017:C1")
@pytest.mark.req("DEL-12")
def test_readme_has_no_llm_flag_and_plain_usage():
    text = readme()
    assert "--llm" not in text
    usage_lines = [line for line in text.split("\n") if "[LOG_PATH]" in line]
    assert usage_lines, "no usage line with [LOG_PATH]"
    assert any("[LOG_PATH] [--out-dir DIR] [--quiet]" in line for line in usage_lines), usage_lines
    for line in usage_lines:
        flags = set(re.findall(r"\[(--[A-Za-z][\w-]*)", line))
        assert flags <= USAGE_FLAGS, line


@pytest.mark.criterion("TASK-017:C1")
@pytest.mark.req("DEL-12")
def test_no_llm_outside_ai_usage():
    text = readme()
    assert re.search(r"^## AI usage\s*$", text, re.MULTILINE), "the ## AI usage section must remain"
    rest = without_ai_usage(text)
    assert "llm" not in rest.lower(), [ln for ln in rest.split("\n") if "llm" in ln.lower()]


@pytest.mark.criterion("TASK-017:C3")
@pytest.mark.req("DEL-10")
def test_no_draft_note():
    text = readme()
    assert "Draft written from the project journal" not in text
    assert "to be confirmed or amended by the author" not in text


@pytest.mark.criterion("TASK-017:C4")
@pytest.mark.req("DEL-11")
def test_project_description():
    desc = project_description()
    assert desc.strip()
    assert desc.strip() != "Add your description here"
    assert "log" in desc.lower()
    body = project_section((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    rest = [
        ln.strip()
        for ln in body.split("\n")
        if ln.strip() and not ln.strip().startswith("#") and not re.match(r"^description\s*=", ln.strip())
    ]
    assert rest == PROJECT_REFERENCE, rest
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    scripts = re.search(r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    assert scripts and re.search(r'^test-task\s*=\s*"test_task:main"\s*$', scripts.group(1), re.MULTILINE)
