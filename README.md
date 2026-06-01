# LapStudio Renderer

LapStudio is a motorsport telemetry overlay generator that creates chroma-key video overlays from ECU and datalogger files. Designed to be composited over dashcam or track footage in any video editor.

Demo video

https://www.youtube.com/watch?v=mCYPJKL59rA

## Features

- **Multiple dash styles** — Gauge-based, text column, logger/trace, and more
- **Chroma key output** — Magenta background for easy compositing in any NLE
- **Live lap timing** — GPS-based lap detection with live elapsed time display
- **G-force trace** — XY g-force trail with optional speed colouring
- **Multi-channel time plots** — TPS, brake, g-forces, gear, speed over time (Dash 6)
- **Adjustable trail time, FPS, and time range**

## Supported File Formats

| Format | Source |
|--------|--------|
| `.csv` | AIM RaceStudio, MoTeC, generic |
| `.vbo` | Racelogic VBOX / VRacer |

> **Note:** AIM XRK/DRK binary format support is under development.

## Dashboard Styles

| Name | Description |
|------|-------------|
| Dash 1 (white gauge) | Classic white-faced RPM gauge |
| Dash 2 (black gauge) | Dark-faced RPM gauge |
| Dash 3 | Race dash with lap badges |
| Dash 4 | Coloured data boxes + g-trace |
| Dash 5 | Slim TPS/brake bars + g-trace |
| Dash 6 (Logger) | Time-series trace plots for all channels |
| Vertical Text | Full-frame vertical column layout |
| Horizontal Text | Full-frame horizontal row layout |

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) — place `ffmpeg.exe` in the same folder as the app

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Running

```bash
python ecu_overlay_app.py
```

## Usage

1. **Load a log file** — CSV or VBO
2. **Configure lap detection** — GPS crossing or beacon markers
3. **Choose a visual style and output FPS**
4. **Set time range and trail time**
5. **Click Render** — output is a chroma-key `.mp4`

Composite the output over your dashcam footage using any video editor (DaVinci Resolve, Premiere, Final Cut, etc.) with the chroma key set to magenta (255, 0, 255).

## Project Structure

```
LapStudio/
├── ecu_overlay_app.py      # Main GUI application
├── renderer_pil.py         # All rendering logic and dash styles
├── vbo_reader.py           # VBOX/VRacer VBO file reader
├── xrk_reader.py           # AIM XRK/DRK reader (in development)
├── xrk_helper.py           # AIM DLL subprocess helper
├── ffmpeg.exe              # Required — not included, download separately
├── requirements.txt
└── README.md
```

## License

MIT License — see [LICENSE](LICENSE)

## Contributing

Pull requests welcome. Bug reports via GitHub Issues.
