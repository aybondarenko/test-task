"""Incident Summary: deterministic writer and validator."""

from __future__ import annotations

import re
from datetime import datetime

from .contracts.causality import CausalAnalysis, Finding, HypothesisKind
from .contracts.common import HEDGE_WORDS, OVERCONFIDENT_PHRASES, Role, Severity
from .contracts.narrative import (
    SERVICE_SUFFIXES,
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    NarrativeFacts,
)
from .contracts.timeline import RecoveryStatus

_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_SERVICE_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
_ID_TOKEN = re.compile(r"\b[FHEN]\d+\b")


def _known_services(analysis: CausalAnalysis) -> set[str]:
    out = {s.service for s in analysis.graph.services}
    for f in analysis.findings:
        out.update(f.services)
    out.update(s.service for s in analysis.impact.services)
    return out


def _known_ids(facts: NarrativeFacts) -> set[str]:
    a = facts.analysis
    ids = {f.id for f in a.findings}
    ids.update(h.id for h in a.causes.primary)
    ids.update(h.id for h in a.causes.alternatives)
    ids.update(e.id for e in facts.timeline.episodes)
    ids.update(n.id for n in facts.recommendations.next_steps)
    return ids


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}


def _fnum(f: Finding) -> int:
    return int(f.id[1:])


def _top_causes(analysis: CausalAnalysis) -> dict[str, Finding]:
    """Episode → its highest-severity CAUSE finding (ties go to the earlier one)."""
    out: dict[str, Finding] = {}
    for f in analysis.findings:
        if f.role != Role.CAUSE or f.episode_id is None:
            continue
        cur = out.get(f.episode_id)
        if cur is None or _cause_key(f) < _cause_key(cur):
            out[f.episode_id] = f
    return out


def _cause_key(f: Finding) -> tuple[int, datetime, int]:
    return (_SEVERITY_RANK[f.severity], f.window.start, _fnum(f))


def _describe(f: Finding) -> str:
    kind = f.kind.value.replace("_", " ")
    when = f.window.start.strftime("%H:%M:%S")
    return (
        f"{_join(list(f.services))} {kind} at {when} UTC ({f.id}, {f.severity.value})"
    )


def _recovery_sentence(facts: NarrativeFacts) -> str | None:
    episodes = facts.timeline.episodes
    if not episodes:
        return None
    last = max(episodes, key=lambda e: (e.onset, e.id))
    if last.recovery_status == RecoveryStatus.NOT_OBSERVED:
        return f"Recovery after {last.id} is not observed in the log."
    if last.recovery_status == RecoveryStatus.OBSERVED_SHORT_WINDOW:
        return (
            f"Recovery after {last.id} was observed, "
            "but the observation window is short."
        )
    return f"Recovery after {last.id} was observed in the log."


def write_summary(facts: NarrativeFacts) -> str:
    """Deterministic Incident Summary from facts (2–4 sentences, hedged)."""
    findings = sorted(facts.analysis.findings, key=lambda f: (f.window.start, f.id))
    if not findings:
        return (
            "No clear signs of an incident were detected in the analyzed log. "
            "This may change with data outside the log, as the analysis is limited to logged events."
        )
    episodes = facts.timeline.episodes
    services = sorted({s for f in findings for s in f.services})
    if episodes:
        first = min(episodes, key=lambda e: (e.onset, e.id))
        s1 = (
            f"The log appears to show {_plural(len(episodes), 'degradation episode')}, "
            f"the first starting at {first.onset.strftime('%H:%M:%S')} UTC, "
            f"with {_plural(len(findings), 'finding')} across {_plural(len(services), 'service')}."
        )
    else:
        s1 = (
            f"The log appears to show {_plural(len(findings), 'finding')} across "
            f"{_plural(len(services), 'service')} outside any detected degradation episode."
        )
    tops = sorted(_top_causes(facts.analysis).values(), key=_cause_key)
    if tops:
        entries = "; ".join(_describe(f) for f in tops)
        s2 = f"The likely causes, by severity, are: {entries}."
    else:
        causes = [f for f in findings if f.role == Role.CAUSE] or findings[:1]
        s2 = f"The degradation likely originated as follows: {_describe(causes[0])}."
    parts = [s1, s2]
    links = [
        h
        for h in (*facts.analysis.causes.primary, *facts.analysis.causes.alternatives)
        if h.kind == HypothesisKind.EPISODE_LINK
    ]
    if links:
        ids = _join([h.id for h in links])
        parts.append(f"The episodes may be linked ({ids}).")
    rec = _recovery_sentence(facts)
    if rec is not None:
        parts.append(rec)
    return " ".join(parts)


def validate_summary(text: str, facts: NarrativeFacts) -> tuple[str, ...]:
    """List of violations (empty means the text is accepted)."""
    violations: list[str] = []
    n = len(_SENTENCE_END.findall(text.strip()))
    if not SUMMARY_MIN_SENTENCES <= n <= SUMMARY_MAX_SENTENCES:
        violations.append(
            f"Summary has {n} sentences; expected {SUMMARY_MIN_SENTENCES} to {SUMMARY_MAX_SENTENCES}"
        )
    if not any(
        re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in HEDGE_WORDS
    ):
        violations.append("Summary has no hedging word")
    low = text.lower()
    for phrase in OVERCONFIDENT_PHRASES:
        if phrase in low:
            violations.append(f"Summary uses overconfident phrase '{phrase}'")
    services = _known_services(facts.analysis)
    for tok in _SERVICE_TOKEN.findall(text):
        if tok.endswith(SERVICE_SUFFIXES) and tok not in services:
            violations.append(f"Summary mentions unknown service '{tok}'")
    ids = _known_ids(facts)
    mentioned = set(_ID_TOKEN.findall(text))
    for tok in _ID_TOKEN.findall(text):
        if tok not in ids:
            violations.append(f"Summary references unknown id '{tok}'")
    for ep_id, cause in sorted(_top_causes(facts.analysis).items()):
        if cause.id not in mentioned:
            violations.append(f"Summary omits cause {cause.id} of episode {ep_id}")
    return tuple(violations)
