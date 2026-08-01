"""
lapdata_render.py — standalone lap-data overlay generator for LapStudio.

Renders a SEPARATE video of the lap information so it can be positioned
anywhere over the race footage, independently of the main dashboard.

Two content modes:
  "current" — the current lap number and its running / last lap time
  "list"    — a vertical list of every lap in the session, current lap highlighted

Graphical styles are lifted from the existing dashes so the widget matches
whichever dash you are using:
  "panels"      — Dash 5: rounded accent-outlined boxes (cyan LAP / gold LAP TIME)
  "hud"         — Dash 7 / 9: dim label above a bright value on a dark bar
  "logger"      — Dash 6: table rows, label left / value right, thin separators
  "plain"       — Dash 10: outlined white text on the bare backdrop (no panel)
  "cells_dark"  — Dash 2: stacked cells, thin accent rule, dim label over amber value
  "cells_light" — Dash 1: the same cells on the light panel with navy / dark amber
  "para"        — Dash 8b: slanted parallelogram panels, small cyan label above a
                  big stretched white value

Lap times are formatted M:SS.ss (minutes : seconds . hundredths).
"""

import os
import subprocess
import numpy as np
from textgrid import _gtext, _gwidth, _vmask, _vw

# ── Fonts (same families the dashes use) ─────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))


def _font_path(*names):
    for n in names:
        p = os.path.join(_HERE, n)
        if os.path.exists(p):
            return p
    return None


_BSB = _font_path("BigShoulders-Bold.ttf")
_POP = _font_path("Poppins-Bold.ttf")


def _f(size, cond=False):
    """Bold display font (BigShoulders) / condensed label font (Poppins)."""
    from PIL import ImageFont
    path = _BSB if not cond else (_POP or _BSB)
    try:
        return ImageFont.truetype(path, max(6, int(size))) if path else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


# ── Style palettes ───────────────────────────────────────────────────────────
CYAN = (80, 200, 255)
GOLD = (255, 180, 40)
WHITE = (245, 245, 248)
BLACK = (0, 0, 0)
DIM = (150, 165, 200)
PANEL = (12, 12, 16)
ROW_ALT = (22, 22, 28)

STYLES = ("panels", "hud", "logger", "plain", "cells_dark", "cells_light",
          "para")
MODES = ("current", "list")

# Dash 1 / Dash 2 gauge-dash palettes (taken from renderer_pil STYLES)
CELLS = {
    "cells_dark":  dict(panel=(10, 12, 20),    value=(220, 220, 240),
                        accent=(255, 165, 0),  grey=(110, 120, 150)),
    "cells_light": dict(panel=(235, 240, 250), value=(25, 60, 140),
                        accent=(200, 120, 0),  grey=(90, 115, 165)),
}


def fmt_laptime(t):
    """Seconds -> M:SS.ss"""
    if t is None or not np.isfinite(t):
        return "--:--.--"
    m = int(t) // 60
    s = t % 60
    return f"{m}:{s:05.2f}"


# Digits are laid out on a fixed pitch (see textgrid.py) so lap numbers and lap
# times can't shuffle sideways as they change.
def _tc(d, xy, text, fnt, col, slots=None, anchor="mm"):
    _gtext(d, xy, text, fnt, col, anchor=anchor, vref=_vmask(text), slots=slots)


def _tco(d, xy, text, fnt, col, ow=2, outline=BLACK, slots=None, anchor="mm"):
    _gtext(d, xy, text, fnt, col, anchor=anchor, outline=outline, ow=int(ow),
           vref=_vmask(text), slots=slots)


def _tw(d, text, fnt):
    """Width of the widest same-length rendering, so column layout is stable."""
    return _vw(text, fnt)


# ── Dash 8b parallelogram panels ─────────────────────────────────────────────
PARA_FILL = (18, 40, 78)        # deep blue, as on Dash 8b
PARA_EDGE = (70, 130, 210)
PARA_FILL_HI = (30, 66, 122)    # current lap
PARA_EDGE_HI = (120, 190, 255)


