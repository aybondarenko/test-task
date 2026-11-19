"""Signal extraction from records.

Rules rely only on levels, field names and keywords.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .contracts.common import (
    LEVEL_RANK,
    SIGNAL_PRIORITY,
    AnalysisConfig,
    FieldValue,
    Level,
    MetricUnit,
    SignalKind,
)
from .contracts.parser import LogRecord
from .contracts.signals import (
    ABORTED_TRANSACTION_KEYWORDS,
    ATTEMPT_FIELD,
    CONNECTION_DROP_KEYWORDS,
    CONNECTION_ERROR_KEYWORDS,
    COUNT_METRICS,
    DB_FIELDS,
    DB_KEYWORDS,
    DB_MESSAGE_KEYWORDS,
    DB_SERVER_FIELDS,
    LOCK_KEYWORDS,
    LOWER_IS_WORSE,
    METRIC_KIND_HINTS,
    MS_NON_METRICS,
    MS_SUFFIX,
    ORDER_ID_FIELDS,
    PEER_FIELDS,
    POOL_KEYWORDS,
    RATE_SUFFIX,
    RATIO_METRICS,
    READY_FIELD,
    RECOVERY_KEYWORDS,
    RELAYED_FIELDS,
    REPLICATION_KEYWORDS,
    REQUEST_ID_FIELDS,
    RESTART_COUNT_FIELD,
    RESTART_KEYWORDS,
    RETRY_KEYWORDS,
    STATUS_FIELD,
    TIMEOUT_KEYWORDS,
    Event,
    MetricSample,
)

TEMPLATE_MAX_LEN = 160

_KEYWORD_GROUPS: tuple[tuple[SignalKind, tuple[str, ...]], ...] = (
    (SignalKind.CONNECTION_ERROR, CONNECTION_ERROR_KEYWORDS),
    (SignalKind.TIMEOUT, TIMEOUT_KEYWORDS),
    (SignalKind.POOL_SATURATION, POOL_KEYWORDS),
    (SignalKind.LOCK, LOCK_KEYWORDS),
    (SignalKind.RETRY, RETRY_KEYWORDS),
    (SignalKind.REPLICATION_LAG, REPLICATION_KEYWORDS),
)

_ERROR_RANK = LEVEL_RANK[Level.ERROR]


def _is_number(value: FieldValue) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _field_texts(record: LogRecord) -> list[str]:
    """String field values, excluding relayed causes from other services (RELAYED_FIELDS)."""
    return [
        v.lower()
        for k, v in record.fields.items()
        if isinstance(v, str) and k not in RELAYED_FIELDS
    ]


def _texts(record: LogRecord) -> list[str]:
    return [record.message.lower(), *_field_texts(record)]


def _has(texts: Sequence[str], words: Sequence[str]) -> bool:
    return any(w in t for t in texts for w in words)


def _as_false(value: FieldValue) -> bool:
    if isinstance(value, bool):
        return value is False
    return isinstance(value, str) and value.strip().lower() == "false"


def _first_str(fields: Mapping[str, FieldValue], names: Sequence[str]) -> str | None:
    for name in names:
        if name in fields:
            return str(fields[name])
    return None


def _intrinsic_kinds(record: LogRecord) -> set[SignalKind]:
    """Signal kinds of a record, ignoring restart_count (it depends on neighboring records)."""
    kinds: set[SignalKind] = set()
    fields = record.fields
    rank = LEVEL_RANK[record.level]
    if rank >= _ERROR_RANK:
        kinds.add(SignalKind.ERROR)
    elif record.level is Level.WARN:
        kinds.add(SignalKind.WARNING)

    message = [record.message.lower()]
    texts = _texts(record)
    if _has(message, RESTART_KEYWORDS):
        kinds.add(SignalKind.RESTART)
    elif _has(_field_texts(record), RESTART_KEYWORDS):
        kinds.add(SignalKind.DEPENDENCY_UNAVAILABLE)
    dropped = _has(texts, CONNECTION_DROP_KEYWORDS)
    for kind, words in _KEYWORD_GROUPS:
        if kind is SignalKind.TIMEOUT and dropped:
            continue
        if _has(texts, words):
            kinds.add(kind)

    status = fields.get(STATUS_FIELD)
    if status is not None and _is_number(status) and 500 <= int(status) <= 599:
        kinds.add(SignalKind.SERVER_ERROR_STATUS)
    attempt = fields.get(ATTEMPT_FIELD)
    if (
        isinstance(attempt, (int, float))
        and not isinstance(attempt, bool)
        and attempt > 1
    ):
        kinds.add(SignalKind.RETRY)
    if "probe" in fields and READY_FIELD in fields and _as_false(fields[READY_FIELD]):
        kinds.add(SignalKind.PROBE_FAILURE)
    if rank >= _ERROR_RANK and "timeout_ms" in fields:
        kinds.add(SignalKind.TIMEOUT)
    for name in ("pool_idle", "idle_connections"):
        v = fields.get(name)
        if v is not None and _is_number(v) and v == 0:
            kinds.add(SignalKind.POOL_SATURATION)
    waiting = fields.get("waiting_clients")
    if (
        isinstance(waiting, (int, float))
        and not isinstance(waiting, bool)
        and waiting > 0
    ):
        kinds.add(SignalKind.POOL_SATURATION)
    return kinds


def _is_db_related(record: LogRecord) -> bool:
    if any(name in record.fields for name in DB_FIELDS):
        return True
    if _has(_texts(record), DB_KEYWORDS):
        return True
    return _has([record.message.lower()], DB_MESSAGE_KEYWORDS)


def _metric_kind(name: str, unit: MetricUnit) -> SignalKind:
    for hint, kind in METRIC_KIND_HINTS:
        if hint in name:
            return kind
    return SignalKind.LATENCY if unit is MetricUnit.MS else SignalKind.WARNING


def _metrics(record: LogRecord, event_db: bool) -> tuple[MetricSample, ...]:
    out: list[MetricSample] = []
    for name, value in record.fields.items():
        if not _is_number(value):
            continue
        unit: MetricUnit | None
        if name.endswith(MS_SUFFIX):
            unit = None if name in MS_NON_METRICS else MetricUnit.MS
        elif name.endswith(RATE_SUFFIX):
            unit = MetricUnit.PER_SECOND
        elif name in COUNT_METRICS:
            unit = MetricUnit.COUNT
        elif name in RATIO_METRICS:
            unit = MetricUnit.RATIO
        else:
            unit = None
        if unit is None:
            continue
        assert isinstance(value, (int, float))
        out.append(
            MetricSample(
                name=name,
                value=float(value),
                unit=unit,
                kind=_metric_kind(name, unit),
                higher_is_worse=not (
                    name in LOWER_IS_WORSE or unit is MetricUnit.PER_SECOND
                ),
                db_related=name in DB_FIELDS or event_db,
            )
        )
    return tuple(out)


_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?")
_HEX0X = re.compile(r"\b0[xX][0-9a-fA-F]+\b")
_HEXBARE = re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{12,}\b")
_NUM = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?(?![A-Za-z_])")
_WS = re.compile(r"\s+")


def message_template(record: LogRecord) -> str:
    """Message template: variable parts masked; for an empty message, the field names."""
    message = _WS.sub(" ", record.message).strip()
    if not message:
        names = sorted(
            n
            for n in record.fields
            if n not in REQUEST_ID_FIELDS
            and n not in ORDER_ID_FIELDS
            and not n.endswith("_id")
        )
        text = "{" + ",".join(names) + "}"
    else:
        text = _UUID.sub("<uuid>", message)
        text = _IP.sub("<ip>", text)
        text = _HEX0X.sub("<hex>", text)
        text = _HEXBARE.sub("<hex>", text)
        text = _NUM.sub("<n>", text)
    text = _WS.sub(" ", text)
    return text[:TEMPLATE_MAX_LEN]


def _restart_flags(records: Sequence[LogRecord]) -> list[bool]:
    last: dict[tuple[str, str], float] = {}
    flags: list[bool] = []
    for rec in records:
        value = rec.fields.get(RESTART_COUNT_FIELD)
        if value is None or not _is_number(value):
            flags.append(False)
            continue
        pod = rec.fields.get("pod")
        key = ("pod", str(pod)) if pod is not None else ("svc", rec.service)
        prev = last.get(key)
        assert isinstance(value, (int, float))
        flags.append(prev is not None and value > prev)
        last[key] = value
    return flags


def extract_events(records: Sequence[LogRecord]) -> tuple[Event, ...]:
    """One ``Event`` per record, in the same order."""
    max_gap = AnalysisConfig().continuation_max_gap_ms / 1000.0
    db_services = {
        r.service for r in records if any(f in r.fields for f in DB_SERVER_FIELDS)
    }
    restarts = _restart_flags(records)

    kind_sets: list[set[SignalKind]] = []
    for rec, restarted in zip(records, restarts):
        ks = _intrinsic_kinds(rec)
        if restarted:
            ks.add(SignalKind.RESTART)
        kind_sets.append(ks)

    cont_of: list[int | None] = []
    heads: dict[tuple[str, str | None], tuple[int, int]] = {}
    for i, rec in enumerate(records):
        rid = _first_str(rec.fields, REQUEST_ID_FIELDS)
        key = (rec.service, rid)
        head: int | None = None
        if rec.is_continuation and key in heads:
            idx, _ = heads[key]
            gap = abs((rec.ts - records[idx].ts).total_seconds())
            if gap <= max_gap:
                head = idx
        if head is None:
            heads[key] = (i, rec.line_no)
            cont_of.append(None)
        else:
            cont_of.append(records[head].line_no)
            kind_sets[head] |= kind_sets[i] - {SignalKind.ERROR, SignalKind.WARNING}

    events: list[Event] = []
    for i, rec in enumerate(records):
        is_cont = cont_of[i] is not None
        kinds = (
            () if is_cont else tuple(k for k in SIGNAL_PRIORITY if k in kind_sets[i])
        )
        db_server = rec.service in db_services
        db_related = db_server or _is_db_related(rec)
        texts = _texts(rec)
        peers: list[str] = []
        for name in PEER_FIELDS:
            if name in rec.fields:
                p = str(rec.fields[name])
                if p not in peers:
                    peers.append(p)
        events.append(
            Event(
                record=rec,
                kinds=kinds,
                metrics=_metrics(rec, db_related),
                db_related=db_related,
                db_server=db_server,
                request_id=_first_str(rec.fields, REQUEST_ID_FIELDS),
                order_id=_first_str(rec.fields, ORDER_ID_FIELDS),
                peers=tuple(peers),
                template=message_template(rec),
                continuation_of=cont_of[i],
                aborted_transaction=(
                    SignalKind.CONNECTION_ERROR in kind_sets[i]
                    and _has(texts, ABORTED_TRANSACTION_KEYWORDS)
                ),
                recovery_marker=_has(texts, RECOVERY_KEYWORDS),
            )
        )
    return tuple(events)
