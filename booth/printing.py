"""Print a composed sheet through CUPS (Linux / macOS) using ``lp``.

DNP dye-sub printers (DS620, DS820, RX1HS, QW410) work on macOS with DNP's
driver and on Linux through Gutenprint. Windows printing is not wired up yet.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class PrintError(RuntimeError):
    pass


def print_file(path: Path, printer_name: str = "", copies: int = 1, extra_options: str = "") -> str:
    if sys.platform.startswith("win"):
        raise PrintError("Printing on Windows is not supported yet; use macOS or Linux (CUPS).")
    lp = shutil.which("lp")
    if lp is None:
        raise PrintError("The 'lp' command was not found. Install CUPS (cups-client).")
    cmd = [lp, "-n", str(copies), "-o", "fit-to-page"]
    if printer_name:
        cmd += ["-d", printer_name]
    if extra_options:
        cmd += ["-o", extra_options]
    cmd.append(str(path))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or result.stdout.strip() or "lp failed")
    return result.stdout.strip()


def list_printers() -> list[str]:
    lpstat = shutil.which("lpstat")
    if lpstat is None:
        return []
    result = subprocess.run([lpstat, "-a"], capture_output=True, text=True, timeout=10)
    names = []
    for line in result.stdout.splitlines():
        if line.strip():
            names.append(line.split()[0])
    return names
