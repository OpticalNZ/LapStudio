"""
motec_check.py - prove a MoTeC .ld survives the whole pipeline.

The reader is only half the job. This drives the real app class headlessly, the
way ui_smoke.py does, loads a .ld through it, and renders every overlay from the
result. That catches the failures a reader test cannot see: a channel the app
maps by a name the reader does not produce, a lap list the lap tab rejects, a
track map that draws nothing because latitude arrived as a string.

Telemetry logs are not in this repository, so the test skips unless one is
present. Point it at any .ld:

    python tests/motec_check.py                     # finds a .ld in the project
    python tests/motec_check.py "path/to/log.ld"
"""
import glob
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))

import numpy as np                               # noqa: E402

_it = types.ModuleType("PIL.ImageTk")


class _PhotoImage:
    def __init__(self, img=None, **kw):
        self.size = getattr(img, "size", None)
        self.img = img


_it.PhotoImage = _PhotoImage
sys.modules["PIL.ImageTk"] = _it
import PIL                                       # noqa: E402
PIL.ImageTk = _it

FAILURES = []


def chk(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   {detail}"))
    if not ok:
        FAILURES.append(label)


def find_log(argv):
    if len(argv) > 1:
        return argv[1]
    found = sorted(glob.glob(os.path.join(ROOT, "*.ld")))
    return found[0] if found else None


def main():
    path = find_log(sys.argv)
    if not path or not os.path.isfile(path):
        print("no .ld file present - skipping (this is not a failure)")
        return 0
    print(f"log: {os.path.basename(path)}\n")

    import motec_reader as MR
    df, meta = MR.read_ld(path)

    print("--- reader ---")
    chk("has a time base", "ts" in df.columns and len(df) > 100)
    chk("time increases", bool((np.diff(df["ts"]) > 0).all()))
    chk("no NaN in any channel", not df.isna().any().any())
    for col, lo, hi in [("speed", 0, 500), ("rpm", 0, 20000), ("throttle", 0, 100),
                        ("brake", 0, 100), ("gear", 0, 12)]:
        v = df[col].to_numpy().astype(float)
        chk(f"{col} within {lo}-{hi}", v.min() >= lo and v.max() <= hi,
            f"{v.min():.2f}..{v.max():.2f}")
    chk("GPS present", meta["has_gps"])
    if meta["has_gps"]:
        # A session sits on one circuit; a degree of latitude is 111 km, so any
        # real track spans a small fraction of one.
        chk("GPS confined to one circuit",
            (df["lat"].max() - df["lat"].min()) < 0.05
            and (df["lon"].max() - df["lon"].min()) < 0.05,
            f"{df['lat'].max()-df['lat'].min():.4f} x {df['lon'].max()-df['lon'].min():.4f} deg")
    chk("distance never goes backwards", bool((np.diff(df["distance"]) >= -1e-9).all()))

    laps = meta.get("laps", [])
    chk("laps found", len(laps) > 0, str(len(laps)))
    chk("laps are ordered and non-overlapping",
        all(laps[i][1] <= laps[i + 1][0] + 1e-6 for i in range(len(laps) - 1)))
    timed = [t for (_s, _e, t) in laps if 5 < t < 3600]
    chk("at least one timed lap", bool(timed))

    # The .ldx is the logger's own report of the session. When it is there, the
    # lap time derived from the channels must agree with it.
    ldx = meta.get("ldx", {})
    if timed and ldx.get("Fastest Time"):
        want = ldx["Fastest Time"]
        mins, _, secs = want.partition(":")
        try:
            want_s = float(mins) * 60 + float(secs)
            chk(f"fastest lap matches the .ldx ({want})",
                abs(min(timed) - want_s) < 0.05,
                f"read {min(timed):.3f}s, sidecar {want_s:.3f}s")
        except ValueError:
            pass
    if ldx.get("Venue"):
        chk("venue matches the .ldx", meta["venue"] == ldx["Venue"])

    # --- the app itself -----------------------------------------------------
    print("\n--- app pipeline ---")
    import ecu_overlay_app as APP

    app = APP.App()
    type(app)._loading_start = lambda self, msg="": None
    type(app)._loading_stop = lambda self, final="": None
    type(app).after = lambda self, ms, fn=None, *a: (fn(*a) if callable(fn) else None) or "t"
    type(app).after_cancel = lambda self, t: None

    app._load_motec(path)
    chk("app loaded the file", getattr(app, "df", None) is not None
        and len(app.df) == len(df))
    chk("app took the lap list", len(getattr(app, "_aim_laps", [])) == len(laps))
    chk("working rows built", getattr(app, "_working_rows", None) is not None)
    chk("sample rate recorded", getattr(app, "_data_fps", 0) > 0, str(getattr(app, "_data_fps", 0)))

    rows = app._working_rows
    for want in ("speed", "rpm", "throttle", "brake", "gear", "lat", "lon"):
        chk(f"mapped channel {want}", want in rows.columns)

    # --- render every overlay for real --------------------------------------
    print("\n--- overlays render ---")
    for child, kw in app._nb._tabs:
        app._nb.select(child)
        app._on_tab_changed()
        label = kw["text"].strip()
        try:
            app._refresh_tab_preview()
        except Exception as e:                    # noqa: BLE001
            chk(f"{label} preview", False, f"{type(e).__name__}: {e}")
            continue
        kind = app._tab_keys.get(str(child))
        if kind not in app._pv_panes:
            chk(f"{label} preview", True)
            continue
        img = getattr(app._pv_panes[kind].image, "img", None)
        if img is None:
            chk(f"{label} preview", False, "nothing was drawn")
            continue
        arr = np.asarray(img.convert("RGB"))
        # A frame that is one flat colour means the renderer ran but the data
        # never reached it.
        chk(f"{label} preview has content", int(len(np.unique(arr.reshape(-1, 3), axis=0))) > 8,
            f"{len(np.unique(arr.reshape(-1,3), axis=0))} distinct colours")

    print()
    if FAILURES:
        print(f"FAILURES: {len(FAILURES)}")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("FAILURES: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
