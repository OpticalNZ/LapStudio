# Getting LapStudio running

Everything in this zip is part of LapStudio. Two things are **not** in it,
because they are other people's software and not ours to redistribute — you
install them yourself, once.

---

## 1. Python

Install Python 3.10 or newer from <https://www.python.org/downloads/>.

On Windows, tick **"Add Python to PATH"** during setup. If you miss it, the
launcher below will not find Python.

## 2. Unzip

Put the folder wherever you like — Desktop is fine. Keep the files together: the
app looks for the two `.ttf` fonts beside `ecu_overlay_app.py`.

## 3. Start it

**Windows:** double-click **`run.bat`**. On the first run it installs the four
Python packages it needs (Pillow, numpy, pandas, aggdraw) and then starts the
app. After that it just starts.

**macOS / Linux:**

```bash
pip install -r requirements.txt
python ecu_overlay_app.py
```

On Debian or Ubuntu you may also need `sudo apt install python3-tk`.

## 4. ffmpeg — needed to export video

The app previews without it, but writing video files needs ffmpeg.

Download a build from <https://ffmpeg.org/download.html> (on Windows the
gyan.dev or BtbN builds are the usual choice), then either:

- put `ffmpeg.exe` in the same folder as `ecu_overlay_app.py`, **or**
- add ffmpeg to your system `PATH`

If you take a *shared* build, keep its `av*.dll` and `sw*.dll` files next to
`ffmpeg.exe` — it will not start without them. A *static* build is a single
file and simpler.

## 5. AiM logs only — the RaceStudio DLL

CSV and VBO logs need nothing extra. **Skip this step unless you are opening
AiM `.drk` / `.xrk` files.**

AiM's reader library ships with RaceStudio 3. Copy these from your RaceStudio
installation into the LapStudio folder:

```
MatLabXRK-2022-64-ReleaseU.dll
libxml2-2.dll
libiconv-2.dll
libz.dll
pthreadVC2_x64.dll
```

You may also need the **Microsoft Visual C++ 2008 SP1 x64 redistributable**,
which provides `msvcr90.dll`. Install it from Microsoft rather than copying the
DLL around.

If a `.drk` file fails to open, run:

```
python diagnose_aim.py "your log.drk"
```

It reports which dependency is missing, and what the app can see in the file.

---

## First render

1. **Data** tab — load your log. Channels are detected automatically; correct any
   that are wrong, and tick Invert on anything reading backwards.
2. **Laps** tab — choose how laps are found. Beacons in the file are the most
   reliable; otherwise set a GPS start/finish line or an estimated lap time.
3. **Dash**, **G-Plot**, **Track Map**, **Lap Data** tabs — pick your styles. Each
   previews live; drag the scrubber under the picture to check other moments in
   the session.
4. Bottom bar — set the Start and End times and the output fps. The output is
   named after your log.
5. **GENERATE VIDEO**. The dashboard is written first, then any ticked widgets as
   separate files alongside it.

Drop the results over your footage and key out the magenta. Some styles come out
opaque or cropped to their panel and need no keying at all — where that is the
case, the Backdrop option disappears.

---

## If something goes wrong

Start the app from a terminal rather than by double-clicking, so you can see the
messages:

```
python ecu_overlay_app.py
```

Run the self-tests to check the installation itself:

```
python jitter_test.py
python tests/ui_smoke.py
```

Both should finish without failures. They use generated data, so they work
before you have loaded any log of your own.
