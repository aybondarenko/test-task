"""Command line (``cli`` contract).

Implementation: ``test_task.cli.main`` (``Main``). Entry points that call it:
``python -m test_task`` (``test_task/__main__.py``), the ``test-task`` console script and
``test_task.main`` (re-export of ``test_task.cli.main``; referenced by ``[project.scripts]``).

Usage::

    test-task [LOG_PATH] [--out-dir DIR] [--quiet]

* ``LOG_PATH`` — path to the log file, defaults to ``DEFAULT_LOG_PATH`` relative to the current
  directory.
* ``--out-dir DIR`` — directory for ``report.md``, ``report-appendix.md`` and ``report.json``
  (names — ``report.REPORT_MD_NAME``, ``REPORT_APPENDIX_MD_NAME``, ``REPORT_JSON_NAME``),
  defaults to ``DEFAULT_OUT_DIR``; created if needed, files are overwritten.
* ``--quiet`` — do not print the short version to stdout (files are still written).
No other options: an unknown argument yields ``ExitCode.USAGE``. The network is never
used.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
from typing import Protocol

DEFAULT_LOG_PATH = "it_task_log (2).log"
"""The log attached to the task, path relative to the current directory."""

DEFAULT_OUT_DIR = "out"
"""Default report directory, relative to the current directory."""

INPUT_ENCODING = "utf-8"
"""Input encoding; undecodable bytes are replaced (``errors="replace"``), no lines are lost."""


class ExitCode(IntEnum):
    """Exit codes of ``main``."""

    OK = 0
    """Report built and written (including when no incident is detected)."""
    FAILURE = 1
    """Unexpected internal error; message on stderr."""
    USAGE = 2
    """Invalid arguments, log file not found/unreadable, or output directory not writable."""


class Main(Protocol):
    """``test_task.cli.main``: parse arguments, read the file, call ``analyze``, write
    the three report files, print the short version (``render_stdout(report, [md, appendix, json])``);
    return an ``ExitCode``.

    ``argv`` — arguments without the program name; ``None`` means ``sys.argv[1:]``.
    Reading: file bytes → sha256 (``SourceRef.sha256``); text is decoded with ``INPUT_ENCODING``;
    lines are split on ``\\n`` (a trailing ``\\n`` at the end of the file does not create an extra line).
    Two runs with the same arguments and input produce byte-identical report.md,
    report-appendix.md, report.json and stdout. Errors go to stderr, without a traceback for USAGE.

    """

    def __call__(self, argv: Sequence[str] | None = None) -> int: ...
