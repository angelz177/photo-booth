"""Print a composed sheet.

* Windows: through the Windows spooler (pywin32). Works with DNP's Windows
  drivers; set the printer's default paper size to 4x6 (or 2x6 for a strip
  cutter) in its Printing Preferences.
* Linux / macOS: through CUPS (``lp``). DNP dye-subs work on macOS with DNP's
  driver and on Linux through Gutenprint.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")


class PrintError(RuntimeError):
    pass


def print_file(path: Path, printer_name: str = "", copies: int = 1, extra_options: str = "") -> str:
    if IS_WINDOWS:
        return _print_windows(path, printer_name, copies)
    return _print_cups(path, printer_name, copies, extra_options)


def list_printers() -> list[str]:
    if IS_WINDOWS:
        return _list_printers_windows()
    return _list_printers_cups()


# -- CUPS ------------------------------------------------------------------


def _print_cups(path: Path, printer_name: str, copies: int, extra_options: str) -> str:
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


def _list_printers_cups() -> list[str]:
    lpstat = shutil.which("lpstat")
    if lpstat is None:
        return []
    result = subprocess.run([lpstat, "-a"], capture_output=True, text=True, timeout=10)
    return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]


# -- Windows ---------------------------------------------------------------

HORZRES, VERTRES = 8, 10  # GetDeviceCaps indices: printable area in device pixels


def _win32():
    try:
        import win32print  # type: ignore
        import win32ui  # type: ignore
    except ImportError as exc:
        raise PrintError("pywin32 is not installed. Run: pip install 'photo-booth[windows]'") from exc
    return win32print, win32ui


def _list_printers_windows() -> list[str]:
    win32print, _ = _win32()
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags, None, 2)]


def _print_windows(path: Path, printer_name: str, copies: int) -> str:
    from PIL import Image, ImageWin

    win32print, win32ui = _win32()
    printer = printer_name or win32print.GetDefaultPrinter()
    hdc = win32ui.CreateDC()
    try:
        hdc.CreatePrinterDC(printer)
    except Exception as exc:  # pywin32 raises its own error type
        raise PrintError(f"Could not open printer '{printer}': {exc}") from exc
    try:
        page_w, page_h = hdc.GetDeviceCaps(HORZRES), hdc.GetDeviceCaps(VERTRES)
        with Image.open(path) as img:
            img = img.convert("RGB")
            # Match the sheet orientation, then fill the printable area (borderless).
            if (img.width > img.height) != (page_w > page_h):
                img = img.rotate(90, expand=True)
            scale = max(page_w / img.width, page_h / img.height)
            w, h = int(img.width * scale), int(img.height * scale)
            x0, y0 = (page_w - w) // 2, (page_h - h) // 2
            dib = ImageWin.Dib(img)
            for _ in range(copies):
                hdc.StartDoc(f"Photo booth {path.name}")
                hdc.StartPage()
                dib.draw(hdc.GetHandleOutput(), (x0, y0, x0 + w, y0 + h))
                hdc.EndPage()
                hdc.EndDoc()
    finally:
        hdc.DeleteDC()
    return f"{copies} copy(ies) sent to {printer}"
