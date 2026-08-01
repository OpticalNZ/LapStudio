# Getting LapStudio running

**If you have the release zip, there is nothing to install.** Unzip it, run
`LapStudio.exe`, and everything it needs is already inside — ffmpeg for video
export and AiM's reader for `.drk` / `.xrk` logs.

Skip to "First render" below.

---

## Running from source instead

Only needed if you are working on the code rather than using a release.

**1. Python 3.10 or newer** from <https://www.python.org/downloads/>. On Windows
tick **"Add Python to PATH"** during setup.

**2. Start it.** On Windows, double-click `run.bat` — the first run installs the
four packages it needs and then starts the app. Otherwise:

```bash
pip install -r requirements.txt
python ecu_overlay_app.py
```

On Debian or Ubuntu you may also need `sudo apt install python3-tk`.

**3. ffmpeg**, for exporting video. Download from
<https://ffmpeg.org/download.html> and either put `ffmpeg.exe` beside
`ecu_overlay_app.py` or add it to your `PATH`. A *static* build is one
self-contained file; a *shared* build also needs its `av*.dll` and `sw*.dll`
files kept alongside it.

**4. AiM logs only.** CSV and VBO need nothing extra. For `.drk` / `.xrk`, copy
these from a RaceStudio 3 installation into the LapStudio folder:

```
MatLabXRK-2022-64-ReleaseU.dll
libxml2-2.dll
libiconv-2.dll
libz.dll
pthreadVC2_x64.dll
```

You may also need the **Microsoft Visual C++ 2008 SP1 x64 redistributable**,
which provides `msvcr90.dll`.

If a `.drk` will not open, run `python diagnose_aim.py "your log.drk"` — it
reports which dependency is missing and what the app can see in the file.

---

## "Windows protected your PC" or a virus warning

Windows Defender may report **Win32/Wacapew.C!ml** the first time you run it.
This is a false positive.

The `!ml` on the end means it came from a machine-learning guess rather than a
match against known malware. Three things about a bundled Python app make that
guess likely: the program carries other executables inside it (ffmpeg and the
AiM reader), it is not signed with a code-signing certificate, and Microsoft has
no reputation data for a file this new.

To run it anyway: **More info** then **Run anyway** on the SmartScreen dialog. In
Defender, open Protection history and choose Allow.

If you would rather satisfy yourself first, upload the file to
<https://www.virustotal.com/> — a genuine threat is flagged by dozens of engines,
whereas a packaging false positive is flagged by one or two heuristics.

You can also report it to Microsoft at
<https://www.microsoft.com/en-us/wdsi/filesubmission> as a software developer;
they normally clear it within a few days.

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
