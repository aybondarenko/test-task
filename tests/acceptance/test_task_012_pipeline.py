"""TASK-012: end-to-end deterministic analyze.

Since 2.0 there is no ``llm`` parameter and no ``summary_source``; the Incident Summary comes only from
the deterministic ``write_summary``. The LLM parts of C1/C5 are not tested (dropped in a later revision).
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import SimpleNamespace

import pytest
from acc_support import (
    ATTACHED_LOG,
    h2_titles,
    impl,
    is_subsequence,
    read_log_lines,
    sentence_count,
    sha256_hex,
    stage_facts,
)

from test_task.contracts.common import Role
from test_task.contracts.pipeline import SourceRef
from test_task.contracts.report import MD_MAIN_EXTRA_TITLES, MD_SECTION_TITLES

SRC = SourceRef("it_task_log (2).log", sha256_hex(ATTACHED_LOG.read_bytes()))


@pytest.fixture(scope="module")
def attached():
    lines = read_log_lines(ATTACHED_LOG)
    return SimpleNamespace(lines=lines, report=impl("pipeline", "analyze")(lines, SRC))


# Report fields with free text (wording, line samples, templates): their content is not compared
# after a time shift; for tuples of such strings only the length is compared.
FREE_TEXT_FIELDS = frozenset(
    {
        "incident_summary",
        "excerpt",
        "title",
        "role_rationale",
        "text",
        "template",
        "statement",
        "supporting",
        "contradicting",
        "action",
        "rationale",
        "criteria",
        "summary",
    }
)
_TS_TEXT = re.compile(r"^(\S+)")
_TS_JSON = re.compile(r'"ts"\s*:\s*"([^"]+)"')


def shift_line_ts(line, delta):
    """Shifts a line's timestamp: parse the time -> +delta -> the same ISO-8601 UTC representation with ms."""
    m = (_TS_JSON.search(line) if line.startswith("{") else None) or _TS_TEXT.match(line)
    assert m, line
    ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) + delta
    new = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"
    return line[: m.start(1)] + new + line[m.end(1) :]


def assert_shifted(orig, moved, delta, path="report", counter=None):
    """Every datetime in ``moved`` equals the original + delta; structural fields match.

    Returns the number of datetimes compared. Free text (``FREE_TEXT_FIELDS``) is not compared.
    """
    counter = [0] if counter is None else counter
    if dataclasses.is_dataclass(orig) and not isinstance(orig, type):
        assert type(moved) is type(orig), path
        for f in dataclasses.fields(orig):
            a, b = getattr(orig, f.name), getattr(moved, f.name)
            sub = f"{path}.{f.name}"
            if f.name in FREE_TEXT_FIELDS:
                if isinstance(a, tuple):
                    assert isinstance(b, tuple) and len(a) == len(b), sub
                continue
            assert_shifted(a, b, delta, sub, counter)
    elif isinstance(orig, datetime):
        assert isinstance(moved, datetime), path
        assert moved == orig + delta, f"{path}: {orig} -> {moved}"
        counter[0] += 1
    elif isinstance(orig, Enum) or orig is None or isinstance(orig, (bool, str)):
        assert moved == orig, f"{path}: {orig!r} != {moved!r}"
    elif isinstance(orig, (int, float)):
        assert isinstance(moved, (int, float)) and math.isclose(moved, orig, rel_tol=1e-9, abs_tol=1e-9), (
            f"{path}: {orig!r} != {moved!r}"
        )
    elif isinstance(orig, tuple):
        assert isinstance(moved, tuple) and len(moved) == len(orig), f"{path}: length"
        for i, (a, b) in enumerate(zip(orig, moved)):
            assert_shifted(a, b, delta, f"{path}[{i}]", counter)
    elif isinstance(orig, Mapping):
        assert isinstance(moved, Mapping) and sorted(moved) == sorted(orig), f"{path}: keys"
        for k in orig:
            assert_shifted(orig[k], moved[k], delta, f"{path}[{k!r}]", counter)
    else:
        assert moved == orig, f"{path}: {orig!r} != {moved!r}"
    return counter[0]


def level_of(line):
    if line.startswith("{"):
        return str(json.loads(line).get("level", "")).upper()
    parts = line.split()
    return parts[1].upper() if len(parts) > 1 else ""


