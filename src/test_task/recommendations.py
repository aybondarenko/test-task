"""DB signals, uncertainty and next steps."""

from __future__ import annotations

from .contracts.causality import (
    CausalAnalysis,
    Finding,
    Hypothesis,
    HypothesisKind,
    Verdict,
)
from .contracts.common import (
    AnalysisConfig,
    Confidence,
    DbCategory,
    EvidenceLine,
    MetricSummary,
    Severity,
    TimeWindow,
)
from .contracts.parser import LogRecord, ParseResult
from .contracts.patterns import PatternGroup, PatternResult
from .contracts.recommendations import (
    DB_CATEGORY_TITLES,
    FORBIDDEN_STEP_PHRASES,
    HYPOTHESIS_STEP_HINTS,
    LOAD_UNCERTAINTY,
    MAX_NEXT_STEPS,
    NEXT_STEP_HINTS,
    SHORT_WINDOW_UNCERTAINTY,
    UNCERTAINTY_BY_CATEGORY,
    DbSignal,
    NextStep,
    Recommendations,
    UncertaintyItem,
)
from .contracts.signals import CONNECTION_DROP_KEYWORDS
from .contracts.timeline import RecoveryStatus, Timeline

_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}
_CONFIDENCE_RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
_CATEGORY_RANK = {c: i for i, c in enumerate(DbCategory)}


def _num(identifier: str) -> tuple[int, str]:
    digits = identifier[1:]
    return (int(digits) if digits.isdigit() else 0, identifier)


def _hypotheses(analysis: CausalAnalysis) -> tuple[Hypothesis, ...]:
    return analysis.causes.primary + analysis.causes.alternatives


def _merge_metrics(groups: list[PatternGroup]) -> tuple[MetricSummary, ...]:
    best: dict[str, MetricSummary] = {}
    for g in groups:
        for m in g.metrics:
            cur = best.get(m.metric)
            if cur is None or m.count > cur.count:
                best[m.metric] = m
    return tuple(best[k] for k in sorted(best))


def _clip(record: LogRecord, limit: int) -> EvidenceLine:
    text = record.raw
    if len(text) > limit:
        text = text[: max(limit - 3, 0)] + "..."
    return EvidenceLine(
        line_no=record.line_no,
        ts=record.ts,
        service=record.service,
        level=record.level,
        text=text,
    )


def _candidates(
    group: PatternGroup,
    category: DbCategory,
    records: dict[int, LogRecord],
    config: AnalysisConfig,
) -> list[EvidenceLine]:
    """Group samples; for QUERY_TIMEOUT, excluding connection-drop lines."""
    if category is not DbCategory.QUERY_TIMEOUT:
        return list(group.samples)
    out: list[EvidenceLine] = []
    for n in group.line_nos:
        rec = records.get(n)
        if rec is None:
            continue
        low = rec.raw.lower()
        if any(k in low for k in CONNECTION_DROP_KEYWORDS):
            continue
        out.append(_clip(rec, config.max_evidence_chars))
    return out or list(group.samples)


def _pick_samples(
    groups: list[PatternGroup],
    category: DbCategory,
    records: dict[int, LogRecord],
    config: AnalysisConfig,
) -> tuple[EvidenceLine, ...]:
    limit = max(1, min(3, config.max_samples_per_finding))
    per_group = [_candidates(g, category, records, config) for g in groups]
    chosen: dict[int, EvidenceLine] = {}
    depth = 0
    while len(chosen) < limit and any(depth < len(c) for c in per_group):
        for cands in per_group:
            if depth < len(cands) and len(chosen) < limit:
                chosen.setdefault(cands[depth].line_no, cands[depth])
        depth += 1
    return tuple(chosen[k] for k in sorted(chosen))


