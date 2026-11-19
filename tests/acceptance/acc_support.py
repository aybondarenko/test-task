"""Shared helpers for acceptance tests: synthetic log lines, running pipeline steps
through the implementation (imports inside functions), a reference JSON serialization rule, text checks.

Nothing from the ``test_task.<module>`` implementation is imported at module level, only
the contracts that also exist in the stub.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from test_task.contracts.common import (
    HEDGE_WORDS,
    OVERCONFIDENT_PHRASES,
    AnalysisConfig,
    SignalKind,
)

UTC = timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
ATTACHED_LOG = PROJECT_ROOT / "it_task_log (2).log"
CHAIN_LOG = FIXTURES / "incident_chain.log"

# Services of the synthetic "gateway -> service -> DB" scenario (suffixes from SERVICE_SUFFIXES).
GW = "front-gateway"
SVC = "orders-service"
DB = "orders-db"
NOISE = "audit-worker"


def impl(module: str, name: str) -> Any:
    """Implementation function ``test_task.<module>.<name>`` (imported at call time)."""
    return getattr(importlib.import_module(f"test_task.{module}"), name)


# ---------- time and lines ----------

T0 = datetime(2026, 3, 27, 14, 0, 0, tzinfo=UTC)
"""Start of synthetic logs; a multiple of 60 s since the Unix epoch."""


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def iso(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def text_line(sec: float, level: str, service: str, rest: str = "") -> str:
    return f"{iso(at(sec))} {level} {service} {rest}".rstrip()


def json_line(sec: float, level: str, service: str, msg: str, **fields: Any) -> str:
    data: dict[str, Any] = {"ts": iso(at(sec)), "level": level, "service": service, "msg": msg}
    data.update(fields)
    return json.dumps(data, separators=(",", ":"))


def read_log_lines(path: Path) -> list[str]:
    """File lines as the CLI reads them: UTF-8 with replacement, split on ``\\n``, no trailing empty line."""
    lines = path.read_bytes().decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------- "gateway -> service -> DB" scenario ----------


def normal_line(service: str, sec: float, i: int) -> str:
    """A normal INFO line of a service: no keywords, metrics within a narrow range."""
    if service == GW:
        return text_line(
            sec,
            "INFO",
            GW,
            f"request served request_id=r-{i:04d} method=GET path=/v1/orders "
            f"status=200 upstream={SVC} latency_ms={40 + (i % 5) * 3}",
        )
    if service == SVC:
        return text_line(
            sec, "INFO", SVC, f"order stored request_id=r-{i:04d} db_latency_ms={10 + i % 3} latency_ms={30 + i % 4}"
        )
    if service == DB:
        return text_line(
            sec,
            "INFO",
            DB,
            f"stats db_cluster=main active_connections={20 + i % 3} max_connections=100 waiting_clients=0",
        )
    return text_line(sec, "INFO", service, f"export done batch={i}")


def normal_block(
    start: float, end: float, step: float = 10, services: Sequence[str] = (GW, SVC, DB, NOISE), first_index: int = 0
) -> list[str]:
    out = []
    i = first_index
    sec = start
    while sec < end:
        out.append(normal_line(services[i % len(services)], sec, i))
        i += 1
        sec += step
    return out


def db_stats_warn(sec: float, waiting: int, active: int = 60) -> str:
    return text_line(
        sec,
        "WARN",
        DB,
        f"stats db_cluster=main active_connections={active} max_connections=100 waiting_clients={waiting}",
    )


def svc_pool_warn(sec: float, rid: str, wait_ms: int = 850) -> str:
    return text_line(
        sec, "WARN", SVC, f"db acquire connection slow request_id={rid} wait_ms={wait_ms} pool_in_use=20 pool_idle=0"
    )


def gw_timeout(sec: float, rid: str, latency: float = 3000.0) -> str:
    return json_line(sec, "ERROR", GW, "upstream timeout", request_id=rid, upstream=SVC, status=504, latency_ms=latency)


def episode_db_first(e: float = 300) -> list[str]:
    """The DB server saturates first (t0), then the client pool (t0+5 s), then 504 at the gateway (t0+15 s)."""
    return [
        db_stats_warn(e + 0, waiting=6, active=88),
        svc_pool_warn(e + 5, "r-1001", 850),
        gw_timeout(e + 15, "r-1001", 3000.12345),
        db_stats_warn(e + 20, waiting=9, active=89),
        svc_pool_warn(e + 25, "r-1002", 900),
        gw_timeout(e + 35, "r-1002", 2999.5),
        svc_pool_warn(e + 45, "r-1003", 950),
        gw_timeout(e + 55, "r-1003"),
        db_stats_warn(e + 65, waiting=7, active=86),
        svc_pool_warn(e + 70, "r-1004", 990),
        gw_timeout(e + 80, "r-1004"),
    ]


def episode_client_first(e: float = 300) -> list[str]:
    """Client pool at t0, DB server stats (waiting_clients=5) at t0+10 s, gateway 504 at t0+20 s."""
    return [
        svc_pool_warn(e + 0, "r-2001", 700),
        db_stats_warn(e + 10, waiting=5),
        gw_timeout(e + 20, "r-2001"),
        svc_pool_warn(e + 30, "r-2002", 760),
        db_stats_warn(e + 40, waiting=6),
        gw_timeout(e + 50, "r-2002"),
        svc_pool_warn(e + 65, "r-2003", 780),
        gw_timeout(e + 75, "r-2003"),
    ]


def chain_lines(episode: Sequence[str], extra: Sequence[str] = (), e: float = 300) -> list[str]:
    """5 minutes of normal traffic, an episode in minute ``e``, then ~5 more normal minutes (recovery observed)."""
    lines = normal_block(0, e) + list(episode) + list(extra) + normal_block(e + 120, e + 420, first_index=100)
    return sorted(lines, key=_line_ts)


def all_normal_lines(seconds: float = 600) -> list[str]:
    return normal_block(0, seconds)


def _line_ts(line: str) -> str:
    if line.startswith("{"):
        return str(json.loads(line)["ts"])
    return line.split(" ", 1)[0]


# ---------- pipeline steps through the implementation ----------


def stage_events(lines: Sequence[str]) -> SimpleNamespace:
    parse = impl("parser", "parse_lines")(list(lines))
    events = impl("signals", "extract_events")(parse.records)
    return SimpleNamespace(lines=list(lines), parse=parse, events=events)


def stage_scored(lines: Sequence[str], config: AnalysisConfig | None = None) -> SimpleNamespace:
    ns = stage_events(lines)
    ns.config = config or AnalysisConfig()
    ns.baseline = impl("baseline", "build_baseline")(ns.events, ns.config)
    ns.scored = impl("baseline", "score_events")(ns.events, ns.baseline, ns.config)
    return ns


def stage_patterns(lines: Sequence[str], config: AnalysisConfig | None = None) -> SimpleNamespace:
    ns = stage_scored(lines, config)
    ns.patterns = impl("patterns", "find_patterns")(ns.scored, ns.baseline, ns.config)
    return ns


def stage_timeline(lines: Sequence[str], config: AnalysisConfig | None = None) -> SimpleNamespace:
    ns = stage_scored(lines, config)
    ns.timeline = impl("timeline", "build_timeline")(ns.scored, ns.baseline, ns.config)
    ns.comparison = impl("timeline", "compare_windows")(ns.scored, ns.timeline, ns.config)
    return ns


def stage_analysis(lines: Sequence[str], config: AnalysisConfig | None = None) -> SimpleNamespace:
    ns = stage_patterns(lines, config)
    ns.timeline = impl("timeline", "build_timeline")(ns.scored, ns.baseline, ns.config)
    ns.comparison = impl("timeline", "compare_windows")(ns.scored, ns.timeline, ns.config)
    ns.graph = impl("causality", "build_service_graph")(ns.scored, ns.patterns)
    ns.analysis = impl("causality", "assess")(ns.scored, ns.patterns, ns.timeline, ns.comparison, ns.graph, ns.config)
    return ns


def stage_recs(lines: Sequence[str], config: AnalysisConfig | None = None) -> SimpleNamespace:
    ns = stage_analysis(lines, config)
    ns.recs = impl("recommendations", "recommend")(ns.parse, ns.patterns, ns.timeline, ns.analysis, ns.config)
    return ns


def stage_facts(lines: Sequence[str], config: AnalysisConfig | None = None) -> SimpleNamespace:
    from test_task.contracts.narrative import NarrativeFacts

    ns = stage_recs(lines, config)
    ns.facts = NarrativeFacts(timeline=ns.timeline, analysis=ns.analysis, recommendations=ns.recs)
    return ns


SYNTHETIC_SUMMARY = (
    "The incident likely started with database connection saturation. "
    "Downstream request failures probably followed from it."
)


def stage_report(
    lines: Sequence[str],
    config: AnalysisConfig | None = None,
    file_name: str = "incident.log",
    summary: str = SYNTHETIC_SUMMARY,
) -> SimpleNamespace:
    """``ReportInputs`` from the steps up to ``recommend`` (no narrative) and ``build_report``."""
    from test_task.contracts.report import ReportInputs

    ns = stage_recs(lines, config)
    ns.sha = sha256_hex("\n".join(lines).encode("utf-8"))
    ns.inputs = ReportInputs(
        file_name=file_name,
        sha256=ns.sha,
        parse=ns.parse,
        patterns=ns.patterns,
        timeline=ns.timeline,
        metric_comparison=ns.comparison.metrics,
        error_rates=ns.comparison.error_rates,
        findings=ns.analysis.findings,
        probable_causes=ns.analysis.causes,
        impact=ns.analysis.impact,
        recommendations=ns.recs,
        incident_summary=summary,
    )
    ns.report = impl("report", "build_report")(ns.inputs, ns.config)
    return ns


def records_by_service(ns: SimpleNamespace, service: str) -> list[Any]:
    return [s for s in ns.scored if s.event.record.service == service]


def finding_kinds(f: Any) -> tuple[Any, ...]:
    """All kinds of a finding: ``kinds`` (2.0) or just ``kind``."""
    return tuple(f.kinds) or (f.kind,)


def finding_with(analysis: Any, kind: SignalKind, service: str) -> Any:
    found = [f for f in analysis.findings if kind in finding_kinds(f) and service in f.services]
    assert len(found) == 1, (
        f"expected exactly one {kind.value} finding with {service}, got: "
        f"{[(f.id, f.kind.value, f.services) for f in analysis.findings]}"
    )
    return found[0]


def findings_of_service(analysis: Any, service: str) -> list[Any]:
    return [f for f in analysis.findings if service in f.services]


def all_hypotheses(analysis: Any) -> list[Any]:
    return list(analysis.causes.primary) + list(analysis.causes.alternatives)


# ---------- text ----------

CYRILLIC = re.compile(r"[\u0400-\u04FF]")
SERVICE_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
ID_TOKEN = re.compile(r"\b[FHEN]\d+\b")


def sentence_count(text: str) -> int:
    """Sentence count: ``.``/``!``/``?`` followed by whitespace or end of text."""
    return len(re.findall(r"[.!?](?=\s|$)", text.strip()))


def has_hedge(text: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in HEDGE_WORDS)


def overconfident(text: str) -> list[str]:
    low = text.lower()
    return [p for p in OVERCONFIDENT_PHRASES if p in low]


def facts_services(facts: Any) -> set[str]:
    a = facts.analysis
    out = {s.service for s in a.graph.services}
    for f in a.findings:
        out.update(f.services)
    out.update(s.service for s in a.impact.services)
    return out


def facts_ids(facts: Any) -> set[str]:
    a = facts.analysis
    ids = {f.id for f in a.findings} | {h.id for h in all_hypotheses(a)}
    ids |= {e.id for e in facts.timeline.episodes}
    ids |= {n.id for n in facts.recommendations.next_steps}
    return ids


# ---------- reference report.json serialization rule ----------


def ref_json(o: Any) -> Any:
    """``report`` serialization rule: dataclass -> object in field order, Enum -> value, datetime ->
    ``YYYY-MM-DDTHH:MM:SS.mmmZ``, tuple -> array, Mapping -> keys sorted ascending, float -> round 3."""
    if o is None or isinstance(o, bool):
        return o
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, int):
        return o
    if isinstance(o, float):
        return round(o, 3)
    if isinstance(o, str):
        return o
    if isinstance(o, datetime):
        return iso(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {f.name: ref_json(getattr(o, f.name)) for f in dataclasses.fields(o)}
    if isinstance(o, Mapping):
        return {str(k): ref_json(o[k]) for k in sorted(o)}
    if isinstance(o, (tuple, list)):
        return [ref_json(x) for x in o]
    raise TypeError(f"unexpected value in report: {type(o)!r}")


def assert_same_json(actual: Any, expected: Any, path: str = "$") -> None:
    """Same structure, key order and values (int/float compared numerically)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected object, got {type(actual).__name__}"
        assert list(actual) == list(expected), f"{path}: keys {list(actual)} != {list(expected)}"
        for k in expected:
            assert_same_json(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected array, got {type(actual).__name__}"
        assert len(actual) == len(expected), f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_same_json(a, e, f"{path}[{i}]")
    elif isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert actual == expected and type(actual) is type(expected), f"{path}: {actual!r} != {expected!r}"
    else:
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), f"{path}: {actual!r}"
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


