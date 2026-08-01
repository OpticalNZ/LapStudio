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


# Which window the fixed zoom is sized for, as a percentile of the per-window
# requirement. Sizing for the WORST case made a typical window fill only about a
# quarter of the frame; the median makes a typical window fill it, and the few
# larger ones simply run off the edges, which is what a moving map should do.
WINDOW_FIT_PCT = 50


def _path_metres(lat, lon):
    """Cumulative distance along the path, in metres."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    cosl = math.cos(math.radians(float(np.nanmean(lat))))
    xm = lon * cosl * 111320.0
    ym = lat * 111320.0
    step = np.hypot(np.diff(xm), np.diff(ym))
    step[~np.isfinite(step)] = 0.0
    return np.concatenate(([0.0], np.cumsum(step)))


def _window_geometry(lat, lon, ts, spd, segment_secs, w, h, pad, samples=120):
    """Fixed zoom and window length for WINDOW mode.

    The window is sliced by DISTANCE along the path, not by time. A time window
    covers wildly different ground depending on speed - on a real circuit a 12s
    window is a few hundred metres down the straight but a few dozen through a
    hairpin - so with one fixed zoom the map appeared to change size. Converting
    the setting to a distance (its length at the session's typical speed) cuts
    that spread by about 6x and keeps the picture consistent.

    Returns (cosl, scale_px_per_deg, dist_metres_array, window_length_metres).
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    ts = np.asarray(ts, dtype=float)
    cosl = math.cos(math.radians(float(np.nanmean(lat))))
    xr = lon * cosl
    yr = lat
    dist = _path_metres(lat, lon)

    # Window length: the chosen seconds at the typical speed of the session.
    v_ms = None
    if spd is not None:
        _v = np.asarray(spd, dtype=float)
        _v = _v[np.isfinite(_v)]
        if len(_v):
            v_ms = float(np.median(_v)) / 3.6        # channel is km/h
    if not v_ms or v_ms <= 0:
        _elapsed = float(ts[-1] - ts[0]) or 1.0
        v_ms = float(dist[-1]) / _elapsed
    win_m = max(10.0, float(segment_secs) * max(v_ms, 0.5))

    avail_w = max(1, w - 2 * pad)
    avail_h = max(1, h - 2 * pad)
    need = []
    for tq in np.linspace(float(ts[0]), float(ts[-1]), int(samples)):
        d0 = float(np.interp(tq, ts, dist))
        a = int(np.searchsorted(dist, d0 - win_m / 2.0))
        b = int(np.searchsorted(dist, d0 + win_m / 2.0))
        if b - a < 2:
            continue
        cx = float(np.interp(tq, ts, xr))
        cy = float(np.interp(tq, ts, yr))
        # car sits at the centre, so the frame must hold twice the largest
        # offset from the car in each axis
        dx = float(np.nanmax(np.abs(xr[a:b] - cx))) * 2.0 or 1e-9
        dy = float(np.nanmax(np.abs(yr[a:b] - cy))) * 2.0 or 1e-9
        need.append(min(avail_w / dx, avail_h / dy))
    need = [s for s in need if np.isfinite(s) and s > 0]
    scale = (float(np.percentile(need, WINDOW_FIT_PCT)) if need
             else min(avail_w, avail_h) / 1e-3)
    return cosl, scale, dist, win_m


def _window_slice(dist, d0, win_m):
    """Index range covering win_m metres of path centred on distance d0."""
    a = int(np.searchsorted(dist, d0 - win_m / 2.0))
    b = int(np.searchsorted(dist, d0 + win_m / 2.0))
    return a, b


def _centred_project(lat_a, lon_a, lat_c, lon_c, cosl, scale, w, h):
    """Project points with (lat_c, lon_c) pinned to the centre of the frame."""
    la = np.asarray(lat_a, dtype=float)
    lo = np.asarray(lon_a, dtype=float)
    px = (w / 2.0) + (lo - lon_c) * cosl * scale
    py = (h / 2.0) - (la - lat_c) * scale          # north up
    return list(zip(px.tolist(), py.tolist()))


def _draw_chain(d, pts, idx, width, colour_at):
    """Draw a polyline through pts[idx] with round joints.

    PIL lines have butt ends, so a chain of short segments around a corner
    leaves notches. A dot at each vertex fills them.
    """
    r = width / 2.0 - 0.5
    for k in range(len(idx) - 1):
        c = colour_at(int(idx[k]))
        p0, p1 = pts[int(idx[k])], pts[int(idx[k + 1])]
        d.line([p0, p1], fill=c, width=width)
        if r >= 1.0:
            d.ellipse([p1[0] - r, p1[1] - r, p1[0] + r, p1[1] + r], fill=c)


def _thin_idx(pts, min_px=1.5):
    """Indices of points spaced at least `min_px` apart along the path.

    A 50 Hz log of a long session is hundreds of thousands of points, most of
    them landing on the same pixel. Drawing every one is slow and adds nothing
    visible, so the path is resampled by arc length before drawing.
    """
    p = np.asarray(pts, dtype=float)
    n = len(p)
    if n < 3:
        return np.arange(n)
    seg = np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1]))
    s = np.concatenate(([0.0], np.cumsum(seg)))
    if s[-1] <= min_px:
        return np.array([0, n - 1], dtype=int)
    idx = np.unique(np.searchsorted(s, np.arange(0.0, s[-1], float(min_px))))
    idx = idx[idx < n]
    if len(idx) == 0 or idx[-1] != n - 1:
        idx = np.append(idx, n - 1)
    return idx.astype(int)


