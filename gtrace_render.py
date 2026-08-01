"""
gtrace_render.py — standalone G-force trace overlay generator for LapStudio.

Renders a transparent / chroma-key video of the G-force trace: a cropped,
Dash-5-style plot where the lateral (Lat G) axis runs to +/-2.5G and the
longitudinal (Lon G) axis runs to +/-2.0G, both gridded in 0.5G steps. The
shorter vertical axis frees room below the plot for big, clear Lat/Lon
readouts (Dash 5 font). Produced as a SEPARATE video so it can be positioned
anywhere over the footage, independent of the chosen dashboard.
"""

import numpy as np
import math
from textgrid import _gtext, _gwidth, _vmask, _vw
import os
import subprocess


# ── Speed colour (matches the dashes) ────────────────────────────────────────
def _speed_colour_fn(spd, min_spd=50.0, max_spd=200.0):
    t = max(0.0, min(1.0, (spd - min_spd) / (max_spd - min_spd)))
    if t < 0.5:
        tt = t / 0.5
        return (0, int(180 * tt), int(255 - 55 * tt))
    else:
        tt = (t - 0.5) / 0.5
        if tt < 0.5:
            ttt = tt / 0.5
            return (int(255 * ttt), 255, int(255 * (1 - ttt)))
        else:
            ttt = (tt - 0.5) / 0.5
            return (255, int(255 * (1 - ttt)), 0)


