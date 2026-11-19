"""TASK-019: recovery from explicit markers, and a short observation window."""

from __future__ import annotations

from datetime import timedelta

import pytest
from acc_support import ATTACHED_LOG, at, read_log_lines, stage_timeline, text_line

from test_task.contracts.timeline import RecoveryStatus

RS = RecoveryStatus
SRC = "ledger-db"
WORKER = "queue-worker"
BYSTANDER = "audit-worker"


def heartbeat(start, end, services=(SRC, WORKER, BYSTANDER)):
    out, i, t = [], 0, start
    while t < end:
        out.append(text_line(t, "INFO", services[i % len(services)], f"heartbeat ok seq={i}"))
        i += 1
        t += 10
    return out


def burst(start, end, step=10):
    """Server errors and worker queue lag: 2 anomalies every ``step`` s."""
    out, t, k = [], start, 0
    while t < end:
        out.append(text_line(t, "ERROR", SRC, f"could not write block {k} of relation base"))
        out.append(text_line(t + 4, "WARN", WORKER, f"queue lag growing queue_depth={400 + k}"))
        t += step
        k += 1
    return out


def ready(sec, service=SRC):
    return text_line(sec, "INFO", service, "database system is ready to accept connections")


def normalized(sec, service=WORKER):
    return text_line(sec, "INFO", service, "queue depth normalized queue_depth=0")


def build(lines):
    lines = sorted(lines, key=lambda x: x.split(" ", 1)[0])
    ns = stage_timeline(lines)
    ns.line_of = {line: i + 1 for i, line in enumerate(lines)}
    return ns


def check_invariants(ep):
    assert ep.recovered == (ep.recovery_status != RS.NOT_OBSERVED) == (ep.recovery is not None), ep
    assert ep.window.start == ep.onset
    assert ep.window.end == (max(ep.last_anomaly, ep.recovery) if ep.recovery is not None else ep.last_anomaly)


