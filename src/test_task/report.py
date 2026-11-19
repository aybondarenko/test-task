"""Report model assembly and deterministic report.json serialization (contract ``report``)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .contracts.common import SEVERITY_CRITERIA, AnalysisConfig, JsonValue
from .contracts.parser import ParseFailure
from .contracts.report import (
    REPORT_SCHEMA_VERSION,
    ParseFailureView,
    Report,
    ReportInputs,
    SeverityCriterion,
    SourceStats,
)


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def _failure_view(failure: ParseFailure, limit: int) -> ParseFailureView:
    return ParseFailureView(
        line_no=failure.line_no,
        reason=failure.reason,
        excerpt=_excerpt(failure.raw, limit),
    )


def _base_name(name: str) -> str:
    return name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def build_report(inputs: ReportInputs, config: AnalysisConfig) -> Report:
    """Compose a ``Report`` from step results without adding new conclusions."""
    parse = inputs.parse
    patterns = inputs.patterns
    source = SourceStats(
        file_name=_base_name(inputs.file_name),
        sha256=inputs.sha256,
        total_lines=parse.total_lines,
        blank_lines=parse.blank_lines,
        parsed_lines=len(parse.records),
        failed_lines=len(parse.failures),
        noise_lines=patterns.noise_lines,
        anomalous_lines=patterns.anomalous_lines,
        continuation_lines=patterns.continuation_lines,
        services=tuple(sorted({r.service for r in parse.records})),
    )
    failures = sorted(parse.failures, key=lambda f: f.line_no)[
        : config.max_parse_failures_shown
    ]
    traces = {t.request_id: t for t in patterns.traces}
    return Report(
        schema_version=REPORT_SCHEMA_VERSION,
        source=source,
        parse_failures=tuple(
            _failure_view(f, config.max_evidence_chars) for f in failures
        ),
        incident_summary=inputs.incident_summary,
        key_findings=inputs.findings,
        probable_causes=inputs.probable_causes,
        impact=inputs.impact,
        uncertainty=inputs.recommendations.uncertainty,
        next_steps=inputs.recommendations.next_steps,
        db_signals=inputs.recommendations.db_signals,
        timeline=inputs.timeline,
        metric_comparison=inputs.metric_comparison,
        error_rates=inputs.error_rates,
        request_traces=tuple(
            traces[i] for i in patterns.example_trace_ids if i in traces
        ),
        severity_scale=tuple(SeverityCriterion(s, t) for s, t in SEVERITY_CRITERIA),
    )


def _format_ts(value: datetime) -> str:
    utc = (
        value.astimezone(timezone.utc)
        if value.tzinfo
        else value.replace(tzinfo=timezone.utc)
    )
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _to_json(obj: Any) -> JsonValue:
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, Enum):
        return _to_json(obj.value)
    if isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return round(obj, 3)
    if isinstance(obj, datetime):
        return _format_ts(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): _to_json(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (tuple, list)):
        return [_to_json(x) for x in obj]
    raise TypeError(f"unsupported value in report: {type(obj)!r}")


def to_json_dict(report: Report) -> dict[str, JsonValue]:
    """Serialize a ``Report`` to a JSON-compatible dict."""
    return {
        f.name: _to_json(getattr(report, f.name)) for f in dataclasses.fields(report)
    }


def render_json(report: Report) -> str:
    """Text of report.json: deterministic, byte-identical for the same ``Report``."""
    return json.dumps(to_json_dict(report), ensure_ascii=False, indent=2) + "\n"


from .render import render_appendix_markdown, render_markdown, render_stdout

__all__ = [
    "build_report",
    "render_appendix_markdown",
    "render_json",
    "render_markdown",
    "render_stdout",
    "to_json_dict",
]
