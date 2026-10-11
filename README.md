# Photo Booth

Self-hosted photo booth for weddings, quinceañeras and parties. A Canon DSLR
(or any libgphoto2-supported camera) streams live view to a full-screen touch
UI; guests tap, pose through a countdown, and get a printed strip plus a QR
code to download their photo.

| Attract | Live view + countdown |
| --- | --- |
| ![Attract screen](docs/screenshots/attract.png) | ![Live view](docs/screenshots/live-countdown.png) |

| Review | Settings |
| --- | --- |
| ![Review screen](docs/screenshots/review.png) | ![Settings](docs/screenshots/admin.png) |

## How it works

```
┌──────────────┐  USB (PTP)   ┌──────────────────────────────┐   HTTP/LAN   ┌────────────────┐
│ Canon DSLR   │ ───────────► │ booth service (Python)       │ ◄──────────► │ booth screen   │
│ live view +  │              │  • camera adapter (gphoto2)  │  MJPEG live  │ (any browser:  │
│ full-res JPG │              │  • compose strips (Pillow)   │  + JSON API  │  iPad, laptop, │
└──────────────┘              │  • print via CUPS            │              │  touchscreen)  │
                              │  • gallery + QR share pages  │              └────────────────┘
                              └──────────────────────────────┘
```

* **`booth/cameras/`**: pluggable camera adapters. `gphoto2` (Canon, Nikon,
  Sony, Fuji over USB), `webcam` (any UVC webcam via OpenCV) and `mock`
  (synthetic frames, no hardware). Adding Canon EDSDK or Sony's SDK later means
  implementing the same four-method interface in `base.py`.
* **`booth/manager.py`**: owns the camera. Pulls live-view frames on a
  background thread only while someone is watching, serializes capture against
  preview, and reconnects automatically if the camera drops.
* **`booth/compose.py`**: 4x6 at 300 dpi output. `strip` (3 photos on a 2x6
  strip, doubled onto one 4x6 sheet), `single` and `grid` layouts, with
  optional PNG overlays for branding.
* **`booth/static/`**: the kiosk UI (`/`), settings (`/admin`) and gallery
  (`/gallery`). Plain HTML, CSS and ES modules; no build step.

## Quick start (no camera needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m booth --camera mock
```

Open http://localhost:8000/ for the booth, `/admin` for settings and
`/gallery` for the event gallery. Hold the top-left corner of the booth screen
for two seconds and enter the PIN (default `1234`) to reach settings from the
kiosk.

## Canon DSLR setup

The booth talks to the camera with libgphoto2 over USB. This works on Linux
(including Raspberry Pi) and macOS. Windows is not supported by libgphoto2;
plan for a Canon EDSDK adapter there.

### 1. Install

Linux / Raspberry Pi OS:

```bash
sudo apt install libgphoto2-dev
pip install -e ".[gphoto2]"
```

macOS:

```bash
brew install libgphoto2
pip install -e ".[gphoto2]"
```

### 2. Prepare the camera

* Image quality: **JPEG** (Large/Fine), not RAW.
* Frame the booth, focus, then switch the lens to **MF** so every shot fires
  instantly without hunting.
* Shooting mode M or Av with your flash/strobe settings dialled in.
* Auto power off: **disable**.
* Use a USB cable rated for data (many "charging" cables are not), and avoid
  hubs when possible.

### 3. Free the USB port

Desktop environments grab PTP cameras to offer file import. Stop that once
per boot:

```bash
# Linux (GNOME and friends)
pkill -f gvfs-gphoto2-volume-monitor

# macOS
killall ptpcamerad 2>/dev/null; killall PTPCamera 2>/dev/null
```

### 4. Run

```bash
python -m booth --camera gphoto2
```

The settings page shows the detected model, live frame counter and a
**Test capture** button so you can confirm the pipeline before guests arrive.

`gphoto2 --auto-detect` and `gphoto2 --capture-preview` (from the `gphoto2`
CLI package) are handy to verify the camera outside the app.

## Deployment shapes

* **One box.** A Linux or macOS mini PC with a touchscreen runs the service
  and shows `http://localhost:8000/` in a kiosk browser
  (`chromium --kiosk http://localhost:8000/`).
* **iPad front end + booth box.** A Raspberry Pi 5 (or any small PC) runs the
  service and the printer. The iPad joins the Pi's Wi-Fi hotspot and opens the
  booth URL in Safari (Add to Home Screen gives a full-screen app). Live view
  streams as MJPEG so it works in every browser with no plugins.

Guests' phones must reach the service to use the QR code. On a local hotspot
that just works; for public links set **Share base URL** in settings to a
tunnel or reverse proxy that forwards to the service.

## Printing

Printing goes through CUPS (`lp`). Enable it in settings and pick the printer.
DNP dye-sub printers (DS620, DS820, RX1HS, QW410) work on macOS with DNP's
driver and on Linux with Gutenprint. The print file is always 1200x1800
(4x6 at 300 dpi); the strip layout puts two identical 2x6 strips on one sheet
for the printer's cutter.

## Branding overlays

Drop a transparent PNG in the data folder and it is composited on top of the
photos, replacing the default text footer:

| Layout | File | Size | Photo windows (x, y, w, h) |
| --- | --- | --- | --- |
| strip | `data/overlays/strip.png` | 600x1800 | (20,20,560,373) (20,413,560,373) (20,806,560,373) |
| single | `data/overlays/single.png` | 1200x1800 | (20,200,1160,773) |
| grid | `data/overlays/grid.png` | 1200x1800 | (20,20,570,380) (610,20,570,380) (20,420,570,380) (610,420,570,380) |

## Settings

All settings live in `data/config.json` and are editable at `/admin`: event
name, layout, countdown, pause between shots, mirrored live view, footer text,
review timeout, printing, share base URL and the admin PIN.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | camera state, config, shots per session |
| GET / PUT | `/api/config` | read or replace settings |
| GET | `/api/live` | MJPEG live view (`multipart/x-mixed-replace`) |
| GET | `/api/live/frame.jpg` | one live-view frame |
| POST | `/api/sessions` | start a session |
| POST | `/api/sessions/{id}/capture` | take the next photo |
| POST | `/api/sessions/{id}/finish` | compose print + share images |
| GET | `/api/sessions/{id}/qr.png` | QR code for the share page |
| POST | `/api/sessions/{id}/print` | send the composite to the printer |
| GET | `/api/sessions` | finished sessions for the current event |
| GET | `/s/{id}` | guest share page |

Photos are served from `/photos/<session id>/`.

## Development

```bash
pip install -e ".[dev]"
pytest
python -m booth --camera mock          # synthetic camera
python -m booth --camera webcam        # any UVC webcam (pip install -e ".[webcam]")
```

Data (captures, composites, config) is written to `./data`, which is
git-ignored. Use `--data /path` or `BOOTH_DATA` to move it.

## Roadmap

* Canon EDSDK adapter for Windows.
* GIF / boomerang mode, video messages.
* SMS and email delivery, cloud gallery sync.
* Template designer and multi-event management.
* Camera settings (ISO, aperture, shutter) from the settings page.
