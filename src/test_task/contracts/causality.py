"""Findings, cause vs symptoms, hypotheses and impact (the ``causality`` contract).

Implementation: ``test_task.causality.build_service_graph`` (``BuildServiceGraph``) and
``test_task.causality.assess`` (``Assess``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from .baseline import ScoredEvent
from .common import (
    AnalysisConfig,
    Confidence,
    DbCategory,
    EvidenceLine,
    MetricSummary,
    MetricUnit,
    Role,
    Severity,
    SignalKind,
    TimeWindow,
)
from .patterns import PatternResult, TemplateCount
from .timeline import Timeline, WindowComparison


class EdgeSource(str, Enum):
    """How the dependency ``dependent → dependency`` is known."""

    PEER_FIELD = "peer_field"
    """A ``dependent`` record has a ``PEER_FIELDS`` field whose value is ``dependency``."""
    DB_ACCESS = "db_access"
    """``dependent`` (not a DB server) has its own DB access events — at least one event with
    ``Event.db_related`` (DB marker in the record itself, not via ``RELAYED_FIELDS``); ``dependency``
    is the DB server service. A service that only relays others' DB errors gets no edge."""
    TRACE_ORDER = "trace_order"
    """In a request trace ``dependent`` first appears after ``dependency``; added only if there is
    no PEER_FIELD/DB_ACCESS edge in the opposite direction."""


class ServiceClass(str, Enum):
    """Service layer."""

    DB_SERVER = "db_server"
    """Has events with ``Event.db_server``."""
    ENTRY_POINT = "entry_point"
    """Has an outgoing PEER_FIELD edge and no incoming PEER_FIELD edges (gateway/entry)."""
    INTERNAL = "internal"
    """All other services."""


@dataclass(frozen=True)
class ServiceEdge:
    """``dependent`` depends on ``dependency``: problems in ``dependency`` may surface in ``dependent``.

    ``count`` — number of supporting records/traces.
    """

    dependent: str
    dependency: str
    source: EdgeSource
    count: int


@dataclass(frozen=True)
class ServiceInfo:
    """A service and its layer."""

    service: str
    service_class: ServiceClass


@dataclass(frozen=True)
class ServiceGraph:
    """Dependency graph inferred from the log alone.

    ``services`` — all services in the log, by name. ``edges`` — by (dependent, dependency, source),
    no self-loops; a (dependent, dependency) pair may have several sources.
    """

    services: tuple[ServiceInfo, ...]
    edges: tuple[ServiceEdge, ...]


@dataclass(frozen=True)
class MetricShift:
    """Deviation of a service metric from baseline within a window.

    Values are taken from all of the service's heads in ``window`` (anomalous or not); baseline comes
    from ``WindowComparison`` (p50/p95 of the baseline window).

    * ``low``/``high`` — min and max in the window; ``worst`` — ``high`` for higher-is-worse,
      otherwise ``low``; ``worst_line_no`` — the line with ``worst`` (first one on ties).
    * A metric counts as shifted (and goes into ``Evidence.shifts``) if ``worst > baseline_p95``
      for higher-is-worse, or ``worst <= composite_drop_ratio * baseline_p50`` for lower-is-worse.
    * ``limit_metric``/``limit`` — the limit field per ``signals.METRIC_LIMITS`` and its max value
      for the service over the whole log; ``None`` if there is no such field. ``limit_reached`` —
      ``limit`` exists and ``worst >= pool_saturation_ratio * limit``.
    * ``relative_deviation`` = ``|worst - baseline_p50| / max(|baseline_p50|, 1)``, 3 decimals.
    * "Strongest" deviation ordering — (``limit_reached`` desc., ``relative_deviation``
      desc., ``metric``). In text, a value with a limit is written next to it:
      "active_connections 100 at max_connections=100".
    """

    service: str
    metric: str
    unit: MetricUnit
    higher_is_worse: bool
    window: TimeWindow
    baseline_p50: float
    baseline_p95: float
    low: float
    high: float
    worst: float
    worst_line_no: int
    limit_metric: str | None
    limit: float | None
    limit_reached: bool
    relative_deviation: float


class CompositeKind(str, Enum):
    """Composite signal type."""

    CONNECTIONS_HELD = "connections_held"
    """Connection count (``signals.CONNECTION_COUNT_METRICS``) rose while throughput
    (``*_per_s``) of the same service fell: connections are being held rather than serving load."""


