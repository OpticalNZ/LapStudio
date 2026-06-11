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
    here = os.path.dirname(os.path.abspath(__file__))
    for p in [os.path.join(here, "BigShoulders-Bold.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
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
    _sz = size
    if fit:
        while _sz > 8:
            fnt = _IF.truetype(_BSB_PATH, _sz)
            tmp = _IM.new("RGBA", (10, 10)); td = _ID.Draw(tmp)
            bb = td.textbbox((0, 0), text, font=fnt)
            if int((bb[2] - bb[0]) * stretch) <= max_w:
                break
            _sz -= 2
    fnt = _IF.truetype(_BSB_PATH, _sz)
    tmp = _IM.new("RGBA", (10, 10)); td = _ID.Draw(tmp)
    bb = td.textbbox((0, 0), text, font=fnt)
    tw = bb[2] - bb[0]; th = bb[3] - bb[1]; pad = ow + 4
    layer = _IM.new("RGBA", (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
    ld = _ID.Draw(layer)
    for dx in range(-ow, ow + 1):
        for dy in range(-ow, ow + 1):
            if dx * dx + dy * dy <= ow * ow:
                ld.text((pad - bb[0] + dx, pad - bb[1] + dy), text, font=fnt, fill=outline)
    ld.text((pad - bb[0], pad - bb[1]), text, font=fnt, fill=fill)
    layer = layer.resize((int(layer.width * stretch), layer.height), _IM.LANCZOS)
    cx, cy = center
    if anchor == "lm":
        img.paste(layer, (int(cx), int(cy - layer.height / 2)), layer)
    else:
        img.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)


# G-axis ranges (G), gridded in 0.5G steps
LAT_MAX = 2.5     # lateral (horizontal) extent each side
LON_MAX = 2.0     # longitudinal (vertical) extent each side
STEP    = 0.5


def build_plot_layer(w, h, chroma=(255, 0, 255), transparent=False):
    """Build the static grid/background for the G-trace plot.
    Returns (img RGBA, geometry dict). The plot is wider than tall because the
    lateral range (2.5G) exceeds the longitudinal range (2.0G)."""
    from PIL import Image, ImageDraw
    if transparent:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    else:
        img = Image.new("RGBA", (w, h), chroma + (255,))
    d = ImageDraw.Draw(img)

    pad = int(min(w, h) * 0.06)
    # Reserve a band at the bottom for the big Lat/Lon readouts
    lbl_band = int(h * 0.18)
    plot_x0 = pad
    plot_x1 = w - pad
    plot_y0 = pad
    plot_y1 = h - pad - lbl_band

    # Pixels-per-G so that the lateral axis fills the width and the
    # longitudinal axis fills the available height, independently scaled
    # (the spec defines different max G per axis, so the cell aspect differs).
    gu_x = (plot_x1 - plot_x0) / (2 * LAT_MAX)
    gu_y = (plot_y1 - plot_y0) / (2 * LON_MAX)
    cx = (plot_x0 + plot_x1) / 2
    cy = (plot_y0 + plot_y1) / 2

    # plot panel
    d.rectangle([plot_x0, plot_y0, plot_x1, plot_y1], fill=(10, 10, 14, 255))

    # ── Graduation lines ────────────────────────────────────────────────────
    # Vertical lines = lateral (Lat G) grads at 0.5..2.5
    gv = STEP
    while gv <= LAT_MAX + 1e-6:
        for sign in (-1, 1):
            lx = int(cx + sign * gv * gu_x)
            is_whole = abs(gv - round(gv)) < 1e-6
            col = (70, 70, 95, 255) if is_whole else (45, 45, 60, 255)
            d.line([(lx, plot_y0 + 1), (lx, plot_y1 - 1)], fill=col, width=1)
        gv += STEP
    # Horizontal lines = longitudinal (Lon G) grads at 0.5..2.0
    gv = STEP
    while gv <= LON_MAX + 1e-6:
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


def render_video(rows, t_start, t_end, output_path, fps,
                 progress_cb=None, done_cb=None, error_cb=None, cancel_check=None,
                 resolution=(720, 600), chroma=(255, 0, 255), transparent=False,
                 speed_colour=True, g_trail_secs=5.0):
    """Render the standalone G-trace overlay video.

    `rows` needs columns: ts, g_lat, g_long, speed.
    The trace shows the recent G path (trail) with a moving dot at the current
    G, plus big Lat/Lon readouts below the plot.
    """
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

        base, geom = build_plot_layer(w, h, chroma=chroma, transparent=transparent)
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
            if len(tl) > 1:
                pts = [(_clampx(int(cx + tl[i] * gu_x)),
                        _clampy(int(cy - (-lo[i]) * gu_y))) for i in range(len(tl))]
                for i in range(len(pts) - 1):
                    if speed_colour and sp is not None and i < len(sp):
                        col = _speed_colour_fn(sp[i])
                    else:
                        a = 0.3 + 0.7 * (i / (len(pts) - 1))
                        col = (int(180 * a), int(110 * a), int(255 * a))
                    d.line([pts[i], pts[i + 1]], fill=col + (255,), width=line_w)

            # current dot + readouts (nearest sample by time)
            idx = int(np.clip(np.searchsorted(ts, tnow), 0, len(ts) - 1))
            cur_lat = glat[idx]; cur_lon = glon[idx]
            dx = _clampx(int(cx + cur_lat * gu_x))
            dy = _clampy(int(cy - (-cur_lon) * gu_y))
            dcol = (_speed_colour_fn(spd[idx]) if (speed_colour and spd is not None)
                    else (180, 110, 255))
            d.ellipse([dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r],
                      fill=dcol + (255,), outline=(255, 255, 255, 255),
                      width=max(2, dot_r // 3))

            # Big Lat/Lon readouts below the plot — FIXED size & position so the
            # text stays steady as the numbers change (no per-frame resize/shift).
            _wide_text(frame, (_lat_x, lbl_cy),
                       f"Lat {cur_lat:+.2f}", lbl_fsz, (255, 255, 255, 255),
                       half - 8, ow=3, stretch=_STRETCH, fit=False, anchor="lm")
            _wide_text(frame, (_lon_x, lbl_cy),
                       f"Lon {-cur_lon:+.2f}", lbl_fsz, (255, 255, 255, 255),
                       half - 8, ow=3, stretch=_STRETCH, fit=False, anchor="lm")

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


def _get_ffmpeg():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in [os.path.join(here, "ffmpeg.exe"),
                 os.path.join(here, "ffmpeg"), "ffmpeg"]:
        if cand == "ffmpeg" or os.path.exists(cand):
            return cand
    return "ffmpeg"
