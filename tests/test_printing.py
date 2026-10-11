"""Paper-size plumbing: config -> print endpoint -> print_file, and the CUPS command line."""

import subprocess
import sys

import pytest

from booth import printing
from booth.printing import PrintError, _print_cups

if sys.platform.startswith("win"):
    pytest.skip("CUPS path is exercised on Linux/macOS only", allow_module_level=True)


def _fake_run(calls):
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="request id 7", stderr="")
    return run


def test_cups_passes_media_and_printer(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(printing.shutil, "which", lambda name: "/usr/bin/lp")
    monkeypatch.setattr(printing.subprocess, "run", _fake_run(calls))
    out = _print_cups(tmp_path / "x.jpg", "ASK300", 2, "6x2inchx2 Type1", "")
    assert out == "request id 7"
    cmd = calls[0]
    assert cmd[:3] == ["/usr/bin/lp", "-n", "2"]
    assert "-d" in cmd and cmd[cmd.index("-d") + 1] == "ASK300"
    assert "media=6x2inchx2 Type1" in cmd


def test_cups_omits_media_when_empty(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(printing.shutil, "which", lambda name: "/usr/bin/lp")
    monkeypatch.setattr(printing.subprocess, "run", _fake_run(calls))
    _print_cups(tmp_path / "x.jpg", "", 1, "", "")
    assert not any(a.startswith("media=") for a in calls[0])


def test_cups_missing_lp_is_a_print_error(monkeypatch, tmp_path):
    monkeypatch.setattr(printing.shutil, "which", lambda name: None)
    with pytest.raises(PrintError, match="lp"):
        printing.print_file(tmp_path / "x.jpg")


def test_list_paper_sizes_is_empty_off_windows():
    assert printing.list_paper_sizes("anything") == []
