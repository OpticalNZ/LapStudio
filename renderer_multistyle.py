"""
renderer_multistyle.py — Multi-style mosaic renderer.

All 8 styles (1-7 + 9) in a 4×2 grid at 960×1080 per cell = 3840×2160.
Strip styles are letterboxed with chroma (not black) in the empty area.
Each style renders at its natural dimensions then is composited into its cell.
"""

from PIL import Image, ImageDraw
import numpy as np
import subprocess, os, math
from collections import deque
import concurrent.futures as cf
import renderer_pil as R

# ── CONFIG ────────────────────────────────────────────────────────────────────
MOSAIC_W  = 3840
MOSAIC_H  = 2160
GRID_COLS = 4
GRID_ROWS = 2
CELL_W    = MOSAIC_W // GRID_COLS   # 960
CELL_H    = MOSAIC_H // GRID_ROWS   # 1080

STYLE_ORDER = [
    'Style 1', 'Style 2',
    'Style 3', 'Style 4', 'Style 5', 'Style 6',
    'Style 7', 'Style 8', 'Style 9', 'Style 10', 'Style 11',
]

# Full-frame styles fill the cell; strip styles are letterboxed with chroma
_STRIP_STYLES     = {"Style 1","Style 3","Style 6","Style 2","Style 4","Style 5"}
_FULLFRAME_STYLES = {"Style 7","Style 8"}


def _natural_dims(style_name):
    """Natural render dimensions for a style."""
    if style_name in _FULLFRAME_STYLES:
        return CELL_W, CELL_H       # 960×1080
    else:
        # Strip styles — 1920×750 scaled to 960 wide
        return CELL_W, int(CELL_W * R.H_VID / R.W_VID)


# ── GAUGE BACKGROUNDS ─────────────────────────────────────────────────────────
_GAUGE_BG = {}   # keyed by (style_name, rpm_max)


def _build_all_gauge_bgs(rpm_max):
    for sname in STYLE_ORDER:
        key = (sname, rpm_max)
        if key in _GAUGE_BG:
            continue
        P = R.STYLES[sname]
        w, h = _natural_dims(sname)
        s = w / R.W_VID
        GS = int(460 * s); GX = int(10 * s)
        cx = GX + GS // 2; cy = h // 2
        r  = GS // 2 - int(12 * s)
        # Clear the shared cache so each style gets its own bg
        R._GAUGE_BG_CACHE.clear()
        bg = R._build_gauge_bg(cx, cy, r, w=w, h=h, rpm_max=rpm_max, P=P)
        _GAUGE_BG[key] = {'bg': bg, 'cx': cx, 'cy': cy, 'r': r, 'w': w, 'h': h, 's': s}
    # Restore cache for single-style renders
    R._GAUGE_BG_CACHE.clear()


# ── TELEMETRY ─────────────────────────────────────────────────────────────────
class FrameState:
    __slots__ = ('ts','rpm','speed','throttle','brake','gear',
                 'g_lat','g_long','trace_glat','trace_glong','trace_speed',
                 'peak_rpm')
    def __init__(self):
        for a in self.__slots__: setattr(self, a, None)


def _extract_frame(rows_np, idx, ts_arr, trail_mask):
    fs = FrameState()
    fs.ts       = float(ts_arr[idx])
    fs.rpm      = float(rows_np['rpm'][idx])
    fs.speed    = float(rows_np['speed'][idx])
    fs.throttle = float(rows_np['throttle'][idx])
    fs.brake    = float(rows_np['brake'][idx])
    fs.gear     = int(rows_np['gear'][idx])
    fs.g_lat    = float(rows_np['g_lat'][idx])
    fs.g_long   = float(rows_np['g_long'][idx])
    fs.trace_glat   = rows_np['g_lat'][trail_mask].tolist()
    fs.trace_glong  = rows_np['g_long'][trail_mask].tolist()
    fs.trace_speed  = rows_np['speed'][trail_mask].tolist()
    # Rolling 1s peak RPM
    peak_mask = (ts_arr >= fs.ts - 1.5) & (ts_arr <= fs.ts)
    fs.peak_rpm = int(rows_np['rpm'][peak_mask].max()) if peak_mask.any() else None
    return fs


# ── CELL RENDERER ─────────────────────────────────────────────────────────────
def _render_cell(sname, fs, laps, rpm_max, speed_colour, g_trail_secs, speed_max=None):
    """Render one style at its natural size, return a CELL_W × CELL_H image."""
    P    = R.STYLES[sname]
    key  = (sname, rpm_max)
    info = _GAUGE_BG.get(key)
    if info is None:
        _build_all_gauge_bgs(rpm_max)
        info = _GAUGE_BG[key]

    w, h, s = info['w'], info['h'], info['s']
    cx, cy, r = info['cx'], info['cy'], info['r']

    img = R.build_frame(
        fs.rpm, fs.throttle, fs.speed, fs.gear,
        fs.g_lat, fs.g_long, fs.ts,
        fs.trace_glat, fs.trace_glong,
        laps, info['bg'], cx, cy, r,
        w=w, h=h, scale=s,
        brake_pct=fs.brake, rpm_max=rpm_max,
        peak_rpm=fs.peak_rpm,
        trace_speed=fs.trace_speed if speed_colour else None,
        speed_colour=speed_colour, speed_max=speed_max, P=P,
    )

    # Fit into cell — letterbox strip styles with chroma, not black
    if img.size == (CELL_W, CELL_H):
        return img

    chroma = P.get('chroma', (255, 0, 255))
    cell = Image.new('RGB', (CELL_W, CELL_H), chroma)
    y_off = (CELL_H - img.size[1]) // 2
    cell.paste(img, (0, y_off))
    return cell


