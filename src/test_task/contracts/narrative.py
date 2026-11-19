"""Incident Summary text (``narrative`` contract).

The text is built only deterministically from extracted facts.

Implementation: ``test_task.narrative.write_summary`` (``WriteSummary``) and
``test_task.narrative.validate_summary`` (``ValidateSummary``). The pipeline (``pipeline.analyze``)
gets the Incident Summary only via ``write_summary``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .causality import CausalAnalysis
from .recommendations import Recommendations
from .timeline import Timeline

SUMMARY_MIN_SENTENCES = 2
SUMMARY_MAX_SENTENCES = 4
"""Incident Summary length bounds in sentences. A sentence is a fragment ending with
``.``, ``!`` or ``?`` before whitespace/end of text."""

SERVICE_SUFFIXES: tuple[str, ...] = (
    "-service",
    "-worker",
    "-gateway",
    "-controller",
    "-primary",
    "-replica",
    "-cache",
    "-db",
    "-api",
)
"""Suffixes by which a text token is treated as a service name during validation."""


@dataclass(frozen=True)
class NarrativeFacts:
    """The only source of facts for the text: the text introduces no services, numbers or
    claims that are not here."""

    timeline: Timeline
    analysis: CausalAnalysis
    recommendations: Recommendations


class WriteSummary(Protocol):
    """``test_task.narrative.write_summary``: deterministic Incident Summary.

    The result passes ``validate_summary`` with no violations. Content:
    * the cause of each episode with CAUSE findings: service(s), what happened, start time and
      finding id — the episode's CAUSE finding with the highest severity (ties go to the earlier one);
    * episodes are listed by descending severity of that finding, then by onset, so the
      highest-severity finding among all CAUSE findings is named first;
    * if there is an EPISODE_LINK hypothesis — a mention of a possible link between episodes with its id;
    * the recovery state (``RecoveryStatus``) of the last episode.
    With no findings — 2 sentences saying no clear signs of an incident were found (with a
    hedge).
    """

    def __call__(self, facts: NarrativeFacts) -> str: ...


class ValidateSummary(Protocol):
    """``test_task.narrative.validate_summary``: list of violations (empty means the text is accepted).

    Checks: sentence count in [SUMMARY_MIN_SENTENCES, SUMMARY_MAX_SENTENCES]; contains a word from
    ``HEDGE_WORDS``; no ``OVERCONFIDENT_PHRASES``; every token ``[a-z0-9]+(-[a-z0-9]+)+``
    ending with a suffix from ``SERVICE_SUFFIXES`` is among the services in ``facts`` (services are
    not invented); every id token ``\\b[FHEN]\\d+\\b`` exists in ``facts``; for each episode with
    CAUSE findings, the id of its highest-severity CAUSE finding is mentioned. Violations are
    short English strings in check order.
    """

    def __call__(self, text: str, facts: NarrativeFacts) -> tuple[str, ...]: ...
