"""Report model and output formats (the ``report`` contract).

Implementation: ``test_task.report.build_report`` (``BuildReport``), ``test_task.report.to_json_dict``
(``ToJsonDict``), ``test_task.report.render_json`` (``RenderJson``),
``test_task.report.render_markdown`` (``RenderMarkdown``),
``test_task.report.render_appendix_markdown`` (``RenderAppendixMarkdown``),
``test_task.report.render_stdout`` (``RenderStdout``).

JSON serialization is a rule, not a separate schema: ``Report`` and nested dataclasses →
objects with keys = field names in declaration order; Enum → ``.value``; ``datetime`` → string
``YYYY-MM-DDTHH:MM:SS.mmmZ`` (UTC, milliseconds); ``tuple`` → array; ``Mapping`` → object with
keys in ascending order; ``float`` → rounded to 3 decimals; ``None`` → ``null``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .causality import Finding, Impact, ProbableCauses
from .common import AnalysisConfig, JsonValue, Severity
from .parser import ParseFailureReason, ParseResult
from .patterns import PatternResult, RequestTrace
from .recommendations import DbSignal, NextStep, Recommendations, UncertaintyItem
from .timeline import ErrorRateComparison, MetricComparison, Timeline

REPORT_SCHEMA_VERSION = "2.0"
"""``report.json`` format version; bumped on any change to ``Report`` fields."""

REPORT_MD_NAME = "report.md"
REPORT_APPENDIX_MD_NAME = "report-appendix.md"
REPORT_JSON_NAME = "report.json"
"""Report file names in the output directory: main report, appendix and JSON."""

MD_SECTION_TITLES: tuple[str, ...] = (
    "Incident Summary",
    "Key Findings",
    "Probable Cause (or Candidates)",
    "Impact",
    "Uncertainty",
    "Next Steps",
    "DB Signals",
)
"""Required sections in this order — ``## <title>`` headings in report.md.
"DB Signals" in the main report is a short, explicit block."""

MD_MAIN_EXTRA_TITLES: tuple[str, ...] = (
    "Timeline",
    "Severity Scale",
)
"""Short ``## <title>`` sections of the main report.md after the required ones, in this order."""

MD_APPENDIX_TITLES: tuple[str, ...] = (
    "DB Signals (detailed)",
    "Baseline vs Incident",
    "Request Traces",
    "Input Statistics",
)
"""``## <title>`` sections of the ``REPORT_APPENDIX_MD_NAME`` file, in this order. They are not
part of the main report.md."""

MD_MAX_LINES = 150
"""Target line limit for the main report.md: no more than this on the bundled log."""

MD_EVIDENCE_MAX_CHARS = 160
"""Sample lines in report.md and the appendix are truncated to this many characters (with "...")."""

MD_MAX_SAMPLES_PER_FINDING = 2
"""At most this many sample lines per finding in the main report.md; the full set is in JSON."""


PARSE_WARNING_MIN_FAILED_SHARE: float = 0.10
"""If more than this share of non-blank lines is not parsed, the reports carry a warning: the log
format probably differs from the supported one and the conclusions may be unreliable."""


@dataclass(frozen=True)
class SourceStats:
    """Input file information.

    ``file_name`` — base file name only (no directory), ``sha256`` — hex digest of the file bytes.
    Invariants: ``total_lines = blank_lines + parsed_lines + failed_lines``;
    ``parsed_lines = noise_lines + anomalous_lines + continuation_lines``.
    ``services`` — all services of parsed records, by name.
    """

    file_name: str
    sha256: str
    total_lines: int
    blank_lines: int
    parsed_lines: int
    failed_lines: int
    noise_lines: int
    anomalous_lines: int
    continuation_lines: int
    services: tuple[str, ...]


@dataclass(frozen=True)
class ParseFailureView:
    """An unparsed line in the report: ``excerpt`` is truncated to ``max_evidence_chars``."""

    line_no: int
    reason: ParseFailureReason
    excerpt: str


@dataclass(frozen=True)
class SeverityCriterion:
    """A severity scale row — a copy of ``SEVERITY_CRITERIA``."""

    severity: Severity
    criteria: str


