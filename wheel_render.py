"""
wheel_render.py — standalone animated steering-wheel overlay for LapStudio.

Most cars have no steering-angle sensor, so the wheel angle is ESTIMATED from
lateral G and speed using a bicycle-model approximation:

    steering_angle  ∝  lateral_G / speed²        (curvature, ∝ 1/radius)

This is the physically sensible link: a slow tight corner (low speed, modest G)
shows lots of lock, while a fast sweeper (high speed, high G) shows only a little
lock — which is how a real driver's hands move. The estimate is normalised to a
reference cornering case, clamped to ±MAX_DEG, and smoothed over time so the
wheel turns fluidly rather than twitching with sensor noise.

It is a visual aid, not a measurement: the direction and the relative amount of
lock corner-to-corner are right, but it won't be frame-accurate to the real
steering input.

Produced as a SEPARATE chroma-key / transparent video, like the track map and
G-trace overlays, so it can be positioned anywhere over the footage.
"""

import numpy as np
import math
import os
import subprocess


# Wheel travel limit from centre (degrees). Real lock-to-lock is larger, but a
# GT/formula wheel rim rarely passes ±120° in a driver's hands on track.
MAX_DEG = 120.0

# Speed floor (m/s) so the G/speed² term doesn't explode at a standstill.
# ~8 m/s ≈ 29 km/h; below this the model is unreliable so we clamp the divisor.
V_FLOOR = 8.0


# ── Selectable wheel images ───────────────────────────────────────────────────
# The user picks one of these in the app. Each is a transparent, centred, square
# PNG that rotates about the hub. Add more wheels by dropping another such PNG in
# the assets folder and adding an entry here. If the chosen file can't be found,
# the renderer falls back to the built-in vector wheel (_draw_wheel) so the
# overlay always works.
WHEELS = {
    "formula": {"label": "Formula (carbon)", "file": "wheel_formula.png"},
    "momo":    {"label": "MOMO (suede)",     "file": "wheel_momo.png"},
}
DEFAULT_WHEEL = "formula"


def wheel_choices():
    """Return [(key, label), ...] for populating the app's wheel dropdown."""
    return [(k, v["label"]) for k, v in WHEELS.items()]


def _wheel_asset_path(wheel_style):
    """Resolve the PNG path for a wheel style, or None if not found.

    Looks in an 'assets' subfolder next to this module first, then alongside
    the module itself.
    """
    spec = WHEELS.get(wheel_style)
    if not spec:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "assets", spec["file"]),
                 os.path.join(here, spec["file"])):
        if os.path.exists(cand):
            return cand
    return None


def _load_wheel_image(size, wheel_style):
    """Return the chosen wheel as a centred, square RGBA image of side `size`.

    Loads the selected PNG (already transparent + centred) and scales it to the
    output box. If the asset is missing or fails to load, falls back to the
    built-in vector wheel so the overlay still renders.
    """
    from PIL import Image
    path = _wheel_asset_path(wheel_style)
    if path:
        try:
            img = Image.open(path).convert("RGBA")
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            return img
        except Exception:
            pass  # fall through to the vector wheel
    return _draw_wheel(size)


def _get_ffmpeg():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in [os.path.join(here, "ffmpeg.exe"),
                 os.path.join(here, "ffmpeg"), "ffmpeg"]:
        if cand == "ffmpeg" or os.path.exists(cand):
            return cand
    return "ffmpeg"


