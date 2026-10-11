"""Print a composed sheet.

* Windows: through the Windows spooler (pywin32). Works with vendor dye-sub
  drivers such as the Fujifilm ASK-300 and DNP DS-series. A paper size name
  from the driver (e.g. ``6x2inchx2 Type1`` for two 2x6 strips cut from one
  4x6) can be applied per job, so strips and 4x6 sheets print without
  changing Printing Preferences.
* Linux / macOS: through CUPS (``lp``); the paper size is passed as
  ``-o media=<name>``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform.startswith("win")


class PrintError(RuntimeError):
    pass


def print_file(path: Path, printer_name: str = "", copies: int = 1, paper_size: str = "",
               extra_options: str = "") -> str:
    """Print ``path`` and return a short job description.

    ``paper_size`` is a driver paper/form name (see ``list_paper_sizes``);
    empty means the printer's default.
    """
    if IS_WINDOWS:
        return _print_windows(path, printer_name, copies, paper_size)
    return _print_cups(path, printer_name, copies, paper_size, extra_options)


def list_printers() -> list[str]:
    if IS_WINDOWS:
        return _list_printers_windows()
    return _list_printers_cups()


def list_paper_sizes(printer_name: str = "") -> list[str]:
    """Paper size names the driver offers for this printer (Windows only)."""
    if IS_WINDOWS:
        return _list_paper_sizes_windows(printer_name)
    return []


# -- CUPS ------------------------------------------------------------------


def _print_cups(path: Path, printer_name: str, copies: int, paper_size: str, extra_options: str) -> str:
    lp = shutil.which("lp")
    if lp is None:
        raise PrintError("The 'lp' command was not found. Install CUPS (cups-client).")
    cmd = [lp, "-n", str(copies), "-o", "fit-to-page"]
    if printer_name:
        cmd += ["-d", printer_name]
    if paper_size:
        cmd += ["-o", f"media={paper_size}"]
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
DC_PAPERS, DC_PAPERNAMES = 2, 16  # DeviceCapabilities queries
DM_PAPERSIZE, DM_FORMNAME = 0x2, 0x10000  # DEVMODE.Fields bits
DM_IN_BUFFER, DM_OUT_BUFFER = 8, 2  # DocumentProperties modes


def _win32():
    try:
        import win32gui  # type: ignore
        import win32print  # type: ignore
        import win32ui  # type: ignore
    except ImportError as exc:
        raise PrintError("pywin32 is not installed. Run: pip install 'photo-booth[windows]'") from exc
    return win32print, win32ui, win32gui


def _list_printers_windows() -> list[str]:
    win32print, _, _ = _win32()
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags, None, 2)]


def _paper_catalog(win32print, printer: str) -> tuple[list[str], list[int], str]:
    """Return (names, ids, port) for the printer's driver paper sizes."""
    handle = win32print.OpenPrinter(printer)
    try:
        port = win32print.GetPrinter(handle, 2)["pPortName"]
    finally:
        win32print.ClosePrinter(handle)
    names = [n.strip("\x00").strip() for n in win32print.DeviceCapabilities(printer, port, DC_PAPERNAMES)]
    ids = list(win32print.DeviceCapabilities(printer, port, DC_PAPERS))
    return names, ids, port


def _list_paper_sizes_windows(printer_name: str) -> list[str]:
    win32print, _, _ = _win32()
    printer = printer_name or win32print.GetDefaultPrinter()
    try:
        names, _, _ = _paper_catalog(win32print, printer)
    except Exception as exc:  # unknown printer, offline driver, ...
        log.warning("could not list paper sizes for %s: %s", printer, exc)
        return []
    return [n for n in names if n]


def _printer_dc(win32print, win32ui, win32gui, printer: str, paper_size: str):
    """Create a printer DC, applying ``paper_size`` through DEVMODE when possible."""
    if paper_size:
        try:
            handle = win32print.OpenPrinter(printer)
            try:
                devmode = win32print.GetPrinter(handle, 2)["pDevMode"]
                names, ids, _ = _paper_catalog(win32print, printer)
                matches = [i for i, n in enumerate(names) if n.lower() == paper_size.lower()]
                if devmode is None or not matches:
                    raise PrintError(f"paper size '{paper_size}' not offered by {printer}")
                devmode.PaperSize = ids[matches[0]]
                devmode.FormName = names[matches[0]]
                devmode.Fields = devmode.Fields | DM_PAPERSIZE | DM_FORMNAME
                # Let the driver validate and fill in the rest of the DEVMODE.
                win32print.DocumentProperties(0, handle, printer, devmode, devmode, DM_IN_BUFFER | DM_OUT_BUFFER)
            finally:
                win32print.ClosePrinter(handle)
            hdc = win32gui.CreateDC("WINSPOOL", printer, devmode)
            return win32ui.CreateDCFromHandle(hdc), names[matches[0]]
        except Exception as exc:
            log.warning("falling back to the driver's default paper size: %s", exc)
    hdc = win32ui.CreateDC()
    try:
        hdc.CreatePrinterDC(printer)
    except Exception as exc:  # pywin32 raises its own error type
        raise PrintError(f"Could not open printer '{printer}': {exc}") from exc
    return hdc, "driver default"


def _print_windows(path: Path, printer_name: str, copies: int, paper_size: str) -> str:
    from PIL import Image, ImageWin

    win32print, win32ui, win32gui = _win32()
    printer = printer_name or win32print.GetDefaultPrinter()
    hdc, paper_used = _printer_dc(win32print, win32ui, win32gui, printer, paper_size)
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
    return f"{copies} copy(ies) sent to {printer} on {paper_used}"