@dataclass(frozen=True)
class Report:
    """Structured incident summary. Field order = report.json key order.

    * ``parse_failures`` — the first ``max_parse_failures_shown`` by line_no; the total is in
      ``source.failed_lines``.
    * ``incident_summary`` — 2–4 sentences, always the deterministic text from
      ``narrative.write_summary``.
    * ``key_findings`` — ``CausalAnalysis.findings``.
    * ``probable_causes``, ``impact``, ``uncertainty`` (non-empty),
      ``next_steps``, ``db_signals``.
    * ``timeline``, ``metric_comparison``/``error_rates``,
      ``request_traces`` — traces from ``example_trace_ids`` in the same order.
    * ``severity_scale`` — the whole scale.
    No generation time, absolute paths, host name or anything else that varies between runs.
    All text is in English.
    """

    schema_version: str
    source: SourceStats
    parse_failures: tuple[ParseFailureView, ...]
    incident_summary: str
    key_findings: tuple[Finding, ...]
    probable_causes: ProbableCauses
    impact: Impact
    uncertainty: tuple[UncertaintyItem, ...]
    next_steps: tuple[NextStep, ...]
    db_signals: tuple[DbSignal, ...]
    timeline: Timeline
    metric_comparison: tuple[MetricComparison, ...]
    error_rates: tuple[ErrorRateComparison, ...]
    request_traces: tuple[RequestTrace, ...]
    severity_scale: tuple[SeverityCriterion, ...]


@dataclass(frozen=True)
class ReportInputs:
    """Everything a ``Report`` is assembled from; ``file_name``/``sha256`` — see ``SourceStats``."""

    file_name: str
    sha256: str
    parse: ParseResult
    patterns: PatternResult
    timeline: Timeline
    metric_comparison: tuple[MetricComparison, ...]
    error_rates: tuple[ErrorRateComparison, ...]
    findings: tuple[Finding, ...]
    probable_causes: ProbableCauses
    impact: Impact
    recommendations: Recommendations
    incident_summary: str


class BuildReport(Protocol):
    """``test_task.report.build_report``: assemble a ``Report`` with no new conclusions, layout only."""

    def __call__(self, inputs: ReportInputs, config: AnalysisConfig) -> Report: ...


class ToJsonDict(Protocol):
    """``test_task.report.to_json_dict``: ``Report`` → JSON-compatible dict per the module rule."""

    def __call__(self, report: Report) -> dict[str, JsonValue]: ...


class RenderJson(Protocol):
    """``test_task.report.render_json``: report.json text.

    ``json.dumps(to_json_dict(report), ensure_ascii=False, indent=2) + "\\n"``; byte-for-byte
    identical for identical ``Report`` values.
    """

    def __call__(self, report: Report) -> str: ...


class RenderMarkdown(Protocol):
    """``test_task.report.render_markdown``: main report.md text.

    * the first line is ``# Incident Report: <file_name>``; then ``## <title>`` from
      ``MD_SECTION_TITLES``, then from ``MD_MAIN_EXTRA_TITLES``, strictly in this order; no
      ``MD_APPENDIX_TITLES`` sections; the last non-empty line is a link to
      ``REPORT_APPENDIX_MD_NAME``;
    * each finding — a ``### <id> [<severity>] (<role>) <title>`` subheading and a few
      lines: services, window, count, role rationale, strongest metric deviations and composite
      signals (``Evidence.shifts``/``composites``), up to ``MD_MAX_SAMPLES_PER_FINDING``
      sample lines with line numbers, truncated to ``MD_EVIDENCE_MAX_CHARS``;
    * each hypothesis — id, kind, confidence, verdict (if any), statement, supporting/contradicting
      arguments;
    * "DB Signals" — one line per ``DbSignal``: title, services, event count, window, finding ids;
    * Timeline — one line per episode: onset, peak, recovery with status (``RecoveryStatus``);
    * an empty section contains an explicit phrase (e.g. ``No database signals detected.``) rather
      than being skipped;
    * no longer than ``MD_MAX_LINES`` lines on the bundled log;
    * ``\\n`` line endings, the file ends with a single ``\\n``.
    """

    def __call__(self, report: Report) -> str: ...


class RenderAppendixMarkdown(Protocol):
    """``test_task.report.render_appendix_markdown``: ``REPORT_APPENDIX_MD_NAME`` text.

    * the first line is ``# Incident Report Appendix: <file_name>``; then ``## <title>`` from
      ``MD_APPENDIX_TITLES`` strictly in order;
    * "DB Signals (detailed)" — metrics and sample lines for each ``DbSignal``; "Baseline vs
      Incident" — ``metric_comparison`` and ``error_rates`` (unchanged rows may be omitted,
      saying so explicitly); "Request Traces" — ``request_traces``; "Input Statistics" —
      ``source`` and ``parse_failures``;
    * sample lines are truncated to ``MD_EVIDENCE_MAX_CHARS``; an empty section gets an explicit
      phrase; ``\\n`` line endings, the file ends with a single ``\\n``.
    """

    def __call__(self, report: Report) -> str: ...


class RenderStdout(Protocol):
    """``test_task.report.render_stdout``: short console version.

    Contains the Incident Summary, one line per finding
    (``<id> [<severity>] (<role>) <title>``), primary hypotheses with confidence, the DB signal count
    and the ``written_paths`` in the given order. At most 40 lines + one line per finding.
    """

    def __call__(self, report: Report, written_paths: Sequence[str]) -> str: ...