# ---------- markdown ----------


def md_section(text: str, title: str, level: int = 2) -> str | None:
    """Text of the ``#…# title`` section up to the next heading of the same or higher level."""
    lines = text.split("\n")
    head = "#" * level + " "
    start = None
    fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.rstrip().lower() == (head + title).lower():
            start = i + 1
            break
    if start is None:
        return None
    body = []
    fence = False
    for line in lines[start:]:
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,6}) ", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body)


# ---------- project copy and network-free subprocess ----------

NETWORK_GUARD = """
import socket as _s
_real = _s.socket
class _Guard(_real):
    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        if fileno is None and family != getattr(_s, "AF_UNIX", None):
            raise OSError("network access is disabled in acceptance tests")
        super().__init__(family, type, proto, fileno)
def _deny(*a, **k):
    raise OSError("network access is disabled in acceptance tests")
_s.socket = _Guard
_s.create_connection = _deny
_s.getaddrinfo = _deny
"""


def copy_project(dst: Path) -> Path:
    """Repository copy for running "as a reviewer would": sources, README, pyproject, log."""
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "src", dst / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("README.md", "pyproject.toml", "uv.lock", ".python-version", ".gitignore", ATTACHED_LOG.name):
        if (PROJECT_ROOT / name).is_file():
            shutil.copy2(PROJECT_ROOT / name, dst / name)
    return dst


