from pathlib import Path

import pytest

from test_task.contracts.common import Role, SignalKind
from test_task.contracts.pipeline import Report, SourceRef
from test_task.pipeline import analyze
from test_task.report import render_json

SRC = SourceRef("x.log", "0" * 64)


def test_empty_input() -> None:
    report = analyze([], SRC)
    assert report.key_findings == ()
    assert report.uncertainty


def test_garbage_and_determinism() -> None:
    lines = ["???\n", "!!!"]
    a = analyze(lines, SRC)
    assert a.source.failed_lines == 2
    assert a == analyze(["???", "!!!"], SRC)
    assert render_json(a) == render_json(analyze(lines, SRC))


FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _run(name: str) -> Report:
    text = (FIX / name).read_text(encoding="utf-8")
    return analyze(text.splitlines(), SourceRef(name, "0" * 64))


@pytest.mark.parametrize(
    "name",
    [
        "qa14_linked_episodes.log",
        "qa14_provider_reset.log",
        "qa14_starting_up.log",
        "qa14_connections_held.log",
        "incident_chain.log",
    ],
)
def test_fixtures_run_end_to_end(name: str) -> None:
    report = _run(name)
    assert report.source.failed_lines <= 1
    assert any(f.role == Role.CAUSE for f in report.key_findings)
    assert render_json(report) == render_json(_run(name))


def test_linked_episodes_summary() -> None:
    report = _run("qa14_linked_episodes.log")
    assert len(report.timeline.episodes) == 2
    low = report.incident_summary.lower()
    assert "recover" in low and "short" in low


def test_provider_reset_not_db() -> None:
    report = _run("qa14_provider_reset.log")
    for f in report.key_findings:
        if f.db_categories:
            assert "courier-worker" not in f.services
    assert all("courier-worker" not in s.services for s in report.db_signals)


def test_starting_up_restart_only_server() -> None:
    report = _run("qa14_starting_up.log")
    for f in report.key_findings:
        if SignalKind.RESTART in set(f.kinds) | {f.kind}:
            assert f.services == ("catalog-db",)
