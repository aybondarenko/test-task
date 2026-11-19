"""TASK-025: regression tests for the 2.0 analysis rules on synthetic fixtures and the attached logs."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from acc_support import (
    APP_NAMES_OF_ATTACHED_LOG,
    ATTACHED_LOG,
    CHAIN_LOG,
    FIXTURES,
    PROJECT_ROOT,
    episode_top_causes,
    id_pos,
    impl,
    read_log_lines,
    run_analyze,
    sha256_hex,
    stage_facts,
)

from test_task.contracts.causality import (
    CompositeKind,
    EdgeSource,
    HypothesisKind,
    ServiceClass,
)
from test_task.contracts.common import Confidence, DbCategory, Role, SignalKind
from test_task.contracts.pipeline import SourceRef
from test_task.contracts.timeline import RecoveryStatus

HK = HypothesisKind
K = SignalKind
DB = "catalog-db"
CLIENTS = ("basket-service", "invoice-service")
COURIER = "courier-worker"
QA14 = {
    "linked": FIXTURES / "qa14_linked_episodes.log",
    "provider": FIXTURES / "qa14_provider_reset.log",
    "starting_up": FIXTURES / "qa14_starting_up.log",
    "held": FIXTURES / "qa14_connections_held.log",
}
LIMIT_ARG = re.compile(
    r"\b(active_connections|pool_in_use) \d+(?:\.\d+)? at (max_connections|pool_max_size|pool_size)=\d+(?:\.\d+)?"
)


def kinds_of(f):
    return set(f.kinds) | {f.kind}


@pytest.fixture(scope="module")
def reports():
    return {name: run_analyze(read_log_lines(path), file_name=path.name) for name, path in QA14.items()}


@pytest.mark.criterion("TASK-025:C1")
@pytest.mark.req("QA-14", "QA-07", "QA-13", "QA-06")
def test_linked_episodes(reports):
    r = reports["linked"]
    eps = r.timeline.episodes
    assert len(eps) == 2, [(e.id, e.onset) for e in eps]
    links = [
        h
        for h in list(r.probable_causes.primary) + list(r.probable_causes.alternatives)
        if h.kind == HK.EPISODE_LINK
    ]
    assert links and all(h.confidence in (Confidence.MEDIUM, Confidence.LOW) for h in links)
    assert eps[1].recovery_status == RecoveryStatus.OBSERVED_SHORT_WINDOW
    top = episode_top_causes(SimpleNamespace(findings=r.key_findings))
    assert set(top) == {eps[0].id, eps[1].id}, top
    sev = ("critical", "high", "medium", "low")
    first, second = sorted(top.values(), key=lambda f: (sev.index(f.severity.value), f.window.start))
    assert first.severity != second.severity, "in the fixture, episode causes have different severities"
    p1, p2 = id_pos(r.incident_summary, first.id), id_pos(r.incident_summary, second.id)
    assert p1 >= 0 and p2 >= 0, r.incident_summary
    assert p1 < p2, r.incident_summary
    # real pipeline output: the summary names the episode link and the short recovery window
    assert any(id_pos(r.incident_summary, h.id) >= 0 for h in links), r.incident_summary
    low = r.incident_summary.lower()
    assert "recover" in low and "short" in low, r.incident_summary


@pytest.mark.criterion("TASK-025:C2")
@pytest.mark.req("QA-14", "FR-14")
def test_provider_reset_not_db(reports):
    r = reports["provider"]
    assert any(f.db_categories for f in r.key_findings)
    assert any(COURIER in f.services for f in r.key_findings)
    for f in r.key_findings:
        if f.db_categories:
            assert COURIER not in f.services, f.id
    for s in r.db_signals:
        assert COURIER not in s.services, s.category
    lines = read_log_lines(QA14["provider"])
    ns = stage_facts(lines)
    assert not [e for e in ns.analysis.graph.edges if e.source == EdgeSource.DB_ACCESS and e.dependent == COURIER]


@pytest.mark.criterion("TASK-025:C3")
@pytest.mark.req("QA-14", "FR-12")
def test_starting_up_clients_not_restart(reports):
    r = reports["starting_up"]
    restart = [f for f in r.key_findings if K.RESTART in kinds_of(f)]
    assert restart
    for f in restart:
        assert f.services == (DB,), (f.id, f.services)
    client = [f for f in r.key_findings if set(CLIENTS) & set(f.services)]
    assert client
    assert any(K.DEPENDENCY_UNAVAILABLE in kinds_of(f) for f in client)
    for f in client:
        assert K.RESTART not in kinds_of(f), f.id
    sig = [s for s in r.db_signals if s.category == DbCategory.SERVER_RESTART]
    assert len(sig) == 1 and sig[0].services == (DB,)


@pytest.mark.criterion("TASK-025:C4")
@pytest.mark.req("QA-14", "QA-08")
def test_connections_held(reports):
    r = reports["held"]
    causes = [f for f in r.key_findings if f.role == Role.CAUSE and f.service_class == ServiceClass.DB_SERVER]
    assert len(causes) == 1, [(f.id, f.services, f.role.value) for f in r.key_findings]
    f = causes[0]
    assert any(c.kind == CompositeKind.CONNECTIONS_HELD for c in f.evidence.composites)
    src = [h for h in r.probable_causes.primary if h.kind == HK.SOURCE and f.id in h.cause_finding_ids]
    assert len(src) == 1
    assert LIMIT_ARG.search(src[0].supporting[0]), src[0].supporting


def stripped(line):
    return line.split(" ", 1)[1] if " " in line else line


@pytest.mark.criterion("TASK-025:C5")
@pytest.mark.req("QA-14", "NEG-07")
@pytest.mark.parametrize("name", sorted(QA14))
def test_fixture_is_original_and_analysed(name):
    shipped = {stripped(x) for x in read_log_lines(ATTACHED_LOG)}
    text = QA14[name].read_text(encoding="utf-8")
    lines = read_log_lines(QA14[name])
    copied = [x for x in lines if stripped(x) in shipped]
    assert copied == []
    assert [n for n in APP_NAMES_OF_ATTACHED_LOG if n in text] == []
    report = run_analyze(lines, file_name=QA14[name].name)
    assert report.source.failed_lines == 0
    assert report.key_findings and any(f.role == Role.CAUSE for f in report.key_findings)


@pytest.mark.criterion("TASK-025:C6")
@pytest.mark.req("NEG-07", "QA-06")
def test_incident_chain_fixture():
    lines = read_log_lines(CHAIN_LOG)
    ns = stage_facts(lines)
    a = ns.analysis
    assert any(f.role == Role.CAUSE and "orders-db" in f.services for f in a.findings)
    gws = [f for f in a.findings if "front-gateway" in f.services]
    assert gws and all(f.role == Role.SYMPTOM for f in gws)
    assert any(h.kind == HK.SOURCE and "orders-db" in h.source_services for h in a.causes.primary)
    report = run_analyze(lines, file_name="incident_chain.log")
    assert impl("narrative", "validate_summary")(report.incident_summary, ns.facts) == ()


def content(report):
    """Report content without input details (source, parse_failures): what is derived from events."""
    d = impl("report", "to_json_dict")(report)
    return {k: v for k, v in d.items() if k not in ("source", "parse_failures")}


@pytest.mark.criterion("TASK-025:C7")
@pytest.mark.req("NEG-07")
def test_no_specifics_and_output_depends_on_input():
    pkg = PROJECT_ROOT / "src" / "test_task"
    files = sorted(pkg.rglob("*.py"))
    assert {"pipeline.py", "signals.py", "causality.py"} <= {p.name for p in files}
    needles = list(APP_NAMES_OF_ATTACHED_LOG) + ["2026-03-27"]
    hits = [
        f"{p.relative_to(pkg)}: {n}" for p in files for n in needles if n in p.read_text(encoding="utf-8")
    ]
    assert hits == []
    lines = read_log_lines(ATTACHED_LOG)

    def level(line):
        if line.startswith("{"):
            return str(json.loads(line).get("level", "")).upper()
        parts = line.split()
        return parts[1].upper() if len(parts) > 1 else ""

    kept = [x for x in lines if level(x) not in ("ERROR", "WARN", "WARNING")]
    assert len(kept) < len(lines)
    src = SourceRef("it_task_log (2).log", sha256_hex(ATTACHED_LOG.read_bytes()))
    analyze = impl("pipeline", "analyze")
    full, reduced = content(analyze(lines, src)), content(analyze(kept, src))
    assert full != reduced
