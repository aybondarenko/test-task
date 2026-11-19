import json
from pathlib import Path

import pytest

from test_task.cli import main


def test_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "a.log"
    log.write_text("2024-01-01 00:00:00 INFO hello\n", encoding="utf-8")
    out = tmp_path / "o"
    assert main([str(log), "--out-dir", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "Incident Summary" in stdout
    for name in ("report.md", "report-appendix.md", "report.json"):
        assert (out / name).is_file()
        assert str(out / name) in stdout
    assert (
        (out / "report-appendix.md")
        .read_text(encoding="utf-8")
        .startswith("# Incident Report Appendix: a.log\n")
    )
    assert (
        json.loads((out / "report.json").read_text(encoding="utf-8"))["source"][
            "file_name"
        ]
        == "a.log"
    )


def test_llm_warns_and_quiet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "a.log"
    log.write_bytes(b"\xff bad\n")
    out = tmp_path / "o"
    assert main([str(log), "--llm", "--out-dir", str(out)]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and err
    assert not out.exists()


def test_quiet_prints_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "a.log"
    log.write_bytes(b"\xff bad\n")
    assert main([str(log), "--out-dir", str(tmp_path / "o"), "--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_usage_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "none")]) == 2
    assert main(["--bogus"]) == 2
    f = tmp_path / "f"
    f.write_text("x", encoding="utf-8")
    log = tmp_path / "a.log"
    log.write_text("x\n", encoding="utf-8")
    assert main([str(log), "--out-dir", str(f)]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_task import cli

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(cli, "analyze", boom)
    log = tmp_path / "a.log"
    log.write_text("x\n", encoding="utf-8")
    assert cli.main([str(log), "--out-dir", str(tmp_path / "o")]) == 1


def test_dunder_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    log = tmp_path / "a.log"
    log.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["test_task", str(log), "--out-dir", str(tmp_path / "o"), "--quiet"]
    )
    with pytest.raises(SystemExit) as info:
        runpy.run_module("test_task", run_name="__main__")
    assert info.value.code == 0


def test_out_dir_uncreatable_and_unwritable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "f"
    f.write_text("x", encoding="utf-8")
    log = tmp_path / "a.log"
    log.write_text("x\n", encoding="utf-8")
    assert main([str(log), "--out-dir", str(f / "sub")]) == 2
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert main([str(log), "--out-dir", str(ro)]) == 2
    finally:
        ro.chmod(0o700)
    assert "Traceback" not in capsys.readouterr().err


def test_parse_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = "2026-03-27T14:00:0{}Z INFO svc-a ok\n"
    log = tmp_path / "mixed.log"
    log.write_text(good.format(1) + "garbage line\n" * 3 + good.format(2), encoding="utf-8")
    out_dir = tmp_path / "out"
    assert main([str(log), "--out-dir", str(out_dir), "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "3 of 5 non-blank lines (60%) could not be parsed" in err
    assert "could not be parsed" in (out_dir / "report.md").read_text(encoding="utf-8")

    clean = tmp_path / "clean.log"
    clean.write_text(good.format(1) + good.format(2), encoding="utf-8")
    assert main([str(clean), "--out-dir", str(out_dir), "--quiet"]) == 0
    assert capsys.readouterr().err == ""
    assert "could not be parsed" not in (out_dir / "report.md").read_text(encoding="utf-8")
