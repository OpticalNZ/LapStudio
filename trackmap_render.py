"""
trackmap_render.py — standalone track-map overlay generator for LapStudio.

Renders a transparent / chroma-key video of the track outline (from GPS
lat/lon), coloured by speed, with a moving dot showing the current position.
Produced as a SEPARATE video file so it can be positioned anywhere over the
race footage in any editor, independently of the main telemetry dashboard.

The outline is static for the whole session/lap; only the position dot moves,
so the coloured path is rendered once and reused for every frame (fast).
"""

import numpy as np
import math
import os
import subprocess


def _speed_colour_fn(spd, min_spd=50.0, max_spd=200.0):
    """Blue → cyan → green → yellow → red across the speed range (matches dashes)."""
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


def _project_path(lat, lon, w, h, pad):
    """Project lat/lon arrays into pixel coordinates that fit (w,h) with `pad`.
    Uses an equirectangular projection scaled to preserve aspect at this latitude.
    Returns list of (x, y) and the chosen scale info."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    # Correct longitude for latitude compression so the shape isn't distorted
    lat0 = float(np.nanmean(lat))
    x_raw = lon * math.cos(math.radians(lat0))
    y_raw = lat
    x_min, x_max = float(np.nanmin(x_raw)), float(np.nanmax(x_raw))
    y_min, y_max = float(np.nanmin(y_raw)), float(np.nanmax(y_raw))
    span_x = (x_max - x_min) or 1e-9
    span_y = (y_max - y_min) or 1e-9
    avail_w = w - 2 * pad
    avail_h = h - 2 * pad
    scale = min(avail_w / span_x, avail_h / span_y)
    # Centre the track within the frame
    draw_w = span_x * scale
    draw_h = span_y * scale
    off_x = pad + (avail_w - draw_w) / 2
    off_y = pad + (avail_h - draw_h) / 2
    pts = []
    for xr, yr in zip(x_raw, y_raw):
        px = off_x + (xr - x_min) * scale
        # invert y (screen y grows downward; north should be up)
        py = off_y + (y_max - yr) * scale
        pts.append((px, py))
    return pts


def build_track_layer(lat, lon, speed, w, h, chroma=(255, 0, 255),
                      transparent=False, line_w=None, speed_colour=True,
                      min_spd=50.0, max_spd=200.0):
    """Build the static base layer: the full track outline coloured by speed.
    Returns (PIL.Image RGBA, projected_pts)."""
    from PIL import Image, ImageDraw
    if transparent:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    else:
        img = Image.new("RGBA", (w, h), chroma + (255,))
    d = ImageDraw.Draw(img)
    pad = int(min(w, h) * 0.10)
    pts = _project_path(lat, lon, w, h, pad)
    if line_w is None:
        line_w = max(4, int(min(w, h) * 0.018))
    n = len(pts)
    if n >= 2:
        spd = np.asarray(speed, dtype=float) if speed is not None else None
        for i in range(n - 1):
            if speed_colour and spd is not None and i < len(spd):
                col = _speed_colour_fn(spd[i], min_spd, max_spd)
            else:
                col = (235, 235, 245)
            d.line([pts[i], pts[i + 1]], fill=col + (255,), width=line_w)
        # round the joins by overdrawing vertices
        r = line_w // 2
        for i in range(n):
            if speed_colour and spd is not None and i < len(spd):
                col = _speed_colour_fn(spd[i], min_spd, max_spd)
            else:
                col = (235, 235, 245)
            x, y = pts[i]
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,))
    return img, pts


def render_video(rows, t_start, t_end, output_path, fps,
                 progress_cb=None, done_cb=None, error_cb=None, cancel_check=None,
                 resolution=(720, 720), chroma=(255, 0, 255), transparent=False,
                 speed_colour=True, lat_col="g_lat_unused"):
    """Render the track-map overlay video.

    `rows` is the prepared dataframe with at least columns:
        ts, lat, lon, speed
    Produces a video at `output_path`. The outline is built once; each frame
    re-uses it and stamps the moving position dot.
    """
    try:
        from PIL import Image, ImageDraw
        import pandas as pd

        w, h = resolution

        # Pull GPS + speed arrays
        if "lat" not in rows.columns or "lon" not in rows.columns:
            raise ValueError("Track map needs 'lat' and 'lon' columns in the data.")
        lat = pd.to_numeric(rows["lat"], errors="coerce").to_numpy()
        lon = pd.to_numeric(rows["lon"], errors="coerce").to_numpy()
        spd = (pd.to_numeric(rows["speed"], errors="coerce").to_numpy()
               if "speed" in rows.columns else None)
        ts = pd.to_numeric(rows["ts"], errors="coerce").to_numpy()

        # Filter to the export time window
        mask = (ts >= t_start) & (ts <= t_end)
        lat, lon, ts = lat[mask], lon[mask], ts[mask]
        spd = spd[mask] if spd is not None else None

        # Drop NaN GPS points
        good = ~(np.isnan(lat) | np.isnan(lon))
        lat, lon, ts = lat[good], lon[good], ts[good]
        spd = spd[good] if spd is not None else None
        if len(lat) < 2:
            raise ValueError("Not enough valid GPS points to draw a track map.")

        rng = (float(np.nanmin(spd)) if spd is not None else 50.0,
               float(np.nanmax(spd)) if spd is not None else 200.0)
        min_spd, max_spd = (rng[0], rng[1] if rng[1] > rng[0] else rng[0] + 1)

        # Build the static outline once
        base, pts = build_track_layer(lat, lon, spd, w, h, chroma=chroma,
                                      transparent=transparent,
                                      speed_colour=speed_colour,
                                      min_spd=min_spd, max_spd=max_spd)

        # Resample to the frame grid
        n_frames = max(1, int(round((t_end - t_start) * fps)))
        frame_ts = t_start + np.arange(n_frames) / fps
        # Position index per frame (nearest GPS sample by time)
        idx_for_frame = np.clip(np.searchsorted(ts, frame_ts), 0, len(pts) - 1)

        dot_r = max(6, int(min(w, h) * 0.022))

        # ffmpeg pipe
        ffmpeg = _get_ffmpeg()
        pix_fmt = "rgba" if transparent else "rgb24"
        vcodec = ["-c:v", "qtrle"] if transparent else ["-c:v", "libx264",
                                                         "-pix_fmt", "yuv420p",
                                                         "-crf", "18"]
        out_ext = os.path.splitext(output_path)[1].lower()
        # For transparency prefer .mov (qtrle); warn-fallback handled by caller
        cmd = [ffmpeg, "-y",
               "-f", "rawvideo", "-pixel_format", pix_fmt,
               "-video_size", f"{w}x{h}", "-framerate", str(fps),
               "-i", "-"] + vcodec + [output_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for fi in range(n_frames):
            if cancel_check and cancel_check():
                proc.stdin.close(); proc.wait()
                return
            frame = base.copy()
            d = ImageDraw.Draw(frame)
            px, py = pts[idx_for_frame[fi]]
            # current speed colour for the dot
            si = idx_for_frame[fi]
            dot_col = (_speed_colour_fn(spd[si], min_spd, max_spd)
                       if (speed_colour and spd is not None and si < len(spd))
                       else (255, 255, 255))
            d.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r],
                      fill=dot_col + (255,), outline=(255, 255, 255, 255),
                      width=max(2, dot_r // 3))
            if transparent:
                proc.stdin.write(frame.tobytes())
            else:
                proc.stdin.write(frame.convert("RGB").tobytes())
            if progress_cb and fi % 10 == 0:
                progress_cb(fi / n_frames)

        proc.stdin.close()
        proc.wait()
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
    """Locate ffmpeg (PATH, or bundled next to this file)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in [os.path.join(here, "ffmpeg.exe"),
                 os.path.join(here, "ffmpeg"), "ffmpeg"]:
        if cand == "ffmpeg" or os.path.exists(cand):
            return cand
    return "ffmpeg"
