"""Verify LapStudio readouts are stationary when only the VALUES change.

Two levels:
  1. engine  - digits must land on identical pixels for equal-length values, and
               on identical pixels for different-length values when the field is
               reserved with `slots`.
  2. dashes  - the frame's overall ink extent must not change with the values.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import renderer_pil as R
from textgrid import _gtext, _vmask, _gwidth

FONTS = [("BigShoulders-Bold.ttf", 90), ("Poppins-Bold.ttf", 70)]


def ink_bbox(img):
    a = np.asarray(img.convert("L")).astype(int)
    ys, xs = np.nonzero(a > 30)
    return (None if len(xs) == 0 else (xs.min(), ys.min(), xs.max(), ys.max()))


def draw(text, path, size, slots=None, anchor="mm"):
    f = ImageFont.truetype(path, size)
    im = Image.new("L", (900, 220), 0)
    _gtext(ImageDraw.Draw(im), (450, 110), text, f, 255, anchor=anchor,
           vref=_vmask(text), slots=slots)
    return im


print("=" * 74)
print("1. ENGINE - each digit cell must sit at the same pixels for any value")
print("=" * 74)


def cell_centres(img):
    """Centre-x of each ink blob (i.e. each rendered digit) in the image."""
    a = np.asarray(img).astype(int) > 30
    cols = a.any(0)
    runs, start = [], None
    for x, on in enumerate(cols):
        if on and start is None:
            start = x
        elif not on and start is not None:
            runs.append((start + x - 1) / 2.0); start = None
    if start is not None:
        runs.append((start + len(cols) - 1) / 2.0)
    return runs


ok = True
for path, size in FONTS:
    # Compare THE SAME glyph in the same slot across different values: any shift
    # is then a genuine layout shift, not a difference in glyph shape.
    a = cell_centres(draw("155", path, size))[1:]     # the two 5s
    b = cell_centres(draw("955", path, size))[1:]
    m1 = all(abs(x - y) < 0.51 for x, y in zip(a, b))
    c = cell_centres(draw("551", path, size))[:2]     # the two 5s
    e = cell_centres(draw("559", path, size))[:2]
    m2 = all(abs(x - y) < 0.51 for x, y in zip(c, e))
    # digit-count change, field reserved, RIGHT anchored (tables/columns):
    # pinned, must not move at all
    f = cell_centres(draw("55", path, size, slots="888", anchor="rm"))
    g = cell_centres(draw("155", path, size, slots="888", anchor="rm"))[1:]
    m3 = len(f) == 2 and all(abs(x - y) < 0.51 for x, y in zip(f, g))
    # digit-count change, CENTRED (gauges): steps by exactly half a cell, no more
    from textgrid import _digit_pitch
    from PIL import ImageFont
    pitch = _digit_pitch(ImageFont.truetype(path, size))
    h2 = cell_centres(draw("55", path, size, slots="888"))
    i2 = cell_centres(draw("155", path, size, slots="888"))[1:]
    step = abs(h2[0] - i2[0])
    m4 = abs(step - pitch / 2.0) < 1.0
    ok &= m1 and m2 and m3 and m4
    print(f"  {path:24s} neighbour digit changed:  {'FIXED' if m1 else 'MOVED'}  {a} / {b}")
    print(f"  {'':24s} trailing digit changed:   {'FIXED' if m2 else 'MOVED'}  {c} / {e}")
    print(f"  {'':24s} reserved + right anchor:  {'PINNED' if m3 else 'MOVED'}  {f} / {g}")
    print(f"  {'':24s} centred digit-count step: {step:.1f}px, expected half a "
          f"cell = {pitch/2:.1f}px  {'OK' if m4 else 'WRONG'}")
    print()

print()
print("=" * 74)
print("2. DASHES - frame ink extent must not move with the values")
print("=" * 74)


def frame(P, rpm, speed, gear, ts, w, h):
    n = 300
    L = lambda v: [float(v)] * n
    return R.build_frame(
        rpm=rpm, throttle=42.0, speed=speed, gear=gear, g_lat=0.8, g_long=-0.4,
        ts=ts, trace_glat=L(0.8), trace_glong_raw=L(-0.4),
        laps=[(0.0, 95.0, 95.0), (95.0, 190.0, 95.0), (190.0, 300.0, None)],
        gauge_bg=None, cx=w // 2, cy=h // 2, radius=int(min(w, h) * 0.42),
        w=w, h=h, scale=h / 750.0, brake_pct=12.0, rpm_max=9000, peak_rpm=8850,
        trace_speed=L(speed), speed_colour=True, P=P, trace_throttle=L(42.0),
        trace_brake=L(12.0), trace_gear=L(gear), chanA=98.0, chanA_label="Oil T",
        chanB=995.0, chanB_label="EGT", chanA_unit="C", chanB_unit="C",
        speed_max=220.0)


print(f"{'dash':30s} {'shape 111->888':>16s} {'count 99->100':>16s}")
for name in [k for k in R.STYLES if k.startswith("Dash")]:
    P = R.STYLES[name]
    w, h = ((2560, 750) if "Dash 10" in name else
            (1920, 1080) if (P.get("style7_layout") or P.get("style8_layout")) else
            (1920, 750))
    res = []
    for a, b in ((( 7111, 111, 1, 111.0), (7888, 188, 8, 118.0)),
                 (( 9999,  99, 9,  99.0), (10000, 100, 1, 100.0))):
        try:
            b1 = ink_bbox(frame(P, *a, w, h))
            b2 = ink_bbox(frame(P, *b, w, h))
            res.append("same" if b1 == b2 else f"{b1} != {b2}")
        except Exception as e:
            res.append(f"ERR {type(e).__name__}: {e}"[:40])
    print(f"{name:30s} {res[0]:>16s} {res[1]:>16s}")
print()
print("engine checks passed:", ok)
