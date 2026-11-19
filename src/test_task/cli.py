"""test-task command line: the only module that performs I/O."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Sequence

from .contracts.cli import DEFAULT_LOG_PATH, DEFAULT_OUT_DIR, INPUT_ENCODING, ExitCode
from .contracts.pipeline import SourceRef
from .contracts.report import (
    REPORT_APPENDIX_MD_NAME,
    REPORT_JSON_NAME,
    REPORT_MD_NAME,
)
from .pipeline import analyze
from .render import (
    parse_warning,
    render_appendix_markdown,
    render_markdown,
    render_stdout,
)
from .report import render_json


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="test-task", description="Analyze an incident log.")
    parser.add_argument(
        "log_path", nargs="?", default=DEFAULT_LOG_PATH, help="path to the log file"
    )
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="report directory")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print the summary to stdout"
    )
    return parser


def _write(path: str, text: str) -> None:
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _run(argv: Sequence[str] | None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else sys.argv[1:])
    try:
        with open(args.log_path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise _UsageError(
            f"cannot read log file '{args.log_path}': {exc.strerror or exc}"
        ) from exc
    try:
        os.makedirs(args.out_dir, exist_ok=True)
    except OSError as exc:
        raise _UsageError(
            f"cannot use output directory '{args.out_dir}': {exc.strerror or exc}"
        ) from exc
    if not os.path.isdir(args.out_dir):
        raise _UsageError(f"output path is not a directory: '{args.out_dir}'")

    lines = data.decode(INPUT_ENCODING, errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    source = SourceRef(
        os.path.basename(args.log_path), hashlib.sha256(data).hexdigest()
    )

    report = analyze(lines, source)

    md_path = os.path.join(args.out_dir, REPORT_MD_NAME)
    appendix_path = os.path.join(args.out_dir, REPORT_APPENDIX_MD_NAME)
    json_path = os.path.join(args.out_dir, REPORT_JSON_NAME)
    try:
        _write(md_path, render_markdown(report))
        _write(appendix_path, render_appendix_markdown(report))
        _write(json_path, render_json(report))
    except OSError as exc:
        raise _UsageError(f"cannot write reports: {exc.strerror or exc}") from exc
    if not args.quiet:
        sys.stdout.write(render_stdout(report, [md_path, appendix_path, json_path]))
    warning = parse_warning(report.source)
    if warning is not None:
        print(f"test-task: {warning}", file=sys.stderr)
    return int(ExitCode.OK)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: returns the exit code (``ExitCode``)."""
    try:
        return _run(argv)
    except _UsageError as exc:
        print(f"test-task: error: {exc}", file=sys.stderr)
        return int(ExitCode.USAGE)
    except Exception as exc:  # noqa: BLE001
        print(f"test-task: internal error: {exc!r}", file=sys.stderr)
        return int(ExitCode.FAILURE)