@dataclass(frozen=True)
class CompositeSignal:
    """Composite signal from two metrics of one service in one window.

    CONNECTIONS_HELD: ``rising.worst >= composite_rise_ratio * rising.baseline_p50`` and
    ``falling.worst <= composite_drop_ratio * falling.baseline_p50``. ``interpretation`` —
    an English sentence with a hedge from ``HEDGE_WORDS`` and both
    "baseline → worst" transitions, e.g. "active_connections rose 30 → 100 while txn_per_s fell 107 → 51:
    connections appear to be held rather than serving load".
    """

    kind: CompositeKind
    service: str
    rising: MetricShift
    falling: MetricShift
    interpretation: str


@dataclass(frozen=True)
class Evidence:
    """Evidence for a finding: metrics, 1..max_samples sample lines,
    up to 5 most frequent templates and up to 5 request_ids (sorted).

    ``shifts`` — shifted metrics of the finding's services in its window, strongest first
    (``MetricShift`` ordering). ``composites`` — composite signals for the finding's services in its
    window, by (service, kind).
    """

    metrics: tuple[MetricSummary, ...]
    samples: tuple[EvidenceLine, ...]
    templates: tuple[TemplateCount, ...]
    request_ids: tuple[str, ...]
    shifts: tuple[MetricShift, ...] = ()
    composites: tuple[CompositeSignal, ...] = ()


@dataclass(frozen=True)
class Finding:
    """A Key Findings entry: a merge of pattern groups with similar meaning.

    A group's episode is the one whose window contains ``group.window.start``; otherwise ``None``
    (outside episodes). Group merge key: (episode, ``ServiceClass`` of the group's service,
    ``group.db_related`` — only for INTERNAL; omitted for DB_SERVER and ENTRY_POINT). Hence an entry
    point has one finding per episode (failures, timeouts, latency together), queues/retries/latency
    of internal services without DB markers form one finding, and DB and non-DB groups never mix.

    * Group ``severity`` — the first matching rule (``SEVERITY_CRITERIA``) by
      (group kind, service class, ``max_level``): CRITICAL — kind RESTART on DB_SERVER, or kind ∈
      {SERVER_ERROR_STATUS, TIMEOUT, CONNECTION_ERROR, DEPENDENCY_UNAVAILABLE} on ENTRY_POINT;
      HIGH — max level >= ERROR, or kind ∈ {RESTART, DEPENDENCY_UNAVAILABLE, POOL_SATURATION,
      CONNECTION_ERROR, LOCK}; MEDIUM — kind ∈ {TIMEOUT, LATENCY, RETRY, QUEUE_BACKLOG,
      REPLICATION_LAG, SERVER_ERROR_STATUS, PROBE_FAILURE}; otherwise LOW. Finding ``severity`` —
      max over its groups.
    * ``kind`` — primary type: the kind of the group with the highest severity, ties broken by larger
      ``count``, then first in ``SIGNAL_PRIORITY``. ``kinds`` — all group kinds in
      ``SIGNAL_PRIORITY`` order.
    * ``id`` — ``"F<n>"`` by (window.start, kind index in ``SIGNAL_PRIORITY``, service_class).
    * ``title`` — one English "what happened" sentence: services, event count and
      up to 3 types from ``kinds``; no phrases from ``OVERCONFIDENT_PHRASES``.
    * ``services`` — sorted. ``count`` — sum of group ``count``.
    * ``db_categories`` — union of group ``db_categories`` (DB-related groups only).
    * ``role``/``role_rationale`` — see ``Assess``; the rationale mentions the start time and
      the dependency.
    """

    id: str
    title: str
    kind: SignalKind
    service_class: ServiceClass
    services: tuple[str, ...]
    severity: Severity
    role: Role
    role_rationale: str
    episode_id: str | None
    window: TimeWindow
    count: int
    db_related: bool
    db_categories: tuple[DbCategory, ...]
    group_ids: tuple[str, ...]
    evidence: Evidence
    kinds: tuple[SignalKind, ...] = ()


class HypothesisKind(str, Enum):
    """Hypothesis kind. Declaration order is the order among alternatives."""

    SOURCE = "source"
    """Source of an episode's degradation: one source service set (services of CAUSE findings) in one
    episode; all its CAUSE findings are listed in ``cause_finding_ids``."""
    EPISODE_LINK = "episode_link"
    """The next episode may be a consequence of the previous one: between them a metric of the
    previous episode's source service did not return to baseline. Confidence at most MEDIUM."""
    CLIENT_SIDE = "client_side"
    """The problem is on the DB clients' side (e.g. the application pool), not the server."""
    LOAD_SPIKE = "load_spike"
    """Incoming load spike at the entry point; ``verdict`` — whether the log supports it."""
    PRE_ONSET_TRIGGER = "pre_onset_trigger"
    """Unusual events of the source service in the ``pre_onset_window_seconds`` window before onset —
    a trigger candidate; ``samples`` — those lines."""


