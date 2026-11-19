"""Human-readable renderers for ``Report``: report.md and the short console version."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from .contracts.causality import CompositeSignal, Hypothesis, MetricShift
from .contracts.common import EvidenceLine, MetricStats, MetricSummary, TimeWindow
from .contracts.report import (
    MD_APPENDIX_TITLES,
    MD_EVIDENCE_MAX_CHARS,
    MD_MAIN_EXTRA_TITLES,
    MD_MAX_SAMPLES_PER_FINDING,
    MD_SECTION_TITLES,
    PARSE_WARNING_MIN_FAILED_SHARE,
    REPORT_APPENDIX_MD_NAME,
    Report,
    SourceStats,
)

_MAX_ARGUMENTS = 3
_MAX_SHIFTS = 2


def _ts(value: datetime) -> str:
    utc = (
        value.astimezone(timezone.utc)
        if value.tzinfo
        else value.replace(tzinfo=timezone.utc)
    )
    return utc.strftime("%Y-%m-%d %H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _window(w: TimeWindow) -> str:
    return f"{_ts(w.start)} to {_ts(w.end)}"


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _csv(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "none"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _trunc(text: str) -> str:
    one = _one_line(text)
    if len(one) <= MD_EVIDENCE_MAX_CHARS:
        return one
    return one[: MD_EVIDENCE_MAX_CHARS - 3] + "..."


def _sample(s: EvidenceLine) -> str:
    return f"- line {s.line_no} [{s.level.value}] {s.service}: {_trunc(s.text)}"


def _shift(s: MetricShift) -> str:
    text = f"- Shift: {s.service} {s.metric} baseline p95 {_num(s.baseline_p95)} -> worst {_num(s.worst)}"
    if s.limit_metric is not None and s.limit is not None:
        text += f" at {s.limit_metric}={_num(s.limit)}"
    return f"{text} (line {s.worst_line_no})"


def _composite(c: CompositeSignal) -> str:
    return f"- Composite signal: {_one_line(c.interpretation)}"


def _metric(m: MetricSummary) -> str:
    base = (
        f", baseline p95 {_num(m.baseline_p95)}" if m.baseline_p95 is not None else ""
    )
    return (
        f"- {m.metric} ({m.unit.value}): n={m.count}, min {_num(m.min)}, "
        f"p50 {_num(m.p50)}, max {_num(m.max)}{base}"
    )


def _stats(s: MetricStats | None) -> str:
    if s is None:
        return "n/a"
    return f"n={s.count} p50={_num(s.p50)} p95={_num(s.p95)} max={_num(s.max)}"


def _hypothesis(h: Hypothesis) -> list[str]:
    verdict = f", verdict: {h.verdict.value}" if h.verdict is not None else ""
    lines = [
        (
            f"- {h.id} (kind: {h.kind.value}, confidence: {h.confidence.value}"
            f"{verdict}): {_one_line(h.statement)} (findings: {_csv(h.finding_ids)})"
        ),
    ]
    for text in h.supporting[:_MAX_ARGUMENTS]:
        lines.append(f"  - for: {_one_line(text)}")
    if h.contradicting:
        for text in h.contradicting[:_MAX_ARGUMENTS]:
            lines.append(f"  - against: {_one_line(text)}")
    else:
        lines.append("  - against: none identified")
    return lines


def parse_warning(stats: SourceStats) -> str | None:
    """Warning text if too many lines could not be parsed, otherwise ``None``."""
    content = stats.total_lines - stats.blank_lines
    if content <= 0 or stats.failed_lines <= content * PARSE_WARNING_MIN_FAILED_SHARE:
        return None
    return (
        f"Warning: {stats.failed_lines} of {content} non-blank lines "
        f"({stats.failed_lines * 100 // content}%) could not be parsed. The log format "
        f"probably differs from the supported one, so the analysis may be "
        f"incomplete or wrong. See {REPORT_APPENDIX_MD_NAME}, Input Statistics."
    )


def _summary_section(report: Report) -> list[str]:
    warning = parse_warning(report.source)
    if warning is not None:
        return [f"> **{warning}**", "", report.incident_summary]
    return [report.incident_summary]


def _findings_section(report: Report) -> list[str]:
    if not report.key_findings:
        return ["No key findings: no anomalous behavior was detected in the log."]
    out: list[str] = []
    for f in report.key_findings:
        out.append(f"### {f.id} [{f.severity.value}] ({f.role.value}) {f.title}")
        info = f"- Services: {_csv(f.services)}; window: {_window(f.window)}; events: {f.count}"
        if f.db_categories:
            info += f"; DB categories: {_csv([c.value for c in f.db_categories])}"
        out.append(info)
        out.append(f"- Role rationale: {f.role_rationale}")
        for sh in f.evidence.shifts[:_MAX_SHIFTS]:
            out.append(_shift(sh))
        for c in f.evidence.composites:
            out.append(_composite(c))
        for s in f.evidence.samples[:MD_MAX_SAMPLES_PER_FINDING]:
            out.append(_sample(s))
    while out and out[-1] == "":
        out.pop()
    return out


def _causes_section(report: Report) -> list[str]:
    causes = report.probable_causes
    if not causes.primary and not causes.alternatives:
        return ["No probable cause candidates were identified from the log."]
    out: list[str] = []
    if causes.primary:
        out.append("Primary candidates:")
        for h in causes.primary:
            out.extend(_hypothesis(h))
    if causes.alternatives:
        out.append("Alternative candidates:")
        for h in causes.alternatives:
            out.extend(_hypothesis(h))
    return out


def _impact_section(report: Report) -> list[str]:
    impact = report.impact
    if not impact.services and not impact.propagation:
        return ["No service impact was detected in the log."]
    out = [f"Failed requests: {impact.failed_requests}"]
    if impact.services:
        out.append("Affected services:")
        for s in impact.services:
            out.append(
                f"- {s.service} ({s.service_class.value}): first affected "
                f"{_ts(s.first_affected)}, anomalous events {s.anomalous_events}, "
                f"error events {s.error_events}, failed requests {s.failed_requests}, "
                f"findings: {_csv(s.finding_ids)}"
            )
    if impact.propagation:
        out.append("Propagation:")
        order: dict[str, list[str]] = {}
        for p in impact.propagation:
            order.setdefault(p.episode_id, []).append(
                f"{p.from_service} -> {p.to_service} via {p.via.value} "
                f"({_ts(p.first_seen)})"
            )
        for episode_id, steps in order.items():
            out.append(f"- {episode_id}: " + "; ".join(steps))
    return out


def _uncertainty_section(report: Report) -> list[str]:
    if not report.uncertainty:
        return ["No uncertainty items were recorded."]
    out = []
    for u in report.uncertainty:
        ref = f" (related: {_csv(u.related_ids)})" if u.related_ids else ""
        out.append(f"- {u.statement}{ref}")
    return out


def _next_steps_section(report: Report) -> list[str]:
    if not report.next_steps:
        return ["No next steps: there are no findings to follow up on."]
    return [
        f"- {n.id} (priority {n.priority}): {n.action} "
        f"Rationale: {n.rationale} (related: {_csv(n.related_ids)})"
        for n in report.next_steps
    ]


def _db_section(report: Report) -> list[str]:
    if not report.db_signals:
        return ["No database signals detected."]
    return [
        f"- {d.title}: services {_csv(d.services)}; {d.count} events; "
        f"{_window(d.window)}; findings {_csv(d.finding_ids)}"
        for d in report.db_signals
    ]


def _db_detailed_section(report: Report) -> list[str]:
    if not report.db_signals:
        return ["No database signals detected."]
    out: list[str] = []
    for d in report.db_signals:
        out.append(f"- {d.title} [{d.category.value}]")
        out.append(f"  - Services: {_csv(d.services)}")
        out.append(f"  - Window: {_window(d.window)}")
        out.append(f"  - Events: {d.count}")
        out.append(f"  - Findings: {_csv(d.finding_ids)}")
        out.append(f"  - Hypotheses: {_csv(d.hypothesis_ids)}")
        for m in d.metrics:
            out.append("  " + _metric(m))
        for s in d.samples:
            out.append("  " + _sample(s))
    return out


def _timeline_section(report: Report) -> list[str]:
    tl = report.timeline
    out: list[str] = []
    if tl.log_window is not None:
        out.append(f"Log window: {_window(tl.log_window)}")
    else:
        out.append("Log window: empty input.")
    if tl.baseline is not None:
        kind = "fallback rule" if tl.baseline_is_fallback else "pre-incident period"
        out.append(f"Baseline window: {_window(tl.baseline)} ({kind})")
    else:
        out.append("Baseline window: not available.")
    out[-1] += f"; residual anomalies outside episodes: {tl.residual_anomalies}"
    if not tl.episodes:
        out.append("No incident episodes were detected.")
        return out
    for e in tl.episodes:
        if e.recovered and e.recovery is not None:
            recovery = f"recovered at {_ts(e.recovery)} ({e.recovery_status.value})"
            if e.recovery_status.value != "observed":
                recovery += ", observation window is short"
        else:
            recovery = f"recovery not observed in the log ({e.recovery_status.value})"
        out.append(
            f"- {e.id}: onset {_ts(e.onset)}, peak {_window(e.peak)} "
            f"({e.peak_anomalous} anomalous, {e.peak_errors} errors), "
            f"last anomaly {_ts(e.last_anomaly)}, {recovery}; "
            f"{e.anomalous_events} anomalous events, {e.error_events} error events"
        )
    return out


def _baseline_section(report: Report) -> list[str]:
    if not report.metric_comparison and not report.error_rates:
        return ["No baseline or incident metrics are available."]
    rows = [
        m
        for m in report.metric_comparison
        if m.incident is not None and (m.p95_ratio is None or m.p95_ratio != 1.0)
    ]
    hidden = len(report.metric_comparison) - len(rows)
    out: list[str] = []
    if rows:
        out.append("| Service | Metric | Unit | Baseline | Incident | p95 ratio |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for m in rows:
            out.append(
                f"| {_cell(m.service)} | {_cell(m.metric)} | {m.unit.value} | "
                f"{_stats(m.baseline)} | {_stats(m.incident)} | {_num(m.p95_ratio)} |"
            )
    else:
        out.append("No metric has incident values that differ from baseline.")
    out.append("")
    out.append(
        "Rows without incident values or without a change in p95 are omitted: "
        f"{hidden} of {len(report.metric_comparison)} metric rows."
    )
    if report.error_rates:
        out.append("")
        out.append(
            "| Service | Baseline errors | Baseline rate | Incident errors | Incident rate |"
        )
        out.append("| --- | --- | --- | --- | --- |")
        for r in report.error_rates:
            out.append(
                f"| {_cell(r.service)} | {r.baseline_errors}/{r.baseline_total} | "
                f"{_num(r.baseline_rate)} | {r.incident_errors}/{r.incident_total} | "
                f"{_num(r.incident_rate)} |"
            )
    return out


def _traces_section(report: Report) -> list[str]:
    if not report.request_traces:
        return ["No request traces are available."]
    out: list[str] = []
    for t in report.request_traces:
        out.append(
            f"- Request {t.request_id}: {' -> '.join(t.services)} "
            f"({t.anomalous_hops} anomalous of {len(t.hops)} hops, {_window(t.window)})"
        )
        for h in t.hops:
            flag = " anomalous" if h.anomalous else ""
            out.append(
                f"  - line {h.line_no} {_ts(h.ts)} {h.service} [{h.level.value}]{flag}: "
                f"{_one_line(h.summary)}"
            )
    return out


def _scale_section(report: Report) -> list[str]:
    return [
        f"- {c.severity.value}: {_one_line(c.criteria)}" for c in report.severity_scale
    ]


def _input_section(report: Report) -> list[str]:
    s = report.source
    out = [
        f"- File: {s.file_name}",
        f"- SHA-256: {s.sha256}",
        f"- Total lines: {s.total_lines}",
        f"- Blank lines: {s.blank_lines}",
        f"- Parsed lines: {s.parsed_lines}",
        f"- Not parsed lines: {s.failed_lines}",
        f"- Dropped as noise: {s.noise_lines}",
        f"- Anomalous lines: {s.anomalous_lines}",
        f"- Continuation lines: {s.continuation_lines}",
        f"- Services: {_csv(s.services)}",
    ]
    if report.parse_failures:
        out.append("")
        out.append(
            f"Unparsed lines shown: {len(report.parse_failures)} of {s.failed_lines}."
        )
        for p in report.parse_failures:
            out.append(f"- line {p.line_no} ({p.reason.value}): {_one_line(p.excerpt)}")
    return out


_BUILDERS: dict[str, Callable[[Report], list[str]]] = {
    "Incident Summary": _summary_section,
    "Key Findings": _findings_section,
    "Probable Cause (or Candidates)": _causes_section,
    "Impact": _impact_section,
    "Uncertainty": _uncertainty_section,
    "Next Steps": _next_steps_section,
    "DB Signals": _db_section,
    "Timeline": _timeline_section,
    "Severity Scale": _scale_section,
    "DB Signals (detailed)": _db_detailed_section,
    "Baseline vs Incident": _baseline_section,
    "Request Traces": _traces_section,
    "Input Statistics": _input_section,
}


def _join(head: str, titles: Sequence[str], report: Report, tail: list[str]) -> str:
    lines = [head, ""]
    for title in titles:
        lines.append(f"## {title}")
        lines.append("")
        lines.extend(_BUILDERS[title](report))
        lines.append("")
    lines.extend(tail)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def render_markdown(report: Report) -> str:
    """Text of the short main report.md; details live in the appendix."""
    titles = MD_SECTION_TITLES + MD_MAIN_EXTRA_TITLES
    note = (
        f"Detailed database signals, baseline comparison, request traces and input "
        f"statistics: see {REPORT_APPENDIX_MD_NAME}."
    )
    return _join(
        f"# Incident Report: {report.source.file_name}", titles, report, [note]
    )


def render_appendix_markdown(report: Report) -> str:
    """Text of report-appendix.md: detailed sections."""
    return _join(
        f"# Incident Report Appendix: {report.source.file_name}",
        MD_APPENDIX_TITLES,
        report,
        [],
    )


def render_stdout(report: Report, written_paths: Sequence[str]) -> str:
    """Short console version: summary, one line per finding, top hypotheses, paths."""
    lines = [
        f"Incident Report: {report.source.file_name}",
        "",
        "Incident Summary",
        report.incident_summary,
        "",
        "Key Findings",
    ]
    if report.key_findings:
        for f in report.key_findings:
            lines.append(f"{f.id} [{f.severity.value}] ({f.role.value}) {f.title}")
    else:
        lines.append("No key findings.")
    lines.append("")
    lines.append("Probable Cause (or Candidates)")
    if report.probable_causes.primary:
        for h in report.probable_causes.primary:
            lines.append(
                f"{h.id} (confidence: {h.confidence.value}) {_one_line(h.statement)}"
            )
    else:
        lines.append("No probable cause candidates.")
    lines.append("")
    lines.append(f"DB signals: {len(report.db_signals)}")
    lines.append("")
    lines.append("Written files:")
    lines.extend(written_paths)
    return "\n".join(lines) + "\n"