def estimate_wheel_angles(g_lat, speed_kmh, sensitivity=1.0,
                          smooth_secs=0.08, fps=25.0):
    """Convert lateral-G + speed into a smoothed wheel angle (degrees) per sample.

    Positive angle = turning right (positive lateral G), negative = left.
    `sensitivity` scales how much lock is shown (1.0 = default calibration).
    """
    g = np.asarray(g_lat, dtype=float)
    v_kmh = np.asarray(speed_kmh, dtype=float)
    v = np.clip(v_kmh, 0, None) / 3.6           # km/h → m/s
    v_eff = np.maximum(v, V_FLOOR)

    # Curvature proxy: a_lat = v²/R  →  1/R = a_lat / v².  Steering angle ∝ 1/R.
    a_lat = g * 9.81
    curvature = a_lat / (v_eff ** 2)            # rad/m, signed

    # Normalise to a reference corner so the calibration is intuitive:
    # ~1.0 G at ~45 km/h (12.5 m/s) ≈ full lock (±MAX_DEG) at sensitivity 1.0.
    ref_curv = (1.0 * 9.81) / (12.5 ** 2)
    norm = (curvature / ref_curv) * sensitivity
    angle = np.clip(norm, -1.0, 1.0) * MAX_DEG

    # Exponential smoothing so the rim turns fluidly (kills sensor jitter).
    if smooth_secs and smooth_secs > 0 and len(angle) > 1:
        alpha = 1.0 - math.exp(-1.0 / max(1e-6, smooth_secs * fps))
        out = np.empty_like(angle)
        out[0] = angle[0] if np.isfinite(angle[0]) else 0.0
        for i in range(1, len(angle)):
            a = angle[i] if np.isfinite(angle[i]) else out[i - 1]
            out[i] = out[i - 1] + alpha * (a - out[i - 1])
        angle = out
    return angle


