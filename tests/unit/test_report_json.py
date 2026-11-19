from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

import pytest

from test_task.contracts.common import Severity
from test_task.report import _base_name, _excerpt, _format_ts, _to_json


class Color(Enum):
    RED = "red"


def test_excerpt_truncates_with_ellipsis() -> None:
    assert _excerpt("abc", 5) == "abc"
    assert _excerpt("abcdefgh", 5) == "ab..."
    assert len(_excerpt("x" * 500, 240)) == 240


def test_base_name() -> None:
    assert _base_name("/a/b/c.log") == "c.log"
    assert _base_name("c:\\x\\d.log") == "d.log"
    assert _base_name("e.log") == "e.log"


def test_format_ts_utc_millis() -> None:
    tz = timezone(timedelta(hours=2))
    assert (
        _format_ts(datetime(2026, 1, 2, 5, 6, 7, 123999, tzinfo=tz))
        == "2026-01-02T03:06:07.123Z"
    )
    assert _format_ts(datetime(2026, 1, 2, 5, 6, 7)) == "2026-01-02T05:06:07.000Z"


def test_to_json_primitives() -> None:
    value = {"b": 1.23456, "a": (Color.RED, None, True, Severity.HIGH)}
    assert _to_json(value) == {"a": ["red", None, True, "high"], "b": 1.235}
    assert list(_to_json({"b": 1, "a": 2})) == ["a", "b"]  # type: ignore[arg-type]


def test_to_json_rejects_unknown() -> None:
    with pytest.raises(TypeError):
        _to_json(object())
