"""TASK-001: parsing log lines into records and counted parse failures."""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest
from acc_support import ATTACHED_LOG, UTC, read_log_lines

from test_task.contracts.common import Level
from test_task.contracts.parser import (
    LineFormat,
    LogRecord,
    ParseFailure,
    ParseFailureReason,
)

TEXT_LINE = (
    "2026-03-27T14:00:00.123Z INFO order-service order created order_id=o-1 latency_ms=42 "
    'amount=19.99 ok=true note="a b" code=killed,'
)
JSON_LINE = (
    '{"timestamp":"2026-03-27T14:00:01Z","severity":"warning","service":"payment-service",'
    '"msg":"upstream timeout","status":504,"trace":null,"ctx":{"b":1,"a":[1,2]}}'
)


@pytest.mark.criterion("TASK-001:C1")
@pytest.mark.req("FR-01")
def test_text_line_fields_and_types(impl):
    parse_line = impl("parser", "parse_line")
    rec = parse_line(TEXT_LINE + "\n", 7)
    assert isinstance(rec, LogRecord)
    assert rec.line_no == 7
    assert rec.ts == datetime(2026, 3, 27, 14, 0, 0, 123000, tzinfo=UTC)
    assert rec.ts.utcoffset() is not None and rec.ts.utcoffset().total_seconds() == 0
    assert rec.level == Level.INFO
    assert rec.service == "order-service"
    assert rec.message == "order created"
    f = rec.fields
    assert f["order_id"] == "o-1" and type(f["order_id"]) is str
    assert f["latency_ms"] == 42 and type(f["latency_ms"]) is int
    assert f["amount"] == 19.99 and type(f["amount"]) is float
    assert f["ok"] is True
    assert f["note"] == "a b"
    assert f["code"] == "killed,"
    assert set(f) == {"order_id", "latency_ms", "amount", "ok", "note", "code"}
    assert list(f) == ["order_id", "latency_ms", "amount", "ok", "note", "code"]
    assert rec.fmt == LineFormat.TEXT
    assert rec.raw == TEXT_LINE
    assert rec.is_continuation is False


@pytest.mark.criterion("TASK-001:C1")
@pytest.mark.req("FR-01")
def test_text_line_repeated_key_last_wins(impl):
    rec = impl("parser", "parse_line")("2026-03-27T14:00:00Z INFO svc-a step attempt=1 note=x attempt=3", 1)
    assert isinstance(rec, LogRecord)
    assert rec.fields["attempt"] == 3
    assert rec.fields["note"] == "x"
    assert rec.message == "step"


@pytest.mark.criterion("TASK-001:C2")
@pytest.mark.req("FR-01")
def test_json_line(impl):
    rec = impl("parser", "parse_line")(JSON_LINE, 3)
    assert isinstance(rec, LogRecord)
    assert rec.fmt == LineFormat.JSON
    assert rec.line_no == 3
    assert rec.ts == datetime(2026, 3, 27, 14, 0, 1, tzinfo=UTC)
    assert rec.level == Level.WARN
    assert rec.service == "payment-service"
    assert rec.message == "upstream timeout"
    assert rec.fields["status"] == 504 and type(rec.fields["status"]) is int
    assert rec.fields["ctx"] == '{"a":[1,2],"b":1}'
    for key in ("trace", "timestamp", "severity", "service", "msg"):
        assert key not in rec.fields
    assert rec.raw == JSON_LINE


@pytest.mark.criterion("TASK-001:C3")
@pytest.mark.req("FR-01")
@pytest.mark.parametrize(
    ("written", "level"),
    [
        ("warning", Level.WARN),
        ("Err", Level.ERROR),
        ("CRITICAL", Level.FATAL),
        ("trace", Level.DEBUG),
        ("WARNING", Level.WARN),
        ("notice", Level.INFO),
        ("Panic", Level.FATAL),
    ],
)
def test_level_aliases_case_insensitive(impl, written, level):
    rec = impl("parser", "parse_line")(f"2026-03-27T14:00:00Z {written} svc-a something happened", 1)
    assert isinstance(rec, LogRecord)
    assert rec.level == level


@pytest.mark.criterion("TASK-001:C3")
@pytest.mark.req("FR-01")
def test_timezone_offset_converted_to_utc(impl):
    rec = impl("parser", "parse_line")("2026-03-27T14:00:00+02:00 INFO svc-a tick", 1)
    assert isinstance(rec, LogRecord)
    assert rec.ts == datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC)
    assert rec.ts.utcoffset().total_seconds() == 0


@pytest.mark.criterion("TASK-001:C3")
@pytest.mark.req("FR-01")
def test_naive_time_is_utc_and_aware(impl):
    rec = impl("parser", "parse_line")("2026-03-27T14:00:00 INFO svc-a tick", 1)
    assert isinstance(rec, LogRecord)
    assert rec.ts.tzinfo is not None
    assert rec.ts.utcoffset().total_seconds() == 0
    assert rec.ts == datetime(2026, 3, 27, 14, 0, 0, tzinfo=UTC)