# ---------- episode causes ----------

SEVERITY_ORDER = ("critical", "high", "medium", "low")


def episode_top_causes(analysis: Any) -> dict[str, Any]:
    """Episode -> its CAUSE finding with the highest severity (earliest one on ties)."""
    from test_task.contracts.common import Role

    out: dict[str, Any] = {}
    for f in analysis.findings:
        if f.role != Role.CAUSE or f.episode_id is None:
            continue
        key = (SEVERITY_ORDER.index(f.severity.value), f.window.start, int(f.id[1:]))
        cur = out.get(f.episode_id)
        if cur is None or key < (SEVERITY_ORDER.index(cur.severity.value), cur.window.start, int(cur.id[1:])):
            out[f.episode_id] = f
    return out


def id_pos(text: str, ident: str) -> int:
    """Position of the first occurrence of id as a standalone token, -1 if absent."""
    m = re.search(rf"\b{re.escape(ident)}\b", text)
    return m.start() if m else -1


def h2_titles(md: str) -> list[str]:
    return [line[3:].strip() for line in md.split("\n") if line.startswith("## ")]


def is_subsequence(needle: Sequence[str], hay: Sequence[str]) -> bool:
    it = iter(hay)
    return all(any(x == y for y in it) for x in needle)


def markdown_with_appendix(report: Any) -> str:
    """The main report.md and, if the implementation already provides ``render_appendix_markdown`` (2.0,
    see test_task_024), the appendix. For tests of completed tasks that must also pass before TASK-024."""
    mod = importlib.import_module("test_task.report")
    text = mod.render_markdown(report)
    appendix = getattr(mod, "render_appendix_markdown", None)
    if appendix is not None:
        text += "\n" + appendix(report)
    return text


