"""TASK-028: integers longer than MAX_INT_DIGITS digits stay str, in text and in JSON, on any Python version.
The boundary is tested exactly, without relying on the interpreter's ``int()`` limit."""

from __future__ import annotations

import pytest
from acc_support import impl

from test_task.contracts.parser import MAX_INT_DIGITS, LogRecord

TS = "2026-03-27T14:00:00.000Z"


def digits(n):
    """n digits, the first one non-zero (no leading zeros)."""
    return ("7" + "1234567890" * (n // 10 + 1))[:n]


def parse(line):
    rec = impl("parser", "parse_line")(line, 1)
    assert isinstance(rec, LogRecord), rec
    return rec


@pytest.mark.criterion("TASK-028:C1")
@pytest.mark.req("DEL-15", "FR-01")
@pytest.mark.parametrize("sign", ["", "-"], ids=["positive", "negative"])
def test_text_boundary(sign):
    at_limit = sign + digits(MAX_INT_DIGITS)
    rec = parse(f"{TS} INFO svc-a checksum computed k={at_limit} tail=ok")
    assert type(rec.fields["k"]) is int
    assert rec.fields["k"] == int(at_limit)
    assert rec.fields["tail"] == "ok"
    for n in (MAX_INT_DIGITS + 1, 5000):
        value = sign + digits(n)
        rec = parse(f"{TS} INFO svc-a checksum computed k={value} tail=ok")
        assert type(rec.fields["k"]) is str and rec.fields["k"] == value, n
        assert rec.message == "checksum computed"


@pytest.mark.criterion("TASK-028:C1")
@pytest.mark.req("DEL-15", "FR-01")
def test_text_parse_lines_counts():
    lines = [f"{TS} WARN svc-b big value k={digits(n)}" for n in (MAX_INT_DIGITS, MAX_INT_DIGITS + 1, 5000)]
    res = impl("parser", "parse_lines")(lines)
    assert res.failures == () and len(res.records) == 3
    assert [type(r.fields["k"]) for r in res.records] == [int, str, str]


def json_line(literal):
    # The literal is inserted as text: json.dumps of large ints on 3.11+ hits the limit itself.
    return f'{{"ts":"{TS}","level":"INFO","service":"svc-c","msg":"blob stored","size":{literal},"tag":"x"}}'


@pytest.mark.criterion("TASK-028:C2")
@pytest.mark.req("DEL-15", "FR-01")
def test_json_boundary():
    at_limit = digits(MAX_INT_DIGITS)
    rec = parse(json_line(at_limit))
    assert type(rec.fields["size"]) is int and rec.fields["size"] == int(at_limit)
    for n in (MAX_INT_DIGITS + 1, 5000):
        literal = digits(n)
        rec = parse(json_line(literal))
        assert type(rec.fields["size"]) is str and rec.fields["size"] == literal, n
        assert rec.fields["tag"] == "x" and rec.message == "blob stored" and rec.service == "svc-c"