def _draw_wheel(size, chroma=(255, 0, 255), transparent=False):
    """Build the static (un-rotated) flat-bottomed GT wheel as an RGBA image,
    centred, facing straight ahead. Returns the image; rotation is applied per
    frame. A small reference mark at top-centre stays on the static frame? No —
    the marker rotates WITH the wheel, so it's drawn here."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2.0
    R = size * 0.42                      # outer rim radius
    rim_w = max(4, int(size * 0.055))    # rim thickness
    inner = R - rim_w                    # inner rim edge

    RIM = (235, 235, 240, 255)
    RIM_DK = (120, 120, 130, 255)
    SPOKE = (60, 62, 70, 255)
    HUB = (40, 42, 50, 255)
    MARK = (220, 40, 40, 255)            # top-centre reference stripe

    # Flat-bottomed rim: full circle on top, chord cut across the bottom.
    flat_y = cy + R * 0.62               # height of the flat bottom
    # Outer rim as a thick arc-ish ring with a flat bottom: draw a filled
    # rounded shape by compositing a full ring then squaring off the base.
    # 1) full ring
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=RIM, width=rim_w)
    # subtle inner shadow ring for depth
    d.ellipse([cx - R + rim_w//2, cy - R + rim_w//2,
               cx + R - rim_w//2, cy + R - rim_w//2],
              outline=RIM_DK, width=max(1, rim_w // 4))

    # 2) flat bottom: draw a horizontal rim bar across the chord
    import math as _m
    # chord half-width where the circle meets flat_y
    dy = flat_y - cy
    if abs(dy) < R:
        half_chord = _m.sqrt(max(0.0, R * R - dy * dy))
        # erase the curved bottom below flat_y by painting transparent, then
        # lay a straight rim segment along the chord
        d.rectangle([cx - R - rim_w, flat_y, cx + R + rim_w, cy + R + rim_w],
                    fill=(0, 0, 0, 0))
        d.line([(cx - half_chord, flat_y), (cx + half_chord, flat_y)],
               fill=RIM, width=rim_w)
        # round the two corners where flat meets the curve
        for sx in (-1, 1):
            d.ellipse([cx + sx*half_chord - rim_w//2, flat_y - rim_w//2,
                       cx + sx*half_chord + rim_w//2, flat_y + rim_w//2], fill=RIM)

    # 3) spokes — GT three-spoke: left, right (slightly down) and a thick bottom
    spoke_w = max(4, int(size * 0.05))
    hub_r = size * 0.10
    # left & right spokes angle slightly downward (classic 9-and-3, dropped)
    for ang_deg in (200, 340):           # measured from +x, going clockwise-ish
        a = math.radians(ang_deg)
        ex = cx + inner * math.cos(a)
        ey = cy - inner * math.sin(a)
        d.line([(cx, cy), (ex, ey)], fill=SPOKE, width=spoke_w)
    # bottom spoke down to the flat
    d.line([(cx, cy), (cx, flat_y)], fill=SPOKE, width=spoke_w)

    # 4) centre hub
    d.ellipse([cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r],
              fill=HUB, outline=RIM_DK, width=max(2, int(size*0.006)))

    # 5) top-centre reference stripe on the rim (turns with the wheel)
    mark_w = max(4, int(size * 0.02))
    d.rectangle([cx - mark_w//2, cy - R - rim_w//2,
                 cx + mark_w//2, cy - inner + rim_w//2], fill=MARK)

    return img


def render_video(rows, t_start, t_end, output_path, fps,
                 progress_cb=None, done_cb=None, error_cb=None, cancel_check=None,
                 resolution=(600, 600), chroma=(255, 0, 255), transparent=False,
                 sensitivity=1.0, smooth_secs=0.08, map_fps=25.0,
                 wheel_style=DEFAULT_WHEEL):
    """Render the steering-wheel overlay video.

    `rows` needs columns: ts, g_lat, speed (speed in km/h).
    The wheel angle is estimated from lateral G and speed (see module docstring).
    `wheel_style` selects which wheel image to use (see WHEELS); if the asset is
    missing, the built-in vector wheel is drawn instead.
    """
    try:
        from PIL import Image
        import pandas as pd

        w, h = resolution
        if "g_lat" not in rows.columns:
            raise ValueError("Steering wheel needs a 'g_lat' (lateral G) column.")
        ts = pd.to_numeric(rows["ts"], errors="coerce").to_numpy()
        glat = pd.to_numeric(rows["g_lat"], errors="coerce").to_numpy()
        spd = (pd.to_numeric(rows["speed"], errors="coerce").to_numpy()
               if "speed" in rows.columns else np.full(len(ts), 100.0))

        _fps = map_fps if map_fps and map_fps > 0 else fps

        # Pre-compute the smoothed wheel angle for every data sample.
        angles = estimate_wheel_angles(glat, spd, sensitivity=sensitivity,
                                       smooth_secs=smooth_secs, fps=_fps)

        size = min(w, h)
        wheel = _load_wheel_image(size, wheel_style)

        n_frames = max(1, int(round((t_end - t_start) * _fps)))
        frame_ts = t_start + np.arange(n_frames) / _fps

        ffmpeg = _get_ffmpeg()
        pix_fmt = "rgba" if transparent else "rgb24"
        vcodec = (["-c:v", "qtrle"] if transparent
                  else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23"])
        cmd = [ffmpeg, "-y", "-f", "rawvideo", "-pixel_format", pix_fmt,
               "-video_size", f"{w}x{h}", "-framerate", str(_fps),
               "-i", "-"] + vcodec + [output_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for fi in range(n_frames):
            if cancel_check and cancel_check():
                proc.stdin.close(); proc.wait(); return
            tnow = frame_ts[fi]
            idx = int(np.clip(np.searchsorted(ts, tnow), 0, len(angles) - 1))
            ang = float(angles[idx]) if np.isfinite(angles[idx]) else 0.0

            # Background frame (chroma or transparent), wheel rotated and centred.
            if transparent:
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            else:
                frame = Image.new("RGBA", (w, h), chroma + (255,))
            # PIL rotate is counter-clockwise positive; we want +angle = turn
            # RIGHT (clockwise), so negate.
            rot = wheel.rotate(-ang, resample=Image.BICUBIC, expand=False)
            frame.alpha_composite(rot, ((w - size) // 2, (h - size) // 2))

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
