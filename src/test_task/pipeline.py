"""End-to-end deterministic analysis: log lines -> ``Report``."""

from __future__ import annotations

from collections.abc import Sequence

from .baseline import build_baseline, score_events
from .causality import assess, build_service_graph
from .contracts.common import AnalysisConfig
from .contracts.narrative import NarrativeFacts
from .contracts.pipeline import SourceRef
from .contracts.report import Report, ReportInputs
from .narrative import write_summary
from .parser import parse_lines
from .patterns import find_patterns
from .recommendations import recommend
from .report import build_report
from .signals import extract_events
from .timeline import build_timeline, compare_windows


def analyze(
    lines: Sequence[str],
    source: SourceRef,
    config: AnalysisConfig | None = None,
) -> Report:
    """Run all analysis steps in a fixed order and assemble the report."""
    cfg = config if config is not None else AnalysisConfig()
    parse = parse_lines(lines)
    events = extract_events(parse.records)
    baseline = build_baseline(events, cfg)
    scored = score_events(events, baseline, cfg)
    patterns = find_patterns(scored, baseline, cfg)
    timeline = build_timeline(scored, baseline, cfg)
    comparison = compare_windows(scored, timeline, cfg)
    graph = build_service_graph(scored, patterns)
    analysis = assess(scored, patterns, timeline, comparison, graph, cfg)
    recs = recommend(parse, patterns, timeline, analysis, cfg)
    summary = write_summary(NarrativeFacts(timeline, analysis, recs))
    return build_report(
        ReportInputs(
            file_name=source.file_name,
            sha256=source.sha256,
            parse=parse,
            patterns=patterns,
            timeline=timeline,
            metric_comparison=comparison.metrics,
            error_rates=comparison.error_rates,
            findings=analysis.findings,
            probable_causes=analysis.causes,
            impact=analysis.impact,
            recommendations=recs,
            incident_summary=summary,
        ),
        cfg,
    )
