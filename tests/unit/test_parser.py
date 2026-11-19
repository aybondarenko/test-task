import pytest

from test_task import parser
from test_task.contracts.common import Level
from test_task.contracts.parser import (
    LineFormat,
    LogRecord,
    ParseFailure,
    ParseFailureReason,
)
from test_task.parser import parse_line, parse_lines

TEXT = (
    "2026-03-27T14:00:00.123Z INFO order-service order created order_id=o-1 "
    'latency_ms=42 amount=19.99 ok=true note="a \\"b\\"" code=killed, order_id=o-2\n'
)


def test_text_line() -> None:
    rec = parse_line(TEXT, 7)
    assert isinstance(rec, LogRecord)
    assert rec.line_no == 7 and rec.level is Level.INFO and rec.fmt is LineFormat.TEXT
    assert rec.message == "order created" and not rec.is_continuation
    assert rec.ts.tzinfo is not None and rec.ts.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert rec.ts.microsecond == 123000
    assert rec.fields == {
        "order_id": "o-2",
        "latency_ms": 42,
        "amount": 19.99,
        "ok": True,
        "note": 'a "b"',
        "code": "killed,",
    }
    assert not rec.raw.endswith("\n")


def test_json_line() -> None:
    line = (
        '{"timestamp":"2026-03-27T14:00:01Z","severity":"warning","service":"p",'
        '"msg":"x","status":504,"trace":null,"ctx":{"b":1,"a":[1,2]}}'
    )
    rec = parse_line(line, 1)
    assert isinstance(rec, LogRecord)
    assert rec.fmt is LineFormat.JSON and rec.level is Level.WARN
    assert rec.fields == {"status": 504, "ctx": '{"a":[1,2],"b":1}'}


def test_json_failures() -> None:
    for line in ("{", "{}", '{"ts":"2026-03-27T14:00:01Z"}', "{1:2}"):
        res = parse_line(line, 1)
        assert isinstance(res, ParseFailure)
        assert res.reason is ParseFailureReason.BAD_JSON
    ok_ts = '"ts":"2026-03-27T14:00:01Z"'
    cases = [
        ('{"ts":5,"level":"INFO","service":"s"}', ParseFailureReason.BAD_TIMESTAMP),
        ("{" + ok_ts + ',"level":1,"service":"s"}', ParseFailureReason.UNKNOWN_LEVEL),
        (
            "{" + ok_ts + ',"level":"INFO","service":""}',
            ParseFailureReason.MISSING_SERVICE,
        ),
    ]
    for line, reason in cases:
        res = parse_line(line, 1)
        assert isinstance(res, ParseFailure) and res.reason is reason


def test_normalization() -> None:
    rec = parse_line("2026-03-27T14:00:00+02:00 Err svc", 1)
    assert isinstance(rec, LogRecord)
    assert rec.level is Level.ERROR and rec.ts.hour == 12
    rec = parse_line("2026-03-27T14:00:00-0130 CRITICAL svc", 1)
    assert isinstance(rec, LogRecord) and rec.level is Level.FATAL
    assert (rec.ts.hour, rec.ts.minute) == (15, 30)
    rec = parse_line("2026-03-27T14:00 trace svc k= x", 1)
    assert isinstance(rec, LogRecord)
    assert rec.ts.tzinfo is not None and rec.level is Level.DEBUG
    assert rec.fields == {"k": ""} and rec.message == "x"


def test_text_failures() -> None:
    cases = {
        "garbage here": ParseFailureReason.BAD_TIMESTAMP,
        "2026-13-27T14:00:00Z INFO s": ParseFailureReason.BAD_TIMESTAMP,
        "2026-03-27T14:00:00+99:00 INFO s": ParseFailureReason.BAD_TIMESTAMP,
        "2026-03-27T14:00:00Z VERBOSE s": ParseFailureReason.UNKNOWN_LEVEL,
        "2026-03-27T14:00:00Z": ParseFailureReason.UNKNOWN_LEVEL,
        "2026-03-27T14:00:00Z INFO": ParseFailureReason.MISSING_SERVICE,
    }
    for line, reason in cases.items():
        res = parse_line(line + "\r\n", 3)
        assert isinstance(res, ParseFailure)
        assert res.reason is reason and res.raw == line and res.line_no == 3


