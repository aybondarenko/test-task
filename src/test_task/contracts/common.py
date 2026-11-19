"""Shared types of the incident analysis pipeline (the ``common`` contract).

All models are immutable ``dataclass(frozen=True)``; sequences are ``tuple``
(deterministic order). Times are ``datetime`` with tzinfo=UTC.
Python 3.9 compatibility: ``X | Y`` only in annotations (they are lazy thanks to
``from __future__ import annotations``), ``Union`` in evaluated expressions; no
``slots``/``kw_only``; do not call ``typing.get_type_hints`` on these models under 3.9.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Union

FieldValue = Union[str, int, float, bool]
"""Value of a ``key=value`` field after type coercion."""

JsonValue = Union[
    None, bool, int, float, str, list["JsonValue"], dict[str, "JsonValue"]
]
"""A value serializable by ``json.dumps`` without extra conversion."""


class Level(str, Enum):
    """Log record level after normalization. Severity order is ``LEVEL_RANK``."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


LEVEL_RANK: Mapping[Level, int] = {
    Level.DEBUG: 0,
    Level.INFO: 1,
    Level.WARN: 2,
    Level.ERROR: 3,
    Level.FATAL: 4,
}
"""Level rank: "level >= ERROR" means ``LEVEL_RANK[l] >= LEVEL_RANK[Level.ERROR]``."""

LEVEL_ALIASES: Mapping[str, Level] = {
    "DEBUG": Level.DEBUG,
    "TRACE": Level.DEBUG,
    "INFO": Level.INFO,
    "NOTICE": Level.INFO,
    "WARN": Level.WARN,
    "WARNING": Level.WARN,
    "ERROR": Level.ERROR,
    "ERR": Level.ERROR,
    "FATAL": Level.FATAL,
    "CRITICAL": Level.FATAL,
    "PANIC": Level.FATAL,
}
"""Accepted level spellings (case-insensitive). Anything else is a parse error."""


class SignalKind(str, Enum):
    """Signal/pattern kind. Priority when choosing the primary kind is ``SIGNAL_PRIORITY``."""

    RESTART = "restart"
    """Crash, restart or shutdown of a process/server that restarts itself: a restart keyword
    in the record's own ``message`` or an increase of ``restart_count``."""
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    """A dependency (e.g. the DB) is unavailable to the client: the restart keyword occurs only in
    the record's field values (``error="... the database system is starting up"``), i.e. the client
    relays another service's state and does not restart itself."""
    CONNECTION_ERROR = "connection_error"
    """Connection dropped or refused: refused, reset, broken pipe, EOF, closed, unavailable."""
    TIMEOUT = "timeout"
    """Timeout: "timeout", "timed out", a ``timeout_ms`` field on a failure event."""
    SERVER_ERROR_STATUS = "server_error_status"
    """Numeric ``status`` field in the 500–599 range."""
    POOL_SATURATION = "pool_saturation"
    """Connection shortage in the client pool or on the DB server (waiting, idle=0, waiting>0)."""
    LOCK = "lock"
    """Locks: lock wait, deadlock, lock timeout."""
    ERROR = "error"
    """A record of level >= ERROR with no more specific kind."""
    RETRY = "retry"
    """Retry: retry/retrying or ``attempt`` > 1."""
    REPLICATION_LAG = "replication_lag"
    """DB replica lag."""
    QUEUE_BACKLOG = "queue_backlog"
    """Queue growth/processing backlog: queue_depth, pending_*, *_age_ms, queue_delay_ms."""
    LATENCY = "latency"
    """Duration (a ``*_ms`` field) well above baseline."""
    PROBE_FAILURE = "probe_failure"
    """Failed readiness/liveness probe (``ready=false``)."""
    WARNING = "warning"
    """A WARN-level record with no more specific kind."""