def _make_projector(lat, lon, w, h, pad):
    """Build a lat/lon → pixel projector from the FULL path so every lap shares
    one coordinate system. Returns a function project(lat_arr, lon_arr)->list(x,y)."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    lat0 = float(np.nanmean(lat))
    cosl = math.cos(math.radians(lat0))
    x_raw = lon * cosl
    y_raw = lat
    x_min, x_max = float(np.nanmin(x_raw)), float(np.nanmax(x_raw))
    y_min, y_max = float(np.nanmin(y_raw)), float(np.nanmax(y_raw))
    span_x = (x_max - x_min) or 1e-9
    span_y = (y_max - y_min) or 1e-9
    avail_w = w - 2 * pad
    avail_h = h - 2 * pad
    scale = min(avail_w / span_x, avail_h / span_y)
    draw_w = span_x * scale
    draw_h = span_y * scale
    off_x = pad + (avail_w - draw_w) / 2
    off_y = pad + (avail_h - draw_h) / 2

    def project(lat_a, lon_a):
        la = np.asarray(lat_a, dtype=float)
        lo = np.asarray(lon_a, dtype=float)
        xr = lo * cosl
        yr = la
        out = []
        for x, y in zip(xr, yr):
            px = off_x + (x - x_min) * scale
            py = off_y + (y_max - y) * scale   # invert y so north is up
            out.append((px, py))
        return out
    return project


def _project_path(lat, lon, w, h, pad):
    """Backwards-compatible single-path projection."""
    return _make_projector(lat, lon, w, h, pad)(lat, lon)


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
        # Black border underlay so the coloured line reads on any background.
        _bord = max(2, int(min(w, h) * 0.006))
        for i in range(n - 1):
            d.line([pts[i], pts[i + 1]], fill=(0, 0, 0, 255), width=line_w + 2 * _bord)
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
                 speed_colour=True, lat_col="g_lat_unused",
                 laps=None, map_fps=4.0,
                 line_colour=(235, 235, 245), show_current_lap=True,
                 segment_secs=None):
    """Render the track-map overlay video.

    The ENTIRE session GPS path is drawn once as a solid dark "ghost" outline
    (all laps overlaid, showing line variation). On top, only the CURRENT lap's
    path is drawn in speed colour, redrawn each frame as the lap progresses, with
    a moving position dot. Rendered at `map_fps` (default 4 fps) independent of
    the main video to keep render time low.

    `rows` needs columns: ts, lat, lon, speed.
    `laps` is an optional list of (start_ts, end_ts, lap_time) used to pick the
    current lap; if omitted the whole session is treated as one lap.
    """
    try:
        from PIL import Image, ImageDraw
        import pandas as pd

        w, h = resolution

        if "lat" not in rows.columns or "lon" not in rows.columns:
            raise ValueError("Track map needs 'lat' and 'lon' columns in the data.")
        lat_all = pd.to_numeric(rows["lat"], errors="coerce").to_numpy()
        lon_all = pd.to_numeric(rows["lon"], errors="coerce").to_numpy()
        spd_all = (pd.to_numeric(rows["speed"], errors="coerce").to_numpy()
                   if "speed" in rows.columns else None)
        ts_all = pd.to_numeric(rows["ts"], errors="coerce").to_numpy()

        # Valid GPS samples. In ALL mode we restrict to the export window
        # (Start/End) so the map covers what is being rendered; in WINDOW mode we
        # keep every sample so a window centred on the car can look either side
        # of the export boundary.
        good = ~(np.isnan(lat_all) | np.isnan(lon_all))
        if not segment_secs:
            good &= (ts_all >= t_start) & (ts_all <= t_end)
        lat_all, lon_all, ts_all = lat_all[good], lon_all[good], ts_all[good]
        spd_all = spd_all[good] if spd_all is not None else None
        if len(lat_all) < 2:
            raise ValueError("Not enough valid GPS points in the selected time range.")

        rng = (float(np.nanmin(spd_all)) if spd_all is not None else 50.0,
               float(np.nanmax(spd_all)) if spd_all is not None else 200.0)
        min_spd, max_spd = (rng[0], rng[1] if rng[1] > rng[0] else rng[0] + 1)

        pad = int(min(w, h) * 0.10)
        project = _make_projector(lat_all, lon_all, w, h, pad)
        all_pts = project(lat_all, lon_all)
        _wcosl = _wscale = _wdist = _wwin = None
        if segment_secs:
            _wcosl, _wscale, _wdist, _wwin = _window_geometry(
                lat_all, lon_all, ts_all, spd_all, segment_secs, w, h, pad)

        # ── Base layer: full session path in solid dark (the "ghost" outline) ─
        if transparent:
            base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        else:
            base = Image.new("RGBA", (w, h), chroma + (255,))
        bd = ImageDraw.Draw(base)
        # ALL mode: the COMPLETE track for the selected range is painted once
        # into the base layer, so every frame is just base + moving dot. Drawing
        # only the current lap (as before) left the outline unfinished whenever
        # that lap was an out-lap or partial.
        _line_w = max(4, int(min(w, h) * 0.018))
        _bord0 = max(2, int(min(w, h) * 0.006))
        if not segment_secs:
            _ti = _thin_idx(all_pts, max(2.0, _line_w * 0.6))
            _col_at = (lambda i: tuple(_speed_colour_fn(spd_all[i], min_spd, max_spd)) + (255,)) \
                if (speed_colour and spd_all is not None) else (lambda i: tuple(line_colour) + (255,))
            _draw_chain(bd, all_pts, _ti, _line_w + 2 * _bord0, lambda i: (0, 0, 0, 255))
            _draw_chain(bd, all_pts, _ti, _line_w, _col_at)

        # ── Frame grid at the (low) track-map fps ────────────────────────────
        _fps = map_fps if map_fps and map_fps > 0 else fps
        n_frames = max(1, int(round((t_end - t_start) * _fps)))
        frame_ts = t_start + np.arange(n_frames) / _fps

        line_w = max(4, int(min(w, h) * 0.018))
        dot_r = max(6, int(min(w, h) * 0.022))

        def _current_lap_bounds(tnow):
            """Return (lap_start, lap_end) for the lap containing tnow."""
            if laps:
                for (st, et, _lt) in laps:
                    if st <= tnow <= et:
                        return st, et
                if tnow < laps[0][0]:
                    return laps[0][0], laps[0][1]
                return laps[-1][0], laps[-1][1]
            return ts_all[0], ts_all[-1]

        ffmpeg = _get_ffmpeg()
        pix_fmt = "rgba" if transparent else "rgb24"
        vcodec = ["-c:v", "qtrle"] if transparent else ["-c:v", "libx264",
                                                         "-pix_fmt", "yuv420p",
                                                         "-crf", "18"]
        cmd = [ffmpeg, "-y",
               "-f", "rawvideo", "-pixel_format", pix_fmt,
               "-video_size", f"{w}x{h}", "-framerate", str(_fps),
               "-i", "-"] + vcodec + [output_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for fi in range(n_frames):
            if cancel_check and cancel_check():
                proc.stdin.close(); proc.wait()
                return
            tnow = frame_ts[fi]
            frame = base.copy()
            d = ImageDraw.Draw(frame)

            _bord = max(2, int(min(w, h) * 0.006))
            if segment_secs:
                # WINDOW mode: ONE fixed scale for the whole render and the car
                # pinned to the centre of the frame, so the track slides past a
                # stationary dot instead of the view zooming and cropping.
                _a, _b = _window_slice(_wdist,
                                       float(np.interp(tnow, ts_all, _wdist)),
                                       _wwin)
                px, py = w / 2.0, h / 2.0
                if _b - _a >= 2:
                    _lc = float(np.interp(tnow, ts_all, lat_all))
                    _oc = float(np.interp(tnow, ts_all, lon_all))
                    _pts = _centred_project(lat_all[_a:_b], lon_all[_a:_b],
                                            _lc, _oc, _wcosl, _wscale, w, h)
                    _wi = _thin_idx(_pts, max(2.0, line_w * 0.6))
                    _cw = (lambda i: tuple(_speed_colour_fn(spd_all[_a + i], min_spd, max_spd)) + (255,)) \
                        if (speed_colour and spd_all is not None) else (lambda i: tuple(line_colour) + (255,))
                    _draw_chain(d, _pts, _wi, line_w + 2 * _bord, lambda i: (0, 0, 0, 255))
                    _draw_chain(d, _pts, _wi, line_w, _cw)
            else:
                # ALL mode: track already painted into the base layer above.
                cur_i = int(np.clip(np.searchsorted(ts_all, tnow), 0, len(all_pts) - 1))
                px, py = all_pts[cur_i]

            # Moving dot at the current position: black ring, RED dot, white outline
            d.ellipse([px - dot_r - _bord, py - dot_r - _bord,
                       px + dot_r + _bord, py + dot_r + _bord], fill=(0, 0, 0, 255))
            d.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r],
                      fill=(230, 30, 30, 255), outline=(255, 255, 255, 255),
                      width=max(2, dot_r // 3))

            if transparent:
                proc.stdin.write(frame.tobytes())
            else:
                proc.stdin.write(frame.convert("RGB").tobytes())
            if progress_cb and fi % 4 == 0:
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


def render_preview_frame(rows, t_now, resolution=(480, 480),
                         chroma=(255, 0, 255), transparent=False,
                         speed_colour=True, line_colour=(235, 235, 245),
                         segment_secs=None, t_start=None, t_end=None, laps=None):
    """Return a single PIL RGBA image of the track map at time t_now, for the
    in-app preview. Mirrors render_video's look without invoking ffmpeg. When
    `segment_secs` is set, a window centred on the car is drawn; otherwise the
    whole track within [t_start, t_end] is drawn."""
    from PIL import Image, ImageDraw
    import pandas as pd
    w, h = resolution
    if "lat" not in rows.columns or "lon" not in rows.columns:
        raise ValueError("Track map needs 'lat' and 'lon' columns.")
    lat = pd.to_numeric(rows["lat"], errors="coerce").to_numpy()
    lon = pd.to_numeric(rows["lon"], errors="coerce").to_numpy()
    spd = (pd.to_numeric(rows["speed"], errors="coerce").to_numpy()
           if "speed" in rows.columns else None)
    ts = pd.to_numeric(rows["ts"], errors="coerce").to_numpy()
    good = ~(np.isnan(lat) | np.isnan(lon))
    if not segment_secs:            # window mode may look either side of the range
        if t_start is not None:
            good &= (ts >= t_start)
        if t_end is not None:
            good &= (ts <= t_end)
    lat, lon, ts = lat[good], lon[good], ts[good]
    spd = spd[good] if spd is not None else None
    if len(lat) < 2:
        raise ValueError("Not enough valid GPS points in the selected time range.")
    min_spd = float(np.nanmin(spd)) if spd is not None else 50.0
    max_spd = float(np.nanmax(spd)) if spd is not None else 200.0
    if max_spd <= min_spd:
        max_spd = min_spd + 1

    pad = int(min(w, h) * 0.10)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0) if transparent else chroma + (255,))
    d = ImageDraw.Draw(img)
    line_w = max(4, int(min(w, h) * 0.018))
    _bord = max(2, int(min(w, h) * 0.006))
    dot_r = max(6, int(min(w, h) * 0.03))

    if segment_secs:
        # WINDOW mode: one fixed scale, car pinned to the centre of the frame -
        # the track moves, the dot does not. The window is a length of track (see
        # _window_geometry), so the picture stays a consistent size.
        _cosl, _scale, _dist, _win_m = _window_geometry(
            lat, lon, ts, spd, segment_secs, w, h, pad)
        _a, _b = _window_slice(_dist, float(np.interp(t_now, ts, _dist)), _win_m)
        px, py = w / 2.0, h / 2.0
        if _b - _a >= 2:
            _lc = float(np.interp(t_now, ts, lat))
            _oc = float(np.interp(t_now, ts, lon))
            _pts = _centred_project(lat[_a:_b], lon[_a:_b], _lc, _oc, _cosl, _scale, w, h)
            _wi = _thin_idx(_pts, max(2.0, line_w * 0.6))
            _cw = (lambda i: tuple(_speed_colour_fn(spd[_a + i], min_spd, max_spd)) + (255,)) \
                if (speed_colour and spd is not None) else (lambda i: tuple(line_colour) + (255,))
            _draw_chain(d, _pts, _wi, line_w + 2 * _bord, lambda i: (0, 0, 0, 255))
            _draw_chain(d, _pts, _wi, line_w, _cw)
    else:
        # ALL mode: dark "ghost" outline of every lap in range, with the WHOLE of
        # the CURRENT lap drawn in speed colour (not revealed by lap progress).
        project = _make_projector(lat, lon, w, h, pad)
        pts = project(lat, lon)
        # ALL mode draws the COMPLETE track for the selected time range. Using
        # only the current lap left the outline unfinished whenever that lap was
        # an out-lap or partial (which is what "full track not showing" was).
        sidx = _thin_idx(pts, max(2.0, line_w * 0.6))
        if len(sidx) >= 2:
            _ca = (lambda i: tuple(_speed_colour_fn(spd[i], min_spd, max_spd)) + (255,)) \
                if (speed_colour and spd is not None) else (lambda i: tuple(line_colour) + (255,))
            _draw_chain(d, pts, sidx, line_w + 2 * _bord, lambda i: (0, 0, 0, 255))
            _draw_chain(d, pts, sidx, line_w, _ca)
        idx = int(np.clip(np.searchsorted(ts, t_now), 0, len(pts) - 1))
        px, py = pts[idx]

    d.ellipse([px - dot_r - _bord, py - dot_r - _bord,
               px + dot_r + _bord, py + dot_r + _bord], fill=(0, 0, 0, 255))
    d.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r],
              fill=(230, 30, 30, 255), outline=(255, 255, 255, 255),
              width=max(2, dot_r // 3))
    return img


def _get_ffmpeg():
    """Locate ffmpeg (PATH, or bundled next to this file)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in [os.path.join(here, "ffmpeg.exe"),
                 os.path.join(here, "ffmpeg"), "ffmpeg"]:
        if cand == "ffmpeg" or os.path.exists(cand):
            return cand
    return "ffmpeg"
