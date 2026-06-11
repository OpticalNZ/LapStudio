# LapStudio — ECU Telemetry Video Overlay

LapStudio turns ECU / data-logger CSV (and optional VBO / AiM) logs into
broadcast-style telemetry dashboards rendered as transparent **chroma-key**
video overlays you can drop onto track footage.

A desktop GUI (tkinter) lets you map your log's columns to channels, pick a
dashboard style, set lap timing, and export an overlay video.

## Dashboard styles

| Style | Description |
|-------|-------------|
| Dash 1 (white gauge) | Analog-style circular gauge, light face, Poppins data font |
| Dash 2 (black gauge) | Analog-style circular gauge, dark face, Poppins data font |
| Dash 3 | Race dash: RPM bar graph + data row |
| Dash 4 | Coloured data-box grid (full-height strip, compact panels) |
| Dash 5 | Top RPM bar + columns + large gear (keeps its internal G-trace) |
| Dash 6 (Logger) | Two data columns + multi-channel trace plot |
| Dash 6b (Wide Logger) | Full-width trace plot + bottom panel row |
| Dash 8 / Dash 8 (Chroma) | Modern circular telemetry gauge; channels below the bars |
| Dash 8b (bars inside) / (…, Chroma) | Bars inside the gauge; 2×2 parallelogram data panels |
| Vertical / Horizontal Text | Minimal text-only readouts |

Most styles render against a magenta (`255,0,255`) chroma background for easy
keying in any video editor.

**G-force trace:** the embedded G-trace plot was removed from Dash 4 and Dash 8/8b
(Dash 5 keeps its internal one; Dash 6 never had one). The G-force trace is now a
**separate overlay video** — see below.

## Optional channels (A & B)

Beyond the standard channels (RPM, speed, gear, throttle, brake, lat/long G), you
can map **two extra channels** (e.g. oil pressure, coolant temp). In the app's
*Optional Channels* section, pick a source column, give each a short (≤4 character)
label, and optionally a **single-character unit** (e.g. `b`, `C`) that is appended
to the value (e.g. `350b`, `87C`). Each dash places Channel A / B sensibly for its
layout. If a channel isn't selected it simply doesn't appear.

## Separate overlay graphics

Two optional tick-boxes render **standalone overlay videos** alongside the main
dashboard (positionable anywhere in your editor):

- **Track map** (`<name>_trackmap.mp4`) — the circuit outline from GPS, coloured by
  speed, with a moving position dot. Needs GPS lat/lon in the log.
- **G-force trace** (`<name>_gtrace.mp4`) — a cropped G-G plot (lateral ±2.5G,
  longitudinal ±2.0G, 0.5G grid), speed-coloured, with live Lat/Lon readouts.

## Requirements

- Python 3.10+
- `Pillow`, `aggdraw`, `numpy`, `pandas` (see `requirements.txt`)
- `tkinter` (bundled with CPython; on Debian/Ubuntu: `sudo apt install python3-tk`)
- `ffmpeg` on your `PATH` (for video export)

```bash
pip install -r requirements.txt
```

The `BigShoulders-Bold.ttf` font must sit next to the Python modules; the
renderer falls back to DejaVu if it's missing.

## Running

```bash
python ecu_overlay_app.py
```

1. Load a log file (CSV; VBO/AiM if the optional readers are present).
2. Map columns to channels and (optionally) set Channel A / B.
3. Choose a dashboard style and output resolution.
4. Set lap detection / timing options.
5. Export — produces a chroma-key overlay video.

## Project layout

```
ecu_overlay_app.py     # tkinter desktop GUI
renderer_pil.py        # multi-style frame renderer + video export pipeline
dash8_render.py        # standalone Dash 8 / 8b gauge renderer
trackmap_render.py     # standalone track-map overlay renderer (separate video)
BigShoulders-Bold.ttf  # display font used across dashes
Poppins-Bold.ttf       # modern bold face used for Dash 1/2 data text
requirements.txt
```

Optional, loaded lazily if present: `renderer_multistyle.py`, `vbo_reader.py`,
`aim_reader.py`.

## Track map overlay

Tick **"Generate track map graphic"** before rendering to also produce a
*separate* video file (`<name>_trackmap.mp4`) containing the track outline drawn
from GPS lat/lon, coloured by speed, with a moving dot for the current position.
It's a standalone chroma-key overlay you position anywhere over your footage,
independent of the chosen dashboard. Requires GPS latitude/longitude in the log
(set them in the Lap section if not auto-detected).

## Notes

- Lap timer counts up live during a lap, then shows the completed lap time
  briefly after the line.
- The G-trace can be coloured by speed.
- Backgrounds for the Dash 8 family are generated once and cached per render.