@pytest.mark.criterion("TASK-012:C1")
@pytest.mark.req("FR-05", "CTX-04", "Q-04")
def test_smoke_attached_log(attached):
    r = attached.report
    assert r.source.total_lines == 521
    assert r.source.failed_lines == 0
    assert r.source.file_name == "it_task_log (2).log"
    assert any(f.db_categories for f in r.key_findings)
    assert any(f.role == Role.CAUSE for f in r.key_findings)
    assert r.db_signals and r.uncertainty and r.next_steps
    assert 2 <= sentence_count(r.incident_summary) <= 4, r.incident_summary
    assert 0 < len(r.key_findings) <= r.source.anomalous_lines / 3


@pytest.mark.criterion("TASK-012:C2")
@pytest.mark.req("DEL-09")
def test_deterministic(attached):
    analyze = impl("pipeline", "analyze")
    again = analyze(list(attached.lines), SRC)
    assert again == attached.report
    for name in ("render_json", "render_markdown"):
        render = impl("report", name)
        assert render(again) == render(attached.report)
    with_nl = analyze([ln + "\n" for ln in attached.lines], SRC)
    assert with_nl == attached.report


@pytest.mark.criterion("TASK-012:C3")
@pytest.mark.req("DEL-03", "FR-11", "NEG-06")
def test_removing_errors_and_warnings_changes_report(attached):
    kept = [ln for ln in attached.lines if level_of(ln) not in ("ERROR", "WARN", "WARNING")]
    assert len(kept) < len(attached.lines)
    r2 = impl("pipeline", "analyze")(kept, SRC)
    assert r2 != attached.report
    assert len(r2.key_findings) < len(attached.report.key_findings)


@pytest.mark.criterion("TASK-012:C3")
@pytest.mark.req("DEL-03", "FR-11", "NEG-06")
def test_time_shift_shifts_report(attached):
    delta = timedelta(days=1)
    shifted = [shift_line_ts(ln, delta) for ln in attached.lines]
    assert all(a != b for a, b in zip(attached.lines, shifted))
    r2 = impl("pipeline", "analyze")(shifted, SRC)
    assert attached.report.key_findings
    compared = assert_shifted(attached.report, r2, delta)
    assert compared > 0


@pytest.mark.criterion("TASK-012:C3")
@pytest.mark.req("DEL-03", "FR-11", "NEG-06")
def test_service_rename_propagates(attached):
    renamed = [ln.replace("payment-service", "checkout-service") for ln in attached.lines]
    r2 = impl("pipeline", "analyze")(renamed, SRC)
    for name in ("render_json", "render_markdown"):
        text = impl("report", name)(r2)
        assert "payment-service" not in text
        assert "checkout-service" in text
    assert "payment-service" in impl("report", "render_json")(attached.report)


@pytest.mark.criterion("TASK-012:C4")
@pytest.mark.req("FR-05")
@pytest.mark.parametrize(
    ("lines", "failed"),
    [([], 0), (["%%% garbage %%%", "\x00\x01", "random words here", ""], 3)],
    ids=["empty", "garbage-only"],
)
def test_degenerate_inputs(lines, failed):
    r = impl("pipeline", "analyze")(lines, SourceRef("degenerate.log", sha256_hex("\n".join(lines).encode())))
    assert r.source.failed_lines == failed
    assert r.source.total_lines == len(lines)
    assert r.key_findings == ()
    assert r.uncertainty
    d = impl("report", "to_json_dict")(r)
    assert len(d) == 15 and "summary_source" not in d
    md = impl("report", "render_markdown")(r)
    h2 = h2_titles(md)
    assert h2[: len(MD_SECTION_TITLES)] == list(MD_SECTION_TITLES), h2
    assert is_subsequence(MD_MAIN_EXTRA_TITLES, h2[len(MD_SECTION_TITLES) :]), h2


@pytest.mark.criterion("TASK-012:C5")
@pytest.mark.req("DEL-08")
def test_runs_offline_with_deterministic_summary(network_blocked):
    lines = read_log_lines(ATTACHED_LOG)
    r = impl("pipeline", "analyze")(lines, SRC)
    assert network_blocked == []
    assert not hasattr(r, "summary_source")
    # The summary comes from the deterministic path: it equals write_summary over the facts of the same steps
    facts = stage_facts(lines).facts
    assert r.incident_summary == impl("narrative", "write_summary")(facts)
