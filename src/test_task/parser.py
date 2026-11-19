"""Parsing log lines into records (``parser`` contract)."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from .contracts.common import LEVEL_ALIASES, FieldValue, Level
from .contracts.parser import (
    CONTINUATION_PATTERN,
    JSON_LEVEL_KEYS,
    JSON_MESSAGE_KEYS,
    JSON_SERVICE_KEYS,
    JSON_TS_KEYS,
    KEY_PATTERN,
    MAX_INT_DIGITS,
    LineFormat,
    LogRecord,
    ParseFailure,
    ParseFailureReason,
    ParseResult,
)

_TS_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?"
    r"(Z|z|[+-]\d{2}(?::?\d{2})?)?",
    re.ASCII,
)
# "YYYY-MM-DD HH:MM:SS..." at the start of a text line: date and time separated by a space.
_SPACED_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}( )\d{2}:\d{2}", re.ASCII)
_KEY_EQ_RE = re.compile("(" + KEY_PATTERN + ")=")
_QUOTED_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"', re.DOTALL)
_INT_RE = re.compile(r"-?[0-9]+")
_FLOAT_RE = re.compile(r"-?[0-9]+\.[0-9]+")
_CONT_RE = re.compile(CONTINUATION_PATTERN)
_WS_RE = re.compile(r"\s+")
_NON_WS_RE = re.compile(r"\S+")

_Failure = ParseFailureReason


def _parse_ts(text: str) -> datetime | None:
    """ISO 8601 -> tz-aware UTC; ``None`` if it cannot be parsed."""
    m = _TS_RE.fullmatch(text)
    if m is None:
        return None
    year, month, day, hour, minute, sec, frac, zone = m.groups()
    micro = int((frac + "000000")[:6]) if frac else 0
    try:
        base = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(sec) if sec else 0,
            micro,
            tzinfo=timezone.utc,
        )
        if zone and zone not in ("Z", "z"):
            digits = zone[1:].replace(":", "")
            hh = int(digits[:2])
            mm = int(digits[2:4]) if len(digits) > 2 else 0
            if hh > 23 or mm > 59:
                return None
            offset = timedelta(hours=hh, minutes=mm)
            base = base - offset if zone[0] == "+" else base + offset
        return base
    except (ValueError, OverflowError):
        return None


def _int_or_str(literal: str) -> int | str:
    """An int if it has at most ``MAX_INT_DIGITS`` digits (sign excluded), otherwise the original text."""
    if len(literal.lstrip("-")) > MAX_INT_DIGITS:
        return literal
    return int(literal)


def _coerce_bare(value: str) -> FieldValue:
    if _INT_RE.fullmatch(value):
        return _int_or_str(value)
    if _FLOAT_RE.fullmatch(value):
        number = float(value)
        return number if math.isfinite(number) else value
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _scan_rest(rest: str) -> tuple[str, dict[str, FieldValue]]:
    """Split the rest of the line into the message and ``key=value`` pairs."""
    words: list[str] = []
    fields: dict[str, FieldValue] = {}
    quotes_ok = True
    pos = 0
    length = len(rest)
    while pos < length:
        ws = _WS_RE.match(rest, pos)
        if ws is not None:
            pos = ws.end()
            continue
        key = _KEY_EQ_RE.match(rest, pos)
        if key is not None:
            vpos = key.end()
            quoted = None
            if quotes_ok and vpos < length and rest[vpos] == '"':
                quoted = _QUOTED_RE.match(rest, vpos)
                if quoted is None:
                    quotes_ok = False
            if quoted is not None:
                fields[key.group(1)] = quoted.group(1).replace('\\"', '"')
                pos = quoted.end()
            else:
                bare = _NON_WS_RE.match(rest, vpos)
                if bare is None:
                    fields[key.group(1)] = ""
                    pos = vpos
                else:
                    fields[key.group(1)] = _coerce_bare(bare.group(0))
                    pos = bare.end()
            continue
        word = _NON_WS_RE.match(rest, pos)
        if word is None:
            raise AssertionError  # pragma: no cover
        words.append(word.group(0))
        pos = word.end()
    return " ".join(words), fields


def _parse_text(line_no: int, raw: str) -> LogRecord | ParseFailure:
    text = raw.lstrip()
    spaced = _SPACED_TS_RE.match(text)
    if spaced is not None:
        # Keep the timestamp a single token: "YYYY-MM-DD HH:MM" -> "YYYY-MM-DDTHH:MM".
        text = text[: spaced.start(1)] + "T" + text[spaced.end(1) :]
    parts = _WS_RE.split(text, maxsplit=3)
    ts = _parse_ts(parts[0])
    if ts is None:
        return ParseFailure(line_no, raw, _Failure.BAD_TIMESTAMP)
    level = LEVEL_ALIASES.get(parts[1].upper()) if len(parts) > 1 else None
    if level is None:
        return ParseFailure(line_no, raw, _Failure.UNKNOWN_LEVEL)
    if len(parts) < 3:
        return ParseFailure(line_no, raw, _Failure.MISSING_SERVICE)
    message, fields = _scan_rest(parts[3] if len(parts) > 3 else "")
    return LogRecord(
        line_no=line_no,
        ts=ts,
        level=level,
        service=parts[2],
        message=message,
        fields=fields,
        raw=raw,
        fmt=LineFormat.TEXT,
        is_continuation=_CONT_RE.match(message) is not None,
    )


def _first_key(obj: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in obj:
            return key
    return None


def _json_value(value: object) -> FieldValue:
    if isinstance(value, (str, bool, int, float)):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _parse_json(line_no: int, raw: str) -> LogRecord | ParseFailure:
    bad = ParseFailure(line_no, raw, _Failure.BAD_JSON)
    try:
        obj = json.loads(raw, parse_int=_int_or_str)
    except (ValueError, RecursionError):
        return bad
    if not isinstance(obj, dict):
        return bad
    ts_key = _first_key(obj, JSON_TS_KEYS)
    level_key = _first_key(obj, JSON_LEVEL_KEYS)
    service_key = _first_key(obj, JSON_SERVICE_KEYS)
    if ts_key is None or level_key is None or service_key is None:
        return bad
    ts_val = obj[ts_key]
    ts = _parse_ts(ts_val) if isinstance(ts_val, str) else None
    if ts is None:
        return ParseFailure(line_no, raw, _Failure.BAD_TIMESTAMP)
    level_val = obj[level_key]
    level: Level | None = (
        LEVEL_ALIASES.get(level_val.strip().upper())
        if isinstance(level_val, str)
        else None
    )
    if level is None:
        return ParseFailure(line_no, raw, _Failure.UNKNOWN_LEVEL)
    service = obj[service_key]
    if not isinstance(service, str) or not service.strip():
        return ParseFailure(line_no, raw, _Failure.MISSING_SERVICE)
    message = ""
    msg_key = _first_key(obj, JSON_MESSAGE_KEYS)
    if msg_key is not None and obj[msg_key] is not None:
        msg_val = obj[msg_key]
        message = msg_val if isinstance(msg_val, str) else str(_json_value(msg_val))
    reserved = set(
        JSON_TS_KEYS + JSON_LEVEL_KEYS + JSON_SERVICE_KEYS + JSON_MESSAGE_KEYS
    )
    fields = {
        str(k): _json_value(v)
        for k, v in obj.items()
        if k not in reserved and v is not None
    }
    return LogRecord(
        line_no=line_no,
        ts=ts,
        level=level,
        service=service,
        message=message,
        fields=fields,
        raw=raw,
        fmt=LineFormat.JSON,
        is_continuation=_CONT_RE.match(message) is not None,
    )


def parse_line(line: str, line_no: int) -> LogRecord | ParseFailure | None:
    """Parse a single line; ``None`` for a blank (whitespace-only) line."""
    raw = line.rstrip("\r\n")
    if not raw.strip():
        return None
    try:
        if raw.lstrip().startswith("{"):
            return _parse_json(line_no, raw)
        return _parse_text(line_no, raw)
    except Exception:  # noqa: BLE001
        return ParseFailure(line_no, raw, _Failure.UNRECOGNIZED)


def parse_lines(lines: Iterable[str]) -> ParseResult:
    """Parse all lines; numbering starts at 1, file order is preserved."""
    records: list[LogRecord] = []
    failures: list[ParseFailure] = []
    total = 0
    blank = 0
    for line_no, line in enumerate(lines, start=1):
        total = line_no
        result = parse_line(line, line_no)
        if result is None:
            blank += 1
        elif isinstance(result, LogRecord):
            records.append(result)
        else:
            failures.append(result)
    return ParseResult(
        records=tuple(records),
        failures=tuple(failures),
        total_lines=total,
        blank_lines=blank,
    )