def _db_signals(
    parse: ParseResult,
    patterns: PatternResult,
    analysis: CausalAnalysis,
    config: AnalysisConfig,
) -> tuple[DbSignal, ...]:
    records = {r.line_no: r for r in parse.records}
    hyps = _hypotheses(analysis)
    in_findings = {gid for f in analysis.findings for gid in f.group_ids}
    out: list[DbSignal] = []
    for cat in DbCategory:
        groups = [
            g for g in patterns.groups if g.id in in_findings and cat in g.db_categories
        ]
        if not groups:
            continue
        gids = {g.id for g in groups}
        fids = {f.id for f in analysis.findings if gids & set(f.group_ids)}
        hids = {h.id for h in hyps if cat in h.db_categories}
        window = TimeWindow(
            min(g.window.start for g in groups), max(g.window.end for g in groups)
        )
        out.append(
            DbSignal(
                category=cat,
                title=DB_CATEGORY_TITLES[cat],
                services=tuple(sorted({g.service for g in groups})),
                count=sum(g.count for g in groups),
                window=window,
                metrics=_merge_metrics(groups),
                samples=_pick_samples(groups, cat, records, config),
                finding_ids=tuple(sorted(fids, key=_num)),
                hypothesis_ids=tuple(sorted(hids, key=_num)),
            )
        )
    return tuple(out)


def _uncertainty(
    parse: ParseResult,
    timeline: Timeline,
    analysis: CausalAnalysis,
    signals: tuple[DbSignal, ...],
) -> tuple[UncertaintyItem, ...]:
    primary = tuple(sorted((h.id for h in analysis.causes.primary), key=_num))
    items = [
        UncertaintyItem(
            "The logs do not name the cause explicitly; the hypotheses are "
            "inferred from timing and dependencies and may be wrong.",
            primary,
        )
    ]
    seen = {items[0].statement}

    def add(statement: str, related: tuple[str, ...]) -> None:
        if statement not in seen:
            seen.add(statement)
            items.append(UncertaintyItem(statement, related))

    for sig in signals:
        related = tuple(
            sorted(set(sig.finding_ids) | set(sig.hypothesis_ids), key=_num)
        )
        add(UNCERTAINTY_BY_CATEGORY[sig.category], related)

    episodes = timeline.episodes
    loads = [h for h in _hypotheses(analysis) if h.kind is HypothesisKind.LOAD_SPIKE]
    if episodes and not any(h.verdict is Verdict.SUPPORTED for h in loads):
        related_load = tuple(h.id for h in loads) or tuple(e.id for e in episodes)
        add(LOAD_UNCERTAINTY, related_load)

    for e in episodes:
        if e.recovery_status is RecoveryStatus.OBSERVED_SHORT_WINDOW:
            add(SHORT_WINDOW_UNCERTAINTY.format(episode=e.id), (e.id,))
        elif e.recovery_status is RecoveryStatus.NOT_OBSERVED:
            add(
                f"Recovery of {e.id} is not observed in the log, "
                "so the incident may still be ongoing.",
                (e.id,),
            )

    if timeline.baseline_is_fallback:
        add(
            "The normal-behaviour baseline is a fallback window taken from the "
            "start of the log because anomalies are present from the beginning; "
            "comparisons with normal may be less reliable.",
            (),
        )
    n = len(parse.failures)
    if n:
        word = "line" if n == 1 else "lines"
        add(
            f"{n} log {word} could not be parsed and were not analysed; "
            "they may contain relevant evidence.",
            (),
        )
    return tuple(items)


def _main_order(analysis: CausalAnalysis) -> list[Hypothesis]:
    by_id = {f.id: f for f in analysis.findings}

    def key(h: Hypothesis) -> tuple[int, int, tuple[int, str]]:
        sev = min(
            (
                _SEVERITY_RANK[by_id[i].severity]
                for i in h.cause_finding_ids
                if i in by_id
            ),
            default=len(_SEVERITY_RANK),
        )
        return (sev, _CONFIDENCE_RANK[h.confidence], _num(h.id))

    return sorted(analysis.causes.primary, key=key)


