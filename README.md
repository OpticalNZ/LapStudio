# LapStudio — motorsport telemetry video overlays

LapStudio turns a datalog into broadcast-style telemetry overlays, rendered as
chroma-key video you can drop straight onto track footage in any editor.

A desktop app (tkinter) maps your log's columns to channels, previews each
overlay live as you change settings, and exports the finished videos.

Reads **CSV**, **VBO** (RaceLogic and compatible), **MoTeC i2 `.ld`** and
**AiM `.drk` / `.xrk`** logs.

---

## What it produces

**The dashboard** — one of fourteen styles, rendered at 1920×750 (or wider where
the layout needs it):

| Style | Description |
|-------|-------------|
| Dash 1 · Round gauge, light | Analogue circular gauge, light face |
| Dash 2 · Round gauge, dark | The same gauge on a dark face |
| Dash 3 · Full panel | Solid panel filling the frame: rpm bar over a data row |
| Dash 4 · Data strip | Coloured data boxes in a compact strip |
| Dash 5 · Gear centre | Rpm bars above, lap column left, large gear centre, G-plot right |
| Dash 6 · Logger | Two data columns beside multi-channel trace plots |
| Dash 6b · Logger, wide | Full-width trace plot over a single panel row |
| Dash 7 · HUD strip | Wide heads-up strip |
| Dash 8 · Circular gauge | Modern circular telemetry gauge with a shift-light ring |
| Dash 8b · Circular gauge, bars inside | The same with throttle/brake inside and slanted data panels |
| Dash 9 · Triple gauge | Three round gauges plus a data strip |
| Dash 10 · Twin arcs | Mirrored rpm arcs meeting at the centre |
| Text · Vertical columns | Minimal text-only readout, stacked |
| Text · Horizontal rows | Minimal text-only readout, in a row |

**Three optional widgets**, each rendered as its own video so you can place them
independently:

- **Track map** — the circuit from GPS, coloured by speed, with a moving car dot.
  Three modes: **All Laps** draws every lap in the range as logged; **One Lap**
  draws one clean circuit taken from the fastest lap; **Stage** draws a length of
  track that scrolls past a car pinned to the centre, with a selectable length.
- **G-force trace** — a G-G plot as a crosshair target or an X-Y graph, with a
  speed-coloured trail and live readouts.
- **Lap data** — the current lap and time, or the full lap list with the current
  lap highlighted and the best in gold, in any of seven graphic styles.

Most overlays render on a magenta (`255,0,255`) or green chroma field. Some
styles fill their frame opaquely, or can be cropped to their panel, and then need
no keying at all — the app hides the backdrop option when it does not apply.

---

## Installing

```bash
git clone https://github.com/OpticalNZ/LapStudio.git
cd LapStudio
pip install -r requirements.txt
```

Or download the zip from the repository and unpack it. See `SETUP.md` for a
step-by-step version including ffmpeg and the AiM reader.

Requirements:

- Python 3.10 or newer. Note the Windows floor comes from Python, not LapStudio:
  3.13+ needs Windows 10, 3.12 still runs on 8.1, 3.8 on 7. Build with 3.12 if
  you need to support Windows 8.1.
- `tkinter` — bundled with CPython on Windows and macOS; on Debian/Ubuntu
  `sudo apt install python3-tk`
- **ffmpeg** on your `PATH`, or `ffmpeg.exe` beside the source. Not bundled here;
  download it from <https://ffmpeg.org/download.html>.
- **AiM logs only:** AiM's `MatLabXRK` DLL and its dependencies, from a
  RaceStudio 3 installation. Not bundled — it is AiM's software, not ours. Put it
  beside the source or point `XRK_DLL_PATH` at it. CSV and VBO need nothing extra.

Run it:

```bash
python ecu_overlay_app.py
```

---

## Using it

The window is organised as tabs, with the settings that apply to the whole job in
a bar along the bottom.