# ── Wide outline font (Big Shoulders), matching the Dash 5 readouts ──────────
def _find_bsb_font():
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    _meipass = getattr(sys, "_MEIPASS", None)   # PyInstaller bundle dir
    _cands = [os.path.join(here, "BigShoulders-Bold.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    if _meipass:
        _cands.insert(0, os.path.join(_meipass, "BigShoulders-Bold.ttf"))
    for p in _cands:
        if os.path.exists(p):
            return p
    return None


_BSB_PATH = _find_bsb_font()


def _wide_text(img, center, text, size, fill, max_w,
               ow=3, outline=(0, 0, 0), stretch=1.5, fit=True, anchor="mm"):
    """Render Big-Shoulders text, stretched, outlined.
    fit=True shrinks to fit max_w (variable size); fit=False uses `size` exactly
    (stable size across frames). anchor 'mm' centres on `center`; 'lm' left-aligns
    so the left edge stays put as the text width changes (stable position)."""
    from PIL import ImageFont as _IF, ImageDraw as _ID, Image as _IM
    if _BSB_PATH is None:
        return
    # Everything is measured off the value's digit mask, so neither the font size
    # nor the layer width changes as the numbers change (see textgrid.py).
    _ref = _vmask(text)
    _sz = size
    if fit:
        while _sz > 8:
            fnt = _IF.truetype(_BSB_PATH, _sz)
            if int(_gwidth(_ref, fnt) * stretch) <= max_w:
                break
            _sz -= 2
    fnt = _IF.truetype(_BSB_PATH, _sz)
    tmp = _IM.new("RGBA", (10, 10)); td = _ID.Draw(tmp)
    bb = td.textbbox((0, 0), _ref, font=fnt)
    tw = int(round(max(_gwidth(_ref, fnt), _gwidth(text, fnt))))
    th = bb[3] - bb[1]; pad = ow + 4
    layer = _IM.new("RGBA", (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
    ld = _ID.Draw(layer)
    _gtext(ld, (pad, pad), text, fnt, fill, anchor="lt", outline=outline,
           ow=int(ow), vref=_ref, slots=_ref)
    layer = layer.resize((max(1, int(layer.width * stretch)), layer.height), _IM.LANCZOS)
    cx, cy = center
    if anchor == "lm":
        img.paste(layer, (int(cx), int(cy - layer.height / 2)), layer)
    else:
        img.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)


# G-axis ranges (G), gridded in 0.5G steps
LAT_MAX = 2.5     # lateral (horizontal) extent each side
LON_MAX = 2.0     # longitudinal (vertical) extent each side
STEP    = 0.5


def auto_g_max(rows, floor=1.0):
    """Return the nearest-greater 0.5G step above the session's peak |G|.
    Looks at both g_lat and g_long. Falls back to `floor` if no data."""
    import numpy as _np
    import math as _m
    vals = []
    for c in ("g_lat", "g_long"):
        if c in rows.columns:
            a = _np.asarray(rows[c], dtype=float)
            a = a[_np.isfinite(a)]
            if a.size:
                vals.append(_np.max(_np.abs(a)))
    peak = max(vals) if vals else 0.0
    if peak <= 0:
        return floor
    # round up to the next 0.5
    step = _m.ceil(peak / STEP) * STEP
    return max(step, floor)


def build_plot_layer(w, h, chroma=(255, 0, 255), transparent=False,
                     lat_max=None, lon_max=None):
    """Build the static grid/background for the G-trace plot.
    Returns (img RGBA, geometry dict). By default the plot is wider than tall
    because the lateral range (2.5G) exceeds the longitudinal range (2.0G);
    `lat_max` / `lon_max` override those ranges (the Max range setting).

    The whole frame is the OPAQUE plot panel (grid fills the frame, readout band
    at the bottom) so the X-Y graph can be overlaid directly with no chroma key.
    """
    from PIL import Image, ImageDraw
    lat_max = float(lat_max) if lat_max else LAT_MAX
    lon_max = float(lon_max) if lon_max else LON_MAX
    PANEL = (10, 10, 14, 255)
    # Opaque panel fills the frame (no chroma border to key out). The transparent
    # option is still honoured for anyone who wants a true alpha overlay.
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0) if transparent else PANEL)
    d = ImageDraw.Draw(img)

    pad = max(2, int(min(w, h) * 0.012))   # tiny inset so the plot fills the frame
    # Reserve a band at the bottom for the big Lat/Lon readouts
    lbl_band = int(h * 0.18)
    plot_x0 = pad
    plot_x1 = w - pad
    plot_y0 = pad
    plot_y1 = h - pad - lbl_band

    # Pixels-per-G so that the lateral axis fills the width and the
    # longitudinal axis fills the available height, independently scaled
    # (the spec defines different max G per axis, so the cell aspect differs).
    gu_x = (plot_x1 - plot_x0) / (2 * lat_max)
    gu_y = (plot_y1 - plot_y0) / (2 * lon_max)
    cx = (plot_x0 + plot_x1) / 2
    cy = (plot_y0 + plot_y1) / 2

    # plot panel
    d.rectangle([plot_x0, plot_y0, plot_x1, plot_y1], fill=(10, 10, 14, 255))

    # ── Graduation lines ────────────────────────────────────────────────────
    # Vertical lines = lateral (Lat G) grads at 0.5..2.5
    gv = STEP
    while gv <= lat_max + 1e-6:
        for sign in (-1, 1):
            lx = int(cx + sign * gv * gu_x)
            is_whole = abs(gv - round(gv)) < 1e-6
            col = (70, 70, 95, 255) if is_whole else (45, 45, 60, 255)
            d.line([(lx, plot_y0 + 1), (lx, plot_y1 - 1)], fill=col, width=1)
        gv += STEP
    # Horizontal lines = longitudinal (Lon G) grads at 0.5..2.0
    gv = STEP
    while gv <= lon_max + 1e-6:
        for sign in (-1, 1):
            ly = int(cy - sign * gv * gu_y)
            is_whole = abs(gv - round(gv)) < 1e-6
            col = (70, 70, 95, 255) if is_whole else (45, 45, 60, 255)
            d.line([(plot_x0 + 1, ly), (plot_x1 - 1, ly)], fill=col, width=1)
        gv += STEP
    # centre cross (bright)
    d.line([(int(cx), plot_y0 + 1), (int(cx), plot_y1 - 1)], fill=(235, 235, 245, 255), width=2)
    d.line([(plot_x0 + 1, int(cy)), (plot_x1 - 1, int(cy)), ], fill=(235, 235, 245, 255), width=2)
    # outer border
    d.rounded_rectangle([plot_x0, plot_y0, plot_x1, plot_y1],
                        radius=8, outline=(90, 90, 105, 255), width=2)

    geom = dict(cx=cx, cy=cy, gu_x=gu_x, gu_y=gu_y,
                x0=plot_x0, x1=plot_x1, y0=plot_y0, y1=plot_y1,
                lbl_band=lbl_band)
    return img, geom


def _channel_colour_fn(val, lo, hi):
    """Map a channel value across [lo,hi] to blue→green→orange→red."""
    if hi <= lo:
        t = 0.5
    else:
        t = max(0.0, min(1.0, (val - lo) / (hi - lo)))
    stops = [(0.0, (40, 120, 255)), (0.4, (30, 200, 90)),
             (0.7, (255, 170, 0)), (1.0, (230, 30, 30))]
    for j in range(len(stops) - 1):
        f0, c0 = stops[j]; f1, c1 = stops[j + 1]
        if t <= f1:
            k = 0.0 if f1 == f0 else (t - f0) / (f1 - f0)
            return tuple(int(c0[m] * (1 - k) + c1[m] * k) for m in range(3))
    return stops[-1][1]


def _draw_label(d, xy, text, fsz):
    """Small white outlined label for ring values."""
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(_BSB_PATH, fsz) if _BSB_PATH else ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    _gtext(d, xy, text, f, (245, 245, 248, 255), anchor="la",
           outline=(0, 0, 0, 255), ow=2, vref=_vmask(text))


def _draw_combined_g(d, w, h, cg, fsz=None, anchor=None):
    """Print the combined-G reading right-justified at the bottom-right corner
    `anchor` (defaults to the frame's bottom-right), in the BigShoulders font
    with a black outline. `cg` is the magnitude in G."""
    from PIL import ImageFont
    if fsz is None:
        fsz = max(16, int(min(w, h) * 0.11))
    try:
        f = ImageFont.truetype(_BSB_PATH, fsz) if _BSB_PATH else ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    txt = f"{cg:.2f}G"
    _ref = "8.88G"                      # reserved field: never moves
    bb = d.textbbox((0, 0), _ref, font=f)
    ax, ay = anchor if anchor else (w, h)
    _m = int(min(w, h) * 0.03)
    _ow = max(2, int(fsz * 0.08))
    _gtext(d, (ax - _m, ay - _m - (bb[3] - bb[1]) - bb[1]), txt, f,
           (245, 245, 248, 255), anchor="ra", outline=(0, 0, 0, 255), ow=_ow,
           vref=_ref, slots=_ref)


def _render_target(rows, t_start, t_end, output_path, fps,
                   progress_cb, done_cb, error_cb, cancel_check,
                   resolution, chroma, transparent, g_trail_secs,
                   g_max, colour_mode, colour_channel):
    """Circular crosshair-target G-trace (the Dash 10 style), standalone.
    Rings sized to g_max (auto if None), white axes, thick trail."""
    try:
        from PIL import Image, ImageDraw
        import pandas as pd
        w, h = resolution
        for c in ("g_lat", "g_long"):
            if c not in rows.columns:
                raise ValueError("G-trace needs 'g_lat' and 'g_long' columns.")
        ts = pd.to_numeric(rows["ts"], errors="coerce").to_numpy()
        glat = pd.to_numeric(rows["g_lat"], errors="coerce").to_numpy()
        glon = pd.to_numeric(rows["g_long"], errors="coerce").to_numpy()
        spd = (pd.to_numeric(rows["speed"], errors="coerce").to_numpy()
               if "speed" in rows.columns else None)

        chan = None; ch_lo = ch_hi = 0.0
        if colour_mode == "channel" and colour_channel and colour_channel in rows.columns:
            chan = pd.to_numeric(rows[colour_channel], errors="coerce").to_numpy()
            fin = chan[np.isfinite(chan)]
            if fin.size:
                ch_lo, ch_hi = float(np.min(fin)), float(np.max(fin))

        # G rings always auto-derived from the log (nearest greater 0.5G).
        # Explicit g_max caps the plot range; None = auto-derive from the log.
        g_max = float(g_max) if g_max else auto_g_max(rows)
        n_rings = max(1, int(round(g_max / STEP)))

        cx, cy = w / 2.0, h / 2.0
        g_rad = int(min(w, h) * 0.46)
        def gpix(gx, gy):
            return (cx + gx / g_max * g_rad, cy - gy / g_max * g_rad)

        s = min(w, h) / 600.0
        axis_w = max(3, int(6 * s)); ring_w = max(3, int(5 * s))
        trail_w = max(4, int(9 * s)); dot_r = max(8, int(16 * s))

        def _colour(i_idx):
            if colour_mode == "bw":
                return (235, 235, 240)
            if chan is not None and i_idx < len(chan) and np.isfinite(chan[i_idx]):
                return _channel_colour_fn(chan[i_idx], ch_lo, ch_hi)
            if spd is not None and i_idx < len(spd):
                return _speed_colour_fn(spd[i_idx])
            return (40, 160, 255)

        if transparent:
            base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        else:
            base = Image.new("RGBA", (w, h), chroma + (255,))
        bd = ImageDraw.Draw(base)
        _bord = max(2, int(3 * s))     # black border each side of white lines
        _RED = (230, 30, 30)           # current-position dot colour
        # Rings: black underlay (bbox enlarged by _bord so the border shows on
        # BOTH sides of the white ring), then the white ring on top.
        for k in range(1, n_rings + 1):
            rr = int(k * STEP / g_max * g_rad)
            bd.ellipse([cx - rr - _bord, cy - rr - _bord, cx + rr + _bord, cy + rr + _bord],
                       outline=(0, 0, 0, 255), width=ring_w + 2 * _bord)
        for k in range(1, n_rings + 1):
            rr = int(k * STEP / g_max * g_rad)
            bd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                       outline=(245, 245, 248, 255), width=ring_w)
            lbl = f"{k*STEP:.1f}".rstrip("0").rstrip(".")
            _lf = max(12, int(26 * s))
            _draw_label(bd, (cx + int(g_rad * 0.05), cy + rr - int(_lf * 0.6)), lbl, _lf)
        # Crosshair axes: black underlay then white.
        _axes = [[(cx - g_rad, cy), (cx + g_rad, cy)], [(cx, cy - g_rad), (cx, cy + g_rad)]]
        for _lp in _axes:
            bd.line(_lp, fill=(0, 0, 0, 255), width=axis_w + 2 * _bord)
        for _lp in _axes:
            bd.line(_lp, fill=(245, 245, 248, 255), width=axis_w)

        n_frames = max(1, int(round((t_end - t_start) * fps)))
        frame_ts = t_start + np.arange(n_frames) / fps

        ffmpeg = _get_ffmpeg()
        pix_fmt = "rgba" if transparent else "rgb24"
        vcodec = (["-c:v", "qtrle"] if transparent
                  else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"])
        cmd = [ffmpeg, "-y", "-f", "rawvideo", "-pixel_format", pix_fmt,
               "-video_size", f"{w}x{h}", "-framerate", str(fps),
               "-i", "-"] + vcodec + [output_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def _clip(px, py):
            dxp, dyp = px - cx, py - cy
            dd = (dxp * dxp + dyp * dyp) ** 0.5
            if dd > g_rad:
                return (cx + dxp / dd * g_rad, cy + dyp / dd * g_rad)
            return (px, py)

        for fi in range(n_frames):
            if cancel_check and cancel_check():
                proc.stdin.close(); proc.wait(); return
            tnow = frame_ts[fi]
            frame = base.copy()
            d = ImageDraw.Draw(frame)
            mask = (ts >= tnow - g_trail_secs) & (ts <= tnow)
            idxs = np.where(mask)[0]
            if len(idxs) > 1:
                pts = [_clip(*gpix(glat[i], glon[i])) for i in idxs]
                # black underlay for the whole trail, then the coloured segments
                for j in range(1, len(pts)):
                    d.line([pts[j - 1], pts[j]], fill=(0, 0, 0, 255),
                           width=trail_w + 2 * _bord)
                for j in range(1, len(pts)):
                    col = _colour(idxs[j])
                    d.line([pts[j - 1], pts[j]], fill=tuple(col) + (255,), width=trail_w)
            idx = int(np.clip(np.searchsorted(ts, tnow), 0, len(ts) - 1))
            dx, dy = _clip(*gpix(glat[idx], glon[idx]))
            dcol = _colour(idx)
            # black ring under the dot, then a RED position dot with white outline
            d.ellipse([dx - dot_r - _bord, dy - dot_r - _bord,
                       dx + dot_r + _bord, dy + dot_r + _bord], fill=(0, 0, 0, 255))
            d.ellipse([dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r],
                      fill=_RED + (255,), outline=(255, 255, 255, 255),
                      width=max(2, int(4 * s)))
            # Combined-G reading, bottom-right corner
            _draw_combined_g(d, w, h, (glat[idx] ** 2 + glon[idx] ** 2) ** 0.5)
            if transparent:
                proc.stdin.write(frame.tobytes())
            else:
                proc.stdin.write(frame.convert("RGB").tobytes())
            if progress_cb and (fi % 10 == 0):
                progress_cb(fi / n_frames)
        proc.stdin.close(); proc.wait()
        if done_cb:
            done_cb(output_path)
    except Exception as e:
        if error_cb:
            error_cb(str(e))


def render_video(rows, t_start, t_end, output_path, fps,
                 progress_cb=None, done_cb=None, error_cb=None, cancel_check=None,
                 resolution=(720, 600), chroma=(255, 0, 255), transparent=False,
                 speed_colour=True, g_trail_secs=5.0,
                 form="classic", g_max=None,
                 colour_mode="speed", colour_channel=None):
    """Render the standalone G-trace overlay video.

    `rows` needs columns: ts, g_lat, g_long, speed.
    form: "classic" (rings + Lat/Lon readout) or "target" (crosshair target).
    g_max: outer-ring G; None → auto from the log (nearest greater 0.5G).
    colour_mode: "speed" | "bw" | "channel". When "channel", colour_channel is a
        column name whose value maps blue→red across its session min/max.
    """
    if form == "target":
        return _render_target(rows, t_start, t_end, output_path, fps,
                              progress_cb, done_cb, error_cb, cancel_check,
                              resolution, chroma, transparent, g_trail_secs,
                              g_max, colour_mode, colour_channel)
    try:
        from PIL import Image, ImageDraw
        import pandas as pd

        w, h = resolution
        for c in ("g_lat", "g_long"):
            if c not in rows.columns:
                raise ValueError("G-trace needs 'g_lat' and 'g_long' columns.")
        ts = pd.to_numeric(rows["ts"], errors="coerce").to_numpy()
        glat = pd.to_numeric(rows["g_lat"], errors="coerce").to_numpy()
        glon = pd.to_numeric(rows["g_long"], errors="coerce").to_numpy()
        spd = (pd.to_numeric(rows["speed"], errors="coerce").to_numpy()
               if "speed" in rows.columns else None)

        # optional colour-by-channel series for the classic trail
        _cl_chan = None; _cl_lo = _cl_hi = 0.0
        if colour_mode == "channel" and colour_channel and colour_channel in rows.columns:
            _cl_chan = pd.to_numeric(rows[colour_channel], errors="coerce").to_numpy()
            _fin = _cl_chan[np.isfinite(_cl_chan)]
            if _fin.size:
                _cl_lo, _cl_hi = float(np.min(_fin)), float(np.max(_fin))

        _lm = float(g_max) if g_max else None
        base, geom = build_plot_layer(w, h, chroma=chroma, transparent=transparent,
                                      lat_max=_lm,
                                      lon_max=(_lm * LON_MAX / LAT_MAX) if _lm else None)
        cx, cy = geom["cx"], geom["cy"]
        gu_x, gu_y = geom["gu_x"], geom["gu_y"]
        x0, x1, y0, y1 = geom["x0"], geom["x1"], geom["y0"], geom["y1"]

        n_frames = max(1, int(round((t_end - t_start) * fps)))
        frame_ts = t_start + np.arange(n_frames) / fps

        def _clampx(px): return max(x0 + 2, min(x1 - 2, px))
        def _clampy(py): return max(y0 + 2, min(y1 - 2, py))

        dot_r = max(5, int(min(w, h) * 0.018))
        line_w = max(3, int(min(w, h) * 0.010))
        half = (x1 - x0) // 2

        # ── Fixed Lat/Lon readout layout (computed ONCE so the text doesn't
        #    resize or shift between frames as the numbers change) ──────────────
        from PIL import ImageFont as _IF2, ImageDraw as _ID2, Image as _IM2
        _STRETCH = 1.4
        _OW = 3
        _worst = "Lat -2.50"      # widest string the readout can show
        lbl_cy = y1 + (h - y1) // 2
        # _wide_text adds an internal border of (_OW + 4) px on each side which is
        # then stretched horizontally; account for it so the glyphs sit flush to
        # the margin and the worst-case string fits inside each half.
        _pad_px = int((_OW + 4) * _STRETCH)
        # Shrink once to fit half the plot width, then keep that size for all frames.
        _rd_fsz = max(14, int(geom["lbl_band"] * 0.54))   # a little smaller
        if _BSB_PATH is not None:
            while _rd_fsz > 10:
                _f = _IF2.truetype(_BSB_PATH, _rd_fsz)
                _tmp = _IM2.new("RGBA", (10, 10)); _td = _ID2.Draw(_tmp)
                _bb = _td.textbbox((0, 0), _worst, font=_f)
                # full rendered width = stretched glyph width + both padded borders
                _full_w = int((_bb[2] - _bb[0]) * _STRETCH) + 2*_pad_px
                if _full_w <= half - 6:
                    break
                _rd_fsz -= 2
        # Left-anchored positions. Shift left by the layer's internal pad so the
        # visible "Lat" glyph aligns flush with the plot's left edge (x0).
        _lat_x = x0 - _pad_px
        _lon_x = x0 + half - _pad_px

        lbl_fsz = _rd_fsz

        ffmpeg = _get_ffmpeg()
        pix_fmt = "rgba" if transparent else "rgb24"
        vcodec = (["-c:v", "qtrle"] if transparent
                  else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"])
        cmd = [ffmpeg, "-y", "-f", "rawvideo", "-pixel_format", pix_fmt,
               "-video_size", f"{w}x{h}", "-framerate", str(fps),
               "-i", "-"] + vcodec + [output_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for fi in range(n_frames):
            if cancel_check and cancel_check():
                proc.stdin.close(); proc.wait(); return
            tnow = frame_ts[fi]
            frame = base.copy()
            d = ImageDraw.Draw(frame)

            # recent trail
            mask = (ts >= tnow - g_trail_secs) & (ts <= tnow)
            tl = glat[mask]; lo = glon[mask]
            sp = spd[mask] if spd is not None else None
            ch = _cl_chan[mask] if _cl_chan is not None else None
            if len(tl) > 1:
                pts = [(_clampx(int(cx + tl[i] * gu_x)),
                        _clampy(int(cy - lo[i] * gu_y))) for i in range(len(tl))]
                for i in range(len(pts) - 1):
                    if colour_mode == "bw":
                        col = (235, 235, 240)
                    elif colour_mode == "channel" and ch is not None and i < len(ch) and np.isfinite(ch[i]):
                        col = _channel_colour_fn(ch[i], _cl_lo, _cl_hi)
                    elif speed_colour and sp is not None and i < len(sp):
                        col = _speed_colour_fn(sp[i])
                    else:
                        a = 0.3 + 0.7 * (i / (len(pts) - 1))
                        col = (int(180 * a), int(110 * a), int(255 * a))
                    d.line([pts[i], pts[i + 1]], fill=col + (255,), width=line_w)

            # current dot + readouts (nearest sample by time)
            idx = int(np.clip(np.searchsorted(ts, tnow), 0, len(ts) - 1))
            cur_lat = glat[idx]; cur_lon = glon[idx]
            dx = _clampx(int(cx + cur_lat * gu_x))
            dy = _clampy(int(cy - cur_lon * gu_y))
            dcol = (_speed_colour_fn(spd[idx]) if (speed_colour and spd is not None)
                    else (180, 110, 255))
            if colour_mode == "bw":
                dcol = (235, 235, 240)
            elif colour_mode == "channel" and _cl_chan is not None and np.isfinite(_cl_chan[idx]):
                dcol = _channel_colour_fn(_cl_chan[idx], _cl_lo, _cl_hi)
            d.ellipse([dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r],
                      fill=dcol + (255,), outline=(255, 255, 255, 255),
                      width=max(2, dot_r // 3))

            # Big Lat/Lon readouts below the plot — FIXED size & position so the
            # text stays steady as the numbers change (no per-frame resize/shift).
            _wide_text(frame, (_lat_x, lbl_cy),
                       f"Lat {cur_lat:+.2f}", lbl_fsz, (255, 255, 255, 255),
                       half - 8, ow=3, stretch=_STRETCH, fit=False, anchor="lm")
            _wide_text(frame, (_lon_x, lbl_cy),
                       f"Lon {cur_lon:+.2f}", lbl_fsz, (255, 255, 255, 255),
                       half - 8, ow=3, stretch=_STRETCH, fit=False, anchor="lm")

            # Combined-G reading in the plot's lower-right corner
            _draw_combined_g(d, w, h, (cur_lat ** 2 + cur_lon ** 2) ** 0.5,
                             fsz=max(14, int(geom["lbl_band"] * 0.42)), anchor=(x1, y1))

            if transparent:
                proc.stdin.write(frame.tobytes())
            else:
                proc.stdin.write(frame.convert("RGB").tobytes())
            if progress_cb and fi % 10 == 0:
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


def render_preview_frame(rows, t_now, resolution=(480, 480),
                         chroma=(255, 0, 255), transparent=False,
                         g_trail_secs=5.0, colour_mode="speed",
                         colour_channel=None, form="target", g_max=None):
    """Return a single PIL RGBA image of the G-trace at t_now, for the in-app
    preview. Renders the selected `form` ("target" circular crosshair, or
    "classic" rectangular grid) with black-bordered lines. Mirrors the video
    renderers without invoking ffmpeg."""
    from PIL import Image, ImageDraw
    import pandas as pd
    w, h = resolution
    for c in ("g_lat", "g_long"):
        if c not in rows.columns:
            raise ValueError("G-trace needs 'g_lat' and 'g_long' columns.")
    ts = pd.to_numeric(rows["ts"], errors="coerce").to_numpy()
    glat = pd.to_numeric(rows["g_lat"], errors="coerce").to_numpy()
    glon = pd.to_numeric(rows["g_long"], errors="coerce").to_numpy()
    spd = (pd.to_numeric(rows["speed"], errors="coerce").to_numpy()
           if "speed" in rows.columns else None)
    chan = None; ch_lo = ch_hi = 0.0
    if colour_mode == "channel" and colour_channel and colour_channel in rows.columns:
        chan = pd.to_numeric(rows[colour_channel], errors="coerce").to_numpy()
        fin = chan[np.isfinite(chan)]
        if fin.size:
            ch_lo, ch_hi = float(np.min(fin)), float(np.max(fin))
    s = min(w, h) / 600.0
    _bord = max(2, int(3 * s))

    def _colour(i_idx):
        if colour_mode == "bw":
            return (235, 235, 240)
        if chan is not None and i_idx < len(chan) and np.isfinite(chan[i_idx]):
            return _channel_colour_fn(chan[i_idx], ch_lo, ch_hi)
        if spd is not None and i_idx < len(spd):
            return _speed_colour_fn(spd[i_idx])
        return (40, 160, 255)

    mask = (ts >= t_now - g_trail_secs) & (ts <= t_now)
    idxs = np.where(mask)[0]
    idx = int(np.clip(np.searchsorted(ts, t_now), 0, len(ts) - 1))

    # ── CLASSIC: rectangular grid plot (rings + Lat/Lon layout) ───────────────
    if form == "classic":
        _lm = float(g_max) if g_max else None
        base, geom = build_plot_layer(w, h, chroma=chroma, transparent=transparent,
                                      lat_max=_lm,
                                      lon_max=(_lm * LON_MAX / LAT_MAX) if _lm else None)
        d = ImageDraw.Draw(base)
        cx, cy = geom["cx"], geom["cy"]
        gu_x, gu_y = geom["gu_x"], geom["gu_y"]
        x0, x1, y0, y1 = geom["x0"], geom["x1"], geom["y0"], geom["y1"]
        line_w = max(3, int(min(w, h) * 0.010)); dot_r = max(5, int(min(w, h) * 0.018))
        _cx2 = lambda px: max(x0 + 2, min(x1 - 2, px))
        _cy2 = lambda py: max(y0 + 2, min(y1 - 2, py))
        if len(idxs) > 1:
            pts = [(_cx2(int(cx + glat[i] * gu_x)), _cy2(int(cy - glon[i] * gu_y)))
                   for i in idxs]
            for j in range(1, len(pts)):
                d.line([pts[j - 1], pts[j]], fill=(0, 0, 0, 255), width=line_w + 2 * _bord)
            for j in range(1, len(pts)):
                d.line([pts[j - 1], pts[j]], fill=tuple(_colour(idxs[j])) + (255,), width=line_w)
        dx = _cx2(int(cx + glat[idx] * gu_x)); dy = _cy2(int(cy - glon[idx] * gu_y))
        d.ellipse([dx - dot_r - _bord, dy - dot_r - _bord,
                   dx + dot_r + _bord, dy + dot_r + _bord], fill=(0, 0, 0, 255))
        d.ellipse([dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r],
                  fill=(230, 30, 30, 255), outline=(255, 255, 255, 255),
                  width=max(2, dot_r // 3))
        _draw_combined_g(d, w, h, (glat[idx] ** 2 + glon[idx] ** 2) ** 0.5,
                         fsz=max(14, int(geom["lbl_band"] * 0.42)),
                         anchor=(geom["x1"], geom["y1"]))
        return base

    # ── TARGET: circular crosshair ───────────────────────────────────────────
    g_max = float(g_max) if g_max else auto_g_max(rows)
    n_rings = max(1, int(round(g_max / STEP)))
    cx, cy = w / 2.0, h / 2.0
    g_rad = int(min(w, h) * 0.46)
    def gpix(gx, gy):
        return (cx + gx / g_max * g_rad, cy - gy / g_max * g_rad)
    axis_w = max(3, int(6 * s)); ring_w = max(3, int(5 * s))
    trail_w = max(4, int(9 * s)); dot_r = max(8, int(16 * s))

    base = Image.new("RGBA", (w, h), (0, 0, 0, 0) if transparent else chroma + (255,))
    bd = ImageDraw.Draw(base)
    for k in range(1, n_rings + 1):                       # black ring underlay (both sides)
        rr = int(k * STEP / g_max * g_rad)
        bd.ellipse([cx - rr - _bord, cy - rr - _bord, cx + rr + _bord, cy + rr + _bord],
                   outline=(0, 0, 0, 255), width=ring_w + 2 * _bord)
    for k in range(1, n_rings + 1):                       # white ring on top
        rr = int(k * STEP / g_max * g_rad)
        bd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                   outline=(245, 245, 248, 255), width=ring_w)
        lbl = f"{k*STEP:.1f}".rstrip("0").rstrip(".")
        _lf = max(12, int(26 * s))
        _draw_label(bd, (cx + int(g_rad * 0.05), cy + rr - int(_lf * 0.6)), lbl, _lf)
    _axes = [[(cx - g_rad, cy), (cx + g_rad, cy)], [(cx, cy - g_rad), (cx, cy + g_rad)]]
    for _lp in _axes:
        bd.line(_lp, fill=(0, 0, 0, 255), width=axis_w + 2 * _bord)
    for _lp in _axes:
        bd.line(_lp, fill=(245, 245, 248, 255), width=axis_w)

    def _clip(px, py):
        dxp, dyp = px - cx, py - cy
        dd = (dxp * dxp + dyp * dyp) ** 0.5
        if dd > g_rad:
            return (cx + dxp / dd * g_rad, cy + dyp / dd * g_rad)
        return (px, py)

    if len(idxs) > 1:
        pts = [_clip(*gpix(glat[i], glon[i])) for i in idxs]
        for j in range(1, len(pts)):
            bd.line([pts[j - 1], pts[j]], fill=(0, 0, 0, 255), width=trail_w + 2 * _bord)
        for j in range(1, len(pts)):
            bd.line([pts[j - 1], pts[j]], fill=tuple(_colour(idxs[j])) + (255,), width=trail_w)
    dx, dy = _clip(*gpix(glat[idx], glon[idx]))
    bd.ellipse([dx - dot_r - _bord, dy - dot_r - _bord,
                dx + dot_r + _bord, dy + dot_r + _bord], fill=(0, 0, 0, 255))
    bd.ellipse([dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r],
               fill=(230, 30, 30, 255), outline=(255, 255, 255, 255),
               width=max(2, int(4 * s)))
    _draw_combined_g(bd, w, h, (glat[idx] ** 2 + glon[idx] ** 2) ** 0.5)
    return base


def _get_ffmpeg():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in [os.path.join(here, "ffmpeg.exe"),
                 os.path.join(here, "ffmpeg"), "ffmpeg"]:
        if cand == "ffmpeg" or os.path.exists(cand):
            return cand
    return "ffmpeg"