# ── MOSAIC COMPOSITOR ─────────────────────────────────────────────────────────
def _composite_mosaic(cells):
    mosaic = Image.new('RGB', (MOSAIC_W, MOSAIC_H), (0, 0, 0))
    for i, cell in enumerate(cells):
        col = i % GRID_COLS
        row = i // GRID_COLS
        mosaic.paste(cell, (col * CELL_W, row * CELL_H))
    return mosaic


# ── MAIN RENDER ───────────────────────────────────────────────────────────────
def render_multistyle_video(
    rows_df, t_start, t_end, output_path, fps,
    laps,
    progress_cb=None, done_cb=None, error_cb=None,
    cancel_check=None, g_trail_secs=5.0, speed_colour=True,
    styles=None, delta_ref=None,
):
    try:
        active_styles = styles or STYLE_ORDER

        seg = rows_df[(rows_df['ts'] >= t_start) & (rows_df['ts'] <= t_end)].copy()
        if len(seg) < 2:
            if error_cb: error_cb("Not enough data in time range."); return

        ts_arr  = seg['ts'].to_numpy(np.float64)
        rows_np = {c: seg[c].to_numpy(np.float32)
                   for c in ['rpm','speed','throttle','brake','g_lat','g_long']}
        rows_np['gear'] = seg['gear'].to_numpy(np.int32)

        # Interpolate to output fps if needed
        data_dt = float(np.median(np.diff(ts_arr)))
        data_fps = 1.0 / data_dt if data_dt > 0 else fps
        if abs(fps - data_fps) > 0.5:
            _t0m = float(ts_arr[0]); _t1m = float(ts_arr[-1])
            _fc_m = int(round((_t1m - _t0m) * fps))
            new_ts = _t0m + np.arange(_fc_m) / fps
            new_rows = {'ts': new_ts}
            for c in ['rpm','speed','throttle','brake','g_lat','g_long']:
                new_rows[c] = np.interp(new_ts, ts_arr, rows_np[c]).astype(np.float32)
            gear_idx = np.searchsorted(ts_arr, new_ts).clip(0, len(ts_arr)-1)
            new_rows['gear'] = rows_np['gear'][gear_idx]
            ts_arr  = new_ts
            rows_np = {k: v for k,v in new_rows.items() if k != 'ts'}
            rows_np['gear'] = new_rows['gear']
            print(f"[multistyle] resampled to {fps}fps ({len(ts_arr)} frames)", flush=True)

        rpm_max = max(int(math.ceil(float(rows_np['rpm'].max()) / 1000) * 1000), 6000)
        speed_max = max(60, int(math.ceil(float(rows_np['speed'].max()) / 10.0) * 10)) if 'speed' in rows_np else 200
        _build_all_gauge_bgs(rpm_max)
        N = len(ts_arr)
        print(f"[multistyle] {N} frames, {len(active_styles)} styles, "
              f"{MOSAIC_W}x{MOSAIC_H}", flush=True)

        ff_cmd = [
            'ffmpeg', '-y',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', f'{MOSAIC_W}x{MOSAIC_H}',
            '-r', str(fps),
            '-i', '-', '-an',
            '-c:v', 'libx264', '-preset', 'fast',
            '-crf', '18', '-pix_fmt', 'yuv420p',
            output_path,
        ]
        ff = subprocess.Popen(ff_cmd, stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        N_WORKERS = min(len(active_styles), max(2, (os.cpu_count() or 4) - 1))

        def _build_mosaic_frame(frame_idx):
            t_now = float(ts_arr[frame_idx])
            trail_mask = (ts_arr >= t_now - g_trail_secs) & (ts_arr <= t_now)
            fs = _extract_frame(rows_np, frame_idx, ts_arr, trail_mask)
            cells = [_render_cell(sn, fs, laps, rpm_max, speed_colour, g_trail_secs, speed_max)
                     for sn in active_styles]
            mosaic = _composite_mosaic(cells)
            # Draw delta widget on mosaic — positioned top-right of mosaic
            if delta_ref is not None:
                from PIL import ImageDraw as _IDrw
                _dd = _IDrw.Draw(mosaic)
                _dw = int(500); _dh = int(220)
                _dx = MOSAIC_W - _dw - 40; _dy = 40
                R.draw_delta_widget(
                    _dd, mosaic,
                    _dx, _dy, _dw, _dh,
                    float(ts_arr[frame_idx]), laps, delta_ref,
                    delta_ref["full_ts"],
                    s=2.0)
            return frame_idx, mosaic.tobytes()

        with cf.ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
            pending = deque()
            submitted = 0
            LOOKAHEAD = N_WORKERS * 2

            while submitted < min(LOOKAHEAD, N):
                pending.append(pool.submit(_build_mosaic_frame, submitted))
                submitted += 1

            for frame_idx in range(N):
                if cancel_check and cancel_check():
                    ff.stdin.close(); ff.wait()
                    try: os.remove(output_path)
                    except: pass
                    if done_cb: done_cb()
                    return

                _, raw = pending.popleft().result()
                ff.stdin.write(raw)

                if submitted < N:
                    pending.append(pool.submit(_build_mosaic_frame, submitted))
                    submitted += 1

                if frame_idx % 10 == 0 and progress_cb:
                    progress_cb(int(100 * frame_idx / N))

        ff.stdin.close()
        ff.wait()
        if progress_cb: progress_cb(100)
        if done_cb: done_cb()

    except Exception as e:
        import traceback
        msg = f"{e}\n{traceback.format_exc()}"
        if error_cb: error_cb(msg)
        else: print(f"[multistyle error] {msg}", flush=True)