- **Data** — load the log, map columns to channels, invert any that read backwards
- **Laps** — how laps are detected: beacons in the file, a GPS start/finish line,
  point-to-point for hillclimbs and stages, or an estimated lap time
- **Dash**, **G-Plot**, **Track Map**, **Lap Data** — one tab per overlay, each
  with a live preview beside the controls and a time scrubber under the picture

The bottom bar holds the export time range, output fps, the render buttons and
progress, plus a light/dark theme toggle in the corner. The output file is named
after the log you loaded.

Previews redraw automatically about 300 ms after you change any setting, and only
for the tab you are looking at.

---

## Notes on how it works

**Text does not jitter.** The display fonts are proportional, so a centred number
would slide sideways as its digits changed. Every digit is drawn in a fixed-width
cell (`textgrid.py`), so a value's position depends only on the slot it occupies.

**Shift-light colours** follow a redline estimated from the data — the 99.5th
percentile of rpm, not the peak, so a single spike cannot push the bands out of
reach. Bands are proportional to it, so they behave the same on a 6000 rpm diesel
and a 15000 rpm bike engine.

**Lap timing** counts up live through a lap, then shows the completed time.

**G sensor levelling** is optional and measured, not assumed. A logger on an
angled mount reads gravity as acceleration, biasing the G plot and shortening
every braking figure. Two methods that need neither a stationary car nor level
ground estimate the tilt - a distance-weighted average over closed laps, and a
regression against acceleration derived from speed - and they must agree before
anything is corrected. The tick sits beside Invert and Smooth in the channel
mapping, one per G axis, and stays greyed out for an axis that is already level
or has not been measured. `gsensor.py`.

**MoTeC logs bring their own lap times.** The `.ld` format holds each completed
lap time in a channel, so laps come from the logger's beacon rather than being
estimated from GPS. Channels are logged at their own rates, from 1 Hz counters to
50 Hz brakes, and are resampled onto a common 25 Hz base on load. The roughly one
third of channels that report the dash's own health - bus utilisation, error
counters, firmware versions - are hidden from the mapping lists by default.

**Backgrounds** for the gauge dashes are generated once and cached per render.

---

## Tests

```bash
python jitter_test.py       # fixed digit-pitch text engine; no readout may move
python tests/ui_smoke.py    # builds the whole UI headlessly and exercises it
python tests/motec_check.py # loads a MoTeC .ld end to end, if one is present
python tests/gsensor_check.py # tilt estimator, on sessions with a planted tilt
```

`tests/ui_smoke.py` uses a stub tkinter (`tests/stubs`) so the interface can be
built and driven without a display: it walks every tab, every overlay setting and
every dash style, rendering each preview for real through PIL. See
`tests/README.md`.

---

## Project layout

```
ecu_overlay_app.py     desktop app: tabs, previews, render jobs
renderer_pil.py        dashboard renderer, style table, video pipeline
dash8_render.py        the Dash 8 / 8b circular gauge
trackmap_render.py     track map overlay
gtrace_render.py       G-force trace overlay
lapdata_render.py      lap data overlay
textgrid.py            fixed digit-pitch text, shared by every renderer
vbo_reader.py          VBO parser
motec_reader.py        MoTeC i2 .ld parser, written from the format up
gsensor.py             measures G sensor mounting tilt and corrects it
xrk_reader.py          AiM reader front end
xrk_helper.py          subprocess that talks to the AiM DLL
diagnose_aim.py        prints what the app sees in a log; useful for mapping bugs
tests/                 headless UI smoke test and its tkinter stubs
```

---

## Licence

LapStudio is MIT licensed — see `LICENSE`.

The two bundled fonts are not covered by that licence:

- **Big Shoulders Display** and **Poppins** are licensed under the
  SIL Open Font License 1.1. See `FONT-LICENSES.md`.

ffmpeg and the AiM DLL are not distributed with this project; install them
yourself as described above.

**Telemetry logs are not included.** The tests generate their own synthetic data,
so nothing in this repository contains anyone's session data.
