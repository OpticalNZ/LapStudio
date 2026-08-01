# Getting LapStudio running

**If you have the release zip, there is nothing to install.** Unzip it, run
`LapStudio.exe`, and everything it needs is already inside — ffmpeg for video
export and AiM's reader for `.drk` / `.xrk` logs.

**Windows 10 or newer.** The released build embeds Python 3.14, which Microsoft's
support lifecycle limits to Windows 10 and later. On Windows 8.1 or 8 it stops
with *"Failed to load Python DLL python314.dll"*. See "Older Windows" below.

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

## Older Windows: "Failed to load Python DLL python314.dll"

The release will not run on Windows 8.1 or 8, and the reason is not fixable on
the machine. Python itself sets the floor — from the Python documentation:

> Python 3.14 supports Windows 10 and newer. If you require Windows 7 support,
> please install Python 3.8. If you require Windows 8.1 support, please install
> Python 3.12.

So a build made with Python 3.14 needs Windows 10. Nothing installed on the old
machine changes that.

If a Windows 8.1 machine has to be supported, LapStudio can be rebuilt from
source using **Python 3.12** — everything it uses works there. Windows 8.0 is
not supported by any current Python and would need the free update to 8.1 first.

---

## "api-ms-win-crt-stdio-l1-1-0.dll is missing"

Or any other `api-ms-win-crt-*.dll`. This is the **Universal C Runtime**, a
Microsoft component that Windows 10 and 11 include as standard but Windows 8.1
and 7 do not. Python, ffmpeg and the imaging library all rely on it.

Fix it on that machine, whichever is easier:

- Run Windows Update and take everything, which pulls in **KB2999226**
  ("Update for Universal C Runtime in Windows"), or
- install the **Visual C++ 2015-2022 Redistributable (x64)** from
  <https://aka.ms/vs/17/release/vc_redist.x64.exe>, which installs the UCRT as
  part of it

Then run LapStudio again.

**Windows 8.0 specifically** cannot be fixed this way. KB2999226 requires 8.1,
and current Python does not support 8.0 either. That machine needs the free
update to 8.1 first.

Newer LapStudio releases may carry the runtime with them, in which case this
never comes up — the build reports "UCRT: bundled" when it does.

---

## "Windows protected your PC" or a virus warning

Windows Defender may report **Trojan:Win32/Wacatac.B!ml** (or Wacapew, or another
generic name) the first time you run it. This is a false positive.

The `!ml` suffix is the giveaway: it means a machine-learning model guessed,
rather than the file matching anything known. Checked against 70 engines on
VirusTotal, LapStudio is flagged by three — Microsoft's ML model and two vendors
with high false-positive rates — while 67, including Kaspersky, ESET,
Bitdefender, Sophos, Symantec and Trend Micro, report it clean. No engine names
an actual threat.

The cause is packaging, not content. LapStudio is a Python program bundled into
an executable with PyInstaller, and that bundling produces a loader which is
unsigned, brand new, and carries other programs inside it (ffmpeg and the AiM
reader). Defender's model treats that shape as suspicious on its own.

To run it: **More info**, then **Run anyway** on the SmartScreen dialog. If
Defender has already quarantined it, open Protection history and choose Allow.

Rather not take our word for it? Upload the exe to <https://www.virustotal.com/>.
A real threat is flagged by dozens of engines with a named family; a packaging
false positive looks like the pattern above.

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
