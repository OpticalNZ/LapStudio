"""
ui_smoke.py — build the whole LapStudio UI headlessly and exercise it.

There is no tkinter on the build machine and no display, so `tests/stubs`
provides a minimal stand-in that records widget construction. That is enough to
catch the errors that matter in a 4,700-line UI file: missing attributes, wrong
parents, bad call signatures, edits landing in a dead duplicate method. Every
preview is then rendered for real through PIL, so the render path is genuinely
covered - only the final "hand the bitmap to Tk" step is stubbed.

Run from the project root:

    PYTHONPATH=tests/stubs python tests/ui_smoke.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stubs"))

import numpy as np
import pandas as pd

# ImageTk needs a real Tk display; stand it in so the display step completes.
_it = types.ModuleType("PIL.ImageTk")


class _PhotoImage:
    """Keeps the bitmap so tests can assert on what was actually displayed."""

    def __init__(self, img=None, **kw):
        self.size = getattr(img, "size", None)
        self.img = img


_it.PhotoImage = _PhotoImage
sys.modules["PIL.ImageTk"] = _it
import PIL                                   # noqa: E402
PIL.ImageTk = _it

import ecu_overlay_app as APP                # noqa: E402
import renderer_pil as _RSTYLES              # noqa: E402

FAILURES = []


def check(label, fn):
    try:
        fn()
    except Exception as e:                    # noqa: BLE001 - report, don't stop
        FAILURES.append(f"{label}: {type(e).__name__}: {e}")


def synthetic_log(secs=180.0, hz=50.0, lap_secs=45.0):
    """A log with a closed GPS circuit, so the track map has something to draw."""
    t = np.arange(0, secs, 1.0 / hz)
    th = 2 * np.pi * (t % lap_secs) / lap_secs
    return pd.DataFrame({
        "Time": t,
        "RPM": 4000 + 4500 * np.abs(np.sin(t / 4)),
        "Speed": 40 + 180 * np.abs(np.sin(t / 5)),
        "Gear": np.clip((t % 7).astype(int), 1, 6),
        "Throttle": 50 + 50 * np.sin(t / 3),
        "Brake": np.clip(-40 * np.sin(t / 3), 0, 100),
        "G_Lat": 1.4 * np.sin(t / 2),
        "G_Long": 1.0 * np.cos(t / 2),
        "GPS Latitude": -40.237 + 0.0022 * np.sin(th),
        "GPS Longitude": 175.556 + 0.003 * np.cos(th),
        "Oil Temp": np.full_like(t, 98.0),
        "EGT": np.full_like(t, 995.0),
    })


def build_app():
    app = APP.App()
    df = synthetic_log()
    app.df = df
    app.ts_col = "Time"
    app.ts_col_var.set("Time")
    app._build_col_mapping(list(df.columns))
    app.t_start_var.set("0")
    app.t_end_var.set("180")
    app._prepare_working_df({ch: v.get() for ch, v in app.col_vars.items()
                             if v.get() not in ("(not detected)", "(None)", "")})
    # Lap detection depends on thresholds that suit real data; pin laps so the
    # lap-data and track-map previews are exercised deterministically.
    app._compute_laps = lambda *a, **k: [(i * 45.0, (i + 1) * 45.0, 44.5 + 0.4 * i)
                                         for i in range(4)]
    return app


def run_timers_now(app):
    """Make after() fire immediately so debounced chains complete in-process.

    The loading spinner reschedules itself forever, so it is stubbed out first.
    """
    type(app)._loading_start = lambda self, msg="": None
    type(app)._loading_stop = lambda self, final="": None
    type(app).after = lambda self, ms, fn=None, *a: (fn(*a) if callable(fn) else None) or "t"
    type(app).after_cancel = lambda self, t: None


def displayed(app, kind):
    """The pixels currently shown in a preview pane, or None."""
    img = getattr(app._pv_panes[kind].image, "img", None)
    if img is None:
        return None
    return np.asarray(img.convert("RGB")).astype(int)


def pixels_changed(a, b):
    if a is None or b is None:
        return -1
    if a.shape != b.shape:
        return -2
    return int((np.abs(a - b).max(2) > 25).sum())


def select(app, kind):
    for child, _kw in app._nb._tabs:
        if app._tab_keys.get(str(child)) == kind:
            app._nb.select(child)
            app._on_tab_changed()
            return True
    return False


def main():
    app = build_app()

    # structure
    tabs = [kw["text"].strip() for _c, kw in app._nb._tabs]
    assert tabs == ["Data", "Laps", "Dash", "G-Plot", "Track Map", "Lap Data"], tabs
    assert set(app._pv_panes) == {"dash", "gplot", "trackmap", "lapdata"}
    assert len(app._tab_canvases) == len(tabs)
    for child, kw in app._nb._tabs:
        app._nb.select(child)
        assert app._active_canvas() is not None, f"no wheel target on {kw['text']}"

    # every tab previews
    for child, kw in app._nb._tabs:
        app._nb.select(child)
        app._on_tab_changed()
        check(f"tab {kw['text'].strip()}", app._refresh_tab_preview)

    # every overlay setting, on its own tab
    matrix = [
        ("gplot", (("gtrace_form", ["Target (Dash 10)", "Classic X-Y"]),
                   ("gtrace_trail", ["5", "40"]),
                   ("gtrace_colour", ["Speed", "Black & white"]),
                   ("gtrace_gmax", ["Auto", "2.5", "2", "1.5", "1"]),
                   ("gt_backdrop", ["Chroma magenta", "Chroma green", "Dark"]))),
        ("trackmap", (("tm_speed_colour", [True, False]),
                      ("tm_amount", ["All Laps", "One Lap", "Stage"]),
                      ("tm_stage_secs", ["20s", "60s", "150s"]),
                      ("tm_backdrop", ["Chroma magenta", "Chroma green", "Dark"]))),
        ("lapdata", (("ld_mode", ["Current lap", "All laps"]),
                     ("ld_style", ["Dash 1 · Cells, light", "Dash 2 · Cells, dark",
                                   "Dash 5 · Panels", "Dash 6 · Logger rows",
                                   "Dash 7 · HUD bar", "Dash 8b · Slanted panels",
                                   "Dash 10 · Plain text"]),
                     ("ld_backdrop", ["Chroma magenta", "Dark"]))),
        ("dash", (("style_var", list(_RSTYLES.DASH_NAMES)),
                  ("g_trail_secs", ["5", "40"]),
                  ("g_trail_speed_colour", [True, False]),
                  ("backdrop_var", ["Chroma magenta", "Chroma green", "Dark"]),
                  ("invert_glat", [True, False]),
                  ("invert_glong", [True, False]),
                  ("crop_to_content", [True, False]))),
    ]
    for kind, settings in matrix:
        if not select(app, kind):
            FAILURES.append(f"tab for {kind} not found")
            continue
        for var, values in settings:
            v = getattr(app, var, None)
            if v is None:
                FAILURES.append(f"{kind}: no variable {var}")
                continue
            for val in values:
                v.set(val)
                check(f"{kind} {var}={val}", app._refresh_tab_preview)

    # scrub the whole range on each overlay tab
    for kind in ("dash", "gplot", "trackmap", "lapdata"):
        select(app, kind)
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            app._pv_var.set(frac)
            check(f"{kind} scrub {frac}", app._on_scrub)
            check(f"{kind} scrub render {frac}", app._refresh_tab_preview)
            if app._pv_time_lbl.get() == "--:--":
                FAILURES.append(f"{kind}: scrub readout blank at {frac}")

    # graceful messages, not crashes, when data is missing
    _rows = app._working_rows
    app._working_rows = _rows.drop(columns=["lat", "lon"])
    select(app, "trackmap")
    check("track map without GPS", app._refresh_tab_preview)
    app._working_rows = _rows

    # the combined popup and the other whole-app paths
    check("popup preview (all overlays)", app._preview_frame)
    check("collect inputs", app._collect_inputs)
    check("update lap display", app._update_lap_display)
    check("lock ui", app._lock_ui)
    check("unlock ui", app._unlock_ui)

    # controls hide themselves for dashes that do not use them
    import renderer_pil as _R
    for style in ("Dash 1 (white gauge)", "Dash 3", "Dash 4", "Dash 5",
                  "Dash 6 (Logger)", "Dash 8", "Dash 10 (Arc)"):
        app.style_var.set(style)
        app._sync_dash_controls()
        P = _R.STYLES[style]
        want_trail = _R.style_has_trail(P)
        want_crop = _R.style_can_crop(P)
        if (app._trail_row_w._grid is not None) != want_trail:
            FAILURES.append(f"{style}: trail row visibility wrong")
        if (app._crop_chk._grid is not None) != want_crop:
            FAILURES.append(f"{style}: crop option visibility wrong")

    # theme switching must leave no trace of the other palette, and survive a
    # round trip (the remap keys on colour value, so a shared value would break it)
    _dark = {v.lower() for v in APP.THEMES["dark"].values()}
    _light = {v.lower() for v in APP.THEMES["light"].values()}

    def _tree_colours(w, acc=None):
        acc = set() if acc is None else acc
        for opt in ("bg", "fg", "activebackground", "highlightbackground",
                    "selectcolor", "troughcolor"):
            try:
                v = str(w.cget(opt))
            except Exception:
                continue
            if v:
                acc.add(v.lower())
        for c in w.winfo_children():
            _tree_colours(c, acc)
        return acc

    for want in ("light", "dark", "light", "dark"):
        app._apply_theme(want, save=False)
        if APP.THEME_NAME != want:
            FAILURES.append(f"theme did not switch to {want}")
        seen = _tree_colours(app)
        stale = sorted(seen & (_dark if want == "light" else _light)
                       - (_light if want == "light" else _dark))
        if stale:
            FAILURES.append(f"after switching to {want}, colours from the other "
                            f"theme remain: {stale}")
    if app._theme_btn.kw.get("text", "").find("Light") < 0:
        FAILURES.append("theme button label does not offer the other theme")

    # the Max range setting must reach BOTH G-plot forms: the X-Y form ignored it
    app3 = build_app()
    run_timers_now(app3)
    select(app3, "gplot")
    app3._pv_var.set(0.4)
    app3._on_scrub()
    for form in ("Target", "X-Y Graph"):
        app3.gtrace_form.set(form)
        app3.gtrace_gmax.set("2.5")
        app3._refresh_tab_preview()
        before = displayed(app3, "gplot")
        app3.gtrace_gmax.set("1")
        if pixels_changed(before, displayed(app3, "gplot")) < 200:
            FAILURES.append(f"G-plot Max range does not change the {form} preview")
        # the X-Y panel is opaque, so its backdrop choice must be hidden
        want_bd = form.startswith("Target")
        if (app3._gp_bd_cb._grid is not None) != want_bd:
            FAILURES.append(f"G-plot backdrop visibility wrong for {form}")

    # the lap table is a single column with every field aligned
    _lts = [580.5, 97.3, 70.084, 140.4, 71.2, 156.8, 70.5, 1103.2]
    _st = 0.0
    _laps = []
    for _lt in _lts:
        _laps.append((_st, _st + _lt, _lt))
        _st += _lt + 1.2
    _tbl = APP.App._format_lap_table(_laps, min(_lts), "Lap Beacons", box_w=96)
    if "|" in _tbl:
        FAILURES.append("lap table is still multi-column")
    import re as _re
    _rows_ = [l for l in _tbl.splitlines() if _re.match(r"^\s+\d+\s+\d+:", l)]
    _marks = {tuple(i for i, c in enumerate(l) if c in ".:") for l in _rows_}
    if len(_marks) != 1:
        FAILURES.append(f"lap table digits not aligned: {sorted(_marks)}")

    # no preview may come back larger than its pane, or the Tk label clips it
    for kind in ("dash", "gplot", "trackmap", "lapdata"):
        select(app, kind)
        app._refresh_tab_preview()
        box = app._preview_box()
        shown = displayed(app, kind)
        if shown is not None:
            ih, iw = shown.shape[0], shown.shape[1]
            if iw > box[0] or ih > box[1]:
                FAILURES.append(f"{kind} preview is {iw}x{ih}, larger than its "
                                f"{box[0]}x{box[1]} pane - it will be cropped")

    # the track map window must fill a sensible share of the frame, and the car
    # must stay dead centre: sizing the zoom for the worst-case window left a
    # typical one at about a quarter size
    import trackmap_render as _TM
    _rows = app._working_rows
    for seg, want in ((None, 60.0), (12.0, 45.0), (60.0, 45.0)):
        fills, dots = [], set()
        for tn in (30.0, 60.0, 95.0, 130.0):
            im = _TM.render_preview_frame(_rows, tn, resolution=(400, 400),
                                          laps=app._compute_laps(), t_start=0.0,
                                          t_end=180.0, segment_secs=seg,
                                          speed_colour=False)
            a = np.asarray(im.convert("RGB"))
            m = ~((a[..., 0] == 255) & (a[..., 1] == 0) & (a[..., 2] == 255))
            ys, xs = np.nonzero(m)
            if len(xs):
                fills.append(100.0 * max((xs.max() - xs.min()) / im.width,
                                         (ys.max() - ys.min()) / im.height))
            red = (a[..., 0] > 190) & (a[..., 1] < 70) & (a[..., 2] < 70)
            ry, rx = np.nonzero(red)
            if len(rx) and seg:
                dots.add((round(rx.mean()), round(ry.mean())))
        med = float(np.median(fills)) if fills else 0.0
        if med < want:
            FAILURES.append(f"track map (window={seg}) only fills {med:.0f}% "
                            f"of the frame, wanted >= {want:.0f}%")
        if seg and len(dots) > 1:
            FAILURES.append(f"track map (window={seg}) car dot moved: {dots}")

    # the output video is named after the loaded log, but a typed name survives
    for log, want in ((r"C:\logs\Session_One.csv", "Session_One.mp4"),
                      ("/home/m/Taupo R1.vbo", "Taupo R1.mp4"),
                      (r"C:/mixed\slashes/log.2026.05.01.drk", "log.2026.05.01.mp4")):
        app.output_name.set("")
        app._set_output_name_from_log(log)
        if app.output_name.get() != want:
            FAILURES.append(f"output name for {log!r}: got "
                            f"{app.output_name.get()!r}, wanted {want!r}")
    app.output_name.set("typed_by_hand.mp4")
    app._set_output_name_from_log(r"C:\logs\another_session.csv")
    if app.output_name.get() != "typed_by_hand.mp4":
        FAILURES.append("a hand-typed output name was overwritten by a load")

    # no dropdown may offer the same value twice
    def _dupe_values(w, acc=None):
        acc = [] if acc is None else acc
        vals = w.kw.get("values") if hasattr(w, "kw") else None
        if vals:
            v = list(vals)
            dupes = sorted({x for x in v if v.count(x) > 1})
            if dupes:
                acc.append((w.kw.get("textvariable"), dupes))
        for c in w.winfo_children():
            _dupe_values(c, acc)
        return acc

    for _var, _dupes in _dupe_values(app):
        FAILURES.append(f"a dropdown lists {_dupes} more than once")

    # every offered dash name must resolve, be ordered numerically, and the old
    # names must still work as aliases
    _names = list(_RSTYLES.DASH_NAMES)
    for _n in _names:
        if _n not in _RSTYLES.STYLES:
            FAILURES.append(f"dash name {_n!r} does not resolve to a style")
    def _num(n):
        _t = n.split("\u00b7")[0].strip()
        if not _t.lower().startswith("dash"):
            return (99, 0)
        _d = _t.split()[1]
        return (int("".join(c for c in _d if c.isdigit())), 1 if _d[-1].isalpha() else 0)
    if [_num(n) for n in _names] != sorted(_num(n) for n in _names):
        FAILURES.append(f"dash names are not in numeric order: {_names}")
    for _legacy in ("Dash 1 (white gauge)", "Dash 6b (Wide Logger)", "Dash 8",
                    "Dash 8b (bars inside)", "Vertical Text", "Horizontal Text"):
        if _legacy not in _RSTYLES.STYLES:
            FAILURES.append(f"legacy name {_legacy!r} no longer resolves")
    # each lap-data label must map to a distinct renderer key
    _keys = []
    for _lbl in ("Dash 1 · Cells, light", "Dash 2 · Cells, dark", "Dash 5 · Panels",
                 "Dash 6 · Logger rows", "Dash 7 · HUD bar",
                 "Dash 8b · Slanted panels", "Dash 10 · Plain text"):
        app.ld_style.set(_lbl)
        _keys.append(app._ld_style_key())
    if len(set(_keys)) != len(_keys):
        FAILURES.append(f"lap-data labels collide: {list(zip(_keys, _keys))}")

    # auto redline: the 99.5th percentile to the nearest 100, NOT the peak, so a
    # single spike cannot push the bands above everything the driver used
    import renderer_pil as _R4
    _v = np.concatenate([np.random.uniform(2000, 10700, 20000), [18000.0]])
    _rl = _R4.auto_redline(_v)
    if _rl is None or not (10500 <= _rl <= 10800):
        FAILURES.append(f"auto_redline gave {_rl}, expected about 10700 "
                        f"(and to ignore the 18000 spike)")
    # bands must be reached by real driving, not only at the very peak
    _above = float((_v >= _R4.RPM_BAND_YELLOW * _rl).mean())
    if _above < 0.01:
        FAILURES.append(f"yellow band only reached {100*_above:.3f}% of the time")

    # Dash 8 readouts must centre on their own digits, not on the widest value
    app.style_var.set("Dash 8")
    _centres = []
    for _rpm in (99.0, 999.0, 9999.0):
        _saved = app._working_rows["rpm"].copy()
        app._working_rows["rpm"] = _rpm
        _im = app._render_dash_pil(60.0, app._compute_laps(), max_w=1920)
        app._working_rows["rpm"] = _saved
        _a = np.asarray(_im.convert("RGB")).astype(int)
        _sc = _im.width / 1920.0
        _y0, _y1 = int(330 * _sc), int(420 * _sc)
        _x0, _x1 = int(250 * _sc), int(760 * _sc)
        _band = _a[_y0:_y1, _x0:_x1]
        _m = (_band[..., 0] > 200) & (_band[..., 1] > 200) & (_band[..., 2] > 200)
        _ys, _xs = np.nonzero(_m)
        if len(_xs):
            _centres.append((_x0 + (_xs.min() + _xs.max()) / 2) / _sc)
    if len(_centres) == 3 and (max(_centres) - min(_centres)) > 8:
        FAILURES.append(f"Dash 8 rpm readout is not centred on its own digits: "
                        f"centres {[round(c, 1) for c in _centres]}")

    # the rpm stage colour must step green -> yellow -> orange -> red
    import renderer_pil as _R3
    # bands are proportional to the redline now, not fixed rpm below the scale
    _stages = [_R3.rpm_stage_colour(r, 12000, redline=10700)
               for r in (5000, 10000, 10300, 10600)]
    if len(set(_stages)) != 4:
        FAILURES.append(f"rpm stage colours do not step distinctly: {_stages}")
    # and with no redline supplied they fall back to the gauge scale
    if _R3.rpm_stage_colour(8900, 9000) == _R3.rpm_stage_colour(3000, 9000):
        FAILURES.append("rpm stage colour ignores the gauge scale fallback")
    # ...and Dash 8's ring must actually follow it
    app.style_var.set("Dash 8")
    _a1 = np.asarray(app._render_dash_pil(60.0, app._compute_laps(), max_w=760).convert("RGB"))
    _saved = app._working_rows["rpm"].copy()
    app._working_rows["rpm"] = 8900.0
    _a2 = np.asarray(app._render_dash_pil(60.0, app._compute_laps(), max_w=760).convert("RGB"))
    app._working_rows["rpm"] = _saved
    if _a1.shape == _a2.shape and int((np.abs(_a1 - _a2).max(2) > 25).sum()) < 500:
        FAILURES.append("Dash 8 looks the same at low rpm and at the redline")

    # dashes flagged opaque must leave no chroma at all in the frame
    import renderer_pil as _R2
    for style in ("Dash 3", "Dash 6b (Wide Logger)"):
        app.style_var.set(style)
        app.backdrop_var.set("Chroma magenta")
        img = app._render_dash_pil(60.0, app._compute_laps(), max_w=960)
        a = np.asarray(img.convert("RGB"))
        chroma = int(((a[..., 0] == 255) & (a[..., 1] == 0) & (a[..., 2] == 255)).sum())
        if chroma:
            FAILURES.append(f"{style} is flagged opaque but still has {chroma} "
                            f"chroma pixels")
        if not _R2.style_is_opaque(_R2.STYLES[style]):
            FAILURES.append(f"{style} should be flagged opaque")
    # ...and a dash that is NOT flagged opaque should still have its chroma
    app.style_var.set("Dash 1 (white gauge)")
    a = np.asarray(app._render_dash_pil(60.0, app._compute_laps(), max_w=960).convert("RGB"))
    if not ((a[..., 0] == 255) & (a[..., 1] == 0) & (a[..., 2] == 255)).any():
        FAILURES.append("Dash 1 lost its chroma backdrop")

    # one scrubber under each preview, all sharing one value
    if len(app._pv_scales) != 4:
        FAILURES.append(f"expected 4 scrubbers, found {len(app._pv_scales)}")
    app._pv_var.set(0.42)
    if abs(app._pv_var.get() - 0.42) > 1e-9:
        FAILURES.append("shared scrub value did not take")

    # ── regressions: settings must reach the preview, and the preview must not
    #    resize itself on every refresh ────────────────────────────────────────
    app2 = build_app()
    run_timers_now(app2)

    select(app2, "gplot")
    app2._pv_var.set(0.4)
    app2._on_scrub()
    before = displayed(app2, "gplot")
    app2.invert_glat.set(True)
    if pixels_changed(before, displayed(app2, "gplot")) < 200:
        FAILURES.append("inverting g_lat did not change the G-plot preview")
    app2.invert_glat.set(False)

    select(app2, "dash")
    app2.style_var.set("Dash 5")
    app2._refresh_tab_preview()
    before = displayed(app2, "dash")
    app2.col_vars["speed"].set("RPM")          # deliberate remap
    app2._schedule_rebuild(0)
    if pixels_changed(before, displayed(app2, "dash")) < 200:
        FAILURES.append("changing a channel source did not update the dash preview")
    app2.col_vars["speed"].set("Speed")
    app2._schedule_rebuild(0)

    sizes = set()
    for frac in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        app2._pv_var.set(frac)
        app2._on_scrub()
        d = displayed(app2, "dash")
        if d is not None:
            sizes.add(d.shape)
    if len(sizes) > 1:
        FAILURES.append(f"preview changed size while scrubbing: {sizes}")

    select(app2, "trackmap")
    app2._refresh_tab_preview()
    sizes = set()
    for tick in (True, False, True):
        app2.gen_trackmap.set(tick)
        app2._refresh_tab_preview()
        d = displayed(app2, "trackmap")
        if d is not None:
            sizes.add(d.shape)
    if len(sizes) > 1:
        FAILURES.append(f"preview changed size when toggling generate: {sizes}")

    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
