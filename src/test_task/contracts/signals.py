"""Signal extraction from records (the ``signals`` contract).

Each record maps to exactly one ``Event``: its own (intrinsic) signal kinds,
numeric metrics, DB relation marker, request identifiers and message template.
The "anomalous or normal" decision is not made here — ``baseline`` does that.

Rules rely only on field names, levels and keywords: no line numbers,
specific timestamps, IDs or service names of a particular file.

Implementation: ``test_task.signals.extract_events`` (``ExtractEvents``) and
``test_task.signals.message_template`` (``MessageTemplate``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .common import MetricUnit, SignalKind
from .parser import LogRecord

# --- Metrics (minimum guaranteed set; the implementation may extend it) ---

MS_SUFFIX = "_ms"
"""A numeric field with this suffix is a duration in ms (``MetricUnit.MS``), except ``MS_NON_METRICS``."""

MS_NON_METRICS: tuple[str, ...] = (
    "timeout_ms",
    "backoff_ms",
    "retry_in_ms",
    "interval_ms",
    "ttl_ms",
)
"""``*_ms`` fields that are settings rather than measurements; not treated as metrics."""

COUNT_METRICS: tuple[str, ...] = (
    "queue_depth",
    "pending_messages",
    "pending_events",
    "waiting_clients",
    "active_connections",
    "idle_connections",
    "pool_in_use",
    "pool_idle",
    "cache_version_lag",
)
"""Numeric counter fields (``MetricUnit.COUNT``)."""

RATE_SUFFIX = "_per_s"
"""A numeric field with this suffix is a rate (``MetricUnit.PER_SECOND``)."""

RATIO_METRICS: tuple[str, ...] = (
    "hit_rate",
    "success_rate",
    "success_rate_5m",
    "error_rate",
)
"""Numeric fraction fields 0..1 (``MetricUnit.RATIO``)."""

LOWER_IS_WORSE: tuple[str, ...] = (
    "idle_connections",
    "pool_idle",
    "hit_rate",
    "success_rate",
    "success_rate_5m",
)
"""Metrics where a drop is a degradation; also all ``*_per_s`` fields. For the rest, a rise is."""

METRIC_KIND_HINTS: tuple[tuple[str, SignalKind], ...] = (
    ("queue", SignalKind.QUEUE_BACKLOG),
    ("pending", SignalKind.QUEUE_BACKLOG),
    ("_age_ms", SignalKind.QUEUE_BACKLOG),
    ("pool", SignalKind.POOL_SATURATION),
    ("connections", SignalKind.POOL_SATURATION),
    ("waiting_clients", SignalKind.POOL_SATURATION),
    ("replication", SignalKind.REPLICATION_LAG),
    ("replay", SignalKind.REPLICATION_LAG),
)
"""Metric name substring → signal kind; first match wins. Otherwise: ``*_ms`` → LATENCY, else → WARNING."""

# --- DB relation ---

DB_FIELDS: tuple[str, ...] = (
    "db",
    "db_cluster",
    "db_latency_ms",
    "db_wait_ms",
    "pool_in_use",
    "pool_idle",
    "active_connections",
    "idle_connections",
    "waiting_clients",
    "max_connections",
    "txn_per_s",
)
"""Presence of any of these fields makes the event DB-related."""

DB_SERVER_FIELDS: tuple[str, ...] = (
    "db_cluster",
    "active_connections",
    "max_connections",
    "txn_per_s",
)
"""Fields that identify a service as a DB server (``Event.db_server``)."""

DB_KEYWORDS: tuple[str, ...] = (
    "postgres",
    "postgresql",
    "psql",
    "pq:",
    "jdbc",
    "hikari",
    "sqlstate",
    ":5432",
    "checkpoint",
    "redo ",
    "wal ",
)
"""Strong DB markers (driver, protocol, server housekeeping events), lowercase. Searched in the
record's own text: ``message`` and string field values, except ``RELAYED_FIELDS``.
The implementation may extend the list only with equally specific markers; generic words
("database", "connection reset") do not belong here."""

DB_MESSAGE_KEYWORDS: tuple[str, ...] = ("database", "db ")
"""Weak DB markers: make an event DB-related only if found in the record's own
``message`` (the service itself reports DB work, e.g. "db connect failed"), but not in
field values — there they relay someone else's error."""