SIGNAL_PRIORITY: tuple[SignalKind, ...] = (
    SignalKind.RESTART,
    SignalKind.DEPENDENCY_UNAVAILABLE,
    SignalKind.CONNECTION_ERROR,
    SignalKind.TIMEOUT,
    SignalKind.SERVER_ERROR_STATUS,
    SignalKind.POOL_SATURATION,
    SignalKind.LOCK,
    SignalKind.ERROR,
    SignalKind.RETRY,
    SignalKind.REPLICATION_LAG,
    SignalKind.QUEUE_BACKLOG,
    SignalKind.LATENCY,
    SignalKind.PROBE_FAILURE,
    SignalKind.WARNING,
)
"""Ordered from more specific/severe to less; an event's primary kind is the first match."""


class DbCategory(str, Enum):
    """DB signal category."""

    LATENCY = "db_latency"
    """Increase of ``db_latency_ms``/``db_wait_ms``/query duration relative to baseline."""
    POOL_EXHAUSTION = "pool_exhaustion"
    """Client-side connection pool exhaustion (pool_idle=0, waiting/timeout acquiring a connection)."""
    SERVER_SATURATION = "server_saturation"
    """DB server saturation: active_connections close to max_connections, waiting_clients > 0."""
    LOCKS = "locks"
    """Locks and lock waits."""
    QUERY_TIMEOUT = "query_timeout"
    """DB query/read timeouts."""
    CONNECTION_ERRORS = "connection_errors"
    """DB connection errors/drops (on either side), including "DB unavailable" at clients.
    Connection drops are not counted as query timeouts."""
    ABORTED_TRANSACTIONS = "aborted_transactions"
    """Aborted transactions: the connection dropped while a transaction was open
    (``Event.aborted_transaction``)."""
    REPLICATION_LAG = "replication_lag"
    """Replication lag."""
    SERVER_RESTART = "server_restart"
    """DB server crash/restart/recovery."""


class Severity(str, Enum):
    """Finding severity scale. Criteria are in ``SEVERITY_CRITERIA``."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_CRITERIA: tuple[tuple[Severity, str], ...] = (
    (
        Severity.CRITICAL,
        (
            "User-facing requests fail (5xx/timeouts at an entry point) or a data store is unavailable "
            "(crash, restart, connections refused)."
        ),
    ),
    (
        Severity.HIGH,
        (
            "Errors inside a service or exhausted shared resources (connection pool, DB connections) "
            "that cause failed or aborted operations."
        ),
    ),
    (
        Severity.MEDIUM,
        (
            "Degradation without outright failure: latency well above baseline, retries that "
            "eventually succeed, growing queues/backlogs."
        ),
    ),
    (
        Severity.LOW,
        "Isolated warnings or minor deviations with no visible effect on requests.",
    ),
)
"""Documented severity scale, printed into the report as is. Ordered from highest."""


class Confidence(str, Enum):
    """Hypothesis confidence level."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Role(str, Enum):
    """Role of a finding in the incident."""

    CAUSE = "cause"
    """Probable cause (or one of the cause candidates)."""
    SYMPTOM = "symptom"
    """Symptom/downstream effect."""


class MetricUnit(str, Enum):
    """Metric unit."""

    MS = "ms"
    """Milliseconds (``*_ms`` fields)."""
    COUNT = "count"
    """Counter/quantity (queue_depth, waiting_clients, pool_in_use, ...)."""
    PER_SECOND = "per_s"
    """Rate (``*_per_s`` fields)."""
    RATIO = "ratio"
    """Fraction 0..1 (hit_rate, success_rate_*)."""