# ---------- 2.0 scenarios: gateway -> two clients -> DB server ----------

SVC_B = "stock-service"
MAILER = "letters-worker"
APP_NAMES_OF_ATTACHED_LOG = (
    "api-gateway",
    "billing-worker",
    "ingress-controller",
    "inventory-service",
    "kubelet",
    "notification-service",
    "order-service",
    "payment-service",
    "postgres-primary",
    "redis-cache",
)
"""Service names from the attached log: allowed only in tests, never in analysis rules."""


def db_stats(sec: float, level: str, active: int, wait: int, txn: int) -> str:
    return text_line(
        sec,
        level,
        DB,
        f"stats db_cluster=main active_connections={active} max_connections=100 waiting_clients={wait} txn_per_s={txn}",
    )


def calm(start: float, end: float, active: Any = 30, txn: int = 107, first_index: int = 0) -> list[str]:
    """Normal operation every 10 s: a gateway request to one of the clients, a client line with
    ``db_latency_ms``, DB server stats (every 20 s). ``active`` is a number or a function of the tick."""
    out: list[str] = []
    i = first_index
    t = start
    while t < end:
        client = SVC if i % 2 == 0 else SVC_B
        out.append(
            text_line(
                t,
                "INFO",
                GW,
                f"request served request_id=q-{i:05d} status=200 upstream={client} latency_ms={40 + i % 5}",
            )
        )
        out.append(text_line(t + 1, "INFO", client, f"order stored request_id=q-{i:05d} db_latency_ms={10 + i % 3}"))
        if i % 2 == 0:
            act = active(i) if callable(active) else active
            out.append(db_stats(t + 2, "INFO", act, 0, txn))
        i += 1
        t += 10
    return out