RELAYED_FIELDS: tuple[str, ...] = ("reason",)
"""Relay fields: a cause passed on from another service (``retrying reason="db timeout acquiring
connection"``). Their values are excluded from signal keyword and DB marker search:
they yield neither RESTART, POOL_SATURATION, TIMEOUT nor ``db_related``."""

METRIC_LIMITS: tuple[tuple[str, str], ...] = (
    ("active_connections", "max_connections"),
    ("pool_in_use", "pool_max_size"),
    ("pool_in_use", "pool_size"),
)
"""Metric → its limit field (first matching pair). A service's limit is the max value of the
limit field across all of that service's records; used in ``MetricShift``."""

CONNECTION_COUNT_METRICS: tuple[str, ...] = ("active_connections", "pool_in_use")
"""Busy connection count metrics for the composite "connections held" signal;
throughput is the ``*_per_s`` fields of the same service."""

# --- Intrinsic signal keywords (case-insensitive search in message and string values) ---

RESTART_KEYWORDS: tuple[str, ...] = (
    "restart",
    "reinitializing",
    "terminating connection because of crash",
    "server processes terminated",
    "main process exited",
    "killed",
    "crash",
    "starting up",
    "was interrupted",
    "redo starts",
    "oomkilled",
    "back-off restarting",
)
"""Process restart. In the record's own ``message`` → RESTART; only in field values (not
``RELAYED_FIELDS``) → DEPENDENCY_UNAVAILABLE: the client got an error from a restarting
dependency and did not restart itself."""
TIMEOUT_KEYWORDS: tuple[str, ...] = ("timeout", "timed out", "deadline exceeded")
CONNECTION_ERROR_KEYWORDS: tuple[str, ...] = (
    "connection refused",
    "connection reset",
    "reset by peer",
    "broken pipe",
    "connection terminated",
    "connection has been closed",
    "closed the connection",
    "connection lost",
    "unexpected eof",
    "eofexception",
    "shut down incorrectly",
    "could not receive data",
    "could not send data",
    "unavailable",
)
CONNECTION_DROP_KEYWORDS: tuple[str, ...] = (
    "could not receive data",
    "could not send data",
    "unexpected eof",
    "broken pipe",
    "connection reset",
    "reset by peer",
)
"""Drop of an established connection (subset of CONNECTION_ERROR). If a record contains such a
word, its ``TIMEOUT_KEYWORDS`` matches do not yield TIMEOUT: "could not receive data from client:
Connection timed out" is a connection drop, not a query timeout. TIMEOUT from the
``timeout_ms`` field on a record of level >= ERROR still applies."""
ABORTED_TRANSACTION_KEYWORDS: tuple[str, ...] = (
    "with an open transaction",
    "in transaction",
    "transaction was aborted",
    "current transaction is aborted",
)
"""Connection drop with an open transaction → ``Event.aborted_transaction``."""
RECOVERY_KEYWORDS: tuple[str, ...] = (
    "ready to accept connections",
    "normalized",
    "recovered",
    "restored",
    "back to normal",
    "recovery complete",
)
"""Explicit recovery markers → ``Event.recovery_marker``. They yield no signal kinds."""
POOL_KEYWORDS: tuple[str, ...] = (
    "acquire connection",
    "acquiring connection",
    "connection is not available",
    "too many connections",
    "remaining connection slots",
    "pool exhausted",
)
LOCK_KEYWORDS: tuple[str, ...] = (
    "lock wait",
    "deadlock",
    "lock timeout",
    "waiting for lock",
    "could not obtain lock",
)
RETRY_KEYWORDS: tuple[str, ...] = ("retry", "retrying", "retried")
REPLICATION_KEYWORDS: tuple[str, ...] = (
    "replication lag",
    "replica lag",
    "replication delay",
    "wal receiver",
)

REQUEST_ID_FIELDS: tuple[str, ...] = ("request_id", "req_id", "trace_id")
"""Request identifier fields; the first one found is used."""
ORDER_ID_FIELDS: tuple[str, ...] = ("order_id",)
"""Business order identifier fields."""
PEER_FIELDS: tuple[str, ...] = ("upstream", "downstream", "target", "peer_service")
"""Fields naming a peer service; used for the dependency graph."""
STATUS_FIELD = "status"
"""HTTP status field; int 500–599 → ``SignalKind.SERVER_ERROR_STATUS``."""
ATTEMPT_FIELD = "attempt"
"""Attempt number field; int > 1 → ``SignalKind.RETRY``."""
RESTART_COUNT_FIELD = "restart_count"
"""An increase relative to the previous record of the same ``pod`` (or the same service) → RESTART."""
READY_FIELD = "ready"
"""``ready=false`` on a record with a ``probe`` field → ``SignalKind.PROBE_FAILURE``."""


