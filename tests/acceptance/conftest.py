"""test-task acceptance tests: markers, implementation loader, network blocking.

The implementation is imported only inside tests and fixtures (via ``acc_support.impl``),
so against the stub it is the test that fails, not collection of the file.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "criterion(*refs): task acceptance criterion, e.g. TASK-001:C1")
    config.addinivalue_line("markers", "req(*ids): requirement covered by the test")


@pytest.fixture
def impl() -> Callable[[str, str], Any]:
    """``impl("parser", "parse_lines")`` → ``test_task.parser.parse_lines``."""
    from acc_support import impl as _impl

    return _impl


@pytest.fixture
def network_blocked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Replaces network socket creation with an exception; returns the list of attempts."""
    attempts: list[tuple[str, object]] = []
    real_socket = socket.socket
    unix = getattr(socket, "AF_UNIX", None)

    class _GuardSocket(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, family: int = -1, type: int = -1, proto: int = -1, fileno: object = None) -> None:
            if fileno is None and (unix is None or family != unix):
                attempts.append(("socket", family))
                raise OSError("network access is disabled in acceptance tests")
            super().__init__(family, type, proto, fileno)

    def _deny(*args: object, **kwargs: object) -> None:
        attempts.append(("connect", args[:1]))
        raise OSError("network access is disabled in acceptance tests")

    monkeypatch.setattr(socket, "socket", _GuardSocket)
    monkeypatch.setattr(socket, "create_connection", _deny)
    monkeypatch.setattr(socket, "getaddrinfo", _deny)
    return attempts