def _category_order(
    categories: set[DbCategory], findings: tuple[Finding, ...]
) -> list[DbCategory]:
    def sev(c: DbCategory) -> int:
        return min(
            (_SEVERITY_RANK[f.severity] for f in findings if c in f.db_categories),
            default=len(_SEVERITY_RANK),
        )

    return sorted(categories, key=lambda c: (sev(c), _CATEGORY_RANK[c]))


def _rationale(sig: DbSignal | None, category: DbCategory) -> str:
    title = DB_CATEGORY_TITLES[category]
    if sig is None:
        return f"{title} is present in the findings."
    return (
        f"{title} appears in {', '.join(sig.services)} ({sig.count} events) "
        "and bears on the leading hypothesis."
    )


def _next_steps(
    signals: tuple[DbSignal, ...], analysis: CausalAnalysis
) -> tuple[NextStep, ...]:
    sig_by_cat = {s.category: s for s in signals}
    findings = analysis.findings
    steps: list[tuple[str, str, tuple[str, ...]]] = []
    used: set[str] = set()
    used_cats: set[DbCategory] = set()

    def related_for(category: DbCategory) -> tuple[str, ...]:
        sig = sig_by_cat.get(category)
        ids = {f.id for f in findings if category in f.db_categories}
        if sig is not None:
            ids |= set(sig.finding_ids) | set(sig.hypothesis_ids)
        return tuple(sorted(ids, key=_num))

    def push(action: str, rationale: str, related: tuple[str, ...]) -> None:
        if (
            action in used
            or not related
            or any(
                p in (action + " " + rationale).lower() for p in FORBIDDEN_STEP_PHRASES
            )
        ):
            return
        used.add(action)
        steps.append((action, rationale, related))

    def push_category(category: DbCategory, first_only: bool) -> None:
        used_cats.add(category)
        hints = NEXT_STEP_HINTS[category]
        for hint in hints[:1] if first_only else hints:
            push(
                hint,
                _rationale(sig_by_cat.get(category), category),
                related_for(category),
            )

    primaries = _main_order(analysis)
    for h in primaries:
        for cat in _category_order(
            {c for c in h.db_categories if c not in used_cats}, findings
        ):
            push_category(cat, first_only=False)
    for h in analysis.causes.alternatives:
        hint = HYPOTHESIS_STEP_HINTS.get(h.kind)
        if hint is not None:
            push(
                hint,
                f"Hypothesis {h.id} is an alternative explanation that the logs "
                "alone cannot settle.",
                (h.id,),
            )
    for cat in _category_order({s.category for s in signals} - used_cats, findings):
        push_category(cat, first_only=True)

    if not steps and findings and primaries:
        h = primaries[0]
        push(
            HYPOTHESIS_STEP_HINTS[HypothesisKind.PRE_ONSET_TRIGGER],
            f"Hypothesis {h.id} has no database signal, so the trigger on its source "
            "is the next thing to establish.",
            (h.id,),
        )
    if not steps and findings:
        f = findings[0]
        push(
            HYPOTHESIS_STEP_HINTS[HypothesisKind.PRE_ONSET_TRIGGER],
            f"Finding {f.id} has no database signal, so the trigger on its services "
            "is the next thing to establish.",
            (f.id,),
        )

    return tuple(
        NextStep(f"N{i}", i, action, rationale, related)
        for i, (action, rationale, related) in enumerate(steps[:MAX_NEXT_STEPS], 1)
    )


def recommend(
    parse: ParseResult,
    patterns: PatternResult,
    timeline: Timeline,
    analysis: CausalAnalysis,
    config: AnalysisConfig,
) -> Recommendations:
    """Collect DB signals, uncertainty items and next steps."""
    signals = _db_signals(parse, patterns, analysis, config)
    return Recommendations(
        db_signals=signals,
        uncertainty=_uncertainty(parse, timeline, analysis, signals),
        next_steps=_next_steps(signals, analysis),
    )


__all__ = ["recommend"]