@dataclass(frozen=True)
class MetricSample:
    """A numeric measurement from a record field.

    ``name`` — field name as in the log. ``kind`` — the signal kind the metric produces when it
    deviates (per ``METRIC_KIND_HINTS``). ``higher_is_worse`` — direction of degradation
    (``False`` for ``LOWER_IS_WORSE`` and ``*_per_s``). ``db_related`` — the name is in ``DB_FIELDS``
    or the whole event is DB-related.
    """

    name: str
    value: float
    unit: MetricUnit
    kind: SignalKind
    higher_is_worse: bool
    db_related: bool


@dataclass(frozen=True)
class Event:
    """Signals of a single record.

    * ``kinds`` — intrinsic kinds found by level, keywords and fields, unique, in
      ``SIGNAL_PRIORITY`` order. Level >= ERROR always adds ``ERROR``, WARN adds
      ``WARNING``. Metrics add no kinds here. Keywords are searched in ``message`` and
      string field values, except ``RELAYED_FIELDS``. RESTART comes only from
      ``message`` or ``restart_count``; otherwise DEPENDENCY_UNAVAILABLE. Keyword-based TIMEOUT
      is suppressed by ``CONNECTION_DROP_KEYWORDS``.
    * ``metrics`` — measurements in record field order.
    * ``db_related`` — DB marker in the record itself: a field from ``DB_FIELDS``, ``db_server``,
      a word from ``DB_KEYWORDS`` in the record's own text, or a word from ``DB_MESSAGE_KEYWORDS`` in
      ``message``. ``RELAYED_FIELDS`` values are ignored.
    * ``db_server`` — the record has a field from ``DB_SERVER_FIELDS``, or its service appears
      in at least one such record.
    * ``request_id``/``order_id`` — per ``REQUEST_ID_FIELDS``/``ORDER_ID_FIELDS`` (as string).
    * ``peers`` — ``PEER_FIELDS`` values (as string), unique, in order of appearance.
    * ``template`` — ``message_template(record)``.
    * ``aborted_transaction`` — ``ABORTED_TRANSACTION_KEYWORDS`` present plus CONNECTION_ERROR.
    * ``recovery_marker`` — ``RECOVERY_KEYWORDS`` present in the record's own text.
    * ``continuation_of`` — for a stack-trace line: ``line_no`` of the "head" — the nearest
      preceding record of the same service with the same ``request_id`` (both may be ``None``)
      that is not itself a continuation, with a time gap no larger than
      ``AnalysisConfig.continuation_max_gap_ms``; otherwise the record is its own head (``None``).
      A continuation has empty ``kinds``, and its intrinsic kinds (except ``ERROR``/``WARNING``)
      are merged into the head's ``kinds``.
    """

    record: LogRecord
    kinds: tuple[SignalKind, ...]
    metrics: tuple[MetricSample, ...]
    db_related: bool
    db_server: bool
    request_id: str | None
    order_id: str | None
    peers: tuple[str, ...]
    template: str
    continuation_of: int | None
    aborted_transaction: bool = False
    recovery_marker: bool = False


class MessageTemplate(Protocol):
    """``test_task.signals.message_template``: message template used for grouping.

    A deterministic function of the record. Guarantees:
    * records differing only in numbers, hex/uuid-like tokens, IPs/ports and
      field values produce the same template;
    * records with different message text (after masking) produce different templates;
    * if ``message`` is empty, the template is built from sorted field names, excluding
      identifiers (``REQUEST_ID_FIELDS``, ``ORDER_ID_FIELDS``, fields with the ``_id`` suffix),
      e.g. ``"{cache_hit,db_latency_ms,items,stage}"``;
    * the template contains no newlines and is at most 160 characters long.
    """

    def __call__(self, record: LogRecord) -> str: ...


class ExtractEvents(Protocol):
    """``test_task.signals.extract_events``: one ``Event`` per record, in the same order.

    A pure function with no I/O; ``continuation_of`` and ``db_server`` are computed over the whole
    sequence. Does not raise on valid ``LogRecord`` values.
    """

    def __call__(self, records: Sequence[LogRecord]) -> tuple[Event, ...]: ...
