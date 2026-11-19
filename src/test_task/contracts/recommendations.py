"""DB signals, uncertainty and next steps (``recommendations`` contract).

Implementation: ``test_task.recommendations.recommend`` (``Recommend``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .causality import CausalAnalysis, HypothesisKind
from .common import AnalysisConfig, DbCategory, EvidenceLine, MetricSummary, TimeWindow
from .parser import ParseResult
from .patterns import PatternResult
from .timeline import Timeline

DB_CATEGORY_TITLES: Mapping[DbCategory, str] = {
    DbCategory.LATENCY: "Elevated database latency",
    DbCategory.POOL_EXHAUSTION: "Client connection pool exhaustion",
    DbCategory.SERVER_SATURATION: "Database server connection saturation",
    DbCategory.LOCKS: "Lock contention / lock waits",
    DbCategory.QUERY_TIMEOUT: "Database query/read timeouts",
    DbCategory.CONNECTION_ERRORS: "Database connection errors",
    DbCategory.ABORTED_TRANSACTIONS: "Connections dropped with open transactions",
    DbCategory.REPLICATION_LAG: "Replication lag",
    DbCategory.SERVER_RESTART: "Database server crash / restart",
}
"""DB signal title by category; defined for every ``DbCategory``."""

NEXT_STEP_HINTS: Mapping[DbCategory, tuple[str, ...]] = {
    DbCategory.LATENCY: (
        "Check pg_stat_statements for the slowest queries in the incident window and compare with baseline.",
        "Check DB host CPU, memory, disk I/O latency and IOPS during the incident window.",
    ),
    DbCategory.POOL_EXHAUSTION: (
        "Inspect pg_stat_activity for long-running and 'idle in transaction' sessions held by the affected services.",
        "Review application pool sizing (max pool size, acquire timeout) against max_connections.",
    ),
    DbCategory.SERVER_SATURATION: (
        "Check pg_stat_activity grouped by state/application_name and compare total with max_connections.",
        "Check whether a connection pooler (e.g. PgBouncer) is in place and its queue/wait metrics.",
    ),
    DbCategory.LOCKS: (
        "Inspect pg_locks joined with pg_stat_activity (pg_blocking_pids) for blocking chains.",
        "Check for long transactions or DDL/migrations running during the incident window.",
    ),
    DbCategory.QUERY_TIMEOUT: (
        "Check statement_timeout / client socket timeouts and pg_stat_statements for queries near the timeout.",
    ),
    DbCategory.CONNECTION_ERRORS: (
        "Check network path between services and the DB (packet loss, TCP resets, load balancer/NAT idle timeouts).",
        "Check PostgreSQL server log for disconnections and tcp_keepalives settings.",
    ),
    DbCategory.ABORTED_TRANSACTIONS: (
        "Check application logs and pg_stat_activity for transactions left open when client connections dropped, and whether the clients retried them.",
    ),
    DbCategory.REPLICATION_LAG: (
        "Check pg_stat_replication (write/flush/replay lag) and WAL generation rate.",
    ),
    DbCategory.SERVER_RESTART: (
        "Check the kernel log (dmesg/journalctl) for OOM killer events and the DB host memory usage and container memory limits around the crash time.",
        "Check the PostgreSQL server log around the crash for the process that was killed and verify crash recovery completed cleanly.",
    ),
}
"""Concrete infrastructure checks by DB signal category; defined for every
``DbCategory``, the first hint of a category is the most important."""

HYPOTHESIS_STEP_HINTS: Mapping[HypothesisKind, str] = {
    HypothesisKind.LOAD_SPIKE: "Compare the request rate at the entry point with load balancer or ingress metrics for the incident window.",
    HypothesisKind.PRE_ONSET_TRIGGER: "Check what triggered the unusual events on the source service shortly before the episode onset (scheduled jobs, configuration changes, maintenance).",
    HypothesisKind.EPISODE_LINK: "Check whether the resource that stayed above baseline between episodes (connections, memory) kept growing until the next episode.",
    HypothesisKind.CLIENT_SIDE: "Review application connection pool settings and pool metrics on the client services.",
}
"""Checks for alternative hypotheses by kind; SOURCE is covered by ``NEXT_STEP_HINTS``."""

UNCERTAINTY_BY_CATEGORY: Mapping[DbCategory, str] = {
    DbCategory.LATENCY: "The logs do not include query plans or statement statistics, so which queries became slow cannot be determined.",
    DbCategory.POOL_EXHAUSTION: "The logs contain no query-level records, so what held the client pool connections (long transactions, locks or slow queries) cannot be determined.",
    DbCategory.SERVER_SATURATION: "The logs contain no query-level records, so what held the database connections (long transactions, locks or slow queries) cannot be determined.",
    DbCategory.LOCKS: "The logs do not show which sessions held the locks or which statements were blocked.",
    DbCategory.QUERY_TIMEOUT: "The logs do not show which statements timed out or their execution plans.",
    DbCategory.CONNECTION_ERRORS: "The logs do not show whether connection failures came from the network path, the database itself or client-side timeouts.",
    DbCategory.ABORTED_TRANSACTIONS: "The logs do not show which transactions were aborted when connections dropped or whether their work was retried.",
    DbCategory.REPLICATION_LAG: "The logs do not show why replication fell behind (WAL volume, network or replica load).",
    DbCategory.SERVER_RESTART: "The logs show that the database process was killed but not who sent the signal (the OOM killer or an operator).",
}
"""What cannot be determined from the logs for each detected DB category; defined for every
``DbCategory``."""

LOAD_UNCERTAINTY = "The logs do not include traffic metrics upstream of the entry point, so whether incoming load changed cannot be fully determined."
"""Uncertainty item about incoming load: added if there are episodes and no
LOAD_SPIKE hypothesis has ``verdict == SUPPORTED``."""

SHORT_WINDOW_UNCERTAINTY = (
    "Recovery of {episode} is observed, but the observation window after it is short."
)
"""Item for an episode with ``RecoveryStatus.OBSERVED_SHORT_WINDOW``; ``{episode}`` is the episode id."""

MAX_NEXT_STEPS = 7
"""At most this many ``NextStep`` items."""

FORBIDDEN_STEP_PHRASES: tuple[str, ...] = ("may confirm or rule out",)
"""Boilerplate tails that must not appear in either ``action`` or ``rationale``."""


@dataclass(frozen=True)
class DbSignal:
    """A database-side signal supporting the conclusion.

    One entry per present category. The source is the pattern groups (``PatternGroup``) that
    have this category, not whole findings: ``services``, ``count``, ``window``,
    ``metrics`` and ``samples`` come only from these groups, so a service without DB signs in
    its own events is not included. ``title`` — ``DB_CATEGORY_TITLES[category]``;
    ``samples`` — 1..max_samples lines; ``finding_ids`` — findings containing these groups,
    ``hypothesis_ids`` — hypotheses with the category in ``db_categories``; existing ids, sorted.
    """

    category: DbCategory
    title: str
    services: tuple[str, ...]
    count: int
    window: TimeWindow
    metrics: tuple[MetricSummary, ...]
    samples: tuple[EvidenceLine, ...]
    finding_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]


@dataclass(frozen=True)
class UncertaintyItem:
    """What cannot be determined from the logs: an English sentence and related ids."""

    statement: str
    related_ids: tuple[str, ...]


@dataclass(frozen=True)
class NextStep:
    """What to check next. ``id`` — ``"N<n>"``, ``priority`` = n (1 comes first).

    ``action`` — a concrete check (system/metric/view), text from ``NEXT_STEP_HINTS``
    or ``HYPOTHESIS_STEP_HINTS``; ``rationale`` — a short English sentence on why
    (category/finding), without ``FORBIDDEN_STEP_PHRASES``; ``related_ids`` — a non-empty list
    of existing finding/hypothesis ids.
    """

    id: str
    priority: int
    action: str
    rationale: str
    related_ids: tuple[str, ...]


@dataclass(frozen=True)
class Recommendations:
    """Result.

    * ``db_signals`` — in ``DbCategory`` declaration order; empty if there are no DB signals.
    * ``uncertainty`` — always non-empty; items are generated from what was found, in
      this order: the root cause is not stated explicitly in the logs (always); ``UNCERTAINTY_BY_CATEGORY`` for
      each category in ``db_signals`` (in ``db_signals`` order; identical texts are not
      repeated); ``LOAD_UNCERTAINTY`` (condition in its docstring); per episode —
      ``SHORT_WINDOW_UNCERTAINTY`` or "recovery is not observed"; the baseline window was taken by
      the fallback rule; there are unparsed lines (with the count). No generic items unrelated
      to the findings (such as "no host metrics").
    * ``next_steps`` — non-empty if there are findings; 1..``MAX_NEXT_STEPS`` items without repeated
      ``action``. Ordered by relevance to the primary hypothesis: the primary hypothesis is the one in
      ``causes.primary`` whose ``cause_finding_ids`` have the highest severity (then higher confidence,
      then lower id). First the steps for its categories (categories by descending severity of findings
      with that category, then by ``DbCategory`` order; within a category, hints in order), then
      the other primaries, then ``HYPOTHESIS_STEP_HINTS`` for alternatives, then the first hints
      of the remaining ``db_signals`` categories; the list is truncated to ``MAX_NEXT_STEPS``.
    """

    db_signals: tuple[DbSignal, ...]
    uncertainty: tuple[UncertaintyItem, ...]
    next_steps: tuple[NextStep, ...]


class Recommend(Protocol):
    """``test_task.recommendations.recommend``: collect DB signals, uncertainty and steps.

    Pure deterministic function; texts are in English, without ``OVERCONFIDENT_PHRASES``.
    """

    def __call__(
        self,
        parse: ParseResult,
        patterns: PatternResult,
        timeline: Timeline,
        analysis: CausalAnalysis,
        config: AnalysisConfig,
    ) -> Recommendations: ...