@dataclass(frozen=True)
class TimeWindow:
    """Time window, both bounds inclusive; ``start <= end``, UTC."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class EvidenceLine:
    """A sample line used as evidence.

    ``line_no`` — physical line number in the input file (1-based). ``text`` — the original line,
    truncated to ``AnalysisConfig.max_evidence_chars`` characters (with "..." when truncated).
    """

    line_no: int
    ts: datetime
    service: str
    level: Level
    text: str


@dataclass(frozen=True)
class MetricStats:
    """Metric statistics over a set of values.

    Percentiles are nearest-rank: ``pN`` = the k-th smallest value, k = ceil(N/100 * count), k >= 1.
    """

    count: int
    p50: float
    p95: float
    max: float


@dataclass(frozen=True)
class MetricSummary:
    """Metric summary within a group/finding: value count, range and baseline for comparison.

    ``baseline_p95`` — p95 of the same metric of the same service in the baseline window, ``None`` if no data.
    """

    metric: str
    unit: MetricUnit
    count: int
    min: float
    p50: float
    max: float
    baseline_p95: float | None


@dataclass(frozen=True)
class AnalysisConfig:
    """Heuristic parameters. Defaults are part of the contract; tests may override them.

    No parameter depends on a specific log file.
    """

    bucket_seconds: int = 60
    """Time bucket width for the timeline."""
    active_bucket_min_events: int = 2
    """A bucket is "active" if it has at least this many anomalous events."""
    episode_merge_gap_seconds: int = 120
    """Active buckets separated by no more than this are merged into one episode."""
    recovery_quiet_seconds: int = 120
    """Recovery is confirmed if the log has this many seconds without active buckets after the episode."""
    min_baseline_seconds: int = 60
    """Minimum baseline window duration; if shorter, the fallback is used."""
    baseline_fallback_fraction: float = 0.1
    """Fraction of the start of the log used as baseline if anomalies are present from the very start."""
    min_baseline_samples: int = 3
    """Minimum number of metric values in the baseline required to compare against it."""
    deviation_factor: float = 3.0
    """A metric is "above normal" if value > deviation_factor * baseline p95."""
    min_abs_delta_ms: float = 50.0
    """...and exceeds baseline p95 by at least this many ms (for ms units)."""
    min_abs_delta_count: float = 5.0
    """...and exceeds baseline p95 by at least this many units (for count units)."""
    drop_ratio: float = 0.5
    """A metric is "below normal" (for lower-is-worse) if value < drop_ratio * baseline p50."""
    pool_saturation_ratio: float = 0.9
    """The DB server is saturated if active_connections >= ratio * max_connections."""
    group_gap_seconds: int = 300
    """Events of the same (kind, service) separated by more than this go into different groups."""
    continuation_max_gap_ms: int = 1000
    """A stack-trace line is attached to its "head" if the time gap is no larger than this."""
    cause_window_seconds: int = 30
    """Findings starting no later than onset + this window are cause candidates for the episode."""
    max_samples_per_finding: int = 3
    """Maximum sample lines per group/finding/DB signal; minimum is 1."""
    max_example_traces: int = 3
    """How many request traces to show in the report."""
    max_parse_failures_shown: int = 20
    """How many unparsed lines to show in the report; the counter is always complete."""
    max_evidence_chars: int = 240
    """Maximum length of ``EvidenceLine.text``."""
    pre_onset_window_seconds: int = 60
    """Window before an episode's onset in which unusual events of the source service are
    searched for as trigger candidates."""
    load_spike_ratio: float = 1.5
    """A load spike is supported by the log if the entry point's head rate in the episode is at least
    ``load_spike_ratio`` × its rate in the baseline window."""
    composite_rise_ratio: float = 1.5
    """Composite "connections held" signal: connection count rose to at least
    ``composite_rise_ratio`` × baseline p50."""
    composite_drop_ratio: float = 0.75
    """...while throughput (``*_per_s``) fell to at most
    ``composite_drop_ratio`` × baseline p50. The same threshold selects lower-is-worse metrics for
    ``MetricShift``."""
    max_evidence_metrics: int = 2
    """How many strongest metric deviations (``MetricShift``) to show in a hypothesis argument."""


HEDGE_WORDS: tuple[str, ...] = (
    "likely",
    "probably",
    "probable",
    "possibly",
    "appears",
    "suggests",
    "consistent with",
    "may",
    "might",
)
"""Hedge words. Matched on word boundaries, case-insensitive."""

OVERCONFIDENT_PHRASES: tuple[str, ...] = (
    "the root cause is",
    "the root cause was",
    "definitely",
    "certainly",
    "undoubtedly",
    "without doubt",
    "proves that",
)
"""Forbidden overconfident phrasing: must not appear anywhere in report text."""