def _para_panel(d, img, x0, y0, pw, ph, slope, label, value, s,
                fill=PARA_FILL, edge=PARA_EDGE, lbl_col=CYAN, val_col=WHITE):
    """One slanted panel: small label near the top, big value below it.

    Uses Dash 8b's own stretched-text renderer when an image is available, so the
    widget matches the dash exactly; falls back to unstretched text otherwise.
    """
    poly = [(x0 + slope, y0), (x0 + slope + pw, y0), (x0 + pw, y0 + ph), (x0, y0 + ph)]
    d.polygon(poly, fill=fill)
    d.polygon(poly, outline=edge, width=max(2, int(2.5 * s)))
    tx = x0 + slope // 2 + int(pw * 0.10)
    lbl_fs = max(7, int(ph * 0.26))
    val_fs = max(10, int(ph * 0.52))
    _wt = None
    if img is not None:
        try:
            from dash8_render import wide_text as _wt
        except Exception:
            _wt = None
    if _wt is not None:
        _wt(img, (tx, y0 + int(ph * 0.26)), label, lbl_fs, lbl_col,
            ow=2, stretch=1.5, anchor="lm")
        _wt(img, (tx, y0 + int(ph * 0.66)), value, val_fs, val_col,
            ow=max(2, int(3 * s)), stretch=1.4, anchor="lm")
    else:
        _tc(d, (tx, y0 + int(ph * 0.26)), label, _f(lbl_fs), lbl_col, anchor="lm")
        _tco(d, (tx, y0 + int(ph * 0.66)), value, _f(val_fs), val_col,
             ow=max(2, int(3 * s)), anchor="lm")


def current_lap_info(laps, t_now):
    """Return (lap_number, time_to_show). While in a lap the running elapsed time
    is shown; for a completed lap its final time is shown."""
    if not laps:
        return 0, None
    for i, (st, et, lt) in enumerate(laps, 1):
        if st <= t_now <= et:
            return i, (t_now - st)
    if t_now < laps[0][0]:
        return 1, None
    return len(laps), laps[-1][2]