class Verdict(str, Enum):
    """Outcome of checking an alternative against the log."""

    SUPPORTED = "supported"
    """Supported by the log."""
    NOT_SUPPORTED = "not_supported"
    """Not supported by the log."""
    INCONCLUSIVE = "inconclusive"
    """Not enough data in the log (e.g. no entry-point requests in the baseline window)."""


MAX_HYPOTHESIS_ARGUMENTS = 3
"""At most this many supporting and this many contradicting arguments per hypothesis."""


@dataclass(frozen=True)
class Hypothesis:
    """A hypothesis about the cause.

    * ``id`` — ``"H<n>"`` in order (primary, then alternatives).
    * ``statement`` — an English sentence with at least one word from ``HEDGE_WORDS`` and no
      ``OVERCONFIDENT_PHRASES``.
    * ``confidence`` by ``kind``:
      SOURCE and CLIENT_SIDE — HIGH if supporting arguments rely on >= 2 services, including both
      the client side and DB_SERVER (or entry point and dependency), and there are no contradicting
      arguments; MEDIUM — there is a supporting argument and no more contradicting than supporting;
      otherwise LOW.
      EPISODE_LINK — MEDIUM if there are no contradicting arguments, otherwise LOW (never HIGH).
      LOAD_SPIKE — MEDIUM when ``verdict == SUPPORTED``, otherwise LOW. PRE_ONSET_TRIGGER — LOW.
    * ``supporting``/``contradicting`` — 0..``MAX_HYPOTHESIS_ARGUMENTS`` short English
      sentences each, with numbers/times/ids; at least one is non-empty, and ``supporting`` is
      non-empty for all kinds except LOAD_SPIKE with ``verdict != SUPPORTED``. Arguments are grouped,
      without repeated boilerplate: no 6-word sequence occurs in two arguments of the same
      hypothesis. The first SOURCE argument — the strongest ``max_evidence_metrics`` deviations
      (``shifts``) with limits; if CAUSE findings have a composite signal, it is mentioned in the
      arguments.
    * ``finding_ids`` — non-empty, existing findings only, sorted by number.
    * ``kind`` — the kind; ``verdict`` — only for LOAD_SPIKE (otherwise ``None``).
    * ``cause_finding_ids`` — CAUSE findings the hypothesis explains (for SOURCE — all
      CAUSE findings of its source in the episode; for EPISODE_LINK — CAUSE findings of both
      episodes), a subset of ``finding_ids``.
    * ``source_services`` — source services (sorted); for LOAD_SPIKE — entry points.
    * ``shifts`` — metrics the arguments rely on; for EPISODE_LINK — the source's
      deviations in the gap between episodes.
    * ``samples`` — evidence lines (for PRE_ONSET_TRIGGER — the unusual events), 0..max_samples.
    """

    id: str
    statement: str
    confidence: Confidence
    episode_ids: tuple[str, ...]
    supporting: tuple[str, ...]
    contradicting: tuple[str, ...]
    finding_ids: tuple[str, ...]
    db_categories: tuple[DbCategory, ...]
    kind: HypothesisKind = HypothesisKind.SOURCE
    verdict: Verdict | None = None
    cause_finding_ids: tuple[str, ...] = ()
    source_services: tuple[str, ...] = ()
    shifts: tuple[MetricShift, ...] = ()
    samples: tuple[EvidenceLine, ...] = ()


@dataclass(frozen=True)
class ProbableCauses:
    """The Probable Cause (or Candidates) section.

    ``primary`` — SOURCE hypotheses only: for each episode, the SOURCE hypotheses with the highest
    confidence (all of them on ties), by (episode, id); non-empty if there is at least one finding
    with role=CAUSE. Exactly one SOURCE hypothesis per (episode, source service set).
    ``alternatives`` — the rest (SOURCE with lower confidence, EPISODE_LINK, CLIENT_SIDE,
    LOAD_SPIKE, PRE_ONSET_TRIGGER), by (first episode, confidence desc., ``HypothesisKind`` order).
    Every finding with role=CAUSE is in ``cause_finding_ids`` of at least one SOURCE hypothesis.
    """

    primary: tuple[Hypothesis, ...]
    alternatives: tuple[Hypothesis, ...]


@dataclass(frozen=True)
class ServiceImpact:
    """Impact on a service. ``failed_requests`` — distinct request_ids with a head of level
    >= ERROR or a 5xx status on this service."""

    service: str
    service_class: ServiceClass
    first_affected: datetime
    anomalous_events: int
    error_events: int
    failed_requests: int
    finding_ids: tuple[str, ...]