def bucket_end(dt, seconds=60):
    epoch = int(dt.timestamp())
    return dt.__class__.fromtimestamp((epoch // seconds + 1) * seconds, tz=dt.tzinfo)


@pytest.mark.criterion("TASK-019:C1")
@pytest.mark.req("QA-13", "FR-07")
def test_markers_with_short_window():
    m1, m2 = ready(500), normalized(510)
    lines = heartbeat(0, 300) + burst(300, 480) + [m1, m2] + heartbeat(520, 570) + [text_line(570, "INFO", BYSTANDER, "heartbeat ok seq=999")]
    ns = build(lines)
    assert len(ns.timeline.episodes) == 1
    ep = ns.timeline.episodes[0]
    check_invariants(ep)
    assert ep.recovery_status == RS.OBSERVED_SHORT_WINDOW
    assert ep.recovered is True
    assert ep.recovery == at(500)
    assert {e.line_no for e in ep.recovery_evidence} >= {ns.line_of[m1], ns.line_of[m2]}


def tail_deviations(start, end):
    """After the marker: WARN pairs every 120 s, i.e. active buckets that merge into the episode."""
    out, t = [], start
    while t < end:
        out.append(text_line(t, "WARN", WORKER, "queue lag growing queue_depth=450"))
        out.append(text_line(t + 5, "WARN", WORKER, "queue lag growing queue_depth=460"))
        t += 120
    return out


@pytest.mark.criterion("TASK-019:C2")
@pytest.mark.req("QA-13")
def test_marker_before_residual_deviations():
    marker = ready(485)
    rest = heartbeat(0, 300) + burst(300, 480) + tail_deviations(540, 1200) + heartbeat(1200, 1500)
    with_marker = build(rest + [marker])
    without = build(rest)
    assert len(with_marker.timeline.episodes) == 1
    ep = with_marker.timeline.episodes[0]
    check_invariants(ep)
    assert ep.recovery_status == RS.OBSERVED
    assert ep.recovery == at(485)
    assert ep.recovery < ep.last_anomaly
    assert ep.window.end == max(ep.last_anomaly, ep.recovery)
    shape = [(e.onset, e.peak, e.last_anomaly) for e in with_marker.timeline.episodes]
    assert shape == [(e.onset, e.peak, e.last_anomaly) for e in without.timeline.episodes]


@pytest.mark.criterion("TASK-019:C3")
@pytest.mark.req("QA-13", "FR-07")
def test_quiet_after_episode_is_observed():
    ns = build(heartbeat(0, 300) + burst(300, 420) + heartbeat(420, 900))
    (ep,) = ns.timeline.episodes
    check_invariants(ep)
    assert ep.recovery_status == RS.OBSERVED
    end = bucket_end(ep.last_anomaly)
    assert end - timedelta(seconds=1) < ep.recovery <= end, (ep.recovery, end)
    assert ep.recovery_evidence == ()


@pytest.mark.criterion("TASK-019:C3")
@pytest.mark.req("QA-13", "FR-07")
def test_log_ends_during_errors_not_observed():
    ns = build(heartbeat(0, 300) + burst(300, 470))
    (ep,) = ns.timeline.episodes
    check_invariants(ep)
    assert ep.recovery_status == RS.NOT_OBSERVED
    assert ep.recovery is None and ep.recovered is False


def recovery_view(ns):
    return [(e.recovery_status, e.recovery) for e in ns.timeline.episodes]


@pytest.mark.criterion("TASK-019:C4")
@pytest.mark.req("QA-13")
def test_marker_of_unaffected_service_ignored():
    base = heartbeat(0, 300) + burst(300, 470) + heartbeat(470, 540)
    with_marker = build(base + [ready(500, service=BYSTANDER)])
    without = build(base)
    assert len(with_marker.timeline.episodes) == 1
    assert recovery_view(with_marker) == recovery_view(without)
    assert with_marker.timeline.episodes[0].recovery_evidence == ()
    for ep in with_marker.timeline.episodes:
        check_invariants(ep)


@pytest.mark.criterion("TASK-019:C4")
@pytest.mark.req("QA-13")
def test_marker_before_peak_ignored():
    # bucket 300: 2 anomalies, 360: 2, 420: 8 (peak); the marker at 370 is before peak.start
    early = [
        text_line(300, "WARN", WORKER, "queue lag growing queue_depth=300"),
        text_line(310, "WARN", WORKER, "queue lag growing queue_depth=310"),
        text_line(360, "WARN", WORKER, "queue lag growing queue_depth=320"),
        text_line(365, "WARN", WORKER, "queue lag growing queue_depth=330"),
    ]
    base = heartbeat(0, 300) + early + burst(420, 480, step=15) + heartbeat(480, 540)
    with_marker = build(base + [normalized(370)])
    without = build(base)
    (ep,) = with_marker.timeline.episodes
    assert ep.peak.start == at(420)
    assert recovery_view(with_marker) == recovery_view(without)
    check_invariants(ep)


@pytest.mark.criterion("TASK-019:C4")
@pytest.mark.req("QA-13")
def test_marker_after_next_onset_not_for_previous_episode():
    marker = ready(950)
    base = heartbeat(0, 300) + burst(300, 420) + heartbeat(420, 900) + burst(900, 1020) + heartbeat(1020, 1500)
    with_marker = build(base + [marker])
    without = build(base)
    assert len(with_marker.timeline.episodes) == 2
    e1, e1_ref = with_marker.timeline.episodes[0], without.timeline.episodes[0]
    assert with_marker.line_of[marker] not in {e.line_no for e in e1.recovery_evidence}
    assert (e1.recovery_status, e1.recovery) == (e1_ref.recovery_status, e1_ref.recovery)
    for ep in with_marker.timeline.episodes:
        check_invariants(ep)


@pytest.mark.criterion("TASK-019:C5")
@pytest.mark.req("QA-13", "FR-07")
def test_attached_log_second_episode_short_window():
    ns = stage_timeline(read_log_lines(ATTACHED_LOG))
    eps = ns.timeline.episodes
    assert len(eps) == 2
    e2 = eps[-1]
    assert e2.recovery_status == RS.OBSERVED_SHORT_WINDOW
    assert e2.recovery is not None
    assert e2.recovery.strftime("%H:%M:%S.") + f"{e2.recovery.microsecond // 1000:03d}" == "14:30:17.936"
    assert {e.line_no for e in e2.recovery_evidence} >= {498, 517}
    for ep in eps:
        check_invariants(ep)