# ── Style renderers: current-lap mode ────────────────────────────────────────
def _draw_current(d, w, h, lap_no, lap_t, style, s, img=None):
    lap_txt = str(lap_no) if lap_no else "-"
    t_txt = fmt_laptime(lap_t)
    # Reserved field widths: the readouts hold their position when the lap
    # rolls past 9 or the time past a minute.
    _LAP_SLOTS = "8" * max(2, len(lap_txt))
    _T_SLOTS = "8" * (len(t_txt) - 6) + "8:88.88" if len(t_txt) > 7 else "8:88.88"

    if style == "panels":
        pad = int(min(w, h) * 0.05)
        gap = int(h * 0.04)
        bh = (h - 2 * pad - gap) // 2
        r = max(6, int(min(w, h) * 0.06))
        # LAP box (cyan) then LAP TIME box (gold) — Dash 5 treatment
        d.rounded_rectangle([pad, pad, w - pad, pad + bh], radius=r,
                            fill=PANEL, outline=CYAN, width=max(2, int(3 * s)))
        _tc(d, (w // 2, pad + int(bh * 0.22)), "LAP", _f(int(bh * 0.26), True), CYAN)
        _tco(d, (w // 2, pad + int(bh * 0.66)), lap_txt, _f(int(bh * 0.56)), WHITE, max(2, int(3 * s)), slots=_LAP_SLOTS)
        y0 = pad + bh + gap
        d.rounded_rectangle([pad, y0, w - pad, h - pad], radius=r,
                            fill=PANEL, outline=GOLD, width=max(2, int(3 * s)))
        bh2 = (h - pad) - y0
        _tc(d, (w // 2, y0 + int(bh2 * 0.22)), "LAP TIME", _f(int(bh2 * 0.24), True), GOLD)
        _tco(d, (w // 2, y0 + int(bh2 * 0.66)), t_txt, _f(int(bh2 * 0.42)), WHITE, max(2, int(3 * s)), slots=_T_SLOTS)

    elif style == "hud":
        # Dash 7/9: dark bar, dim label above bright value, two columns
        d.rounded_rectangle([0, int(h * 0.10), w, int(h * 0.90)],
                            radius=max(6, int(h * 0.10)), fill=PANEL)
        for cx, lbl, val, vf, sl in ((int(w * 0.28), "LAP", lap_txt, 0.34, _LAP_SLOTS),
                                     (int(w * 0.70), "LAP TIME", t_txt, 0.30, _T_SLOTS)):
            _tc(d, (cx, int(h * 0.34)), lbl, _f(int(h * 0.13), True), DIM)
            _tco(d, (cx, int(h * 0.64)), val, _f(int(h * vf)), WHITE, max(1, int(2 * s)), slots=sl)

    elif style == "logger":
        # Dash 6: table rows, label left / value right, thin separators
        pad = int(min(w, h) * 0.04)
        rh = (h - 2 * pad) // 2
        d.rectangle([0, 0, w, h], fill=PANEL)
        for i, (lbl, val, _sl) in enumerate((("LAP", lap_txt, _LAP_SLOTS),
                                            ("LAP TIME", t_txt, _T_SLOTS))):
            y0 = pad + i * rh
            if i:
                d.line([(pad, y0), (w - pad, y0)], fill=(70, 70, 85), width=max(1, int(2 * s)))
            fl = _f(int(rh * 0.30), True); fv = _f(int(rh * 0.52))
            bb = d.textbbox((0, 0), _vmask(lbl), font=fl)
            d.text((pad + int(w * 0.03), y0 + rh / 2 - (bb[3] - bb[1]) / 2 - bb[1]),
                   lbl, font=fl, fill=DIM)
            vw = _tw(d, _sl, fv)
            _tco(d, (w - pad - int(w * 0.03) - vw // 2, y0 + rh // 2), val, fv,
                 WHITE, max(1, int(2 * s)), slots=_sl)

    elif style in CELLS:
        # Dash 1 / Dash 2: stacked cells, thin accent rule at the top of each,
        # small dim label centred above the value.
        C = CELLS[style]
        d.rectangle([0, 0, w, h], fill=C["panel"])
        ch = h // 2
        for i, (lbl, val, col, _sl) in enumerate((("LAP", lap_txt, C["accent"], _LAP_SLOTS),
                                                  ("LAP TIME", t_txt, C["value"], _T_SLOTS))):
            y0 = i * ch
            d.line([(int(w * 0.06), y0), (int(w * 0.94), y0)],
                   fill=tuple(int(c * 0.35) for c in col), width=max(1, int(2 * s)))
            _tc(d, (w // 2, y0 + int(ch * 0.26)), lbl, _f(int(ch * 0.20), True),
                tuple(int(c * 0.55) for c in col))
            _tc(d, (w // 2, y0 + int(ch * 0.66)), val, _f(int(ch * 0.52)), col, slots=_sl)

    elif style == "para":
        # Dash 8b: two slanted panels, the lower stepped left so their leading
        # edges form one continuous diagonal, as on the dash.
        pad = int(min(w, h) * 0.05)
        slope = int(h * 0.10)
        gap = int(h * 0.05)
        ph = (h - 2 * pad - gap) // 2
        step = slope + int(slope * (gap / max(1, ph)))
        pw = w - 2 * pad - slope - step
        for i, (lbl, val) in enumerate((("LAP", lap_txt), ("LAP TIME", t_txt))):
            x0 = pad + step - i * step
            y0 = pad + i * (ph + gap)
            _para_panel(d, img, x0, y0, pw, ph, slope, lbl, val, s)

    else:  # "plain" — Dash 10: outlined text, no panel
        _tco(d, (w // 2, int(h * 0.32)), f"LAP {lap_txt}", _f(int(h * 0.30)),
             WHITE, max(2, int(3 * s)), slots=f"LAP {_LAP_SLOTS}")
        _tco(d, (w // 2, int(h * 0.72)), t_txt, _f(int(h * 0.34)),
             WHITE, max(2, int(3 * s)), slots=_T_SLOTS)


# ── Style renderers: all-laps list mode ──────────────────────────────────────
def _draw_list(d, w, h, laps, cur_no, style, s, best_no=None, img=None):
    n = len(laps)
    # Reserve both columns at the widest row so every row's digits line up and
    # nothing shifts as the list scrolls.
    _LSL = "8" * max(2, len(str(max((l[0] for l in laps), default=1)))) if laps else "88"
    _TSL = "8" * max(0, max((len(fmt_laptime(l[1])) for l in laps), default=7) - 7) + "8:88.88"
    # Right edges of the two columns: values are pinned to these, so a 1-digit
    # lap lines up under the tens digit of a 2-digit one.
    _LAP_R = int(w * 0.34)
    _TIME_R = int(w * 0.90)
    pad = int(min(w, h) * 0.035)
    head_h = int(h * 0.10)
    C = CELLS.get(style)

    if style == "para":
        # Dash 8b look, but laid out as a real list: a slanted panel per lap with
        # the lap number and the time each right-aligned in its own column, so the
        # digits line up down the page instead of running on as printed text.
        if n <= 0:
            return
        _wt = None
        if img is not None:
            try:
                from dash8_render import wide_text as _wt
            except Exception:
                _wt = None
        slope = max(4, int(h * 0.016))
        gap = max(2, int(h * 0.007))
        head_h = int(h * 0.075)
        top = pad + head_h + gap * 2
        avail = (h - pad) - top
        min_rh = max(14, int(h * 0.05))
        rows_fit = max(1, int((avail + gap) // (min_rh + gap)))
        if n <= rows_fit:
            first, shown = 0, n
        else:
            shown = rows_fit
            first = max(0, min(n - shown, (cur_no or 1) - 1 - shown // 2))
        ph = int((avail - (shown - 1) * gap) / shown)
        pw = w - 2 * pad - slope
        lap_r = pad + slope + int(pw * 0.34)     # right edge of the lap column
        time_r = pad + slope + int(pw * 0.96)    # right edge of the time column
        val_fs = max(10, int(ph * 0.62))
        hdr_fs = max(8, int(head_h * 0.72))

        def _txt(x, y, s_, fs, col, slots=None):
            if _wt is not None:
                _wt(img, (x, y), s_, fs, col, ow=max(2, int(2.5 * s)),
                    stretch=1.4, anchor="rm", slots=slots)
            else:
                _tco(d, (x, y), s_, _f(fs), col, ow=max(2, int(2 * s)),
                     anchor="rm", slots=slots)

        _txt(lap_r, pad + head_h // 2, "LAP", hdr_fs, CYAN)
        _txt(time_r, pad + head_h // 2, "TIME", hdr_fs, CYAN)
        for k in range(shown):
            i2 = first + k
            lap_no = i2 + 1
            y0 = top + k * (ph + gap)
            is_cur = (lap_no == cur_no)
            is_best = (best_no is not None and lap_no == best_no)
            poly = [(pad + slope, y0), (pad + slope + pw, y0),
                    (pad + pw, y0 + ph), (pad, y0 + ph)]
            d.polygon(poly, fill=PARA_FILL_HI if is_cur else PARA_FILL)
            d.polygon(poly, outline=PARA_EDGE_HI if is_cur else PARA_EDGE,
                      width=max(2, int(2 * s)))
            _col = GOLD if is_best else WHITE
            _yc = y0 + ph // 2
            _txt(lap_r, _yc, str(lap_no), val_fs, _col, slots=_LSL)
            _txt(time_r, _yc, fmt_laptime(laps[i2][2]), val_fs, _col, slots=_TSL)
        return

    if style == "panels":
        d.rounded_rectangle([0, 0, w, h], radius=max(6, int(min(w, h) * 0.05)),
                            fill=PANEL, outline=CYAN, width=max(2, int(3 * s)))
    elif style in ("hud", "logger"):
        d.rectangle([0, 0, w, h], fill=PANEL)
    elif C:
        d.rectangle([0, 0, w, h], fill=C["panel"])

    list_top = pad + head_h + int(h * 0.015)
    avail = (h - pad) - list_top
    if n <= 0:
        return
    # Row height: fit all laps; if too many, window around the current lap.
    min_rh = max(12, int(h * 0.055))
    max_rows = max(1, int(avail // min_rh))
    if n <= max_rows:
        first = 0; shown = n
    else:
        shown = max_rows
        first = max(0, min(n - shown, (cur_no or 1) - 1 - shown // 2))
    rh = avail / float(shown)
    fv = _f(int(rh * 0.62))
    fl = _f(int(rh * 0.62))

    # Header, centred over each column's reserved field so the titles sit
    # squarely above the digits below them.
    if style != "plain":
        _hcol = C["grey"] if C else DIM
        _rule = tuple(int(c * 0.6) for c in C["accent"]) if C else (70, 70, 85)
        _hf = _f(int(head_h * 0.62), True)
        _hy = pad + head_h // 2
        _tc(d, (_LAP_R - _gwidth(_LSL, fl) / 2, _hy), "LAP", _hf, _hcol)
        _tc(d, (_TIME_R - _gwidth(_TSL, fv) / 2, _hy), "TIME", _hf, _hcol)
        d.line([(pad, pad + head_h), (w - pad, pad + head_h)],
               fill=_rule, width=max(1, int(2 * s)))
    for k in range(shown):
        i = first + k
        lap_no = i + 1
        lt = laps[i][2]
        y0 = list_top + k * rh
        yc = y0 + rh / 2
        is_cur = (lap_no == cur_no)
        is_best = (best_no is not None and lap_no == best_no)
        if style == "logger" and (k % 2) and not is_cur:
            d.rectangle([pad, y0, w - pad, y0 + rh], fill=ROW_ALT)
        if is_cur:
            # Highlight the current lap
            if style == "plain":
                pass
            elif style == "panels":
                d.rounded_rectangle([pad, y0 + 1, w - pad, y0 + rh - 1],
                                    radius=max(3, int(rh * 0.22)), fill=(30, 44, 60),
                                    outline=CYAN, width=max(1, int(2 * s)))
            elif C:
                # Dash 1/2: tinted row with an accent bar down the left edge
                _tint = tuple(int(p * 0.72 + a * 0.28)
                              for p, a in zip(C["panel"], C["accent"]))
                d.rectangle([pad, y0 + 1, w - pad, y0 + rh - 1], fill=_tint)
                d.rectangle([pad, y0 + 1, pad + max(3, int(w * 0.014)), y0 + rh - 1],
                            fill=C["accent"])
            else:
                d.rectangle([pad, y0 + 1, w - pad, y0 + rh - 1], fill=(30, 44, 60))
                d.rectangle([pad, y0 + 1, pad + max(3, int(w * 0.012)), y0 + rh - 1], fill=CYAN)
        if C:
            # Cell styles are flat (no outline) like the gauge dashes
            col_lap = C["accent"] if (is_cur or is_best) else C["value"]
            col_val = C["accent"] if is_best else C["value"]
            _tc(d, (_LAP_R, yc), str(lap_no), fl, col_lap, slots=_LSL, anchor="rm")
            _tc(d, (_TIME_R, yc), fmt_laptime(lt), fv, col_val, slots=_TSL, anchor="rm")
        else:
            col_lap = CYAN if is_cur else (GOLD if is_best else WHITE)
            col_val = WHITE if is_cur else (GOLD if is_best else (215, 218, 228))
            ow = max(1, int(2 * s))
            _tco(d, (_LAP_R, yc), str(lap_no), fl, col_lap, ow, slots=_LSL, anchor="rm")
            _tco(d, (_TIME_R, yc), fmt_laptime(lt), fv, col_val, ow, slots=_TSL, anchor="rm")


def _frame(w, h, chroma, transparent):
    from PIL import Image
    return Image.new("RGBA", (w, h), (0, 0, 0, 0) if transparent else tuple(chroma) + (255,))


def render_preview_frame(rows, t_now, laps, resolution=(480, 260),
                         chroma=(255, 0, 255), transparent=False,
                         mode="current", style="panels"):
    """Single still frame of the lap-data widget, for the in-app preview."""
    from PIL import ImageDraw
    w, h = resolution
    img = _frame(w, h, chroma, transparent)
    d = ImageDraw.Draw(img)
    s = min(w, h) / 300.0
    cur_no, cur_t = current_lap_info(laps or [], t_now)
    if mode == "list" and laps:
        best_no = None
        try:
            best_no = 1 + min(range(len(laps)), key=lambda i: laps[i][2])
        except Exception:
            best_no = None
        _draw_list(d, w, h, laps, cur_no, style, s, best_no=best_no, img=img)
    else:
        _draw_current(d, w, h, cur_no, cur_t, style, s, img=img)
    return img


def render_video(rows, t_start, t_end, output_path, fps,
                 progress_cb=None, done_cb=None, error_cb=None, cancel_check=None,
                 resolution=None, chroma=(255, 0, 255), transparent=False,
                 laps=None, mode="current", style="panels", map_fps=10.0):
    """Render the standalone lap-data overlay video."""
    try:
        from PIL import ImageDraw
        laps = laps or []
        if resolution is None:
            resolution = (520, 700) if mode == "list" else (520, 300)
        w, h = resolution
        s = min(w, h) / 300.0
        best_no = None
        if laps:
            try:
                best_no = 1 + min(range(len(laps)), key=lambda i: laps[i][2])
            except Exception:
                best_no = None

        _fps = map_fps if map_fps and map_fps > 0 else fps
        n_frames = max(1, int(round((t_end - t_start) * _fps)))
        frame_ts = t_start + np.arange(n_frames) / _fps

        ffmpeg = _get_ffmpeg()
        pix_fmt = "rgba" if transparent else "rgb24"
        vcodec = (["-c:v", "qtrle"] if transparent
                  else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"])
        cmd = [ffmpeg, "-y", "-f", "rawvideo", "-pixel_format", pix_fmt,
               "-video_size", f"{w}x{h}", "-framerate", str(_fps),
               "-i", "-"] + vcodec + [output_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for fi in range(n_frames):
            if cancel_check and cancel_check():
                proc.stdin.close(); proc.wait(); return
            tnow = float(frame_ts[fi])
            img = _frame(w, h, chroma, transparent)
            d = ImageDraw.Draw(img)
            cur_no, cur_t = current_lap_info(laps, tnow)
            if mode == "list" and laps:
                _draw_list(d, w, h, laps, cur_no, style, s, best_no=best_no, img=img)
            else:
                _draw_current(d, w, h, cur_no, cur_t, style, s, img=img)
            if transparent:
                proc.stdin.write(img.tobytes())
            else:
                proc.stdin.write(img.convert("RGB").tobytes())
            if progress_cb and fi % 5 == 0:
                progress_cb(fi / n_frames)

        proc.stdin.close(); proc.wait()
        if progress_cb:
            progress_cb(1.0)
        if done_cb:
            done_cb(output_path)
    except Exception as e:
        if error_cb:
            error_cb(str(e))
        else:
            raise


def _get_ffmpeg():
    for cand in [os.path.join(_HERE, "ffmpeg.exe"),
                 os.path.join(_HERE, "ffmpeg"), "ffmpeg"]:
        if cand == "ffmpeg" or os.path.exists(cand):
            return cand
    return "ffmpeg"
