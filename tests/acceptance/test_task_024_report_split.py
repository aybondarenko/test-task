"""TASK-024: a short report.md, report-appendix.md, and a CLI that writes three files."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil

import pytest
from acc_support import (
    ATTACHED_LOG,
    CHAIN_LOG,
    assert_same_json,
    calm,
    h2_titles,
    impl,
    md_section,
    read_log_lines,
    ref_json,
    restart_burst,
    run_analyze,
    saturation_burst,
    scenario,
    sha256_hex,
)

from test_task.contracts.pipeline import SourceRef
from test_task.contracts.recommendations import DB_CATEGORY_TITLES
from test_task.contracts.report import (
    MD_APPENDIX_TITLES,
    MD_EVIDENCE_MAX_CHARS,
    MD_MAIN_EXTRA_TITLES,
    MD_MAX_LINES,
    MD_MAX_SAMPLES_PER_FINDING,
    MD_SECTION_TITLES,
    REPORT_APPENDIX_MD_NAME,
)

FILES = ("report.md", "report-appendix.md", "report.json")


def md(r):
    return impl("report", "render_markdown")(r)


def appendix(r):
    return impl("report", "render_appendix_markdown")(r)


@pytest.fixture(scope="module")
def chain():
    lines = read_log_lines(CHAIN_LOG)
    return impl("pipeline", "analyze")(lines, SourceRef("incident_chain.log", sha256_hex(CHAIN_LOG.read_bytes())))


@pytest.fixture(scope="module")
def rich():
    """Two episodes: DB server saturation, and its restart with client errors."""
    return run_analyze(
        scenario(
            calm(0, 300),
            saturation_burst(300),
            calm(420, 900, first_index=100),
            restart_burst(900, marker_at=1000),
            calm(1010, 1500, first_index=300),
        ),
        file_name="rich.log",
    )


@pytest.fixture(scope="module")
def quiet():
    return run_analyze(calm(0, 900), file_name="quiet.log")


@pytest.mark.criterion("TASK-024:C1")
@pytest.mark.req("OUT-15", "FR-05")
@pytest.mark.parametrize("which", ["chain", "rich", "quiet"])
def test_main_report_structure(chain, rich, quiet, which):
    r = {"chain": chain, "rich": rich, "quiet": quiet}[which]
    text = md(r)
    assert text.split("\n", 1)[0] == f"# Incident Report: {r.source.file_name}"
    assert h2_titles(text) == list(MD_SECTION_TITLES) + list(MD_MAIN_EXTRA_TITLES)
    assert not set(h2_titles(text)) & set(MD_APPENDIX_TITLES)
    assert REPORT_APPENDIX_MD_NAME in text
    assert "\r" not in text and text.endswith("\n") and not text.endswith("\n\n")


@pytest.mark.criterion("TASK-024:C2")
@pytest.mark.req("OUT-15", "DEL-09")
@pytest.mark.parametrize("which", ["chain", "rich", "quiet"])
def test_appendix_structure(chain, rich, quiet, which):
    r = {"chain": chain, "rich": rich, "quiet": quiet}[which]
    text = appendix(r)
    assert text.split("\n", 1)[0] == f"# Incident Report Appendix: {r.source.file_name}"
    assert h2_titles(text) == list(MD_APPENDIX_TITLES)
    assert "\r" not in text and text.endswith("\n") and not text.endswith("\n\n")
    assert text == appendix(r)
    for title in MD_APPENDIX_TITLES:
        body = md_section(text, title)
        assert body is not None and body.strip(), title


LONG = "2026-01-01T00:00:00.000Z WARN orders-db " + "q" * 360


def with_long_samples(r):
    """All samples of the first finding and the first DB signal replaced with a 400-character line."""
    f0 = r.key_findings[0]
    samples = tuple(dataclasses.replace(x, text=LONG) for x in f0.evidence.samples)
    f0 = dataclasses.replace(f0, evidence=dataclasses.replace(f0.evidence, samples=samples))
    sigs = list(r.db_signals)
    assert sigs
    sigs[0] = dataclasses.replace(sigs[0], samples=tuple(dataclasses.replace(x, text=LONG) for x in sigs[0].samples))
    return dataclasses.replace(r, key_findings=(f0,) + r.key_findings[1:], db_signals=tuple(sigs))


def check_truncated(text):
    """A 400-character sample is shown truncated: the ``q…q`` tail is shorter than the original and followed by "...",
    and the whole shown fragment (including the prefix, if printed) is no longer than ``MD_EVIDENCE_MAX_CHARS``."""
    assert LONG not in text
    runs = list(re.finditer(r"q{20,}", text))
    for m in runs:
        assert len(m.group(0)) < 360
        assert text[m.end() : m.end() + 3] == "...", text[m.start() : m.end() + 10]
        start = m.start()
        prefix = LONG[: LONG.index("q")]
        if text[max(0, start - len(prefix)) : start] == prefix:
            start -= len(prefix)
        assert m.end() + 3 - start <= MD_EVIDENCE_MAX_CHARS
    return runs


def finding_block(text, f):
    lines = text.split("\n")
    head = f"### {f.id} [{f.severity.value}] ({f.role.value}) {f.title}"
    assert head in lines, head
    out = []
    for line in lines[lines.index(head) + 1 :]:
        if re.match(r"^#{1,3} ", line):
            break
        out.append(line)
    return "\n".join(out)


@pytest.mark.criterion("TASK-024:C3")
@pytest.mark.req("OUT-15", "OUT-10", "OUT-08")
def test_findings_compact_and_samples_truncated(rich):
    plain = md(rich)
    for f in rich.key_findings:
        block = finding_block(plain, f)
        shown = [
            s for s in f.evidence.samples if re.search(rf"\b{s.line_no}\b", block) and s.text[25:45] in block
        ]
        assert len(shown) <= MD_MAX_SAMPLES_PER_FINDING, f.id
    r = with_long_samples(rich)
    assert check_truncated(md(r)), "the first finding's sample must be shown (truncated)"
    assert check_truncated(appendix(r)), "the DB signal sample must be shown in the appendix (truncated)"


@pytest.mark.criterion("TASK-024:C4")
@pytest.mark.req("OUT-07", "OUT-15")
def test_db_signals_short_block(rich, quiet):
    assert rich.db_signals
    body = md_section(md(rich), "DB Signals")
    lines = body.split("\n")
    for s in rich.db_signals:
        assert len([ln for ln in lines if DB_CATEGORY_TITLES[s.category] in ln]) == 1, s.category
        for x in s.samples:
            assert x.text[:60] not in body
    detailed = md_section(appendix(rich), "DB Signals (detailed)")
    for s in rich.db_signals:
        assert DB_CATEGORY_TITLES[s.category] in detailed
    empty = md_section(md(quiet), "DB Signals")
    assert quiet.db_signals == ()
    assert re.search(r"\bno\b", empty, re.IGNORECASE), empty


def hypothesis_blocks(section, hyps):
    lines = section.split("\n")
    starts = {}
    for h in hyps:
        # a line where the hypothesis id comes first (heading or list item), not a mention in another's evidence
        idx = next((i for i, ln in enumerate(lines) if re.search(rf"\b{h.id}\b", ln[:24])), None)
        assert idx is not None, h.id
        starts[h.id] = idx
    order = sorted(starts.values())
    out = {}
    for h in hyps:
        i = starts[h.id]
        nxt = [j for j in order if j > i]
        out[h.id] = "\n".join(lines[i : (nxt[0] if nxt else len(lines))])
    return out


def mentions(block, value):
    def norm(x):
        return re.sub(r"[^a-z]", "", x.lower())

    return norm(value) in norm(block)


@pytest.mark.criterion("TASK-024:C5")
@pytest.mark.req("OUT-15", "OUT-16", "OUT-08")
def test_attached_main_report_short_with_hypotheses():
    lines = read_log_lines(ATTACHED_LOG)
    r = impl("pipeline", "analyze")(lines, SourceRef("it_task_log (2).log", sha256_hex(ATTACHED_LOG.read_bytes())))
    text = md(r)
    assert len(text.split("\n")) - 1 <= MD_MAX_LINES, len(text.split("\n"))
    hyps = list(r.probable_causes.primary) + list(r.probable_causes.alternatives)
    assert hyps
    blocks = hypothesis_blocks(md_section(text, "Probable Cause (or Candidates)"), hyps)
    for h in hyps:
        b = blocks[h.id]
        assert mentions(b, h.kind.value), (h.id, b)
        assert mentions(b, h.confidence.value), (h.id, b)
        if h.verdict is not None:
            assert mentions(b, h.verdict.value), (h.id, b)
        assert len(h.supporting) <= 3 and len(h.contradicting) <= 3


@pytest.mark.criterion("TASK-024:C6")
@pytest.mark.req("OUT-15", "DEL-09", "FR-05")
def test_json_has_new_fields_and_is_full(rich):
    d = impl("report", "to_json_dict")(rich)
    assert json.loads(impl("report", "render_json")(rich)) == d
    assert_same_json(d, ref_json(rich))
    for f in d["key_findings"]:
        assert "kinds" in f and "shifts" in f["evidence"] and "composites" in f["evidence"]
    hyps = d["probable_causes"]["primary"] + d["probable_causes"]["alternatives"]
    assert hyps
    for h in hyps:
        for key in ("kind", "verdict", "cause_finding_ids", "source_services"):
            assert key in h, key
    assert d["timeline"]["episodes"]
    for e in d["timeline"]["episodes"]:
        assert "recovery_status" in e and "recovery_evidence" in e
    assert len(d["metric_comparison"]) == len(rich.metric_comparison)
    assert len(d["request_traces"]) == len(rich.request_traces)
    src = rich.source
    assert d["source"]["total_lines"] == src.total_lines and d["source"]["anomalous_lines"] == src.anomalous_lines
    assert len(all_hypotheses_json(d)) == len(list(rich.probable_causes.primary) + list(rich.probable_causes.alternatives))


def all_hypotheses_json(d):
    return d["probable_causes"]["primary"] + d["probable_causes"]["alternatives"]


def run_main(argv, capsys):
    try:
        code = impl("cli", "main")(argv)
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.criterion("TASK-024:C7")
@pytest.mark.req("Q-03", "DEL-09", "OUT-15")
def test_cli_writes_three_files(tmp_path, capsys):
    log = tmp_path / "in" / "incident_chain.log"
    log.parent.mkdir()
    shutil.copy2(CHAIN_LOG, log)
    out_dir = tmp_path / "out"
    code, out, _ = run_main([str(log), "--out-dir", str(out_dir)], capsys)
    assert code == 0
    r = impl("pipeline", "analyze")(read_log_lines(log), SourceRef(log.name, sha256_hex(log.read_bytes())))
    expected = {"report.md": md(r), "report-appendix.md": appendix(r), "report.json": impl("report", "render_json")(r)}
    for name in FILES:
        assert (out_dir / name).read_text(encoding="utf-8") == expected[name], name
    marks = ["ZZMDZZ", "ZZAPPZZ", "ZZJSONZZ"]
    template = impl("report", "render_stdout")(r, marks).rstrip("\n")
    pattern, seen = "", set()
    for part in re.split("(" + "|".join(marks) + ")", template):
        if part in marks:
            g = f"p{marks.index(part)}"
            pattern += f"(?P={g})" if g in seen else f"(?P<{g}>.+?)"
            seen.add(g)
        else:
            pattern += re.escape(part)
    assert seen == {"p0", "p1", "p2"}
    m = re.fullmatch(pattern, out.rstrip("\n"), re.DOTALL)
    assert m, out
    from pathlib import Path

    for i, name in enumerate(FILES):
        assert Path(m.group(f"p{i}")).resolve() == (out_dir / name).resolve()
    first = {n: (out_dir / n).read_bytes() for n in FILES}
    code2, out2, _ = run_main([str(log), "--out-dir", str(out_dir)], capsys)
    assert code2 == 0 and out2 == out
    assert {n: (out_dir / n).read_bytes() for n in FILES} == first
    qdir = tmp_path / "q"
    code3, out3, _ = run_main([str(log), "--out-dir", str(qdir), "--quiet"], capsys)
    assert code3 == 0 and out3 == ""
    assert {n: (qdir / n).read_bytes() for n in FILES} == first