@dataclass(frozen=True)
class PropagationStep:
    """A problem propagation step ``from_service → to_service`` within an episode.

    Goes against the dependency edge: ``to_service`` depends on ``from_service``. ``first_seen`` —
    first anomaly of ``to_service`` in the episode (may precede the first anomaly of ``from_service``
    if the source emits metrics less often than its clients). ``evidence`` — 1..2 lines (first
    anomalies of both services).
    """

    episode_id: str
    from_service: str
    to_service: str
    via: EdgeSource
    first_seen: datetime
    evidence: tuple[EvidenceLine, ...]


@dataclass(frozen=True)
class Impact:
    """The Impact section.

    ``services`` — only services with anomalies, by (first_affected, service).
    ``propagation`` — by (episode_id, first_seen, from_service, to_service). An episode's steps are a
    breadth-first traversal from CAUSE-finding services against dependency edges, over services
    anomalous in the episode: a service at level d gets steps along all PEER_FIELD/DB_ACCESS edges
    from level d-1 services; TRACE_ORDER is used only if the service is otherwise unreachable (one
    step, from the lowest-level service, then by name). Each step corresponds to a graph edge; at
    least one step of the episode starts at a CAUSE-finding service.
    ``failed_requests`` — distinct request_ids with a failed step anywhere in the log.
    ``example_trace_ids`` — ``PatternResult.example_trace_ids``.
    """

    services: tuple[ServiceImpact, ...]
    propagation: tuple[PropagationStep, ...]
    failed_requests: int
    example_trace_ids: tuple[str, ...]


@dataclass(frozen=True)
class CausalAnalysis:
    """Result of the causality analysis."""

    graph: ServiceGraph
    findings: tuple[Finding, ...]
    causes: ProbableCauses
    impact: Impact


class BuildServiceGraph(Protocol):
    """``test_task.causality.build_service_graph``: dependency graph from events and traces."""

    def __call__(
        self, scored: Sequence[ScoredEvent], patterns: PatternResult
    ) -> ServiceGraph: ...


class Assess(Protocol):
    """``test_task.causality.assess``: findings, roles, hypotheses, impact.

    Role rule, per episode:
    * candidates — the episode's findings with ``window.start <= onset + cause_window_seconds``;
    * candidate f is "dominated" if there is another candidate g whose service is reachable along
      dependency edges from at least one service of f, while no service of g reaches the services
      of f (g is deeper in the chain);
    * non-dominated candidates are CAUSE. A cycle exists only between candidates with no shared
      services where a service of f reaches a service of g and a service of g reaches a service of f
      via paths of at least one edge; in a cycle only the earliest by window.start gets CAUSE, the
      rest are SYMPTOM. Candidates sharing a service or without connecting paths are not a cycle;
    * all other findings of the episode are SYMPTOM.
    Findings outside episodes are SYMPTOM with rationale "residual deviation outside detected episodes".
    Every episode with findings has >= 1 CAUSE.

    Hypotheses, for each episode with CAUSE findings:
    * SOURCE — one per service set of the episode's CAUSE findings (findings with the same
      ``services`` are merged);
    * CLIENT_SIDE — at the implementation's discretion (e.g. on POOL_EXHAUSTION at clients);
    * LOAD_SPIKE — if the graph has an ENTRY_POINT: rate of entry-point heads in the episode window
      vs the ``timeline.baseline`` window; ``ratio >= load_spike_ratio`` → SUPPORTED, otherwise
      NOT_SUPPORTED; no heads in the baseline or zero duration → INCONCLUSIVE. The argument contains
      both rates (in min⁻¹ or s⁻¹) and the conclusion "supported / not supported by the log";
    * PRE_ONSET_TRIGGER — if the source service has heads in [onset − ``pre_onset_window_seconds``, onset)
      whose template is absent from the rest of the baseline window, or whose rate in the window
      exceeds ``deviation_factor`` × its rate in the rest of the baseline with count >= 2.
      ``samples`` — those lines.
    For each pair of adjacent episodes (E_k, E_k+1) — EPISODE_LINK, if the source service of E_k
    has a metric that did not return to baseline in the gap [``E_k.recovery`` or ``E_k.window.end``,
    ``E_k+1.onset``): for higher-is-worse, p50 of the gap values > baseline p95 and the difference
    >= ``min_abs_delta_*`` for the unit; for lower-is-worse, p50 <= ``composite_drop_ratio`` ×
    baseline p50. The argument is the value range and the baseline, e.g. "active_connections 53–61
    between E1 and E2 vs baseline 30"; ``episode_ids`` — both episodes.
    Rules rely only on fields, levels, templates and the graph, never on service names or line
    numbers. Pure deterministic function.
    """

    def __call__(
        self,
        scored: Sequence[ScoredEvent],
        patterns: PatternResult,
        timeline: Timeline,
        comparison: WindowComparison,
        graph: ServiceGraph,
        config: AnalysisConfig,
    ) -> CausalAnalysis: ...
