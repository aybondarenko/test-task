"""Parsing log lines into records (``parser`` contract).

Two line formats are supported:

* text — ``<ISO-ts> <LEVEL> <service> <rest>``, where the rest is a mix of
  ``key=value`` pairs and free text;
* a single-line JSON object with time/level/service keys (``JSON_*_KEYS``).

Implementation: module ``test_task.parser``, functions ``parse_line`` (``ParseLine``) and
``parse_lines`` (``ParseLines``). No I/O: the caller supplies the lines.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from .common import FieldValue, Level


class LineFormat(str, Enum):
    """Format of the source line."""

    TEXT = "text"
    JSON = "json"


class ParseFailureReason(str, Enum):
    """Why a line could not be parsed (such lines are counted and reported)."""

    BAD_TIMESTAMP = "bad_timestamp"
    """The first field / time key is not ISO 8601."""
    UNKNOWN_LEVEL = "unknown_level"
    """The level is not in ``LEVEL_ALIASES``."""
    MISSING_SERVICE = "missing_service"
    """No service name."""
    BAD_JSON = "bad_json"
    """The line starts with ``{`` but is not a JSON object or lacks required keys."""
    UNRECOGNIZED = "unrecognized"
    """The line matches no supported format."""


KEY_PATTERN = r"[A-Za-z_][A-Za-z0-9_.\-]*"
"""Regular expression for a key name in the text format (``key=value`` token)."""

CONTINUATION_PATTERN = r"^(at\s+\S+\(.*\)|Caused by:|\.\.\.\s+\d+\s+more)"
"""Message (after removing key=value pairs and leading whitespace) is a stack-trace continuation."""

JSON_TS_KEYS: tuple[str, ...] = ("ts", "timestamp", "time", "@timestamp")
"""Time keys of a JSON line, checked in order; the first one found is used."""
JSON_LEVEL_KEYS: tuple[str, ...] = ("level", "severity", "lvl")
"""Level keys of a JSON line."""
JSON_SERVICE_KEYS: tuple[str, ...] = ("service", "app", "logger")
"""Service keys of a JSON line."""
JSON_MESSAGE_KEYS: tuple[str, ...] = ("msg", "message")
"""Free-text keys of a JSON line."""
MAX_INT_DIGITS = 4300
"""An integer longer than ``MAX_INT_DIGITS`` digits (sign excluded) stays ``str`` in both text and JSON,
consistently across Python versions: 3.10+ ``int()`` rejects such strings, 3.9 does not."""


@dataclass(frozen=True)
class LogRecord:
    """A parsed log line.

    * ``line_no`` — physical line number in the input, from 1 (blank lines are numbered too).
    * ``ts`` — tz-aware UTC. A ``Z`` suffix and ``±HH:MM`` offsets are converted to UTC; time
      without a zone is treated as UTC.
    * ``service`` — as in the log, without case normalization.
    * ``message`` — free text: tokens of the rest that are not ``key=value``, in original
      order, joined by a single space; for JSON — the value of the first key from
      ``JSON_MESSAGE_KEYS`` (or ``""``).
    * ``fields`` — ``key=value`` pairs in order of appearance; on a repeated key the last one wins.
      Text: a double-quoted value (with ``\\"`` inside) is always ``str`` without quotes;
      otherwise the longest run of non-whitespace characters, coerced as follows: ``-?\\d+`` →
      ``int`` (if it has at most ``MAX_INT_DIGITS`` digits, otherwise ``str`` as is), ``-?\\d+\\.\\d+`` → ``float``, ``true``/``false`` → ``bool``, anything else → ``str``
      as is. JSON: str/int/float/bool as is (an integer literal longer than ``MAX_INT_DIGITS`` digits → ``str``
      with its text), ``null`` — the key is skipped, arrays and
      objects — ``json.dumps(v, sort_keys=True, separators=(",", ":"))``. The JSON time, level,
      service and message keys do not go into ``fields``.
    * ``raw`` — the source line without trailing ``\\r``/``\\n``.
    * ``is_continuation`` — ``message`` matches ``CONTINUATION_PATTERN`` (stack-trace frame,
      "Caused by:").
    """

    line_no: int
    ts: datetime
    level: Level
    service: str
    message: str
    fields: Mapping[str, FieldValue]
    raw: str
    fmt: LineFormat
    is_continuation: bool


@dataclass(frozen=True)
class ParseFailure:
    """A line that could not be parsed. ``raw`` — without trailing ``\\r``/``\\n``."""

    line_no: int
    raw: str
    reason: ParseFailureReason


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing the input.

    Invariant: ``total_lines == blank_lines + len(records) + len(failures)``.
    ``records`` and ``failures`` are in file order (not re-sorted by time).
    A blank line is one consisting only of whitespace.
    """

    records: tuple[LogRecord, ...]
    failures: tuple[ParseFailure, ...]
    total_lines: int
    blank_lines: int


class ParseLine(Protocol):
    """``test_task.parser.parse_line``: parse a single line.

    ``line`` may contain a trailing ``\\n``. Returns ``None`` for a blank line,
    otherwise a ``LogRecord`` or ``ParseFailure``. Never raises on input data.
    """

    def __call__(self, line: str, line_no: int) -> LogRecord | ParseFailure | None: ...


class ParseLines(Protocol):
    """``test_task.parser.parse_lines``: parse all lines; line numbering starts at 1.

    Deterministic, never raises on input data; empty input gives an empty result.
    """

    def __call__(self, lines: Iterable[str]) -> ParseResult: ...