@pytest.mark.criterion("TASK-001:C4")
@pytest.mark.req("FR-01")
@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("2026-02-30T14:00:00Z INFO svc-a order created", ParseFailureReason.BAD_TIMESTAMP),
        ("2026-03-27T25:61:00Z INFO svc-a order created", ParseFailureReason.BAD_TIMESTAMP),
        ("2026-03-27T14:00:00Z VERBOSE svc-a order created", ParseFailureReason.UNKNOWN_LEVEL),
        ("2026-03-27T14:00:00Z INFO", ParseFailureReason.MISSING_SERVICE),
        ("{not json at all", ParseFailureReason.BAD_JSON),
        ('{"a": 1}', ParseFailureReason.BAD_JSON),
        ('{"ts":"2026-03-27T14:00:00Z","level":"INFO","msg":"no service"}', ParseFailureReason.BAD_JSON),
        ('{"ts":"2026-03-27T14:00:00Z","service":"svc-a","msg":"no level"}', ParseFailureReason.BAD_JSON),
        ('{"level":"INFO","service":"svc-a","msg":"no time"}', ParseFailureReason.BAD_JSON),
        ('{"ts":"2026-03-27T14:00:00Z","level":"INFO","service":"svc-a"} trailing', ParseFailureReason.BAD_JSON),
    ],
)
def test_failure_reasons(impl, line, reason):
    res = impl("parser", "parse_line")(line + "\r\n", 11)
    assert isinstance(res, ParseFailure)
    assert res.reason == reason
    assert res.line_no == 11
    assert res.raw == line


@pytest.mark.criterion("TASK-001:C4")
@pytest.mark.req("FR-01")
@pytest.mark.parametrize(
    "garbage",
    [
        "\x00\x01\x02\x07",
        "��� broken �",
        "\x1b[31mred\x1b[0m \x00 nonsense",
        "garbage\x00" * 12500,
        "random words without any structure",
        "{",
        "}{",
    ],
)
def test_garbage_never_raises(impl, garbage):
    res = impl("parser", "parse_line")(garbage, 5)
    assert isinstance(res, ParseFailure)
    assert res.line_no == 5


@pytest.mark.criterion("TASK-001:C5")
@pytest.mark.req("FR-01")
def test_parse_lines_counts_and_order(impl):
    lines = [
        "2026-03-27T14:05:00Z INFO svc-a later event",
        "",
        "   ",
        "%%% definitely not a log line %%%",
        "2026-03-27T14:00:00Z INFO svc-b earlier event\n",
    ]
    res = impl("parser", "parse_lines")(lines)
    assert res.total_lines == 5
    assert res.blank_lines == 2
    assert [r.line_no for r in res.records] == [1, 5]
    assert [r.service for r in res.records] == ["svc-a", "svc-b"]
    assert [f.line_no for f in res.failures] == [4]
    assert res.total_lines == res.blank_lines + len(res.records) + len(res.failures)


@pytest.mark.criterion("TASK-001:C5")
@pytest.mark.req("FR-01")
def test_parse_lines_empty_input(impl):
    res = impl("parser", "parse_lines")([])
    assert res.records == () and res.failures == ()
    assert res.total_lines == 0 and res.blank_lines == 0


@pytest.mark.criterion("TASK-001:C5")
@pytest.mark.req("FR-01")
def test_parse_lines_accepts_generator(impl):
    gen = (f"2026-03-27T14:00:0{i}Z INFO svc-a tick n={i}" for i in range(3))
    res = impl("parser", "parse_lines")(gen)
    assert res.total_lines == 3
    assert [r.fields["n"] for r in res.records] == [0, 1, 2]


@pytest.mark.criterion("TASK-001:C6")
@pytest.mark.req("FR-01")
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("\tat com.x.Y.z(Y.java:1)", True),
        ("Caused by: java.net.SocketTimeoutException: Read timed out", True),
        ("... 12 more", True),
        ("order created", False),
    ],
)
def test_continuation_flag(impl, message, expected):
    rec = impl("parser", "parse_line")(f"2026-03-27T14:00:00.500Z ERROR svc {message}", 2)
    assert isinstance(rec, LogRecord)
    assert rec.is_continuation is expected


@pytest.mark.criterion("TASK-001:C7")
@pytest.mark.req("FR-01", "CTX-05", "Q-04")
def test_smoke_attached_log(impl):
    lines = read_log_lines(ATTACHED_LOG)
    res = impl("parser", "parse_lines")(lines)
    assert res.total_lines == 521
    assert res.failures == ()
    assert res.blank_lines + len(res.records) == 521
    services = {r.service for r in res.records}
    assert {"api-gateway", "order-service", "payment-service", "inventory-service", "kubelet"} <= services
    assert {r.fmt for r in res.records} == {LineFormat.TEXT, LineFormat.JSON}


@pytest.mark.criterion("TASK-001:C8")
@pytest.mark.req("FR-11")
def test_leading_blank_lines_shift_only_line_numbers(impl):
    parse_lines = impl("parser", "parse_lines")
    lines = read_log_lines(ATTACHED_LOG)
    base = parse_lines(lines)
    shifted = parse_lines(["", "", ""] + lines)
    assert shifted.total_lines == base.total_lines + 3
    assert shifted.blank_lines == base.blank_lines + 3
    assert len(shifted.records) == len(base.records)
    for a, b in zip(base.records, shifted.records):
        assert b == dataclasses.replace(a, line_no=a.line_no + 3)


@pytest.mark.criterion("TASK-001:C8")
@pytest.mark.req("FR-11")
def test_parse_line_depends_only_on_text_and_line_no(impl):
    parse_line = impl("parser", "parse_line")
    parse_lines = impl("parser", "parse_lines")
    lines = [ln for ln in read_log_lines(ATTACHED_LOG) if ln.strip()]
    reversed_res = parse_lines(list(reversed(lines)))
    by_raw = {}
    for r in reversed_res.records:
        by_raw.setdefault(r.raw, r)
    for i, line in enumerate(lines[:200]):
        single = parse_line(line, 1000 + i)
        assert isinstance(single, LogRecord)
        assert dataclasses.replace(single, line_no=by_raw[line].line_no) == by_raw[line]
