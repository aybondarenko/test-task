"""TASK-026: Python 3.9 support: resolvable dev dependencies and compatible code."""

from __future__ import annotations

import ast
import os
import re
import subprocess

import pytest
from acc_support import NETWORK_GUARD, PROJECT_ROOT, copy_project, md_section

PY39 = {"python_version": "3.9", "python_full_version": "3.9.18"}
PY312 = {"python_version": "3.12", "python_full_version": "3.12.8"}
PKG = PROJECT_ROOT / "src" / "test_task"


def pyproject():
    return (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def toml_section(text, name):
    m = re.search(rf"^\[{re.escape(name)}\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else None


def toml_list(body, key):
    if body is None:
        return None
    m = re.search(rf"^{re.escape(key)}\s*=\s*\[(.*?)\]", body, re.MULTILINE | re.DOTALL)
    if m is None:
        return None
    items = re.sub(r"#[^\n]*", "", m.group(1))
    return [a or b for a, b in re.findall(r'"([^"]*)"|\'([^\']*)\'', items)]


def applies(req, env):
    return req.marker is None or req.marker.evaluate(env)


@pytest.mark.criterion("TASK-026:C1")
@pytest.mark.req("DEL-15")
def test_pyproject_resolves_for_python39():
    from packaging.markers import Marker
    from packaging.requirements import Requirement

    text = pyproject()
    project = toml_section(text, "project")
    assert project is not None
    assert re.search(r'^requires-python\s*=\s*">=3\.9"\s*$', project, re.MULTILINE), "requires-python"
    envs = toml_list(toml_section(text, "tool.uv"), "environments")
    if envs is not None:
        assert any(Marker(e).evaluate(PY39) for e in envs), envs
        assert any(Marker(e).evaluate(PY312) for e in envs), envs
    dev = toml_list(toml_section(text, "dependency-groups"), "dev")
    assert dev, "no [dependency-groups] dev"
    reqs = [Requirement(x) for x in dev]
    for name in ("pytest", "pytest-cov"):
        on39 = [r for r in reqs if r.name.lower() == name and applies(r, PY39)]
        assert on39, f"{name} does not install on 3.9"
        assert [r for r in reqs if r.name.lower() == name and applies(r, PY312)], name
    mypy = [r for r in reqs if r.name.lower() == "mypy"]
    assert mypy and all(not applies(r, PY39) for r in mypy), [str(r) for r in mypy]
    assert any(applies(r, PY312) for r in mypy)
    proc = subprocess.run(["uv", "lock", "--check"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600, check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]


def py_files():
    files = sorted(PKG.rglob("*.py"))
    files += sorted((PROJECT_ROOT / "tests" / "unit").glob("*.py"))
    files += sorted((PROJECT_ROOT / "tests" / "acceptance").glob("*.py"))
    return files


def call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def violations_39(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node)
            kws = {k.arg for k in node.keywords}
            if name == "zip" and "strict" in kws:
                out.append("zip(strict=)")
            if name == "dataclass" and kws & {"slots", "kw_only"}:
                out.append("dataclass(slots/kw_only)")
            if name == "pairwise":
                out.append("pairwise")
            if (
                name in ("isinstance", "issubclass")
                and len(node.args) == 2
                and isinstance(node.args[1], ast.BinOp)
                and isinstance(node.args[1].op, ast.BitOr)
            ):
                out.append(f"{name}(x, A | B)")
        if isinstance(node, ast.Attribute) and node.attr == "pairwise":
            out.append("itertools.pairwise")
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "itertools"
            and any(a.name == "pairwise" for a in node.names)
        ):
            out.append("from itertools import pairwise")
    return out


def has_annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            every = args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]
            if node.returns is not None or any(a.annotation is not None for a in every):
                return True
    return False


def has_future_annotations(tree):
    return any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__" and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )


@pytest.mark.criterion("TASK-026:C3")
@pytest.mark.req("DEL-15", "Q-01")
def test_sources_are_python39_compatible():
    names = {p.name for p in PKG.glob("*.py")}
    assert {"cli.py", "pipeline.py", "parser.py", "signals.py", "report.py"} <= names, names
    problems = []
    for path in py_files():
        src = path.read_text(encoding="utf-8")
        rel = path.relative_to(PROJECT_ROOT)
        try:
            tree = ast.parse(src, filename=str(path), feature_version=(3, 9))
        except SyntaxError as e:
            problems.append(f"{rel}: syntax 3.9: {e}")
            continue
        if "acceptance" not in path.parts:
            problems += [f"{rel}: {v}" for v in violations_39(tree)]
        if path.parent == PKG and has_annotations(tree) and not has_future_annotations(tree):
            problems.append(f"{rel}: missing from __future__ import annotations")
    assert problems == [], "\n".join(problems)


def first_command(body):
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


@pytest.mark.criterion("TASK-026:C5")
@pytest.mark.req("DEL-15", "DEL-07")
def test_readme_python39_and_quick_start(tmp_path):
    text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Python 3.9+" in text
    assert "uv run --python 3.9 pytest" in text
    body = md_section(text, "Quick start")
    assert body is not None
    cmd = first_command(body)
    repo = copy_project(tmp_path / "repo")
    guard = tmp_path / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    env.update({"PYTHONPATH": str(guard), "UV_OFFLINE": "1", "PIP_NO_INDEX": "1"})
    proc = subprocess.run(["bash", "-c", cmd], cwd=repo, env=env, capture_output=True, text=True, timeout=600, check=False)
    assert proc.returncode == 0, f"{cmd}\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    assert (repo / "out" / "report.md").is_file() and (repo / "out" / "report.json").is_file()