def test_garbage_never_raises() -> None:
    for line in ("\x00\x01", "�" * 5, "x" * 100000, "k=" + "9" * 5000, 'a="' * 30000):
        assert isinstance(parse_line(line, 1), ParseFailure)
    assert isinstance(parse_line("{" * 100000, 1), ParseFailure)
    rec = parse_line(
        "2026-03-27T14:00:00Z INFO s k=" + "9" * 5000 + ' q="unterminated x=1', 1
    )
    assert isinstance(rec, LogRecord)
    assert rec.fields["k"] == "9" * 5000


def test_parse_lines() -> None:
    ok = "2026-03-27T14:00:05Z INFO s a"
    early = "2026-03-27T13:00:00Z INFO s b\n"
    res = parse_lines([ok, "", "   ", "junk", early])
    assert (res.total_lines, res.blank_lines) == (5, 2)
    assert [r.line_no for r in res.records] == [1, 5]
    assert [f.line_no for f in res.failures] == [4]
    empty = parse_lines([])
    assert (empty.total_lines, empty.blank_lines, empty.records, empty.failures) == (
        0,
        0,
        (),
        (),
    )


def test_continuation() -> None:
    for msg in (
        "\tat com.x.Y.z(Y.java:1)",
        "Caused by: java.io.IOException: x",
        "... 12 more",
    ):
        rec = parse_line("2026-03-27T14:00:00Z ERROR svc " + msg, 1)
        assert isinstance(rec, LogRecord) and rec.is_continuation
    json_rec = parse_line(
        '{"ts":"2026-03-27T14:00:00Z","level":"ERROR","service":"s"}', 1
    )
    assert isinstance(json_rec, LogRecord) and not json_rec.is_continuation
    assert json_rec.message == ""


def test_unexpected_error_becomes_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(line_no: int, raw: str) -> LogRecord:
        raise RuntimeError

    monkeypatch.setattr(parser, "_parse_text", boom)
    res = parse_line("anything", 2)
    assert isinstance(res, ParseFailure)
    assert res.reason is ParseFailureReason.UNRECOGNIZED


def test_long_int_text_and_json_limits() -> None:
    from test_task.contracts.parser import MAX_INT_DIGITS, LogRecord
    from test_task.parser import parse_line

    for n, sign, expect_int in (
        (MAX_INT_DIGITS, "", True),
        (MAX_INT_DIGITS, "-", True),
        (MAX_INT_DIGITS + 1, "", False),
        (5000, "-", False),
    ):
        lit = sign + "9" * n
        rec = parse_line(f"2024-01-01T00:00:00Z INFO svc msg k={lit}", 1)
        assert isinstance(rec, LogRecord)
        assert isinstance(rec.fields["k"], int) is expect_int
        if not expect_int:
            assert rec.fields["k"] == lit
        rec = parse_line(
            '{"ts":"2024-01-01T00:00:00Z","level":"INFO","service":"s","k":'
            + lit
            + "}",
            1,
        )
        assert isinstance(rec, LogRecord)
        assert isinstance(rec.fields["k"], int) is expect_int
        if not expect_int:
            assert rec.fields["k"] == lit


def test_space_separated_timestamp() -> None:
    raw = "2026-03-27 14:00:02,968Z WARN svc-a pool_idle=0 db acquire slow"
    rec = parse_line(raw, 7)
    assert not isinstance(rec, ParseFailure) and rec is not None
    assert rec.ts == parse_line("2026-03-27T14:00:02.968Z INFO x", 1).ts  # type: ignore[union-attr]
    assert rec.level is Level.WARN
    assert rec.service == "svc-a"
    assert rec.fields == {"pool_idle": 0}
    assert rec.raw == raw
    js = parse_line('{"ts":"2026-03-27 14:00:02Z","level":"INFO","service":"g"}', 2)
    assert not isinstance(js, ParseFailure) and js is not None
    assert js.service == "g"