SAT_ACTIVE = (60, 80, 95, 100, 100, 98, 100, 97, 96, 95, 92, 90)
SAT_WAIT = (3, 8, 15, 31, 25, 20, 18, 12, 10, 8, 6, 4)
SAT_TXN = (100, 90, 75, 60, 51, 55, 58, 60, 62, 65, 70, 72)


def saturation_burst(e: float, gw_extra: int = 0) -> list[str]:
    """The DB server saturates first (t), client pool (t+3 s), gateway 504 (t+6 s); 12 ticks of 10 s.
    ``gw_extra``: extra gateway requests per tick (to test a load spike)."""
    out: list[str] = []
    for k in range(len(SAT_ACTIVE)):
        t = e + 10 * k
        client = SVC if k % 2 == 0 else SVC_B
        rid = f"r-{int(e)}-{k}"
        out.append(db_stats(t, "WARN", SAT_ACTIVE[k], SAT_WAIT[k], SAT_TXN[k]))
        out.append(
            text_line(
                t + 3,
                "WARN",
                client,
                f"db acquire connection slow request_id={rid} wait_ms={800 + 10 * k} pool_in_use=20 pool_idle=0",
            )
        )
        out.append(
            json_line(t + 6, "ERROR", GW, "upstream timeout", request_id=rid, upstream=client, status=504, latency_ms=3000)
        )
        for j in range(gw_extra):
            out.append(
                text_line(
                    t + 7 + j,
                    "INFO",
                    GW,
                    f"request served request_id={rid}-x{j} status=200 upstream={client} latency_ms={45 + j}",
                )
            )
    return out


def restart_burst(e: float, clients: bool = True, marker_at: float | None = None) -> list[str]:
    """DB server crash and restart in minute ``e``; clients get "starting up" in the error field."""
    out = [
        text_line(e, "ERROR", DB, "server process was terminated by signal 9: killed"),
        text_line(e + 8, "INFO", DB, "database system was interrupted; last known up at checkpoint"),
        text_line(e + 16, "INFO", DB, "redo starts at 0/1A2B3C"),
    ]
    if clients:
        for k in range(4):
            client = SVC if k % 2 == 0 else SVC_B
            out.append(
                text_line(
                    e + 20 + 10 * k,
                    "ERROR",
                    client,
                    f'failed to persist order request_id=s-{int(e)}-{k} error="pq: the database system is starting up"',
                )
            )
    if marker_at is not None:
        out.append(text_line(marker_at, "INFO", DB, "database system is ready to accept connections"))
    return out


def scenario(*parts: Sequence[str]) -> list[str]:
    lines: list[str] = []
    for p in parts:
        lines.extend(p)
    return sorted(lines, key=_line_ts)


def run_analyze(lines: Sequence[str], file_name: str = "scenario.log") -> Any:
    from test_task.contracts.pipeline import SourceRef

    src = SourceRef(file_name, sha256_hex("\n".join(lines).encode("utf-8")))
    return impl("pipeline", "analyze")(list(lines), src)


def hyps_of_kind(analysis_or_causes: Any, kind: Any) -> list[Any]:
    causes = getattr(analysis_or_causes, "causes", analysis_or_causes)
    return [h for h in list(causes.primary) + list(causes.alternatives) if h.kind == kind]


def hyp_texts(h: Any) -> str:
    return " ".join([h.statement, *h.supporting, *h.contradicting])


def six_word_repeats(args: Sequence[str]) -> list[str]:
    """6-word fragments that occur in two different evidence items."""
    seen: dict[tuple[str, ...], int] = {}
    bad = []
    for i, a in enumerate(args):
        words = re.findall(r"\S+", a.lower())
        grams = {tuple(words[j : j + 6]) for j in range(len(words) - 5)}
        for g in grams:
            if g in seen and seen[g] != i:
                bad.append(" ".join(g))
            seen.setdefault(g, i)
    return bad


def scan_for(paths: Sequence[Path], needles: Sequence[str], ignore_case: bool = False) -> list[str]:
    """``file: substring`` pairs for substrings found; a missing file fails the test."""
    hits = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        hay = text.lower() if ignore_case else text
        for n in needles:
            if (n.lower() if ignore_case else n) in hay:
                hits.append(f"{p.name}: {n}")
    return hits
