"""End-to-end analysis pipeline without I/O (``pipeline`` contract).

Step order: ``parse_lines`` → ``extract_events`` → ``build_baseline`` → ``score_events`` →
``find_patterns`` → ``build_timeline`` → ``compare_windows`` → ``build_service_graph`` →
``assess`` → ``recommend`` → ``write_summary`` → ``build_report``.

Implementation: ``test_task.pipeline.analyze`` (``Analyze``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .common import AnalysisConfig
from .report import Report


@dataclass(frozen=True)
class SourceRef:
    """Input identification for the report: file base name and sha256 of its bytes (lowercase hex)."""

    file_name: str
    sha256: str


class Analyze(Protocol):
    """``test_task.pipeline.analyze``: log lines → ``Report``.

    * ``lines`` — file lines in order (with or without ``\\n``).
    * ``config`` — ``None`` means ``AnalysisConfig()``.
    * ``Report.incident_summary`` = ``narrative.write_summary(NarrativeFacts(timeline, analysis,
      recommendations))`` — deterministic text only, no network.
    * The result is a pure function of (``lines``, ``source``, ``config``): the same input gives
      an equal ``Report``. The result is derived only from the input — no constants
      specific to a particular log.
    * Never raises on any text input, including empty input.
    """

    def __call__(
        self,
        lines: Sequence[str],
        source: SourceRef,
        config: AnalysisConfig | None = None,
    ) -> Report: ...
