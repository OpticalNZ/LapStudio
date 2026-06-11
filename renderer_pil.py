"""
Fast PIL-based ECU overlay renderer.
~10-20x faster than matplotlib version.
"""
from PIL import Image, ImageDraw, ImageFont
import aggdraw
import numpy as np, math, os, subprocess, sys

# ── Colours ───────────────────────────────────────────────────────────────────
C_CHROMA  = (255,   0, 255)
C_PANEL   = ( 14,  14,  22)
C_DARK    = (  8,   8,  14)
C_AMBER   = (255, 160,   0)
C_AMBER2  = (220,  80,   0)
C_RED     = (255,  35,  35)
C_WHITE   = (240, 240, 255)
C_GREY    = (110, 110, 140)
C_DIM     = ( 32,  26,  10)
C_DIMRED  = ( 45,   8,   8)
C_GREEN   = ( 50, 205,  80)
C_CYAN    = ( 50, 208, 255)
C_ORANGE  = (255, 130,  30)
C_BORDER  = ( 55,  55,  85)
C_SUBFACE = ( 10,  10,  16)

# ── Style palettes ────────────────────────────────────────────────────────
STYLES = {
    "Style 8": {
        "chroma":        (255,   0, 255),
        "style7_layout": True,
        "white":         (255, 255, 255),
        "dim":           (200, 200, 200),
        "amber":         (255, 200,  50),
        "green":         (100, 255, 120),
        "red":           (255,  80,  80),
        "grey":          (160, 160, 160),
        # Required by gauge functions
        "dark":          (255,   0, 255),
        "subface":       (255,   0, 255),
        "panel":         (255,   0, 255),
        "dimred":        (255,   0, 255),
        "amber2":        (255,   0, 255),
        "data":          (255, 255, 255),
        "cyan":          (255,   0, 255),
        "hex_mesh":      False,
        "burst_centre":  (255,   0, 255),
        "burst_edge":    (255,   0, 255),
        "num_colour":    (255,   0, 255),
    },
    "Style 7": {
        "chroma":        (255,   0, 255),
        "style4_layout": True,
        "white":         (255, 255, 255),
        "dim":           (200, 200, 200),
        "amber":         (255, 200,  50),
        "green":         (100, 255, 120),
        "red":           (255,  80,  80),
        "grey":          (160, 160, 160),
        # Required by gauge functions (unused visually — all chroma green)
        "dark":          (255,   0, 255),
        "subface":       (255,   0, 255),
        "panel":         (255,   0, 255),
        "dimred":        (255,   0, 255),
        "amber2":        (255,   0, 255),
        "data":          (255, 255, 255),
        "cyan":          (255,   0, 255),
        "hex_mesh":      False,
        "burst_centre":  (255,   0, 255),
        "burst_edge":    (255,   0, 255),
        "num_colour":    (255,   0, 255),
    },
    "Style 6": {  # AIM SmartyCam — white faces, blue borders, red needles
        "chroma":       (255,   0, 255),
        "panel":        (245, 245, 248),   # light grey background
        "dark":         (255, 255, 255),   # gauge face white
        "subface":      (230, 232, 238),   # sub-face light grey
        "border":       ( 40,  80, 160),   # medium blue borders
        "white":        ( 25,  50, 130),   # blue labels
        "data":         (  0,   0,   0),   # black data values
        "grey":         ( 80, 100, 160),   # muted blue secondary
        "gridlo":       (190, 195, 210),   # faint grid
        "gridhi":       (130, 140, 175),   # grid lines
        "glabel":       ( 40,  60, 140),   # G axis labels
        "dim":          (195, 195, 200),   # unlit segs
        "dimred":       (210, 185, 185),   # unlit red segs
        "amber":        (200, 100,   0),   # amber segs
        "amber2":       (180,  50,   0),
        "red":          (210,  20,  20),   # red needle + segs
        "green":        ( 20, 160,  50),
        "cyan":         ( 30, 100, 220),   # GG dot colour
        "accent":       ( 25,  50, 130),
        "accent_line":  ( 40,  80, 160),
        "emboss_dark":  (100, 120, 180),   # bevel shadow
        "emboss_light": (255, 255, 255),   # bevel highlight
        "hex_mesh":     False,
        "burst_centre": (255, 255, 255),   # white burst centre
        "burst_edge":   (200, 205, 215),   # very slight edge shading
        "num_colour":   ( 10,  10,  10),   # near-black numbers on white face
        "style3_layout": True,
    },
    "Style 11": {  # Race dash — horizontal RPM bar, speed right, data grid bottom
        "chroma":        (255,   0, 255),
        "panel":         (  8,   8,  10),   # near-black
        "dark":          (  4,   4,   6),
        "subface":       ( 15,  15,  20),
        "border":        ( 40,  40,  55),
        "white":         (240, 240, 245),
        "data":          (240, 240, 245),
        "grey":          (130, 130, 145),
        "dim":           ( 60,  60,  70),
        "amber":         (255, 160,   0),
        "red":           (210,  20,  20),
        "green":         ( 50, 210,  80),
        "cyan":          ( 40, 170, 255),
        "rpm_bar_col":   (200,  15,  15),   # bright red RPM bar
        "rpm_bar_bg":    ( 25,  10,  10),   # dark red bg
        "dimred":        ( 40,  10,  10),
        "amber2":        (200, 100,   0),
        "hex_mesh":      False,
        "burst_centre":  (255, 255, 255),
        "burst_edge":    ( 20,  20,  30),
        "num_colour":    (200, 200, 220),
        "subface":       ( 15,  15,  20),
        "emboss_dark":   (  4,   4,   6),
        "emboss_light":  ( 50,  50,  60),
        "style11_layout": True,
    },
    "Style 12": {  # Coloured-box race dash — gear centre, lap left, data right, g-trace
        "chroma":        (255,   0, 255),
        "panel":         (  8,   8,  12),
        "dark":          (  8,   8,  12),
        "subface":       (  8,   8,  12),
        "white":         (240, 240, 245),
        "data":          (240, 240, 245),
        "grey":          (140, 140, 150),
        "dim":           ( 40,  40,  40),
        "dimred":        ( 30,  10,  10),
        "amber":         (255, 160,   0),
        "amber2":        (200, 100,   0),
        "green":         ( 60, 200,  80),
        "red":           (220,  60,  60),
        "cyan":          ( 80, 200, 255),
        "gold":          (255, 200,  60),
        "purple":        (160,  80, 255),
        "blue":          ( 80, 160, 255),
        "border":        ( 50,  60,  80),
        "hex_mesh":      False,
        "burst_centre":  (255,   0, 255),
        "burst_edge":    (255,   0, 255),
        "num_colour":    (255,   0, 255),
        "style12_layout": True,
    },
    "Style 13": {  # Style 12 variant — slim TPS/Brake bars, g-trace right
        "chroma":        (255,   0, 255),
        "panel":         (  8,   8,  12),
        "dark":          (  8,   8,  12),
        "subface":       (  8,   8,  12),
        "white":         (240, 240, 245),
        "data":          (240, 240, 245),
        "grey":          (140, 140, 150),
        "dim":           ( 40,  40,  40),
        "dimred":        ( 30,  10,  10),
        "amber":         (255, 160,   0),
        "amber2":        (200, 100,   0),
        "green":         ( 60, 200,  80),
        "red":           (220,  60,  60),
        "cyan":          ( 80, 200, 255),
        "gold":          (255, 200,  60),
        "purple":        (160,  80, 255),
        "blue":          ( 80, 160, 255),
        "border":        ( 50,  60,  80),
        "hex_mesh":      False,
        "burst_centre":  (255,   0, 255),
        "burst_edge":    (255,   0, 255),
        "num_colour":    (255,   0, 255),
        "style13_layout": True,
    },
    "Style 14": {  # Time-trail data panel — stacked labels + time plots
        "chroma":        (255,   0, 255),
        "panel":         (  8,   8,  12),
        "dark":          (  8,   8,  12),
        "subface":       (  8,   8,  12),
        "white":         (240, 240, 245),
        "data":          (240, 240, 245),
        "grey":          (120, 120, 130),
        "dim":           ( 40,  40,  40),
        "dimred":        ( 30,  10,  10),
        "amber":         (255, 160,   0),
        "amber2":        (200, 100,   0),
        "green":         ( 50, 210,  80),
        "red":           (220,  50,  50),
        "cyan":          ( 80, 200, 255),
        "gold":          (255, 200,  60),
        "purple":        (180,  80, 255),
        "blue":          ( 80, 160, 255),
        "orange":        (255, 130,  30),
        "hex_mesh":      False,
        "burst_centre":  (255,   0, 255),
        "burst_edge":    (255,   0, 255),
        "num_colour":    (255,   0, 255),
        "border":        ( 50,  60,  80),
        "style14_layout": True,
    },
    "Style 20": {  # Dash 9 — full-width plot + bottom panel row
        "chroma":        (255,   0, 255),
        "panel":         (  8,   8,  12),
        "dark":          (  8,   8,  12),
        "subface":       (  8,   8,  12),
        "white":         (240, 240, 245),
        "data":          (240, 240, 245),
        "grey":          (120, 120, 130),
        "dim":           ( 40,  40,  40),
        "dimred":        ( 30,  10,  10),
        "amber":         (255, 160,   0),
        "amber2":        (200, 100,   0),
        "green":         ( 50, 210,  80),
        "red":           (220,  50,  50),
        "cyan":          ( 80, 200, 255),
        "gold":          (255, 200,  60),
        "purple":        (180,  80, 255),
        "blue":          ( 80, 160, 255),
        "orange":        (255, 130,  30),
        "hex_mesh":      False,
        "burst_centre":  (255,   0, 255),
        "burst_edge":    (255,   0, 255),
        "num_colour":    (255,   0, 255),
        "border":        ( 50,  60,  80),
        "style20_layout": True,
    },
    "Style 10": {  # Style 5 variant — dark/black faced gauge
        "chroma":        (255,   0, 255),
        "panel":         ( 10,  12,  20),
        "dark":          (  8,   8,  14),
        "subface":       ( 18,  22,  38),
        "border":        ( 60,  80, 130),
        "white":         (220, 220, 240),
        "data":          (220, 220, 240),
        "grey":          (110, 120, 150),
        "gridlo":        ( 30,  38,  65),
        "gridhi":        ( 50,  65, 110),
        "glabel":        (100, 130, 200),
        "dim":           (100,  90,  60),
        "dimred":        (120,  40,  40),
        "amber":         (255, 165,   0),
        "amber2":        (210,  80,   0),
        "red":           (220,  40,  40),
        "green":         ( 60, 200,  80),
        "cyan":          ( 40, 160, 255),
        "accent":        ( 80, 140, 255),
        "accent_line":   ( 60, 120, 220),
        "emboss_dark":   (  5,   5,  10),
        "emboss_light":  ( 50,  60,  90),
        "hex_mesh":      False,
        "burst_centre":  (255, 255, 255),
        "burst_edge":    ( 20,  30,  60),
        "num_colour":    (200, 210, 240),
        "gear_col":      (220,  32,  32),
        "spd_col":       (220, 220, 240),
        "spd_outline":   (  8,   8,  14),   # dark outline on light RPM/speed text
        "gear_outline":        (220, 220, 240),   # white outline on red gear number
        "speed_panel_outline": (220, 220, 240),   # white outline on speed number
        "gauge_start_ang": 225,
        "gauge_sweep":     225,
        "style9_layout": True,
        "style5_layout": True,
        "style5_transparent": True,
    },
    "Style 5": {  # Style 6 variant — arc ends at 3 o'clock, speed top-right
        "chroma":        (255,   0, 255),
        "panel":         (235, 240, 250),
        "dark":          (210, 220, 235),
        "subface":       (220, 228, 240),
        "border":        (160, 175, 210),
        "white":         ( 25,  60, 140),
        "data":          (  0,   0,   0),
        "grey":          ( 90, 115, 165),
        "gridlo":        (185, 195, 220),
        "gridhi":        (140, 160, 205),
        "glabel":        ( 80, 110, 170),
        "dim":           (210, 200, 170),
        "dimred":        (230, 200, 200),
        "amber":         (200, 120,   0),
        "amber2":        (180,  60,   0),
        "red":           (200,  20,  20),
        "green":         ( 20, 160,  50),
        "cyan":          ( 20, 120, 210),
        "accent":        ( 25,  60, 140),
        "accent_line":   ( 25,  80, 180),
        "emboss_dark":   (140, 150, 180),
        "emboss_light":  (255, 255, 255),
        "hex_mesh":      False,
        "burst_centre":  (255, 255, 255),
        "burst_edge":    ( 60,  75, 115),
        "num_colour":    ( 20,  40, 100),
        "gear_col":      (  0,   0,   0),
        "spd_col":       (  0,   0,   0),
        "spd_outline":   (255, 255, 255),
        "gauge_start_ang": 225,
        "gauge_sweep":     225,
        "style9_layout": True,
        "style5_layout": True,
        "style5_transparent": True,
    },
    "Style 15": {  # Dash 7 — trapezoid stack on gauge
        "chroma":        (255,   0, 255),
        "panel":         (252, 254, 255),
        "dark":          (230, 238, 252),
        "subface":       (242, 248, 255),
        "border":        (160, 175, 210),
        "white":         ( 25,  60, 140),
        "data":          (  0,   0,   0),
        "grey":          ( 90, 115, 165),
        "gridlo":        (185, 195, 220),
        "gridhi":        (140, 160, 205),
        "glabel":        ( 80, 110, 170),
        "dim":           (210, 200, 170),
        "dimred":        (230, 200, 200),
        "amber":         (200, 120,   0),
        "amber2":        (180,  60,   0),
        "red":           (200,  20,  20),
        "green":         ( 20, 160,  50),
        "cyan":          ( 20, 120, 210),
        "accent":        ( 25,  60, 140),
        "accent_line":   ( 25,  80, 180),
        "emboss_dark":   (140, 150, 180),
        "emboss_light":  (255, 255, 255),
        "hex_mesh":      False,
        "burst_centre":  (255, 255, 255),
        "burst_edge":    ( 60,  75, 115),
        "num_colour":    ( 20,  40, 100),
        "gear_col":      (  0,   0,   0),
        "spd_col":       (  0,   0,   0),
        "spd_outline":   (255, 255, 255),
        "gauge_start_ang": 180,
        "gauge_sweep":     180,
        "style9_layout": True,
        "style5_layout": True,
        "style15_layout": True,
        "trap_fill":     ( 20,  60, 160),   # blue trapezoid fill
        "trap_text":     (255, 255, 255),   # white text in trapezoids
    },
}

W_VID, H_VID = 1280, 500   # base resolution — scaled at render time
FPS_DEFAULT  = 30

# Resolution presets: label -> (width, height)
RESOLUTIONS = {
    "720p  (1280×500)"  : (1280,  500),
    "1080p (1920×750)"  : (1920,  750),
    "2K    (2560×1000)" : (2560, 1000),
    "4K    (3840×1500)" : (3840, 1500),
}

# Style name aliases
STYLES["Dash 1 (white gauge)"] = STYLES["Style 5"]
STYLES["Dash 2 (black gauge)"] = STYLES["Style 10"]
STYLES["Dash 3"] = STYLES["Style 11"]
STYLES["Dash 4"] = STYLES["Style 12"]
STYLES["Dash 5"] = STYLES["Style 13"]
STYLES["Dash 6 (Logger)"] = STYLES["Style 14"]
STYLES["Dash 6b (Wide Logger)"] = STYLES["Style 20"]
STYLES["Vertical Text"] = STYLES["Style 7"]
STYLES["Horizontal Text"] = STYLES["Style 8"]

# ── Dash 8 (circular telemetry gauge) ─────────────────────────────────────
STYLES["Style 16"] = {
    "chroma":        (255, 0, 255),
    "dash8_layout":  True,
    "dash8_chroma":  False,
    "amber":         (255, 160, 0),
    "green":         (50, 205, 80),
    "grey":          (110, 110, 140),
}
STYLES["Style 17"] = {
    "chroma":        (255, 0, 255),
    "dash8_layout":  True,
    "dash8_chroma":  True,
    "amber":         (255, 160, 0),
    "green":         (50, 205, 80),
    "grey":          (110, 110, 140),
}
STYLES["Dash 8"]          = STYLES["Style 16"]
STYLES["Dash 8 (Chroma)"] = STYLES["Style 17"]

# Dash 8b — variant with throttle/brake bars inside the gauge
STYLES["Style 18"] = {
    "chroma":        (255, 0, 255),
    "dash8_layout":  True,
    "dash8_chroma":  False,
    "dash8_bars_inside": True,
    "amber":         (255, 160, 0),
    "green":         (50, 205, 80),
    "grey":          (110, 110, 140),
}
STYLES["Style 19"] = {
    "chroma":        (255, 0, 255),
    "dash8_layout":  True,
    "dash8_chroma":  True,
    "dash8_bars_inside": True,
    "amber":         (255, 160, 0),
    "green":         (50, 205, 80),
    "grey":          (110, 110, 140),
}
STYLES["Dash 8b (bars inside)"]        = STYLES["Style 18"]
STYLES["Dash 8b (bars inside, Chroma)"] = STYLES["Style 19"]

# Dash 8 module + cached backgrounds (built once per render run)
try:
    import dash8_render as _dash8
except Exception:
    _dash8 = None
_dash8_bg_cache = {}

def _dash8_get_bg(chroma, rpm_max=9000):
    """Return (and cache) the Dash 8 background at native resolution."""
    key = ("chroma" if chroma else "std", int(rpm_max))
    if key not in _dash8_bg_cache:
        if chroma:
            _dash8_bg_cache[key] = _dash8.build_background_chroma(rpm_max=rpm_max)
        else:
            _dash8_bg_cache[key] = _dash8.build_background(rpm_max=rpm_max)
    return _dash8_bg_cache[key]

def _font(size, bold=True):
    paths = [
        'C:/Windows/Fonts/courbd.ttf',
        'C:/Windows/Fonts/cour.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def _tc(draw, xy, text, fnt, color):
    """Draw text centred at xy."""
    bb = draw.textbbox((0,0), text, font=fnt)
    tw, th = bb[2]-bb[0], bb[3]-bb[1]
    draw.text((xy[0]-tw//2-bb[0], xy[1]-th//2-bb[1]), text, font=fnt, fill=color)

def _chan_vstr(chan, which):
    """Format a channel's value with its optional single-char unit, e.g. '350' or '350b'.
    `which` is 'A' or 'B'. Returns '' if the value isn't usable."""
    import math as _m
    v = chan.get(f"{which}_val") if chan else None
    if v is None or (isinstance(v, float) and _m.isnan(v)):
        return ""
    u = (chan.get(f"{which}_unit") or "") if chan else ""
    return f"{int(round(v))}{u}"

def _tc_outlined(draw, xy, text, fnt, color, outline=(0,0,0), ow=2):
    """Draw text centred at xy with dark outline stroke."""
    bb = draw.textbbox((0,0), text, font=fnt)
    tw, th = bb[2]-bb[0], bb[3]-bb[1]
    # Account for the font's bbox origin (ascent/side bearing) so the glyphs
    # are truly centred — important for faces like Poppins with large bearings.
    x0 = xy[0] - tw//2 - bb[0]
    y0 = xy[1] - th//2 - bb[1]
    for dx in range(-ow, ow+1):
        for dy in range(-ow, ow+1):
            if dx or dy:
                draw.text((x0+dx, y0+dy), text, font=fnt, fill=outline)
    draw.text((x0, y0), text, font=fnt, fill=color)

def _alpha_col(base, alpha_frac):
    """Darken a colour by alpha fraction (simulate transparency over dark bg)."""
    return tuple(int(c * alpha_frac) for c in base)

# Big Shoulders wide font for Dash-8-style panel text
def _find_bsb_font():
    import os
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
    for p in [os.path.join(here,"BigShoulders-Bold.ttf"),
              "/home/claude/BigShoulders-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p): return p
    return None
_BSB_PATH = _find_bsb_font()

def _wide_panel_text(img, center, text, size, fill, max_w,
                     ow=3, outline=(0,0,0), stretch=1.5):
    """Render text in Big Shoulders, stretched horizontally, with outline,
    centred at `center`, shrunk to fit max_w. Matches the Dash 8 look."""
    from PIL import ImageFont as _IF, ImageDraw as _ID, Image as _IM
    if _BSB_PATH is None:
        return
    _sz = size
    while _sz > 8:
        fnt=_IF.truetype(_BSB_PATH,_sz)
        tmp=_IM.new("RGBA",(10,10)); td=_ID.Draw(tmp)
        bb=td.textbbox((0,0),text,font=fnt)
        if int((bb[2]-bb[0])*stretch) <= max_w: break
        _sz -= 2
    fnt=_IF.truetype(_BSB_PATH,_sz)
    tmp=_IM.new("RGBA",(10,10)); td=_ID.Draw(tmp)
    bb=td.textbbox((0,0),text,font=fnt); tw=bb[2]-bb[0]; th=bb[3]-bb[1]; pad=ow+4
    layer=_IM.new("RGBA",(tw+2*pad,th+2*pad),(0,0,0,0)); ld=_ID.Draw(layer)
    for dx in range(-ow,ow+1):
        for dy in range(-ow,ow+1):
            if dx*dx+dy*dy<=ow*ow:
                ld.text((pad-bb[0]+dx,pad-bb[1]+dy),text,font=fnt,fill=outline)
    ld.text((pad-bb[0],pad-bb[1]),text,font=fnt,fill=fill)
    layer=layer.resize((int(layer.width*stretch),layer.height),_IM.LANCZOS)
    cx,cy=center
    img.paste(layer,(int(cx-layer.width/2),int(cy-layer.height/2)),layer)

def _draw_hex_mesh(d, x0, y0, x1, y1, size=18, col=(30,32,48), line_w=1):
    """Draw a subtle hexagonal mesh pattern in a rectangle."""
    # Flat-top hexagons: each hex has width=size*2, height=size*sqrt(3)
    w  = size * 2
    h  = int(size * 1.732)
    hh = h // 2
    for row in range(-1, (y1 - y0) // hh + 2):
        for col_i in range(-1, (x1 - x0) // w + 2):
            # Offset every other row
            ox = x0 + col_i * w + (hh if row % 2 else 0)
            oy = y0 + row * hh
            cx2 = ox + size; cy2 = oy
            # 6 vertices of flat-top hex
            pts = [
                (cx2 + int(size * 0.5),  cy2 - hh//2),
                (cx2 + size,             cy2),
                (cx2 + int(size * 0.5),  cy2 + hh//2),
                (cx2 - int(size * 0.5),  cy2 + hh//2),
                (cx2 - size,             cy2),
                (cx2 - int(size * 0.5),  cy2 - hh//2),
            ]
            # Only draw segments that intersect the bounding box
            for j in range(6):
                p1 = pts[j]; p2 = pts[(j+1)%6]
                if (x0 <= p1[0] <= x1 or x0 <= p2[0] <= x1) and \
                   (y0 <= p1[1] <= y1 or y0 <= p2[1] <= y1):
                    d.line([p1, p2], fill=col, width=line_w)

# ── Pre-cache fonts ────────────────────────────────────────────────────────────
_FONT_CACHE = {}
def fc(size):
    if size not in _FONT_CACHE:
        _FONT_CACHE[size] = _font(size)
    return _FONT_CACHE[size]

# ── Poppins (modern bold display face for Dash 1/2) ──────────────────────────
def _find_poppins_font():
    import os
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
    for p in [os.path.join(here, "Poppins-Bold.ttf"),
              "/home/claude/Poppins-Bold.ttf",
              "C:/Windows/Fonts/Poppins-Bold.ttf",
              "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"]:
        if os.path.exists(p):
            return p
    return None
_POPPINS_PATH = _find_poppins_font()
_POPPINS_CACHE = {}
def fp(size):
    """Poppins Bold at given size; falls back to the bold sans (fb) if missing."""
    if _POPPINS_PATH is None:
        return fb(size)
    if size not in _POPPINS_CACHE:
        try:
            _POPPINS_CACHE[size] = ImageFont.truetype(_POPPINS_PATH, size)
        except Exception:
            _POPPINS_CACHE[size] = fb(size)
    return _POPPINS_CACHE[size]

_BOLD_CACHE = {}
def fb(size):
    """Bold font at given size."""
    _p = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    if size not in _BOLD_CACHE:
        try:
            _BOLD_CACHE[size] = ImageFont.truetype(_p, size)
        except Exception:
            _BOLD_CACHE[size] = fc(size)
    return _BOLD_CACHE[size]

_ITALIC_CACHE = {}
_FUTURA_CACHE = {}
def ff(size):
    """Futuristic/italic font for Style 9 gear display."""
    if size not in _FUTURA_CACHE:
        _paths = [
            'C:/Windows/Fonts/calibrii.ttf',
            'C:/Windows/Fonts/calibrib.ttf',
            'C:/Windows/Fonts/segoeuii.ttf',
            'C:/Windows/Fonts/trebucit.ttf',
            'C:/Windows/Fonts/ariali.ttf',
            'C:/Windows/Fonts/ariblk.ttf',
            'C:/Windows/Fonts/impact.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf',
        ]
        _fnt = None
        for _p2 in _paths:
            if os.path.exists(_p2):
                try: _fnt = ImageFont.truetype(_p2, size); break
                except: pass
        _FUTURA_CACHE[size] = _fnt or fc(size)
    return _FUTURA_CACHE[size]

def fi(size):
    """Bold-italic font at given size."""
    _p = "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf"
    if size not in _ITALIC_CACHE:
        try:
            _ITALIC_CACHE[size] = ImageFont.truetype(_p, size)
        except Exception:
            _ITALIC_CACHE[size] = fc(size)
    return _ITALIC_CACHE[size]

# ── Static gauge background (rendered once, reused every frame) ───────────────
_GAUGE_BG_CACHE = {}
_BOX_BURST_CACHE = {}  # (bW, bH, bc, be) -> PIL Image

def _build_gauge_bg(cx, cy, radius, w=None, h=None, rpm_max=9000, P=None):
    """Render the static parts of the gauge — face, ticks, numbers, rings."""
    P = P or STYLES["Style 5"]
    _w = w or W_VID; _h = h or H_VID
    START_ANG = P.get("gauge_start_ang", 225)
    SWEEP     = P.get("gauge_sweep",     270)
    _panel_key = P.get("panel", (0,0,0)) if P else (0,0,0)
    _subface_key = P.get("subface", (0,0,0)) if P else (0,0,0)
    key = (cx, cy, radius, _w, _h, rpm_max, START_ANG, SWEEP, _panel_key, _subface_key)
    if key in _GAUGE_BG_CACHE:
        return _GAUGE_BG_CACHE[key]

    r0 = radius

    # Start with chroma background so gauge edges key cleanly
    img = Image.new('RGBA', (_w, _h), (*P["chroma"], 255))
    d   = ImageDraw.Draw(img)

    # Outer bezel
    d.ellipse([cx-r0, cy-r0, cx+r0, cy+r0],
              fill=(20,20,30), outline=(65,65,85), width=5)
    # Face
    ri = int(r0*0.935)
    d.ellipse([cx-ri, cy-ri, cx+ri, cy+ri], fill=P["dark"])

    # Subtle inner ring gradient effect (two rings)
    for rr, oc in [(int(r0*0.93),(40,40,55)), (int(r0*0.91),(25,25,38))]:
        d.ellipse([cx-rr, cy-rr, cx+rr, cy+rr],
                  outline=oc, width=2, fill=None)

    # Hex mesh on the gauge face (Style 1 only)
    if P.get("hex_mesh", False):
        _hex_col  = P.get("hex_col", (18, 20, 34))
        _hex_size = max(6, int(r0 * 0.055))
        # Draw hex over entire face bounding box — the face ellipse clips it visually
        # We'll use a mask approach: draw on a temp layer clipped to the face circle
        _hx_layer = Image.new('RGBA', (_w, _h), (0, 0, 0, 0))
        _hx_d     = ImageDraw.Draw(_hx_layer)
        # Draw hex across full face bounding box
        _draw_hex_mesh(_hx_d, cx-ri, cy-ri, cx+ri, cy+ri,
                        size=_hex_size, col=_hex_col, line_w=1)
        # Mask to face circle: create circle mask
        _mask = Image.new('L', (_w, _h), 0)
        _mask_d = ImageDraw.Draw(_mask)
        _mask_d.ellipse([cx-ri, cy-ri, cx+ri, cy+ri], fill=255)
        # Erase inner sub-face area from mask (keep hex only in ring zone)
        _ir_mask = int(r0 * 0.46)
        _mask_d.ellipse([cx-_ir_mask, cy-_ir_mask, cx+_ir_mask, cy+_ir_mask], fill=0)
        img.paste(_hx_layer, mask=_mask)

    # Radial hatch lines on the gauge face (subtle texture)
    _hatch_col = tuple(min(255, c+18) for c in P["dark"])   # slightly lighter than face
    _R_inner = int(r0 * 0.48)   # just outside sub-face
    _R_outer = int(r0 * 0.74)   # just inside tick marks
    N_HATCH = 72                 # lines around full circle
    for h in range(N_HATCH):
        ha = math.radians(h / N_HATCH * 360)
        x1 = cx + _R_inner * math.cos(ha); y1 = cy - _R_inner * math.sin(ha)
        x2 = cx + _R_outer * math.cos(ha); y2 = cy - _R_outer * math.sin(ha)
        d.line([(x1,y1),(x2,y2)], fill=_hatch_col, width=1)

    # DIM segment outlines (unlit positions) — full arc
    agg = aggdraw.Draw(img)
    N_SEGS  = 52
    SEG_GAP = 2.2
    ARC_R   = int(r0 * 0.845)
    ARC_W   = int(r0 * 0.115)

    _seg_clip_y = (cy + int(r0*0.460) + 8) if P and P.get("style15_layout") else 9999
    for i in range(N_SEGS):
        fs      = i / N_SEGS
        a_start = START_ANG - fs * SWEEP
        a_end   = START_ANG - (i+1)/N_SEGS*SWEEP + SEG_GAP
        # Clip: skip segments whose midpoint is below gear-bottom
        _seg_mid_ang = math.radians((a_start+a_end)/2)
        _seg_y = cy - ARC_R*math.sin(_seg_mid_ang)
        if _seg_y > _seg_clip_y: continue
        if fs > 0.87:
            col = P["dimred"]
        else:
            col = P["dim"]
        pen = aggdraw.Pen(col, ARC_W)
        agg.arc([cx-ARC_R, cy-ARC_R, cx+ARC_R, cy+ARC_R],
                a_end, a_start, pen)
    agg.flush()

    d = ImageDraw.Draw(img)

    # Minor ticks — evenly spaced across sweep (needle scale: 0→rpm_max)
    TR_OUT = int(r0*0.715); TR_MIN = int(r0*0.690)
    _n_marks = rpm_max // 1000
    N_MINOR  = _n_marks * 5   # 5 minor per major
    for i in range(N_MINOR + 1):
        frac_i = i / N_MINOR
        a  = math.radians(START_ANG - frac_i * SWEEP)
        ca = math.cos(a); sa = math.sin(a)
        d.line([cx+TR_OUT*ca, cy-TR_OUT*sa,
                cx+TR_MIN*ca, cy-TR_MIN*sa], fill=P["grey"], width=1)

    # Major ticks — at 1000, 2000, ... rpm_max
    # Position = rpm_value / rpm_max (same scale as needle frac)
    TR_MAJ = int(r0*0.660)
    _tick_clip_y = (cy + int(r0*0.460) + 8) if P and P.get("style15_layout") else 9999
    for i in range(1, _n_marks + 1):
        frac_i = i / _n_marks
        a  = math.radians(START_ANG - frac_i * SWEEP)
        ca = math.cos(a); sa = math.sin(a)
        if cy - TR_OUT*sa > _tick_clip_y: continue
        d.line([cx+TR_OUT*ca, cy-TR_OUT*sa,
                cx+TR_MAJ*ca, cy-TR_MAJ*sa], fill=P["white"], width=3)

    # Numbers at each major tick (clipped at gear-bottom for style15)
    NUM_R = int(r0*0.565)
    fnt_n = fc(max(8, int(r0*0.160 * min(1.0, 9/_n_marks))))
    _clip_y = (cy + int(r0*0.460) + 10) if P and P.get("style15_layout") else 9999
    # Draw "0" at start of arc for style15
    if P and P.get("style15_layout"):
        _a0 = math.radians(START_ANG)
        _lx0 = cx + NUM_R*math.cos(_a0)
        _ly0 = cy - NUM_R*math.sin(_a0)
        if _ly0 <= _clip_y:
            _tc(d, (int(_lx0), int(_ly0)), "0", fnt_n, P["white"])
    for i in range(1, _n_marks + 1):
        frac_i = i / _n_marks
        a  = math.radians(START_ANG - frac_i * SWEEP)
        lx = cx + NUM_R*math.cos(a)
        ly = cy - NUM_R*math.sin(a)
        if ly > _clip_y: continue
        _tc(d, (int(lx), int(ly)), str(i), fnt_n, P["white"])

    # Inner sub-face
    ir = int(r0*0.460)
    d.ellipse([cx-ir, cy-ir, cx+ir, cy+ir], fill=P["subface"])
    d.ellipse([cx-ir, cy-ir, cx+ir, cy+ir], outline=(30,30,45), width=2, fill=None)



    # Radial burst — drawn LAST so it sits over face but under segment arcs/ticks/numbers
    # Uses RGBA masking so burst only applies in the annular ring zone
    if "burst_centre" in P and "burst_edge" in P:
        bc       = P["burst_centre"]
        be       = P["burst_edge"]
        ir_burst = int(r0 * 0.462)       # just outside sub-face
        or_burst = int(r0 * 0.91)        # just inside bezel
        span     = max(1, or_burst - ir_burst)
        # Draw on a temp RGBA layer then composite with an annular mask
        _bl = Image.new("RGBA", (_w, _h), (0, 0, 0, 0))
        _bd = ImageDraw.Draw(_bl)
        for _rr in range(or_burst, ir_burst, -1):
            t   = ((_rr - ir_burst) / span) ** 0.65
            col = tuple(int(bc[j]*(1-t) + be[j]*t) for j in range(3))
            _bd.ellipse([cx-_rr, cy-_rr, cx+_rr, cy+_rr], fill=col, outline=col)
        # Annular mask: outer ring only (exclude sub-face and bezel)
        _bm = Image.new("L", (_w, _h), 0)
        _bmd = ImageDraw.Draw(_bm)
        _bmd.ellipse([cx-or_burst, cy-or_burst, cx+or_burst, cy+or_burst], fill=180)
        _bmd.ellipse([cx-ir_burst, cy-ir_burst, cx+ir_burst, cy+ir_burst], fill=0)
        img.paste(_bl.convert("RGB"), mask=_bm)

    # Redraw numbers on top of burst with strong contrast colour
    d = ImageDraw.Draw(img)
    _num_col = P.get("num_colour", P["white"])
    if P and P.get("style15_layout"):
        _a0 = math.radians(START_ANG)
        _lx0 = cx + NUM_R*math.cos(_a0)
        _ly0 = cy - NUM_R*math.sin(_a0)
        if _ly0 <= _clip_y:
            _tc(d, (int(_lx0), int(_ly0)), "0", fnt_n, _num_col)
    for i in range(1, _n_marks + 1):
        frac_i = i / _n_marks
        a  = math.radians(START_ANG - frac_i * SWEEP)
        lx = cx + NUM_R*math.cos(a)
        ly = cy - NUM_R*math.sin(a)
        if ly > _clip_y: continue
        _tc(d, (int(lx), int(ly)), str(i), fnt_n, _num_col)

    _GAUGE_BG_CACHE[key] = img
    return img


def _draw_gauge_dynamic(base_img, cx, cy, radius, rpm, rpm_max=9000, peak_rpm=None, P=None):
    """Draw lit segments + digital RPM + needle. rpm_max sets full-scale.
    peak_rpm: if provided, shown in smaller font below live RPM.
    """
    P = P or STYLES["Style 5"]
    img = base_img.copy()
    d   = ImageDraw.Draw(img)
    r0  = radius
    frac      = min(rpm/rpm_max, 1.0)
    START_ANG = P.get("gauge_start_ang", P.get("gauge_start", 225)) if P else 225
    SWEEP     = P.get("gauge_sweep", 270) if P else 270
    N_SEGS    = 52
    SEG_GAP   = 2.2
    ARC_R     = int(r0*0.845)
    ARC_W     = int(r0*0.115)

    # Pre-compute how many segments to light
    n_lit = int(frac * N_SEGS)
    _dyn_clip_y = (cy + int(r0*0.460) + 8) if P and P.get("style15_layout") else 9999
    if n_lit > 0:
        agg = aggdraw.Draw(img)
        for i in range(n_lit):
            fs      = i / N_SEGS
            a_start = START_ANG - fs * SWEEP
            a_end   = START_ANG - (i+1)/N_SEGS*SWEEP + SEG_GAP
            _seg_mid = math.radians((a_start+a_end)/2)
            if cy - ARC_R*math.sin(_seg_mid) > _dyn_clip_y: continue
            if fs > 0.87:
                col = P["red"]
            elif fs > 0.70:
                t   = (fs - 0.70) / 0.17
                col = tuple(int(P["amber"][j]*(1-t) + P["amber2"][j]*t) for j in range(3))
            else:
                col = P["amber"]
            pen = aggdraw.Pen(col, ARC_W)
            agg.arc([cx-ARC_R, cy-ARC_R, cx+ARC_R, cy+ARC_R],
                    a_end, a_start, pen)
        agg.flush()

    d = ImageDraw.Draw(img)

    # Clear sub-face area before drawing RPM so previous frame does not bleed through
    ir = int(r0*0.455)
    d.ellipse([cx-ir, cy-ir, cx+ir, cy+ir], fill=P["subface"])
    d.ellipse([cx-ir, cy-ir, cx+ir, cy+ir], outline=(30,30,45), width=2, fill=None)

    # Digital RPM — data value
    _tc(d, (cx, cy + int(r0*0.13)),
        f"{int(rpm)}", fc(int(r0*0.260)), P.get("data", P["white"]))

    # Peak RPM (last gear) in smaller font below
    if peak_rpm is not None:
        _tc(d, (cx, cy + int(r0*0.32)),
            f"({int(peak_rpm)})", fc(int(r0*0.130)), P.get("data", P["white"]))

    # Needle — tapered polygon
    na  = math.radians(START_ANG - frac*SWEEP)
    ca  = math.cos(na); sa = math.sin(na)
    NTR = int(r0*0.740)
    NBR = int(r0*0.130)
    ntx = cx + NTR*ca;  nty = cy - NTR*sa
    nbx = cx - NBR*ca;  nby = cy + NBR*sa
    perp = na + math.pi/2
    cp   = math.cos(perp); sp = math.sin(perp)
    pw   = int(r0*0.066)   # 3× thicker needle
    poly = [
        (nbx + pw*cp, nby - pw*sp),
        (ntx, nty),
        (nbx - pw*cp, nby + pw*sp),
    ]
    d.polygon(poly, fill=P["red"])

    # Needle shine line
    nmx = cx + int(NTR*0.4)*ca;  nmy = cy - int(NTR*0.4)*sa
    d.line([(cx, cy), (nmx, nmy)],
           fill=(255,180,180), width=1)

    # Centre cap
    cap_r = int(r0*0.052)
    d.ellipse([cx-cap_r, cy-cap_r, cx+cap_r, cy+cap_r],
              fill=(195,195,210), outline=(80,80,100), width=2)
    cap_r2 = int(r0*0.028)
    d.ellipse([cx-cap_r2, cy-cap_r2, cx+cap_r2, cy+cap_r2], fill=(35,35,50))

    # Style 9: cover needle by redrawing sub-face on top
    if P.get("style9_layout"):
        d.ellipse([cx-ir, cy-ir, cx+ir, cy+ir], fill=P["subface"])
        d.ellipse([cx-ir, cy-ir, cx+ir, cy+ir],
                  outline=P.get("border", (160,175,210)), width=max(2,int(r0*0.009)), fill=None)

    return img


def apply_lowpass(series, alpha=0.15):
    """Exponential moving average low-pass filter.
    alpha: smoothing factor 0-1. Lower = smoother but more lag.
    0.15 gives subtle smoothing without noticeable lag at 30fps.
    """
    out = series.copy().astype(float)
    for i in range(1, len(out)):
        out.iloc[i] = alpha * out.iloc[i] + (1-alpha) * out.iloc[i-1]
    return out

def find_lap_events(spd, ts, min_spd=185, min_gap=60.0, merge_gap=15.0):
    min_spd = float(min_spd); min_gap = float(min_gap)
    """
    Detect laps by finding the highest speed peak within each high-speed burst.
    A burst is a contiguous window where speed stays above min_spd.
    Bursts within merge_gap seconds of each other are merged (same straight).
    Lap boundaries are consecutive burst peaks separated by >= min_gap seconds.
    """
    if len(spd) < 10:
        return []

    # Step 1: find contiguous high-speed windows
    in_straight = False
    straight_start = 0
    straights = []  # (start_idx, end_idx)
    for i in range(len(spd)):
        if not in_straight and spd[i] > min_spd:
            in_straight = True
            straight_start = i
        elif in_straight and spd[i] < min_spd:
            in_straight = False
            straights.append((straight_start, i))
    if in_straight:
        straights.append((straight_start, len(spd)-1))

    if not straights:
        return []

    # Step 2: for each straight, find the peak speed and its timestamp
    events = []
    for s, e in straights:
        best_i = s + int(np.argmax(spd[s:e+1]))
        events.append((ts[s], ts[e], spd[best_i], ts[best_i]))

    # Step 3: merge events within merge_gap seconds of each other
    merged = [events[0]]
    for ev in events[1:]:
        if ev[0] - merged[-1][1] <= merge_gap:
            prev = merged[-1]
            # Keep the higher peak
            if ev[2] > prev[2]:
                merged[-1] = (prev[0], ev[1], ev[2], ev[3])
            else:
                merged[-1] = (prev[0], ev[1], prev[2], prev[3])
        else:
            merged.append(ev)

    # Step 4: apply 60s min gap for lap boundaries using peak timestamps
    lap_peaks = [m[3] for m in merged]
    laps = []
    i = 0
    while i < len(lap_peaks):
        st = lap_peaks[i]
        for j in range(i+1, len(lap_peaks)):
            if lap_peaks[j] - st >= min_gap:
                laps.append((st, lap_peaks[j], lap_peaks[j]-st))
                i = j
                break
        else:
            break
    return laps

def get_timer_display(t_now, laps, show_duration=5.0, P=None, seconds_only=False):
    P = P or STYLES["Style 5"]
    def fmt(t):
        if seconds_only:
            return f"{t:05.2f}"
        m = int(t)//60; s2 = t%60
        return f"{m}:{s2:05.2f}" if m else f"{s2:05.2f}"
    for (st,et,lt) in laps:
        if et <= t_now <= et+show_duration:
            return fmt(lt), P["amber"], "LAP TIME"
    for (st,et,lt) in laps:
        if st <= t_now < et:
            elapsed = t_now-st
            return fmt(elapsed), P["green"], "TIMER"
    return "--:--.---", P["grey"], "TIMER"

def get_ffmpeg_path():
    base = os.path.dirname(os.path.abspath(sys.argv[0]))
    candidates = [
        os.path.join(base,'ffmpeg.exe'), os.path.join(base,'ffmpeg'),
        'ffmpeg.exe','ffmpeg']
    if hasattr(sys,'_MEIPASS'):
        candidates.insert(0, os.path.join(sys._MEIPASS,'ffmpeg.exe'))
    for c in candidates:
        try:
            subprocess.run([c,'-version'], capture_output=True, check=True)
            return c
        except Exception:
            pass
    raise FileNotFoundError(
        "ffmpeg not found. Place ffmpeg.exe in the same folder as this program.")


def _draw_analogue_gauge(img, cx, cy, radius, value, val_min, val_max,
                          label, unit, tick_step, P):
    """Draw an AIM-style analogue gauge: white face, dark ticks, red needle."""
    import math as _m
    d   = ImageDraw.Draw(img)
    r0  = radius
    START_ANG = 225   # degrees (bottom-left)
    SWEEP     = 270

    # Bezel
    d.ellipse([cx-r0, cy-r0, cx+r0, cy+r0],
              fill=(160,165,175), outline=(100,105,120), width=3)
    # Face
    ri = int(r0*0.92)
    d.ellipse([cx-ri, cy-ri, cx+ri, cy+ri], fill=P["dark"])

    # Tick marks + numbers
    n_major = int((val_max - val_min) / tick_step)
    fnt_num = fc(max(7, int(r0*0.130)))
    TR_OUT  = int(r0*0.82); TR_MAJ = int(r0*0.70); TR_MIN = int(r0*0.76)
    # Minor ticks (5 per major)
    for i in range(n_major * 5 + 1):
        a   = _m.radians(START_ANG - i/(n_major*5)*SWEEP)
        ca  = _m.cos(a); sa = _m.sin(a)
        r_in = TR_MIN if i % 5 != 0 else TR_MAJ
        d.line([cx+TR_OUT*ca, cy-TR_OUT*sa,
                cx+r_in*ca,   cy-r_in*sa],
               fill=(30,30,30), width=2 if i%5==0 else 1)
    # Numbers at major ticks
    NUM_R = int(r0*0.58)
    for i in range(n_major+1):
        val_at = val_min + i * tick_step
        a  = _m.radians(START_ANG - i/n_major*SWEEP)
        lx = cx + NUM_R*_m.cos(a)
        ly = cy - NUM_R*_m.sin(a)
        _tc(d, (int(lx), int(ly)), str(int(val_at)), fnt_num, P["data"])

    # Label below centre
    fnt_lbl = fc(max(6, int(r0*0.095)))
    _tc(d, (cx, cy + int(r0*0.35)), unit, fnt_lbl, P["grey"])

    # Digital readout box at bottom of face
    dbox_w = int(r0*0.85); dbox_h = int(r0*0.28)
    dbox_x = cx - dbox_w//2; dbox_y = cy + int(r0*0.48)
    d.rectangle([dbox_x, dbox_y, dbox_x+dbox_w, dbox_y+dbox_h],
                 fill=(25,50,130), outline=(25,50,130))
    fnt_dig = fc(max(8, int(r0*0.200)))
    _tc(d, (cx, dbox_y + dbox_h//2), str(int(value)), fnt_dig, (255,255,255))

    # Red needle
    frac = (value - val_min) / max(1, val_max - val_min)
    frac = max(0, min(1, frac))
    na   = _m.radians(START_ANG - frac*SWEEP)
    ca   = _m.cos(na); sa = _m.sin(na)
    NTR  = int(r0*0.75); NBR = int(r0*0.15)
    ntx  = cx + NTR*ca;  nty = cy - NTR*sa
    nbx  = cx - NBR*ca;  nby = cy + NBR*sa
    perp = na + _m.pi/2
    cp   = _m.cos(perp); sp = _m.sin(perp)
    pw   = max(2, int(r0*0.025))
    poly = [(nbx+pw*cp, nby-pw*sp), (ntx, nty),
            (nbx-pw*cp, nby+pw*sp), (nbx, nby)]
    d.polygon(poly, fill=P["red"])
    d.ellipse([cx-int(r0*0.06), cy-int(r0*0.06),
               cx+int(r0*0.06), cy+int(r0*0.06)], fill=P["red"])


def _build_frame_style3(img, d, rpm, throttle, speed, gear,
                          g_lat, g_long, ts, trace_glat, trace_glong_raw,
                          laps, _w, _h, s, brake_pct, rpm_max, peak_rpm,
                          trace_speed, speed_colour, P):
    """Concept B — Minimal Linear. Pure black, horizontal bars, monospace numerics."""
    import math as _m
    import numpy as _np

    # ── Palette ───────────────────────────────────────────────────────────────
    BG       = (8, 8, 8)
    PANEL    = (18, 18, 18)
    BORDER   = (40, 40, 40)
    WHITE    = (255, 255, 255)
    DIM      = (60, 60, 60)
    CYAN     = (0, 210, 240)     # RPM bar
    GREEN    = (34, 197, 94)     # throttle
    RED      = (239, 68, 68)     # brake
    AMBER    = (234, 179, 8)     # timer / lap
    PURPLE   = (167, 139, 250)   # G-lat
    BLUE     = (56, 189, 248)    # G-lon
    REDZONE  = (80, 20, 20)      # RPM red zone track

    d.rectangle([0, 0, _w, _h], fill=BG)

    def _text_outlined(d2, xy, text, font, fill, outline=(0,0,0), ow=2):
        x, y = xy
        d2.text((x, y), text, font=font, fill=fill,
                stroke_width=ow, stroke_fill=outline)

    # ── Layout constants ──────────────────────────────────────────────────────
    PAD  = int(10*s)
    H    = _h

    # Right GG plot drives the shared height for all columns
    LC_W   = int(_w * 0.20)
    LC_X   = PAD
    BAR_X  = LC_X + LC_W + int(12*s)
    GG_SZ  = _h - PAD*2            # full available height
    GG_X   = _w - PAD - GG_SZ      # right-align GG box
    BAR_W  = GG_X - BAR_X - int(12*s)  # bars fill remaining space
    GG_CX  = GG_X + GG_SZ//2
    GG_CY  = PAD + GG_SZ//2
    COL_Y0 = PAD                    # shared top
    COL_Y1 = PAD + GG_SZ           # shared bottom

    # 3 bar rows — RPM, THROTTLE, BRAKE
    N_BARS   = 3
    # 70% of available height
    BAR_H    = int((_h - PAD*2) * 0.70 / N_BARS)
    LBL_H    = max(int(BAR_H*0.48), int(14*s))
    TRACK_H  = max(BAR_H - LBL_H - int(3*s), int(7*s))

    def bar_y(i):
        return PAD + i*(BAR_H + int(4*s))

    # ── Helper: font ──────────────────────────────────────────────────────────
    def lbl_fnt():  return fb(max(9, int(LBL_H * 0.78)))
    def val_fnt():  return fc(max(9, int(TRACK_H * 0.80)))
    def big_fnt():  return fc(max(12, int(BAR_H * 0.75)))

    # ── Helper: draw one bar row ───────────────────────────────────────────────
    def draw_bar(i, label, pct, bar_col, dim_col, val_str, val_col=WHITE, redzone_start=None):
        by      = bar_y(i)
        lbl_y   = by + LBL_H//2          # label row y (centred in label band)
        track_y = by + LBL_H + int(3*s)
        track_x = BAR_X
        track_w = BAR_W
        RIGHT_X = BAR_X + BAR_W          # right edge of bar

        fnt = lbl_fnt()
        lbl_col = tuple(int(c*0.55) for c in bar_col)

        # Label — left-justified, outlined for legibility
        bb = d.textbbox((0,0), label, font=fnt)
        _text_outlined(d, (BAR_X, lbl_y - (bb[3]-bb[1])//2),
                       label, font=fnt, fill=lbl_col, outline=(0,0,0), ow=2)

        # Value — right-justified, outlined
        bb2 = d.textbbox((0,0), val_str, font=fnt)
        _text_outlined(d, (RIGHT_X - (bb2[2]-bb2[0]), lbl_y - (bb2[3]-bb2[1])//2),
                       val_str, font=fnt, fill=val_col, outline=(0,0,0), ow=2)

        # Separator line under label row
        d.line([(BAR_X, by+LBL_H), (RIGHT_X, by+LBL_H)],
               fill=tuple(int(c*0.12) for c in bar_col), width=1)

        # Track background
        d.rectangle([track_x, track_y, track_x+track_w, track_y+TRACK_H], fill=PANEL)

        # Red zone on RPM track
        if redzone_start is not None:
            rz_x = track_x + int(track_w * redzone_start)
            d.rectangle([rz_x, track_y, track_x+track_w, track_y+TRACK_H], fill=REDZONE)
            d.line([(rz_x, track_y), (rz_x, track_y+TRACK_H)],
                   fill=(139, 30, 30), width=max(1,int(1*s)))

        # Fill bar
        fill_w = int(track_w * max(0, min(pct, 1.0)))
        if fill_w > 1:
            d.rectangle([track_x, track_y, track_x+fill_w, track_y+TRACK_H], fill=bar_col)

        # Track border
        d.rectangle([track_x, track_y, track_x+track_w, track_y+TRACK_H],
                    fill=None, outline=tuple(int(c*0.18) for c in bar_col), width=1)

    # ── 1. RPM bar ────────────────────────────────────────────────────────────
    rpm_pct = min(rpm / rpm_max, 1.0)
    rpm_col = CYAN
    if rpm_pct > 0.87:    rpm_col = RED
    elif rpm_pct > 0.72:
        t2 = (rpm_pct - 0.72) / 0.15
        rpm_col = tuple(int(CYAN[j]*(1-t2) + AMBER[j]*t2) for j in range(3))
    peak_str = f"({int(peak_rpm)})" if peak_rpm else ""
    draw_bar(0, f"RPM   {peak_str}", rpm_pct, CYAN, DIM,
             str(int(rpm)), CYAN, redzone_start=0.87)

    # Tick marks above RPM bar
    by0 = bar_y(0)
    track_y0 = by0 + LBL_H + int(3*s)
    n_major = rpm_max // 1000
    fnt_tick = fc(max(6, int(8*s)))
    for i in range(n_major+1):
        tx = BAR_X + int(BAR_W * i / n_major)
        d.line([(tx, track_y0-4), (tx, track_y0-1)], fill=DIM, width=1)
        if i % 2 == 0:
            _tc(d, (tx, track_y0-8), str(i), fnt_tick, DIM)

    # ── 2. Throttle ───────────────────────────────────────────────────────────
    draw_bar(1, "THROTTLE", throttle/100, GREEN, DIM, f"{int(throttle)}%", GREEN)

    # ── 3. Brake ─────────────────────────────────────────────────────────────
    brake_v = float(brake_pct or 0)
    draw_bar(2, "BRAKE", brake_v/100, RED, DIM, f"{int(brake_v)}%", RED)

    # ── 4. G-Lat ─────────────────────────────────────────────────────────────
    # Bar centred: negative=left, positive=right
    # g_lat range ±2G
    def g_bar(i, label, val, col, max_g=2.0):
        by      = bar_y(i)
        lbl_y   = by + LBL_H//2
        track_y = by + LBL_H + int(3*s)
        mid_x   = BAR_X + BAR_W//2
        RIGHT_X = BAR_X + BAR_W
        fnt = lbl_fnt()
        lbl_col = tuple(int(c*0.55) for c in col)
        val_str = f"{val:+.2f}G"

        # Label left, value right, using PIL stroke
        bb = d.textbbox((0,0), label, font=fnt)
        d.text((BAR_X, lbl_y - (bb[3]-bb[1])//2), label, font=fnt,
               fill=lbl_col, stroke_width=2, stroke_fill=(0,0,0))
        bb2 = d.textbbox((0,0), val_str, font=fnt)
        d.text((RIGHT_X - (bb2[2]-bb2[0]), lbl_y - (bb2[3]-bb2[1])//2),
               val_str, font=fnt, fill=col, stroke_width=2, stroke_fill=(0,0,0))

        d.line([(BAR_X, by+LBL_H), (RIGHT_X, by+LBL_H)],
               fill=tuple(int(c*0.12) for c in col), width=1)
        d.rectangle([BAR_X, track_y, BAR_X+BAR_W, track_y+TRACK_H], fill=PANEL)
        d.rectangle([BAR_X, track_y, BAR_X+BAR_W, track_y+TRACK_H],
                    fill=None, outline=tuple(int(c*0.18) for c in col), width=1)
        d.line([(mid_x, track_y), (mid_x, track_y+TRACK_H)],
               fill=tuple(int(c*0.4) for c in col), width=max(1,int(1*s)))
        g1_x = BAR_X + int(BAR_W * (1/(max_g*2)))
        g2_x = BAR_X + BAR_W - int(BAR_W * (1/(max_g*2)))
        for gx in [g1_x, g2_x]:
            d.line([(gx, track_y+TRACK_H-3), (gx, track_y+TRACK_H)],
                   fill=tuple(int(c*0.3) for c in col), width=1)
        clamped = max(-max_g, min(max_g, val))
        if clamped >= 0:
            fill_w = int(BAR_W/2 * clamped/max_g)
            d.rectangle([mid_x, track_y, mid_x+fill_w, track_y+TRACK_H], fill=col)
        else:
            fill_w = int(BAR_W/2 * abs(clamped)/max_g)
            d.rectangle([mid_x-fill_w, track_y, mid_x, track_y+TRACK_H], fill=col)


    # ── Left column: Speed, Gear, Lap, Timer ─────────────────────────────────
    cell_h = GG_SZ // 4

    def left_cell(i, label, value, val_col=WHITE, lbl_col=None):
        cy0 = COL_Y0 + i*cell_h
        cy1 = cy0 + cell_h - int(4*s)
        lc  = lbl_col or tuple(int(c*0.45) for c in val_col)
        # Separator line at top (accent)
        d.line([(LC_X, cy0), (LC_X + LC_W, cy0)],
               fill=tuple(int(c*0.3) for c in val_col), width=max(1,int(1*s)))
        fnt_l = fc(max(7, int(cell_h*0.18)))
        fnt_v = fc(max(10, int(cell_h*0.52)))
        _tc(d, (LC_X + LC_W//2, cy0 + int(cell_h*0.28)), label, fnt_l, lc)
        _tc(d, (LC_X + LC_W//2, cy0 + int(cell_h*0.68)), value, fnt_v, val_col)

    left_cell(0, "KM/H", str(int(speed)), WHITE)
    left_cell(1, "GEAR", str(gear) if gear > 0 else "N", WHITE)

    # Lap number
    lap_num = "—"
    for i2,(st,et,lt) in enumerate(laps,1):
        if st <= ts < et: lap_num = str(i2)
        elif ts >= et and ts < et+5: lap_num = str(i2)
    left_cell(2, "LAP", lap_num, AMBER)

    timer_str, timer_col, timer_lbl = get_timer_display(ts, laps, P=P, seconds_only=True)
    left_cell(3, timer_lbl, timer_str, timer_col)

    # ── GG plot — far right ────────────────────────────────────────────────────
    GU = GG_SZ // 4
    d.rectangle([GG_X, PAD, GG_X+GG_SZ, PAD+GG_SZ], fill=PANEL)

    # Grid lines
    for gv in [-2,-1,0,1,2]:
        lx = GG_CX + gv*GU
        ly = GG_CY - gv*GU
        col_g = WHITE if gv==0 else (35,35,35)
        w_g   = 1 if gv==0 else 1
        d.line([(lx, PAD+2), (lx, PAD+GG_SZ-2)], fill=col_g, width=w_g)
        d.line([(GG_X+2, ly), (GG_X+GG_SZ-2, ly)], fill=col_g, width=w_g)

    # ±0.5G subtle
    for gv in [-1.5, -0.5, 0.5, 1.5]:
        lx = GG_CX + int(gv*GU)
        ly = GG_CY - int(gv*GU)
        d.line([(lx, PAD+2), (lx, PAD+GG_SZ-2)], fill=(25,25,25), width=1)
        d.line([(GG_X+2, ly), (GG_X+GG_SZ-2, ly)], fill=(25,25,25), width=1)

    # Border
    d.rectangle([GG_X, PAD, GG_X+GG_SZ, PAD+GG_SZ],
                fill=None, outline=(35,35,35), width=1)

    # Trace
    n = len(trace_glat)
    if n > 1:
        pts = []
        for i2 in range(n):
            tx2 = GG_CX + int(trace_glat[i2]*GU)
            ty2 = GG_CY - int((-trace_glong_raw[i2])*GU)
            tx2 = max(GG_X+2, min(GG_X+GG_SZ-2, tx2))
            ty2 = max(PAD+2,  min(PAD+GG_SZ-2, ty2))
            pts.append((tx2,ty2))
        lw = max(3, int(6*s))
        for i2 in range(len(pts)-1):
            alpha = 0.08 + 0.92*(i2/(n-1))
            if speed_colour and trace_speed and i2 < len(trace_speed):
                base = _speed_colour_fn(trace_speed[i2])
                col_t = _alpha_col(base, alpha)
            else:
                col_t = _alpha_col(WHITE, alpha)
            d.line([pts[i2], pts[i2+1]], fill=col_t, width=lw)

    # Current dot
    dx2 = GG_CX + int(g_lat*GU)
    dy2 = GG_CY - int((-g_long)*GU)
    dx2 = max(GG_X+4, min(GG_X+GG_SZ-4, dx2))
    dy2 = max(PAD+4,  min(PAD+GG_SZ-4, dy2))
    dr2 = max(3, int(5*s))
    d.ellipse([dx2-dr2,dy2-dr2,dx2+dr2,dy2+dr2], fill=WHITE, outline=(120,120,120), width=1)

    # G readings — auto-fit font to half the GG box width
    _max_txt_w = GG_SZ // 2 - int(12*s)
    _g_sz = max(7, int(LBL_H * 0.78))
    while _g_sz > 7:
        _fnt_gg = fb(_g_sz)
        _bb_t = d.textbbox((0,0), "Lat +0.00G", font=_fnt_gg)
        if (_bb_t[2]-_bb_t[0]) <= _max_txt_w:
            break
        _g_sz -= 1
    _live_y = PAD + GG_SZ - int(14*s)
    _pad_x  = int(4*s); _pad_y = int(3*s)
    for _txt, _tx, _col in [
            (f"Lat {g_lat:+.2f}G",  GG_X + GG_SZ//4,   PURPLE),
            (f"Lon {-g_long:+.2f}G", GG_X + GG_SZ*3//4, BLUE)]:
        _bb = d.textbbox((0, 0), _txt, font=_fnt_gg)
        _tw = _bb[2]-_bb[0]; _th = _bb[3]-_bb[1]
        d.rectangle([_tx-_tw//2-_pad_x, _live_y-_th//2-_pad_y,
                      _tx+_tw//2+_pad_x, _live_y+_th//2+_pad_y], fill=PANEL)
        _tc(d, (_tx, _live_y), _txt, _fnt_gg, _col)

    return img


def _speed_colour_fn(spd, min_spd=50.0, max_spd=200.0):
    t = max(0.0, min(1.0, (spd - min_spd) / (max_spd - min_spd)))
    if t < 0.5:
        tt = t/0.5; return (0, int(180*tt), int(255-55*tt))
    else:
        tt = (t-0.5)/0.5
        if tt < 0.5: ttt=tt/0.5; return (int(255*ttt),255,int(255*(1-ttt)))
        else: ttt=(tt-0.5)/0.5; return (255,int(255*(1-ttt)),0)




def _build_frame_style5(img, d, rpm, throttle, speed, gear,
                          g_lat, g_long, ts, trace_glat, trace_glong_raw,
                          laps, _w, _h, s, brake_pct, rpm_max, peak_rpm,
                          trace_speed, speed_colour, P,
                          GX, GS, _cx, _cy, _r, BAR_Y, BAR_H, gauge_bg, chan=None):
    """Style 5: gauge with speed/gear inside, vertical bars, GG trace, clean side panels."""
    import math as _m

    # ── Speed + Gear INSIDE gauge sub-face ───────────────────────────────────
    _s15 = P.get("style15_layout", False)
    r0 = _r
    ir = int(r0 * 0.460)           # sub-face radius

    _gear_col     = P.get("gear_col", (220,32,32))
    _gear_out_col = P.get("gear_outline", P.get("spd_outline", P["white"]))
    _spd_col      = P.get("spd_col", P["white"])
    _spd_out_col  = P.get("spd_outline", P["dark"])

    if P.get("style9_layout"):
        # ── Style 9 layout ────────────────────────────────────────────────
        # GEAR — italic, centred in sub-face circle, 30% larger
        _gear_str = str(gear) if gear > 0 else "N"
        _gear_fsz = max(9, int(ir * 1.43))  # 1.10 * 1.30
        while _gear_fsz > 6:
            _bb = d.textbbox((0,0), _gear_str, font=ff(_gear_fsz))
            if (_bb[2]-_bb[0]) < int(ir * 1.75): break
            _gear_fsz -= 1
        # Centre vertically in the sub-face circle so the gaps above and below
        # the glyph are even. (Previously offset 30% up, which sat too high.)
        _gear_bb = d.textbbox((0,0), _gear_str, font=ff(_gear_fsz))
        _gear_visual_cy = _cy - int(ir * 0.08)   # very slight lift, near-centred
        # The centring helper accounts for the font bbox origin; add back the
        # equivalent half-top-bearing bias so the optical centre matches.
        _gear_visual_cy += _gear_bb[1] // 2
        if P.get("gauge_wide_font"):
            _wide_panel_text(img, (_cx, _gear_visual_cy), _gear_str,
                             int(_gear_fsz*0.92), _gear_col, int(ir*1.75),
                             ow=max(3,int(4*s)), outline=_gear_out_col)
        else:
            _tc_outlined(d, (_cx, _gear_visual_cy), _gear_str,
                         ff(_gear_fsz), _gear_col, _gear_out_col, max(2,int(3*s)))

        # RPM — in the bottom zone between "1" and "9" (where speed used to be)
        if not P.get("style15_layout"):
            _rpm_zone_top    = _cy + ir + int(6*s)
            _rpm_zone_bottom = _cy + int(_r * 0.88)
            _rpm_zone_h      = _rpm_zone_bottom - _rpm_zone_top
            _rpm_str = str(int(rpm))
            _rpm_fsz = max(7, int(_rpm_zone_h * 0.44 * 1.30))  # +30%
            while _rpm_fsz > 6:
                _bb = d.textbbox((0,0), _rpm_str, font=fc(_rpm_fsz))
                if (_bb[2]-_bb[0]) < int(_r * 0.82): break
                _rpm_fsz -= 1
            _rpm_cy = _rpm_zone_top + int(_rpm_zone_h * 0.28)  # moved up
            # Use dark colour on light subface (Style 5), white on dark (Style 10)
            _rpm_txt_col = P.get("data", P.get("white", (255,255,255)))
            _rpm_out_col = P.get("panel", (0,0,0))
            if P.get("gauge_wide_font"):
                _wide_panel_text(img, (_cx, _rpm_cy), _rpm_str,
                                 int(_rpm_fsz*0.95), _rpm_txt_col, int(_r*0.82),
                                 ow=max(2,int(3*s)), outline=_rpm_out_col)
            else:
                _tc_outlined(d, (_cx, _rpm_cy), _rpm_str,
                             fp(_rpm_fsz), _rpm_txt_col, _rpm_out_col, max(1,int(2*s)))
            # Peak RPM — below main RPM number (clearer bold, brighter than grey)
            _pk_fsz = max(7, int(_rpm_fsz * 0.62))
            if peak_rpm is not None:
                _pk_col = P.get("amber", (255, 175, 60))
                # Position so it clears the RPM glyphs: half RPM height + half peak
                # height + a small gap (measured from actual rendered heights).
                _rpm_h = d.textbbox((0,0), _rpm_str, font=fp(_rpm_fsz))
                _pk_h  = d.textbbox((0,0), "(0000)", font=fp(_pk_fsz))
                _rpm_th = _rpm_h[3]-_rpm_h[1]; _pk_th = _pk_h[3]-_pk_h[1]
                _pk_y = _rpm_cy + _rpm_th//2 + _pk_th//2 + int(6*s)
                _tc_outlined(d, (_cx, _pk_y), f"({int(peak_rpm)})", fp(_pk_fsz),
                             _pk_col, _rpm_out_col, max(1,int(2*s)))

        # SPEED — panel at 4 o'clock (suppressed for style15)
        if not P.get("style15_layout"):
            import math as _mg
            _panel_pad  = max(4, int(6*s))
            # Fixed size: 130px wide × 91px tall (wide enough for 3 digits)
            _panel_w    = int(110*s)
            _panel_h_fixed = int(65*s)
            # Position at 4 o'clock
            _spd_ang    = _mg.radians(-30)
            _spd_dist   = int(_r * 0.72)
            _panel_cx   = _cx + int(_spd_dist * _mg.cos(_spd_ang))
            _panel_cy   = _cy - int(_spd_dist * _mg.sin(_spd_ang))
            _panel_x0   = _panel_cx - _panel_w // 2
            _panel_x1   = _panel_cx + _panel_w // 2
            _spd_str    = str(int(speed))
            _spd_fsz    = max(8, int(51*s))
            while _spd_fsz > 8:
                _bb = d.textbbox((0,0), "220", font=fi(_spd_fsz))
                if (_bb[2]-_bb[0]) < _panel_w - _panel_pad*2: break
                _spd_fsz -= 1
            _spd_bb9    = d.textbbox((0,0), _spd_str, font=fi(_spd_fsz))
            _spd_h9     = _spd_bb9[3] - _spd_bb9[1]
            _kph_h      = 0
            _panel_h    = _panel_h_fixed
            _panel_y0   = _panel_cy - _panel_h // 2
            _panel_y1   = _panel_y0 + _panel_h
            _panel_fill = P.get("subface", (220,228,240))
            _panel_bord = P.get("border",  (100,130,185))
            _prad       = max(3, int(5*s))
            d.rounded_rectangle([_panel_x0, _panel_y0, _panel_x1, _panel_y1],
                                 radius=_prad, fill=_panel_fill, outline=_panel_bord,
                                 width=max(2, int(2.5*s)))
            _text_cx    = (_panel_x0 + _panel_x1) // 2
            _text_y_spd = _panel_y0 + _panel_pad
            # Compensate for the corrected centring helper (see gear note).
            _spd_cy9    = _text_y_spd + _spd_h9//2 + _spd_bb9[1] // 2
            if P.get("gauge_wide_font"):
                _wide_panel_text(img, (_text_cx, _spd_cy9),
                                 _spd_str, int(_spd_fsz*0.95), (220,32,32),
                                 _panel_w - _panel_pad*2,
                                 ow=max(1,int(2*s)),
                                 outline=P.get("speed_panel_outline", P.get("spd_outline", P["white"])))
            else:
                _tc_outlined(d, (_text_cx, _spd_cy9),
                             _spd_str, fi(_spd_fsz),
                             (220,32,32), P.get("speed_panel_outline", P.get("spd_outline", P["white"])),
                             max(1,int(2*s)))

    else:
        # ── Style 5/6: gear + speed in bottom zone ────────────────────────────
        _zone_top    = _cy + ir + int(6*s)
        _zone_bottom = _cy + int(_r * 0.88)
        _zone_h      = _zone_bottom - _zone_top
        _zone_cx     = _cx

        _gear_str = str(gear) if gear > 0 else "N"
        _gear_fsz = max(9, int(_zone_h * 0.48))
        while _gear_fsz > 6:
            _bb = d.textbbox((0,0), _gear_str, font=fc(_gear_fsz))
            if (_bb[2]-_bb[0]) < int(_r * 0.55): break
            _gear_fsz -= 1
        _tc_outlined(d, (_zone_cx, _zone_top + int(_zone_h * 0.30)), _gear_str,
                     fc(_gear_fsz), _gear_col, _gear_out_col, max(2,int(3*s)))

        _spd_str = str(int(speed))
        _spd_fsz = max(9, int(_zone_h * 0.44))
        while _spd_fsz > 6:
            _bb = d.textbbox((0,0), _spd_str, font=fc(_spd_fsz))
            if (_bb[2]-_bb[0]) < int(_r * 0.75): break
            _spd_fsz -= 1
        _tc_outlined(d, (_zone_cx, _zone_top + int(_zone_h * 0.76)), _spd_str,
                     fc(_spd_fsz), _spd_col, _spd_out_col, max(2,int(3*s)))

    # ── TPS + BRAKE vertical bars — larger, positioned in the freed space ────
    # (G-trace removed from this dash — available as a separate overlay video.)
    _bar_lbl_fsz = max(11, int(20*s))
    # Bottom-justify the bars; percentage label sits ABOVE each bar (no THR/BRK text).
    _bottom_margin = int(_h * 0.05)
    _bar_bot = _h - _bottom_margin
    _bar_h_v = int(_h * 0.62)
    _bar_top = _bar_bot - _bar_h_v
    _bar_zone_x0 = GX + GS + int(20*s)
    _bar_zone_x1 = _w - int(20*s)
    _BAR_W   = int(64*s)
    _BAR_GAP = int(28*s)
    _brake_v = float(np.clip(brake_pct if brake_pct is not None else 0.0, 0, 100))

    def _tapered_bar(bx, by, bw, bh, pct, col, bg_col, outline_col):
        d.rounded_rectangle([bx, by, bx+bw, by+bh],
                              radius=6, fill=bg_col, outline=outline_col, width=2)
        fh = int((bh - 4) * pct / 100.0)
        if fh > 4:
            d.rounded_rectangle([bx+3, by+bh-3-fh, bx+bw-3, by+bh-3],
                                  radius=4, fill=col)

    _BARS_X = _bar_zone_x0
    for ix, (pct, col, lbl) in enumerate([(throttle, P["green"], "THR"),
                                          (_brake_v, P["red"], "BRK")]):
        bx = _BARS_X + ix * (_BAR_W + _BAR_GAP)
        _tapered_bar(bx, _bar_top, _BAR_W, _bar_h_v, pct, col, P["panel"], P["grey"])
        # percentage label ABOVE the bar (no THR/BRK text)
        _tc_outlined(d, (bx+_BAR_W//2, _bar_top - int(_bar_lbl_fsz*0.85)),
            f"{int(pct)}%", fp(_bar_lbl_fsz), (245,245,250), (0,0,0), max(2,int(2*s)))

    _px = _BARS_X + 2*(_BAR_W + _BAR_GAP) + int(20*s)


    # ── LAP + TIMER + optional Channel A/B panels — 2×2 grid ──────────────────
    _TXT_SZ = max(12, int(28*s))
    _PAD_V  = max(6, int(10*s))
    _P_H    = _TXT_SZ + _PAD_V * 2
    _GAP2   = int(8*s)

    _lap_num = "—"
    for _li,(_st,_et,_lt) in enumerate(laps,1):
        if _st<=ts<_et: _lap_num=str(_li)
        elif ts>=_et and ts<_et+5: _lap_num=str(_li)

    timer_str = "--:--"
    for (_st,_et,_lt) in laps:
        if _et<=ts<=_et+5:
            _m2,_s2=divmod(_lt,60); timer_str=f"{int(_m2)}:{_s2:05.2f}"; break
        elif _st<=ts<_et:
            _el=ts-_st; _m2,_s2=divmod(_el,60); timer_str=f"{int(_m2)}:{_s2:05.2f}"; break

    _outline_col = P.get("dark",(8,8,14))
    _ow = max(1,int(2*s))
    _data_col = P.get("data",P["white"])

    def _plain_panel(x0,y0,x1,y1,text):
        d.rectangle([x0,y0,x1,y1], fill=P["panel"])
        bv=P.get("emboss_light",P["grey"]); bs=P.get("emboss_dark",(0,0,0))
        bw=max(2,int(3*s))
        d.line([x0,y0,x1,y0],fill=bv,width=bw); d.line([x0,y0,x0,y1],fill=bv,width=bw)
        d.line([x0,y1,x1,y1],fill=bs,width=bw); d.line([x1,y0,x1,y1],fill=bs,width=bw)
        if P.get("panel_wide_font"):
            # Dash-8 style: Big Shoulders stretched 1.5× with black outline
            _wide_panel_text(img, ((x0+x1)//2,(y0+y1)//2), text,
                             max(14,int(_TXT_SZ*0.9)), P["white"],
                             (x1-x0)-int(10*s))
            return
        _fsz = _TXT_SZ
        while _fsz > 8:
            _bb=d.textbbox((0,0),text,font=fp(_fsz))
            if (_bb[2]-_bb[0]) < (x1-x0)-int(8*s): break
            _fsz -= 1
        _tc_outlined(d,((x0+x1)//2,(y0+y1)//2),text,fp(_fsz),_data_col,_outline_col,_ow)

    if not P.get("style15_layout"):
        # Compact 2×2 grid, bottom-justified to the frame bottom:
        #   LEFT column = Lap (top) / Timer (bottom)
        #   RIGHT column = Channel A (top) / Channel B (bottom)
        # Narrower columns so the panels take less horizontal space.
        _col_w   = int(150*s)
        _grid_x0 = _px
        _cxL     = _grid_x0
        _cxR     = _grid_x0 + _col_w + _GAP2

        # Optional channels — collect selected ones (value text + unit)
        _chan_cells = []
        if chan:
            for _which, _lkey in [("A","A_lbl"), ("B","B_lbl")]:
                _vstr = _chan_vstr(chan, _which)
                if not _vstr:
                    continue
                _lbl = chan.get(_lkey) or ""
                _txt = f"{_lbl}: {_vstr}" if _lbl else _vstr
                _chan_cells.append(_txt)

        # Compact panel size (previous size); bottom-aligned to the frame bottom
        _bottom_margin = int(_h * 0.05)
        _gy1    = _h - _bottom_margin - _P_H          # bottom row
        _gy0    = _gy1 - _GAP2 - _P_H                 # top row

        # LEFT column: Lap (top), Timer (bottom)
        _plain_panel(_cxL, _gy0, _cxL+_col_w, _gy0+_P_H, f"Lap: {_lap_num}")
        _plain_panel(_cxL, _gy1, _cxL+_col_w, _gy1+_P_H, timer_str)

        # RIGHT column: Channel A (top), Channel B (bottom) — only if selected
        if len(_chan_cells) >= 1:
            _plain_panel(_cxR, _gy0, _cxR+_col_w, _gy0+_P_H, _chan_cells[0])
        if len(_chan_cells) >= 2:
            _plain_panel(_cxR, _gy1, _cxR+_col_w, _gy1+_P_H, _chan_cells[1])

    return img


def _build_frame_style8(img, d, rpm, throttle, speed, gear,
                         g_lat, g_long, ts, laps, _w, _h, s,
                         brake_pct, rpm_max, peak_rpm, P):
    """Style 8 — Style 7 horizontal row on top, Style 4 vertical column on left.
    Remaining area is chroma key."""

    CHROMA = P.get("chroma", (255, 0, 255))
    WHITE  = P["white"]
    BLACK  = (0, 0, 0)

    # Fill entire frame with chroma
    d.rectangle([0, 0, _w, _h], fill=CHROMA)

    # Split frame vertically:
    #   Style 7 row: top band
    #   Style 4 col: left side of remaining area
    ROW_H = int(_h * 0.22)   # top band height for Style 7
    COL_W = int(_w * 0.12)   # left column width for Style 4

    # ── STYLE 7 ROW (top band) ────────────────────────────────────────────────
    N_COLS = 9
    PAD7   = int(10*s)
    COL_W7 = (_w - PAD7*2) // N_COLS

    ICO    = max(12, min(int(COL_W7 * 0.20), int(36*s)))
    SZ_LBL = max(8,  min(int(COL_W7 * 0.12), int(18*s)))
    SZ_VAL = max(14, min(int(ROW_H  * 0.38), int(52*s)))
    OW     = max(1, int(2.4*s))

    _ico_h = ICO
    _lbl_h = SZ_LBL + int(3*s)
    _val_h = SZ_VAL + int(3*s)
    _inner = _ico_h + int(3*s) + _lbl_h + int(6*s) + _val_h
    _top   = (ROW_H - _inner) // 2

    ICO_Y7 = _top
    LBL_Y7 = ICO_Y7 + _ico_h + int(3*s)
    VAL_Y7 = LBL_Y7 + _lbl_h + int(6*s)

    # Icon helpers (shared between row and column)
    import math as _m

    def _ico_speed(x0,y0,sz):
        cx2,cy2,r2=x0+sz//2,y0+sz//2,sz//2-1; lw=max(2,sz//7)
        d.arc([cx2-r2,cy2-r2,cx2+r2,cy2+r2],200,340,fill=WHITE,width=lw)
        a=_m.radians(290)
        d.line([cx2,cy2,cx2+int(r2*0.8*_m.cos(a)),cy2+int(r2*0.8*_m.sin(a))],fill=WHITE,width=max(2,sz//6))
        d.ellipse([cx2-sz//7,cy2-sz//7,cx2+sz//7,cy2+sz//7],fill=WHITE)
    def _ico_rpm(x0,y0,sz):
        cx2,cy2,r2=x0+sz//2,y0+sz//2,sz//2-1; lw=max(2,sz//7)
        d.arc([cx2-r2,cy2-r2,cx2+r2,cy2+r2],200,340,fill=WHITE,width=lw)
        a=_m.radians(240)
        d.line([cx2,cy2,cx2+int(r2*0.8*_m.cos(a)),cy2+int(r2*0.8*_m.sin(a))],fill=WHITE,width=max(2,sz//6))
        d.ellipse([cx2-sz//7,cy2-sz//7,cx2+sz//7,cy2+sz//7],fill=WHITE)
        for ang in [210,240,270,300,330]:
            a2=_m.radians(ang)
            d.line([cx2+int((r2-sz//4)*_m.cos(a2)),cy2+int((r2-sz//4)*_m.sin(a2)),cx2+int(r2*_m.cos(a2)),cy2+int(r2*_m.sin(a2))],fill=WHITE,width=max(1,sz//9))
    def _ico_gear(x0,y0,sz):
        cx2,cy2=x0+sz//2,y0+sz//2; r_out=sz//2-1; r_in=int(sz*0.28)
        pts=[(cx2+(r_out if i%4<2 else int(r_out*0.70))*_m.cos(_m.radians(i*360/32)),
               cy2+(r_out if i%4<2 else int(r_out*0.70))*_m.sin(_m.radians(i*360/32))) for i in range(32)]
        d.polygon(pts,fill=WHITE); d.ellipse([cx2-r_in,cy2-r_in,cx2+r_in,cy2+r_in],fill=CHROMA)
    def _ico_throttle(x0,y0,sz):
        bw=max(2,sz//5); gap=max(1,sz//8)
        for i,h in enumerate([int(sz*0.4),int(sz*0.65),int(sz*0.9),int(sz*0.55)]):
            bx=x0+i*(bw+gap); d.rectangle([bx,y0+sz-h,bx+bw,y0+sz],fill=WHITE)
    def _ico_brake(x0,y0,sz):
        cx2,cy2=x0+sz//2,y0+sz//2; r_out=sz//2-1; r_in=int(sz*0.22)
        d.ellipse([cx2-r_out,cy2-r_out,cx2+r_out,cy2+r_out],fill=WHITE)
        d.ellipse([cx2-r_in,cy2-r_in,cx2+r_in,cy2+r_in],fill=CHROMA)
        for ang in [0,60,120,180,240,300]:
            a=_m.radians(ang)
            d.line([cx2+int(r_in*_m.cos(a)),cy2+int(r_in*_m.sin(a)),cx2+int(r_out*_m.cos(a)),cy2+int(r_out*_m.sin(a))],fill=CHROMA,width=max(1,sz//8))
    def _ico_glat(x0,y0,sz):
        cy2=y0+sz//2; mx=x0+sz//2; aw=sz//2; lw=max(2,sz//6)
        d.line([x0+aw//2,cy2,mx-2,cy2],fill=WHITE,width=lw)
        d.polygon([(x0+1,cy2),(x0+aw//2,cy2-aw//3),(x0+aw//2,cy2+aw//3)],fill=WHITE)
        d.line([mx+2,cy2,x0+sz-aw//2,cy2],fill=WHITE,width=lw)
        d.polygon([(x0+sz-1,cy2),(x0+sz-aw//2,cy2-aw//3),(x0+sz-aw//2,cy2+aw//3)],fill=WHITE)
    def _ico_glon(x0,y0,sz):
        cx2=x0+sz//2; my=y0+sz//2; aw=sz//2; lw=max(2,sz//6)
        d.line([cx2,y0+aw//2,cx2,my-2],fill=WHITE,width=lw)
        d.polygon([(cx2,y0+1),(cx2-aw//3,y0+aw//2),(cx2+aw//3,y0+aw//2)],fill=WHITE)
        d.line([cx2,my+2,cx2,y0+sz-aw//2],fill=WHITE,width=lw)
        d.polygon([(cx2,y0+sz-1),(cx2-aw//3,y0+sz-aw//2),(cx2+aw//3,y0+sz-aw//2)],fill=WHITE)
    def _ico_lap(x0,y0,sz):
        sq=max(2,sz//4)
        for rr in range(4):
            for cc in range(4):
                if (rr+cc)%2==0: d.rectangle([x0+cc*sq,y0+rr*sq,x0+cc*sq+sq-1,y0+rr*sq+sq-1],fill=WHITE)
    def _ico_timer(x0,y0,sz):
        cx2,cy2=x0+sz//2,y0+sz//2; r2=sz//2-1; lw=max(2,sz//7)
        d.ellipse([cx2-r2,cy2-r2,cx2+r2,cy2+r2],fill=None,outline=WHITE,width=lw)
        a_h=_m.radians(-60); d.line([cx2,cy2,cx2+int(r2*0.5*_m.cos(a_h)),cy2+int(r2*0.5*_m.sin(a_h))],fill=WHITE,width=max(2,sz//6))
        a_m=_m.radians(-90); d.line([cx2,cy2,cx2+int(r2*0.72*_m.cos(a_m)),cy2+int(r2*0.72*_m.sin(a_m))],fill=WHITE,width=max(2,sz//7))

    ICONS8 = [_ico_speed,_ico_rpm,_ico_gear,_ico_throttle,_ico_brake,_ico_glat,_ico_glon,_ico_lap,_ico_timer]

    timer_str, _, timer_lbl = get_timer_display(ts, laps, seconds_only=True)
    lap_num = "—"
    for i,(st,et,lt) in enumerate(laps,1):
        if st<=ts<et: lap_num=str(i)
        elif ts>=et and ts<et+5: lap_num=str(i)

    channels = [
        ("km/h",   str(int(speed))),
        ("RPM",    str(int(rpm))),
        ("Gear",   str(gear) if gear>0 else "N"),
        ("Throttle",f"{int(throttle)}%"),
        ("Brake",  f"{int(brake_pct or 0)}%"),
        ("Lat G",  f"{g_lat:+.2f}"),
        ("Lon G",  f"{-g_long:+.2f}"),
        ("Lap",    lap_num),
        (timer_lbl, timer_str),
    ]

    fnt_lbl7 = fi(SZ_LBL)

    for col_i,(label,value) in enumerate(channels):
        col_x  = PAD7 + col_i*COL_W7
        cx_col = col_x + COL_W7//2

        # Icon left of label
        lbl_bb  = d.textbbox((0,0),label,font=fnt_lbl7)
        lbl_w   = lbl_bb[2]-lbl_bb[0]; lbl_h2=lbl_bb[3]-lbl_bb[1]
        gap_il  = max(3,int(3*s))
        row_w   = ICO+gap_il+lbl_w
        row_x   = cx_col - row_w//2
        row_cy  = ICO_Y7 + ICO//2

        ix0=row_x; iy0=row_cy-ICO//2
        for _dx in range(-OW,OW+1,max(1,OW)):
            for _dy in range(-OW,OW+1,max(1,OW)):
                if _dx or _dy: ICONS8[col_i](ix0+_dx,iy0+_dy,ICO)
        ICONS8[col_i](ix0,iy0,ICO)

        lbl_x=row_x+ICO+gap_il; lbl_y=row_cy-lbl_h2//2
        for _dx in range(-OW,OW+1):
            for _dy in range(-OW,OW+1):
                if _dx or _dy: d.text((lbl_x+_dx,lbl_y+_dy),label,font=fnt_lbl7,fill=BLACK)
        d.text((lbl_x,lbl_y),label,font=fnt_lbl7,fill=WHITE)

        _fsz=SZ_VAL
        while _fsz>8:
            _bb=d.textbbox((0,0),value,font=fi(_fsz))
            if (_bb[2]-_bb[0])<COL_W7-int(6*s): break
            _fsz-=1
        _tc_outlined(d,(cx_col,VAL_Y7+_val_h//2),value,fi(_fsz),WHITE,BLACK,OW)

        if col_i<N_COLS-1:
            div_x=col_x+COL_W7
            d.line([(div_x,ICO_Y7),(div_x,VAL_Y7+_val_h)],fill=(80,80,80),width=max(1,int(1*s)))

    # ── STYLE 4 COLUMN (left side, below row) ────────────────────────────────
    N_ROWS4  = 9
    PAD4     = int(20*s)
    avail4   = (_h - ROW_H) - 2*PAD4
    SZ_LBL4  = max(8, int(avail4 / (N_ROWS4 * 6.0)))
    ICO4     = int(SZ_LBL4 * 2.2)
    SZ_VAL4  = int(SZ_LBL4 * 3.0)
    GAP4     = max(2, int(SZ_LBL4 * 0.30))
    ROW4     = max(3, int(SZ_LBL4 * 0.50))
    OW4      = max(1, int(2.4*s))

    X4 = PAD4
    y4 = ROW_H + PAD4

    fnt_lbl4 = fi(SZ_LBL4)

    for col_i,(label,value) in enumerate(channels):
        # Icon left of label on same row
        lbl_bb4  = d.textbbox((0,0),label,font=fnt_lbl4)
        lbl_h4   = lbl_bb4[3]-lbl_bb4[1]
        row_cy4  = y4 + ICO4//2
        ix4=X4; iy4=row_cy4-ICO4//2
        ICONS8[col_i](ix4,iy4,ICO4)

        lbl_x4=X4+ICO4+max(3,ICO4//4); lbl_y4=row_cy4-lbl_h4//2
        d.text((lbl_x4,lbl_y4),label,font=fnt_lbl4,fill=WHITE)

        y4 += ICO4 + GAP4

        # Value below
        _fsz4=SZ_VAL4
        while _fsz4>8:
            _bb=d.textbbox((0,0),value,font=fi(_fsz4))
            if (_bb[2]-_bb[0])<COL_W-int(8*s): break
            _fsz4-=1
        _tc(d,(X4+COL_W//2, y4+SZ_VAL4//2),value,fi(_fsz4),WHITE)
        y4 += SZ_VAL4 + ROW4

    return img


def _build_frame_style7(img, d, rpm, throttle, speed, gear,
                         g_lat, g_long, ts, laps, _w, _h, s,
                         brake_pct, rpm_max, peak_rpm, P):
    """Style 7 — horizontal layout: 9 channels spread across frame width.
    Each column: icon + label on top row, large value on bottom row."""

    CHROMA = P.get("chroma", (255,0,255))
    d.rectangle([0, 0, _w, _h], fill=CHROMA)
    WHITE = P["white"]

    N_COLS  = 9
    PAD     = int(12*s)
    COL_W   = (_w - PAD*2) // N_COLS

    # Layout: large label, small icon beside it, large value below
    SZ_LBL_MAX = max(18, int(60*s))  # max label font size
    ICO_MAX    = max(8,  int(20*s))  # small icon
    SZ_VAL  = max(18, min(int(COL_W * 0.52), int(100*s)))  # larger, fills column

    # Vertical layout — use max sizes for initial positioning
    _ico_h  = ICO_MAX
    _lbl_h  = SZ_LBL_MAX + int(4*s)
    _val_h  = SZ_VAL + int(4*s)
    _inner  = _ico_h + int(4*s) + _lbl_h + int(8*s) + _val_h
    _top    = (_h - _inner) // 2

    ICO_Y   = _top
    LBL_Y   = ICO_Y + _ico_h + int(4*s)
    VAL_Y   = LBL_Y + _lbl_h + int(8*s)

    # Icon definitions (same as Style 4)
    def _icon_speedometer(x0, y0, sz):
        cx2,cy2,r2 = x0+sz//2,y0+sz//2,sz//2-2
        lw=max(2,sz//7)
        d.arc([cx2-r2,cy2-r2,cx2+r2,cy2+r2],200,340,fill=WHITE,width=lw)
        import math as _m; a=_m.radians(290)
        d.line([cx2,cy2,cx2+int(r2*0.82*_m.cos(a)),cy2+int(r2*0.82*_m.sin(a))],fill=WHITE,width=max(2,sz//6))
        d.ellipse([cx2-sz//7,cy2-sz//7,cx2+sz//7,cy2+sz//7],fill=WHITE)
    def _icon_tachometer(x0, y0, sz):
        cx2,cy2,r2=x0+sz//2,y0+sz//2,sz//2-2; lw=max(2,sz//7)
        d.arc([cx2-r2,cy2-r2,cx2+r2,cy2+r2],200,340,fill=WHITE,width=lw)
        import math as _m; a=_m.radians(240)
        d.line([cx2,cy2,cx2+int(r2*0.82*_m.cos(a)),cy2+int(r2*0.82*_m.sin(a))],fill=WHITE,width=max(2,sz//6))
        d.ellipse([cx2-sz//7,cy2-sz//7,cx2+sz//7,cy2+sz//7],fill=WHITE)
        for ang in [200,230,260,290,320,340]:
            a2=_m.radians(ang)
            d.line([cx2+int((r2-sz//4)*_m.cos(a2)),cy2+int((r2-sz//4)*_m.sin(a2)),
                    cx2+int(r2*_m.cos(a2)),cy2+int(r2*_m.sin(a2))],fill=WHITE,width=max(2,sz//9))
    def _icon_gear(x0, y0, sz):
        import math as _m; cx2,cy2=x0+sz//2,y0+sz//2; r_out=sz//2-1; r_in=int(sz*0.28)
        pts=[(cx2+( r_out if i%4<2 else int(r_out*0.70))*_m.cos(_m.radians(i*360/32)),
               cy2+(r_out if i%4<2 else int(r_out*0.70))*_m.sin(_m.radians(i*360/32))) for i in range(32)]
        d.polygon(pts,fill=WHITE); d.ellipse([cx2-r_in,cy2-r_in,cx2+r_in,cy2+r_in],fill=CHROMA)
    def _icon_throttle(x0, y0, sz):
        bw=max(2,sz//5); gap=max(1,sz//8)
        for i,h in enumerate([int(sz*0.4),int(sz*0.65),int(sz*0.9),int(sz*0.55)]):
            bx=x0+i*(bw+gap); d.rectangle([bx,y0+sz-h,bx+bw,y0+sz],fill=WHITE)
    def _icon_brake(x0, y0, sz):
        import math as _m; cx2,cy2=x0+sz//2,y0+sz//2; r_out=sz//2-1; r_in=int(sz*0.22)
        d.ellipse([cx2-r_out,cy2-r_out,cx2+r_out,cy2+r_out],fill=WHITE)
        d.ellipse([cx2-r_in,cy2-r_in,cx2+r_in,cy2+r_in],fill=CHROMA)
        for ang in [0,60,120,180,240,300]:
            a=_m.radians(ang)
            d.line([cx2+int(r_in*_m.cos(a)),cy2+int(r_in*_m.sin(a)),
                    cx2+int(r_out*_m.cos(a)),cy2+int(r_out*_m.sin(a))],fill=CHROMA,width=max(1,sz//8))
    def _icon_g_lat(x0, y0, sz):
        cy2=y0+sz//2; mx=x0+sz//2; aw=sz//2; lw=max(2,sz//6)
        d.line([x0+aw//2,cy2,mx-2,cy2],fill=WHITE,width=lw)
        d.polygon([(x0+1,cy2),(x0+aw//2,cy2-aw//3),(x0+aw//2,cy2+aw//3)],fill=WHITE)
        d.line([mx+2,cy2,x0+sz-aw//2,cy2],fill=WHITE,width=lw)
        d.polygon([(x0+sz-1,cy2),(x0+sz-aw//2,cy2-aw//3),(x0+sz-aw//2,cy2+aw//3)],fill=WHITE)
    def _icon_g_lon(x0, y0, sz):
        cx2=x0+sz//2; my=y0+sz//2; aw=sz//2; lw=max(2,sz//6)
        d.line([cx2,y0+aw//2,cx2,my-2],fill=WHITE,width=lw)
        d.polygon([(cx2,y0+1),(cx2-aw//3,y0+aw//2),(cx2+aw//3,y0+aw//2)],fill=WHITE)
        d.line([cx2,my+2,cx2,y0+sz-aw//2],fill=WHITE,width=lw)
        d.polygon([(cx2,y0+sz-1),(cx2-aw//3,y0+sz-aw//2),(cx2+aw//3,y0+sz-aw//2)],fill=WHITE)
    def _icon_lap(x0, y0, sz):
        sq=max(2,sz//4)
        for row2 in range(4):
            for col2 in range(4):
                if (row2+col2)%2==0: d.rectangle([x0+col2*sq,y0+row2*sq,x0+col2*sq+sq-1,y0+row2*sq+sq-1],fill=WHITE)
    def _icon_timer(x0, y0, sz):
        import math as _m; cx2,cy2=x0+sz//2,y0+sz//2; r2=sz//2-2; lw=max(2,sz//7)
        d.ellipse([cx2-r2,cy2-r2,cx2+r2,cy2+r2],fill=None,outline=WHITE,width=lw)
        a_h=_m.radians(-60); d.line([cx2,cy2,cx2+int(r2*0.5*_m.cos(a_h)),cy2+int(r2*0.5*_m.sin(a_h))],fill=WHITE,width=max(2,sz//6))
        a_m=_m.radians(-90); d.line([cx2,cy2,cx2+int(r2*0.72*_m.cos(a_m)),cy2+int(r2*0.72*_m.sin(a_m))],fill=WHITE,width=max(2,sz//7))

    ICONS = [_icon_speedometer,_icon_tachometer,_icon_gear,_icon_throttle,
             _icon_brake,_icon_g_lat,_icon_g_lon,_icon_lap,_icon_timer]

    # Timer / lap
    timer_str, _, timer_lbl = get_timer_display(ts, laps, seconds_only=True)
    lap_num = "—"
    for i,(st,et,lt) in enumerate(laps,1):
        if st<=ts<et: lap_num=str(i)
        elif ts>=et and ts<et+5: lap_num=str(i)

    channels = [
        ("km/h",        str(int(speed))),
        ("RPM",         str(int(rpm))),
        ("Gear",        str(gear) if gear>0 else "N"),
        ("Throttle",    f"{int(throttle)}%"),
        ("Brake",       f"{int(brake_pct or 0)}%"),
        ("Lat G",       f"{g_lat:+.2f}"),
        ("Lon G",       f"{-g_long:+.2f}"),
        ("Lap",         lap_num),
        (timer_lbl,     timer_str),
    ]

    BLACK   = (0, 0, 0)
    OW      = max(2, int(4*s))   # thicker outline for clarity

    def _draw_outlined(draw_fn):
        """Draw something in black offset in 8 dirs, then white at centre."""
        from PIL import Image as _Img
        # Draw black layer
        for _dx in range(-OW, OW+1):
            for _dy in range(-OW, OW+1):
                if _dx or _dy:
                    draw_fn(BLACK, _dx, _dy)
        # Draw white on top
        draw_fn(WHITE, 0, 0)

    for col_i, (label, value) in enumerate(channels):
        col_x  = PAD + col_i * COL_W
        cx_col = col_x + COL_W//2

        # Shrink label font to fit within column (with icon)
        ICO = ICO_MAX
        _gap_il = max(2, int(3*s))
        _lbl_fsz = SZ_LBL_MAX
        while _lbl_fsz > 8:
            lbl_bb = d.textbbox((0,0), label, font=fb(_lbl_fsz))
            lbl_w = lbl_bb[2]-lbl_bb[0]; lbl_h = lbl_bb[3]-lbl_bb[1]
            if ICO + _gap_il + lbl_w <= COL_W - int(4*s): break
            _lbl_fsz -= 1
        lbl_bb  = d.textbbox((0,0), label, font=fb(_lbl_fsz))
        lbl_w   = lbl_bb[2]-lbl_bb[0]; lbl_h = lbl_bb[3]-lbl_bb[1]
        _row_w  = ICO + _gap_il + lbl_w
        _row_x  = cx_col - _row_w//2   # left edge of icon+label row
        _row_cy = LBL_Y + (ICO)//2      # vertical centre of label row

        # Icon — outlined, left side
        ix0 = _row_x
        iy0 = _row_cy - ICO//2
        for _dx in range(-OW, OW+1, max(1,OW)):
            for _dy in range(-OW, OW+1, max(1,OW)):
                if _dx or _dy:
                    ICONS[col_i](ix0+_dx, iy0+_dy, ICO)
        ICONS[col_i](ix0, iy0, ICO)

        # Label — bold, outlined, right of icon
        lbl_x = _row_x + ICO + _gap_il
        lbl_y = _row_cy - lbl_h//2
        for _dx in range(-OW, OW+1):
            for _dy in range(-OW, OW+1):
                if _dx or _dy:
                    d.text((lbl_x+_dx, lbl_y+_dy), label, font=fb(_lbl_fsz), fill=BLACK)
        d.text((lbl_x, lbl_y), label, font=fb(_lbl_fsz), fill=WHITE)

        # Value — bold, outlined, fills available column width
        _fsz = SZ_VAL
        while _fsz > 8:
            _bb = d.textbbox((0,0), value, font=fb(_fsz))
            if (_bb[2]-_bb[0]) < COL_W - int(6*s): break
            _fsz -= 1
        _tc_outlined(d, (cx_col, VAL_Y + _val_h//2), value, fb(_fsz), WHITE, BLACK, OW)

        # Subtle column divider
        if col_i < N_COLS-1:
            div_x = col_x + COL_W
            d.line([(div_x, ICO_Y), (div_x, VAL_Y+_val_h)],
                   fill=(80,80,80), width=max(1,int(1*s)))

    return img


def _build_frame_style4(img, d, rpm, throttle, speed, gear,
                         g_lat, g_long, ts, laps, _w, _h, s,
                         brake_pct, rpm_max, peak_rpm, P):
    """Style 4 — full-frame transparent overlay, white italic column."""

    # Full transparent background (chroma key green)
    CHROMA = P.get("chroma", (255,0,255))
    d.rectangle([0, 0, _w, _h], fill=CHROMA)

    WHITE = P["white"]
    DIM   = P["dim"]
    AMBER = P["amber"]
    GREEN = P["green"]
    RED   = P["red"]

    PAD   = int(28*s)
    X     = PAD

    # 9 rows — icon+label on one line, large value below, fill full height
    N_ROWS   = 9
    available = _h - 2*PAD
    # Row = SZ_LBL + GAP + SZ_VAL + ROW_GAP
    # VAL=2.2*LBL, GAP=0.2*LBL, RGAP=0.3*LBL => factor=3.7
    SZ_LBL   = max(14, int(available / (N_ROWS * 3.7)))
    ICO      = SZ_LBL  # icon same height as label text
    SZ_VAL   = int(SZ_LBL * 2.2)
    GAP      = max(2, int(SZ_LBL * 0.20))
    ROW      = max(3, int(SZ_LBL * 0.30))

    y = PAD

    def _icon_speedometer(x0, y0, sz):
        """Arc + needle speedometer."""
        cx2, cy2, r2 = x0+sz//2, y0+sz//2, sz//2-2
        lw = max(2, sz//7)
        d.arc([cx2-r2,cy2-r2,cx2+r2,cy2+r2], 200, 340, fill=WHITE, width=lw)
        import math as _m
        a = _m.radians(290)
        d.line([cx2, cy2, cx2+int(r2*0.82*_m.cos(a)), cy2+int(r2*0.82*_m.sin(a))],
               fill=WHITE, width=max(2,sz//6))
        d.ellipse([cx2-sz//7,cy2-sz//7,cx2+sz//7,cy2+sz//7], fill=WHITE)

    def _icon_tachometer(x0, y0, sz):
        """Arc + steeper needle for RPM."""
        cx2, cy2, r2 = x0+sz//2, y0+sz//2, sz//2-2
        lw = max(2, sz//7)
        d.arc([cx2-r2,cy2-r2,cx2+r2,cy2+r2], 200, 340, fill=WHITE, width=lw)
        import math as _m
        a = _m.radians(240)
        d.line([cx2, cy2, cx2+int(r2*0.82*_m.cos(a)), cy2+int(r2*0.82*_m.sin(a))],
               fill=WHITE, width=max(2,sz//6))
        d.ellipse([cx2-sz//7,cy2-sz//7,cx2+sz//7,cy2+sz//7], fill=WHITE)
        for ang in [200, 230, 260, 290, 320, 340]:
            a2 = _m.radians(ang)
            d.line([cx2+int((r2-sz//4)*_m.cos(a2)), cy2+int((r2-sz//4)*_m.sin(a2)),
                    cx2+int(r2*_m.cos(a2)),           cy2+int(r2*_m.sin(a2))],
                   fill=WHITE, width=max(2,sz//9))

    def _icon_gear(x0, y0, sz):
        """Gear shape — circle with teeth stubs."""
        import math as _m
        cx2, cy2 = x0+sz//2, y0+sz//2
        r_out = sz//2-1; r_in = int(sz*0.28)
        teeth = 8
        pts = []
        for i in range(teeth*4):
            angle = _m.radians(i*360/(teeth*4))
            r = r_out if (i%4 < 2) else int(r_out*0.70)
            pts.append((cx2+r*_m.cos(angle), cy2+r*_m.sin(angle)))
        d.polygon(pts, fill=WHITE)
        d.ellipse([cx2-r_in,cy2-r_in,cx2+r_in,cy2+r_in], fill=CHROMA)

    def _icon_throttle(x0, y0, sz):
        """Upward bar chart (power/acceleration)."""
        bw = max(2, sz//5); gap = max(1, sz//8)
        heights = [int(sz*0.4), int(sz*0.65), int(sz*0.9), int(sz*0.55)]
        for i, h in enumerate(heights):
            bx = x0 + i*(bw+gap)
            d.rectangle([bx, y0+sz-h, bx+bw, y0+sz], fill=WHITE)

    def _icon_brake(x0, y0, sz):
        """Disc brake — circle with inner ring and caliper lines."""
        cx2, cy2 = x0+sz//2, y0+sz//2
        r_out = sz//2-1; r_in = int(sz*0.22)
        d.ellipse([cx2-r_out,cy2-r_out,cx2+r_out,cy2+r_out], fill=WHITE)
        d.ellipse([cx2-r_in, cy2-r_in, cx2+r_in, cy2+r_in], fill=CHROMA)
        # Spokes
        import math as _m
        for ang in [0, 60, 120, 180, 240, 300]:
            a = _m.radians(ang)
            d.line([cx2+int(r_in*_m.cos(a)),  cy2+int(r_in*_m.sin(a)),
                    cx2+int(r_out*_m.cos(a)), cy2+int(r_out*_m.sin(a))],
                   fill=CHROMA, width=max(1,sz//8))

    def _icon_g_lat(x0, y0, sz):
        """Left-right arrows for lateral G."""
        cy2 = y0+sz//2; mx = x0+sz//2
        aw = sz//2
        lw = max(2, sz//6)
        d.line([x0+aw//2, cy2, mx-2, cy2], fill=WHITE, width=lw)
        d.polygon([(x0+1,cy2), (x0+aw//2,cy2-aw//3), (x0+aw//2,cy2+aw//3)], fill=WHITE)
        d.line([mx+2, cy2, x0+sz-aw//2, cy2], fill=WHITE, width=lw)
        d.polygon([(x0+sz-1,cy2), (x0+sz-aw//2,cy2-aw//3), (x0+sz-aw//2,cy2+aw//3)], fill=WHITE)

    def _icon_g_lon(x0, y0, sz):
        """Up-down arrows for longitudinal G."""
        cx2 = x0+sz//2; my = y0+sz//2
        aw = sz//2; lw = max(2, sz//6)
        d.line([cx2, y0+aw//2, cx2, my-2], fill=WHITE, width=lw)
        d.polygon([(cx2,y0+1),(cx2-aw//3,y0+aw//2),(cx2+aw//3,y0+aw//2)], fill=WHITE)
        d.line([cx2, my+2, cx2, y0+sz-aw//2], fill=WHITE, width=lw)
        d.polygon([(cx2,y0+sz-1),(cx2-aw//3,y0+sz-aw//2),(cx2+aw//3,y0+sz-aw//2)], fill=WHITE)

    def _icon_lap(x0, y0, sz):
        """Chequered flag pattern."""
        sq = max(2, sz//4)
        for row in range(4):
            for col in range(4):
                if (row+col)%2==0:
                    d.rectangle([x0+col*sq, y0+row*sq,
                                  x0+col*sq+sq-1, y0+row*sq+sq-1], fill=WHITE)

    def _icon_timer(x0, y0, sz):
        """Clock face — circle with hands."""
        cx2, cy2 = x0+sz//2, y0+sz//2; r2 = sz//2-2
        lw = max(2, sz//7)
        d.ellipse([cx2-r2,cy2-r2,cx2+r2,cy2+r2], fill=None, outline=WHITE, width=lw)
        import math as _m
        a_h = _m.radians(-60)
        d.line([cx2,cy2, cx2+int(r2*0.5*_m.cos(a_h)), cy2+int(r2*0.5*_m.sin(a_h))],
               fill=WHITE, width=max(2,sz//6))
        a_m = _m.radians(-90)
        d.line([cx2,cy2, cx2+int(r2*0.72*_m.cos(a_m)), cy2+int(r2*0.72*_m.sin(a_m))],
               fill=WHITE, width=max(2,sz//7))

    ICONS = [_icon_speedometer, _icon_tachometer, _icon_gear,
             _icon_throttle, _icon_brake, _icon_g_lat, _icon_g_lon,
             _icon_lap, _icon_timer]
    _row_idx = [0]

    OW_s4 = max(2, int(3*s))  # outline width
    def draw_row(label, value, val_col=WHITE):
        nonlocal y
        fnt_l = fb(SZ_LBL)
        fnt_v = fb(SZ_VAL)
        icon_fn = ICONS[_row_idx[0] % len(ICONS)]
        # Icon — small, left of label
        _iy = y + (SZ_LBL - ICO)//2
        icon_fn(X, _iy, ICO)
        _row_idx[0] += 1
        # Label — bold, outlined
        _lx = X + ICO + max(3, ICO//3)
        for _dx in range(-OW_s4, OW_s4+1):
            for _dy in range(-OW_s4, OW_s4+1):
                if _dx or _dy:
                    d.text((_lx+_dx, y+_dy), label, font=fnt_l, fill=(0,0,0))
        d.text((_lx, y), label, font=fnt_l, fill=WHITE)
        y += SZ_LBL + GAP
        # Value — bold, outlined
        for _dx in range(-OW_s4, OW_s4+1):
            for _dy in range(-OW_s4, OW_s4+1):
                if _dx or _dy:
                    d.text((X+_dx, y+_dy), value, font=fnt_v, fill=(0,0,0))
        d.text((X, y), value, font=fnt_v, fill=val_col)
        y += SZ_VAL + ROW

    # Timer / lap info
    timer_str, timer_col, timer_lbl = get_timer_display(ts, laps, seconds_only=True)
    lap_num = "—"
    for i,(st,et,lt) in enumerate(laps,1):
        if st <= ts < et: lap_num = str(i)
        elif ts >= et and ts < et+5: lap_num = str(i)

    draw_row("km/h",     str(int(speed)))
    draw_row("RPM",      str(int(rpm)))
    draw_row("Gear",     str(gear) if gear > 0 else "N")
    draw_row("Throttle", f"{int(throttle)}%")
    draw_row("Brake",    f"{int(brake_pct or 0)}%")
    draw_row("Lat G",    f"{g_lat:+.2f}")
    draw_row("Lon G",    f"{-g_long:+.2f}")
    draw_row("Lap",      lap_num)
    draw_row(timer_lbl,  timer_str)

    return img





def _build_frame_style14(img, d, rpm, throttle, speed, gear,
                          g_lat, g_long, ts, laps, _w, _h, s,
                          brake_pct, rpm_max, peak_rpm, P,
                          trace_glat=None, trace_glong=None, trace_speed=None,
                          trace_throttle=None, trace_brake=None, trace_gear=None,
                          chan=None):
    """Style 14 — two box columns left (info + data), full-height overlaid time plots right."""
    WHITE  = P["white"]; GREY = P["grey"]; BLACK = P["panel"]
    CHROMA = P["chroma"]
    PAD = 14; INNER = 6

    # Strip: bottom 75% of frame
    PH = int(_h * 0.75); PY0 = _h - PH
    d.rectangle([0, 0, _w, _h], fill=CHROMA)
    d.rectangle([0, PY0, _w, _h], fill=(12, 12, 18))
    d.line([(0, PY0), (_w, PY0)], fill=(50, 60, 80), width=2)

    n = len(trace_glat) if trace_glat else 1

    def _norm(arr, lo, hi):
        span = hi - lo or 1
        return [max(0.0, min(1.0, (v-lo)/span)) for v in (arr or [])] or [0.5]*n

    # Lap info
    _lap_num = 0; _last_lt = None
    for _li,(_st,_et,_lt) in enumerate(laps,1):
        if _st <= ts: _lap_num = _li
        if ts >= _et: _last_lt = _lt
    _lt_m,_lt_s = divmod(_last_lt,60) if _last_lt else (0,0)
    _lt_str  = f"{int(_lt_m)}:{_lt_s:05.2f}" if _last_lt else "--:--.--"
    # Live lap time (elapsed in current lap, or recent completed lap)
    _timer_str14, _timer_col14, _ = get_timer_display(ts, laps, P=P)
    _pk_str  = str(int(peak_rpm)) if peak_rpm else "—"

    # ── TWO COLUMNS + LAP/TIMER panels over the plot ─────────────────────────
    # Col A: RPM, Peak RPM, Speed, Gear
    # Col B: TPS, Brake, Lat, Lon, [Channel A], [Channel B]  (only col B is plotted)
    # Lap + Lap Time: two horizontal panels, top-right corner of the plot
    col2 = [
        ("RPM",      str(int(rpm)),                  P["amber"]),
        ("Peak RPM", _pk_str,                        P["gold"]),
        ("km/h",     str(int(speed)),                P["cyan"]),
        ("Gear",     str(gear) if gear > 0 else "N", P["orange"]),
    ]
    # Column 3 — plotted channels (label, value, colour, normalised-trail)
    col3 = [
        ("TPS",   f"{int(throttle)}%",       P["green"],
         _norm(trace_throttle, 0, 100) if trace_throttle else [(throttle/100)]*n),
        ("Brake", f"{int(brake_pct or 0)}%", P["red"],
         _norm(trace_brake, 0, 100) if trace_brake else [((brake_pct or 0)/100)]*n),
        ("Lat G", f"{g_lat:+.2f}",           P["purple"],
         _norm(trace_glat, -3, 3) if trace_glat else [0.5]*n),
        ("Lon G", f"{-g_long:+.2f}",         P["blue"],
         _norm(trace_glong, -3, 3) if trace_glong else [0.5]*n),
    ]
    # Optional channels A/B appended to column 3 (plotted too) when selected
    _ch_cols = [(80,200,255), (255,180,40)]   # cyan, gold — mix of colours
    if chan:
        for _ci,(_vkey,_lkey) in enumerate([("A_val","A_lbl"),("B_val","B_lbl")]):
            _val = chan.get(_vkey)
            if _val is None or (isinstance(_val,float) and math.isnan(_val)):
                continue
            _lbl = chan.get(_lkey) or ("A" if _ci==0 else "B")
            col3.append((_lbl, f"{int(round(_val))}", _ch_cols[_ci],
                         [0.5]*n))   # flat trail (single-value channel)

    BOX_W = int(_w * 0.115)
    CB_X0 = PAD;                      CB_X1 = CB_X0 + BOX_W
    CC_X0 = CB_X1 + PAD;              CC_X1 = CC_X0 + BOX_W
    PLT_X0 = CC_X1 + PAD;             PLT_X1 = _w - PAD
    PLT_Y0 = PY0 + PAD;               PLT_Y1 = _h - PAD
    PLT_W  = PLT_X1 - PLT_X0;         PLT_H  = PLT_Y1 - PLT_Y0

    def _draw_col(items, x0, x1):
        nb = len(items)
        bh = (PH - PAD*(nb+1)) // nb
        bw = x1 - x0
        bx = (x0+x1)//2
        for i,it in enumerate(items):
            lbl,val,col = it[0],it[1],it[2]
            by0 = PY0+PAD+i*(bh+PAD); by1 = by0+bh
            lf = max(7, int(bh*0.26))
            while lf > 7 and d.textbbox((0,0),lbl,font=fc(lf))[2] > bw-INNER*2:
                lf -= 1
            vf = max(9, int(bh*0.46))
            while vf > 9 and d.textbbox((0,0),val,font=fb(vf))[2] > bw-INNER*2:
                vf -= 1
            d.rounded_rectangle([x0,by0,x1,by1],radius=5,fill=(16,18,26))
            d.rounded_rectangle([x0,by0,x1,by1],radius=5,outline=col,width=2)
            d.text((bx,by0+INNER+lf//2),lbl,font=fc(lf),fill=col,anchor="mm")
            d.text((bx,by1-INNER-vf//2),val,font=fb(vf),fill=WHITE,anchor="mm")

    # Column-3 panel height (used to size the Lap/Lap Time panels)
    _c3_bh = (PH - PAD*(len(col3)+1)) // len(col3)

    _draw_col(col2, CB_X0, CB_X1)
    _draw_col(col3, CC_X0, CC_X1)

    # Plot background
    d.rounded_rectangle([PLT_X0,PLT_Y0,PLT_X1,PLT_Y1],radius=6,fill=(8,10,14))
    d.rounded_rectangle([PLT_X0,PLT_Y0,PLT_X1,PLT_Y1],radius=6,outline=(35,40,50),width=1)
    for _gi in range(1, 11):
        _gy = PLT_Y0 + int(PLT_H * _gi / 10)
        _col_g = (200,200,210) if _gi == 5 else (130,130,140)
        _lw_g = 2 if _gi == 5 else 1
        d.line([(PLT_X0+4,_gy),(PLT_X1-4,_gy)], fill=_col_g, width=_lw_g)

    # Draw overlaid trails — ONLY column 3 channels
    lw = max(3, int(3.75*s))
    for it in col3:
        lbl,val,col,trail = it
        n_pts = len(trail)
        if n_pts < 2: continue
        pts = []
        for j,v in enumerate(trail):
            px = PLT_X0+4+int((PLT_W-8)*j/(n_pts-1))
            py = PLT_Y1-4-int((PLT_H-8)*v)
            pts.append((px, max(PLT_Y0+2,min(PLT_Y1-2,py))))
        for j in range(len(pts)-1):
            d.line([pts[j],pts[j+1]],fill=col,width=lw)
        dr = max(3,int(4*s))
        d.ellipse([pts[-1][0]-dr,pts[-1][1]-dr,pts[-1][0]+dr,pts[-1][1]+dr],fill=col)

    # ── LAP + LAP TIME — two horizontal panels, top-right of the plot ────────
    # Fixed compact size (independent of channel count): sized to the content.
    _lt_lf = max(9, int(20*s))        # label font
    _lt_vf = max(12, int(30*s))       # value font
    # Panel width = widest of label / "Lap Time" / a sample time value, + padding
    _lt_inner_pad = int(14*s)
    _w_lap   = d.textbbox((0,0), "Lap Time", font=fc(_lt_lf))[2]
    _w_time  = d.textbbox((0,0), "0:00.00",  font=fb(_lt_vf))[2]
    _lt_pw   = max(_w_lap, _w_time) + _lt_inner_pad*2
    _lt_panel_h = _lt_lf + _lt_vf + int(18*s)   # label + value + padding
    _lt_gap     = PAD
    _lt_y0      = PLT_Y0 + INNER
    _lt_y1      = _lt_y0 + _lt_panel_h
    # Right-anchored: Lap Time on the right, Lap to its left
    _ltt_x1 = PLT_X1 - INNER
    _ltt_x0 = _ltt_x1 - _lt_pw
    _lap_x1 = _ltt_x0 - _lt_gap
    _lap_x0 = _lap_x1 - _lt_pw

    def _hpanel(x0, y0, x1, y1, lbl, val, col):
        bw = x1-x0; bh = y1-y0; bx=(x0+x1)//2
        lf = _lt_lf
        while lf > 7 and d.textbbox((0,0),lbl,font=fc(lf))[2] > bw-INNER*2: lf -= 1
        vf = _lt_vf
        while vf > 9 and d.textbbox((0,0),val,font=fb(vf))[2] > bw-INNER*2: vf -= 1
        d.rounded_rectangle([x0,y0,x1,y1],radius=5,fill=(16,18,26))
        d.rounded_rectangle([x0,y0,x1,y1],radius=5,outline=col,width=2)
        d.text((bx,y0+int(8*s)+lf//2),lbl,font=fc(lf),fill=col,anchor="mm")
        d.text((bx,y1-int(8*s)-vf//2),val,font=fb(vf),fill=WHITE,anchor="mm")

    _hpanel(_lap_x0, _lt_y0, _lap_x1, _lt_y1,
            "Lap", str(_lap_num) if _lap_num else "—", P["cyan"])
    _hpanel(_ltt_x0, _lt_y0, _ltt_x1, _lt_y1,
            "Lap Time", _timer_str14, _timer_col14)

    return img


def _build_frame_style20(img, d, rpm, throttle, speed, gear,
                          g_lat, g_long, ts, laps, _w, _h, s,
                          brake_pct, rpm_max, peak_rpm, P,
                          trace_glat=None, trace_glong=None, trace_speed=None,
                          trace_throttle=None, trace_brake=None, trace_gear=None,
                          chan=None):
    """Style 20 - Dash 9: full-width time plot on top, single panel row along the
    bottom edge. Panels and graph are separate non-overlapping regions; the panel
    row is sized to fit the frame width exactly and reflows when channels are added."""
    WHITE  = P["white"]; GREY = P["grey"]; BLACK = P["panel"]
    CHROMA = P["chroma"]
    PAD = 14; INNER = 6

    d.rectangle([0, 0, _w, _h], fill=CHROMA)

    n = len(trace_glat) if trace_glat else 1
    def _norm(arr, lo, hi):
        span = hi - lo or 1
        return [max(0.0, min(1.0, (v-lo)/span)) for v in (arr or [])] or [0.5]*n

    _lap_num = 0
    for _li,(_st,_et,_lt) in enumerate(laps,1):
        if _st <= ts: _lap_num = _li
    _timer_str, _timer_col, _ = get_timer_display(ts, laps, P=P)
    _pk_str = str(int(peak_rpm)) if peak_rpm else "-"

    G_TPS   = (60, 220, 90)
    G_BRK   = (255, 70, 70)
    G_LAT   = (190, 110, 255)
    G_LON   = (90, 170, 255)
    G_CHANA = (255, 200, 40)
    G_CHANB = (255, 120, 220)

    plot_items = [
        ("TPS",   f"{int(throttle)}%",       G_TPS,
         _norm(trace_throttle, 0, 100) if trace_throttle else [(throttle/100)]*n),
        ("Brake", f"{int(brake_pct or 0)}%", G_BRK,
         _norm(trace_brake, 0, 100) if trace_brake else [((brake_pct or 0)/100)]*n),
        ("Lat G", f"{g_lat:+.2f}",           G_LAT,
         _norm(trace_glat, -3, 3) if trace_glat else [0.5]*n),
        ("Lon G", f"{-g_long:+.2f}",         G_LON,
         _norm(trace_glong, -3, 3) if trace_glong else [0.5]*n),
    ]
    if chan:
        for _ci,(_vkey,_lkey,_gcol) in enumerate(
                [("A_val","A_lbl",G_CHANA),("B_val","B_lbl",G_CHANB)]):
            _val = chan.get(_vkey)
            if _val is None or (isinstance(_val,float) and math.isnan(_val)):
                continue
            _lbl = chan.get(_lkey) or ("A" if _ci==0 else "B")
            plot_items.append((_lbl, f"{int(round(_val))}", _gcol, [0.5]*n))

    info_items = [
        ("Lap",      str(_lap_num) if _lap_num else "-", P["cyan"]),
        ("Lap Time", _timer_str,                         _timer_col),
        ("RPM",      str(int(rpm)),                       P["amber"]),
        ("Peak RPM", _pk_str,                             P["gold"]),
        ("km/h",     str(int(speed)),                     (210,210,220)),
        ("Gear",     str(gear) if gear > 0 else "N",      P["orange"]),
    ]

    all_panels = info_items + [(lbl, val, col) for (lbl, val, col, _tr) in plot_items]
    n_panels = len(all_panels)

    _row_h = int(_h * 0.22)
    ROW_Y0 = _h - _row_h
    ROW_Y1 = _h
    # Proportional cell widths: the Lap Time cell is wider so over-a-minute
    # times (e.g. "1:15.00") render at the same size as the other values.
    _weights = [1.45 if lbl == "Lap Time" else 1.0 for (lbl, val, col) in all_panels]
    _wsum = sum(_weights)
    _edges = [0.0]
    for _w_ in _weights:
        _edges.append(_edges[-1] + _w_)
    _x_at = lambda k: int(round(_edges[k] / _wsum * _w))

    PLT_X0 = PAD;            PLT_X1 = _w - PAD
    PLT_Y0 = PAD;            PLT_Y1 = ROW_Y0 - PAD
    PLT_W  = PLT_X1 - PLT_X0; PLT_H = PLT_Y1 - PLT_Y0

    d.rounded_rectangle([PLT_X0,PLT_Y0,PLT_X1,PLT_Y1],radius=6,fill=(8,10,14))
    d.rounded_rectangle([PLT_X0,PLT_Y0,PLT_X1,PLT_Y1],radius=6,outline=(35,40,50),width=1)
    for _gi in range(1, 10):
        _gy = PLT_Y0 + int(PLT_H * _gi / 10)
        _col_g = (90,90,100) if _gi == 5 else (40,44,52)
        d.line([(PLT_X0+4,_gy),(PLT_X1-4,_gy)], fill=_col_g, width=(2 if _gi==5 else 1))

    lw = max(3, int(3.75*s))
    for (lbl,val,col,trail) in plot_items:
        if len(trail) < 2: continue
        pts = []
        for j,v in enumerate(trail):
            px = PLT_X0+4+int((PLT_W-8)*j/(len(trail)-1))
            py = PLT_Y1-4-int((PLT_H-8)*v)
            pts.append((px, max(PLT_Y0+2,min(PLT_Y1-2,py))))
        for j in range(len(pts)-1):
            d.line([pts[j],pts[j+1]],fill=col,width=lw)
        dr = max(3,int(4*s))
        d.ellipse([pts[-1][0]-dr,pts[-1][1]-dr,pts[-1][0]+dr,pts[-1][1]+dr],fill=col)

    for i,(lbl,val,col) in enumerate(all_panels):
        x0 = _x_at(i)
        x1 = _w if i == n_panels-1 else _x_at(i+1)
        bw = x1 - x0; bx = (x0+x1)//2
        d.rectangle([x0,ROW_Y0,x1,ROW_Y1],fill=(16,18,26))
        d.rectangle([x0,ROW_Y0,x1,ROW_Y1],outline=col,width=2)
        lf = max(8, int(_row_h*0.24))
        while lf > 8 and d.textbbox((0,0),lbl,font=fc(lf))[2] > bw-INNER*2: lf -= 1
        vf = max(10, int(_row_h*0.42))
        while vf > 10 and d.textbbox((0,0),val,font=fb(vf))[2] > bw-INNER*2: vf -= 1
        d.text((bx,ROW_Y0+INNER+lf//2),lbl,font=fc(lf),fill=col,anchor="mm")
        d.text((bx,ROW_Y1-INNER-vf//2),val,font=fb(vf),fill=WHITE,anchor="mm")

    return img


def _draw_trapezoid_stack(d, img, cx, cy, r, s, speed, rpm, peak_rpm, ts, laps, P):
    """Style 15: 2x2 trapezoid grid filling lower gauge hemisphere.
    Boxes span the full gauge circle width at each row height.
    Left col: Lap, Timer. Right col: km/h, RPM."""
    import math as _m
    BLACK  = (0, 0, 0)
    BLUE   = (0, 100, 255)
    WHITE  = (255, 255, 255)
    OW     = max(2, int(3*s))
    PAD    = max(6, int(9*s))

    # Lap info
    _lap_num = 0
    for _li, (_st,_et,_lt) in enumerate(laps,1):
        if _st <= ts: _lap_num = _li
    _timer_str, _timer_col, _timer_lbl = get_timer_display(ts, laps, P=P)

    # Geometry
    ir = int(r * 0.460)
    # Move boxes up — start 25px above gear bottom
    BOX_TOP = cy + ir - int(25*s)
    BOX_BOT = cy + r           # outer rim bottom
    GAP     = max(3, int(5*s))
    TH      = (BOX_BOT - BOX_TOP - GAP) // 2

    # Width: use gauge circle width at BOX_TOP
    _dy_top = max(0, BOX_TOP - cy)
    _dx_top = int(_m.sqrt(max(0, r**2 - _dy_top**2)))
    X_LEFT  = cx - _dx_top
    X_RIGHT = cx + _dx_top
    FULL_W  = X_RIGHT - X_LEFT

    COL_GAP = max(3, int(5*s))
    SLOPE   = int(TH * 0.50)
    TW      = (FULL_W - COL_GAP) // 2 - SLOPE//2

    _vfsz = max(12, int(TH * 0.52))
    _lfsz = max(9,  int(_vfsz * 0.72))

    left_panels = [
        ("Lap",      str(_lap_num) if _lap_num else "—"),
        (_timer_lbl, _timer_str),
    ]
    right_panels = [
        ("km/h", str(int(speed))),
        ("RPM",  str(int(rpm))),
    ]

    # Both cols share same x0 base so left edges align (slopes align)
    x0_L = X_LEFT
    x0_R = X_LEFT + TW + SLOPE + COL_GAP

    def _outlined(pos, txt, font, anchor="lm"):
        x,y = pos
        for dx in range(-OW,OW+1):
            for dy in range(-OW,OW+1):
                if dx or dy: d.text((x+dx,y+dy),txt,font=font,fill=BLACK,anchor=anchor)
        d.text(pos, txt, font=font, fill=WHITE, anchor=anchor)

    def _draw_trap(x0, y0, w, h, slope, label, value):
        poly = [
            (x0 + slope, y0),
            (x0 + slope + w, y0),
            (x0 + w, y0 + h),
            (x0, y0 + h),
        ]
        d.polygon(poly, fill=BLUE)
        d.polygon(poly, outline=(0, 60, 180), width=max(1, int(2*s)))
        _vcy  = y0 + h//2
        _mid  = x0 + slope//2
        _outlined((_mid + PAD, _vcy), label, fb(_lfsz), anchor="lm")
        _outlined((_mid + w - PAD, _vcy), value, fb(_vfsz), anchor="rm")

    for row, (lp, rp) in enumerate(zip(left_panels, right_panels)):
        py0 = BOX_TOP + row*(TH+GAP)
        _draw_trap(x0_L, py0, TW, TH, SLOPE, lp[0], lp[1])
        _draw_trap(x0_R, py0, TW, TH, SLOPE, rp[0], rp[1])


def _build_frame_style13(img, d, rpm, throttle, speed, gear,
                          g_lat, g_long, ts, laps, _w, _h, s,
                          brake_pct, rpm_max, peak_rpm, P,
                          trace_glat=None, trace_glong=None,
                          trace_throttle=None, trace_brake=None,
                          trace_speed=None, speed_colour=False, chan=None):
    """Style 13 — Style 12 variant: slim TPS/Brake bars, g-trace under them."""
    WHITE  = P["white"];  GREY  = P["grey"];   BLACK = P["panel"]
    CYAN   = P["cyan"];   GOLD  = P["gold"]
    GREEN  = P["green"];  RED   = P["red"];    AMBER = P["amber"]
    PURPLE = P["purple"]; BLUE  = P["blue"]
    CHROMA = P["chroma"]
    PAD    = 14

    # Chroma above strip, black strip
    PH = int(_h * 0.70); PY0 = _h - PH
    d.rectangle([0, 0, _w, _h], fill=CHROMA)

    # Right zone: g-trace square, full strip height
    GG_SZ = PH - PAD * 2
    GG_X1 = _w - PAD; GG_X0 = GG_X1 - GG_SZ
    GG_Y0 = PY0 + PAD; GG_Y1 = _h - PAD
    GG_CX = (GG_X0 + GG_X1) // 2; GG_CY = (GG_Y0 + GG_Y1) // 2
    GU = GG_SZ // 4

    # ── Pre-compute SW and gear box geometry (needed for speed + rpm bar) ──
    SW = GG_X0 - 10
    GW = int(SW * 0.26); GCX = SW // 2
    GX0 = GCX - GW//2; GX1 = GCX + GW//2

    # ── Heights: compute bar_h first, speed box matches ───────────────────
    _top_zone_h = PY0 - PAD  # total space above strip
    _bar_h_pre  = max(12, int(_top_zone_h * 0.30))
    SPD_Y1 = PY0 - PAD//2
    SPD_Y0 = SPD_Y1 - _bar_h_pre
    SPD_H  = _bar_h_pre
    # Size box to fit exactly 3 digits with padding
    _spd_str = str(int(speed))
    _spd_fsz = max(10, int(SPD_H * 0.72))
    while _spd_fsz > 10:
        _bb3 = d.textbbox((0,0), "000", font=fb(_spd_fsz))
        if (_bb3[2]-_bb3[0]) < GW - PAD*2: break
        _spd_fsz -= 2
    _spd_bb   = d.textbbox((0,0), "000", font=fb(_spd_fsz))
    _spd_box_w = (_spd_bb[2]-_spd_bb[0]) + PAD*3
    _spd_cx   = (GX0+GX1)//2
    _spd_x0   = _spd_cx - _spd_box_w//2
    _spd_x1   = _spd_cx + _spd_box_w//2
    d.rounded_rectangle([_spd_x0,SPD_Y0,_spd_x1,SPD_Y1], radius=8, fill=(12,12,18))
    d.rounded_rectangle([_spd_x0,SPD_Y0,_spd_x1,SPD_Y1], radius=8, outline=CYAN, width=2)
    d.text((_spd_cx, (SPD_Y0+SPD_Y1)//2), _spd_str,
           font=fb(_spd_fsz), fill=WHITE, anchor="mm")

    # ── RPM BARS ──────────────────────────────────────────────────────────
    _rpm_pct = min(1.0, rpm / max(rpm_max, 1))
    # Stepped colour by RPM relative to max (hard steps, not a blend):
    #   red    ≥ max-500
    #   orange ≥ max-750
    #   yellow ≥ max-1000
    #   green  below that
    if rpm >= rpm_max - 500:
        _rpm_col = (220, 40, 40)      # red
    elif rpm >= rpm_max - 750:
        _rpm_col = (255, 140, 0)      # orange
    elif rpm >= rpm_max - 1000:
        _rpm_col = (240, 210, 0)      # yellow
    else:
        _rpm_col = (30, 200, 60)      # green
    _rpm_bg = (15, 20, 15)
    _bar_h  = _bar_h_pre
    _bar_y1 = SPD_Y1
    _bar_y0 = SPD_Y0
    _brad   = max(3, _bar_h//4)
    _n_ticks = rpm_max // 1000
    _rpm_str = str(int(rpm))
    _pk_str  = str(int(peak_rpm)) if peak_rpm else ""
    _lbl_fsz = max(7, int(_bar_h * 0.72))
    for _bx0, _bx1, _from_left, _lbl, _lbl_anch in [
            (PAD, _spd_x0, True,  _rpm_str, "lm"),
            (_spd_x1, SW-PAD, False, _pk_str,  "rm")]:
        if _bx1 <= _bx0 + 10: continue
        _bw = _bx1 - _bx0
        d.rounded_rectangle([_bx0,_bar_y0,_bx1,_bar_y1], radius=_brad, fill=_rpm_bg)
        _fw = int(_bw * _rpm_pct)
        if _fw > _brad * 2:
            if _from_left:
                d.rounded_rectangle([_bx0,_bar_y0,_bx0+_fw,_bar_y1], radius=_brad, fill=_rpm_col)
            else:
                d.rounded_rectangle([_bx1-_fw,_bar_y0,_bx1,_bar_y1], radius=_brad, fill=_rpm_col)
        d.rounded_rectangle([_bx0,_bar_y0,_bx1,_bar_y1], radius=_brad, outline=(40,50,40), width=1, fill=None)
        # Tick marks
        for _ti in range(1, _n_ticks):
            _tx = _bx0 + int(_bw*_ti/_n_ticks) if _from_left else _bx1 - int(_bw*_ti/_n_ticks)
            d.line([(_tx,_bar_y0+2),(_tx,_bar_y1-2)], fill=(50,70,50), width=1)
        # Label overlay: black pill box, white text
        if _lbl:
            _bb = d.textbbox((0,0), _lbl, font=fb(_lbl_fsz))
            _lw = _bb[2]-_bb[0]; _lh = _bb[3]-_bb[1]
            _pill_pad = max(3, int(_bar_h*0.12))
            if _lbl_anch == "lm":
                _px0=_bx0+4; _px1=_px0+_lw+_pill_pad*2
                _txt_x=_px0+_pill_pad
            else:
                _px1=_bx1-4; _px0=_px1-_lw-_pill_pad*2
                _txt_x=_px1-_pill_pad
            _py0=_bar_y0+2; _py1=_bar_y1-2
            d.rounded_rectangle([_px0,_py0,_px1,_py1], radius=3, fill=(0,0,0))
            d.text((_txt_x, (_bar_y0+_bar_y1)//2), _lbl,
                   font=fb(_lbl_fsz), fill=WHITE, anchor=_lbl_anch)

    # Strip background
    d.rectangle([0, PY0, SW, _h], fill=BLACK)
    d.line([(0, PY0), (SW, PY0)], fill=(50, 60, 80), width=3)

    def _box(x0,y0,x1,y1,outline,fill=(15,15,22),radius=10,stroke=2):
        d.rounded_rectangle([x0,y0,x1,y1],radius=radius,fill=fill)
        d.rounded_rectangle([x0,y0,x1,y1],radius=radius,outline=outline,width=stroke)

    def _tc(pos,text,font,fill): d.text(pos,text,font=font,fill=fill,anchor="mm")

    def _tco(pos,text,font,fill,outline=BLACK,stroke=3):
        x,y=pos
        for dx in range(-stroke,stroke+1):
            for dy in range(-stroke,stroke+1):
                if dx or dy: d.text((x+dx,y+dy),text,font=font,fill=outline,anchor="mm")
        d.text(pos,text,font=font,fill=fill,anchor="mm")

    # ── GEAR: centre ─────────────────────────────────────────────────────────
    _box(GX0, PY0+PAD, GX1, _h-PAD, AMBER, fill=(20,15,5), radius=16, stroke=5)
    _gear_str = str(gear) if gear > 0 else "N"
    gfsz = int(PH * 0.75)
    while gfsz > 10:
        _bb = d.textbbox((0,0), _gear_str, font=fb(gfsz))
        if (_bb[2]-_bb[0]) < GW-PAD*4 and (_bb[3]-_bb[1]) < PH-PAD*2: break
        gfsz -= 2
    # Fit gear number in upper 80% of box, leaving room for label
    _tco((GCX, PY0+PAD+int(((_h-PY0-PAD*2)*0.45))), _gear_str, fb(gfsz), AMBER, BLACK, 5)
    # "GEAR" label at bottom of gear box
    _glbl_fsz = max(8, int((_h-PY0-PAD*2)*0.10))
    d.text((GCX, _h-PAD-int(_glbl_fsz*0.6)), "GEAR",
           font=fc(_glbl_fsz), fill=GREY, anchor="mm")

    # ── LEFT: optional Channel A/B row (top) + lap number + lap time stacked ──
    LX1 = GX0 - PAD; lxc = (PAD + LX1) // 2

    # Collect active channels
    _chan_cells = []
    if chan:
        for _vkey, _lkey, _col in [("A_val","A_lbl",CYAN), ("B_val","B_lbl",GOLD)]:
            _val = chan.get(_vkey)
            if _val is None or (isinstance(_val,float) and math.isnan(_val)):
                continue
            _lbl = chan.get(_lkey) or ""
            _chan_cells.append((_lbl, f"{int(round(_val))}", _col))

    _col_top = PY0 + PAD
    _col_bot = _h - PAD
    _col_h   = _col_bot - _col_top

    if _chan_cells:
        # Channel row occupies ~22% of the column height at the top
        _ch_h   = int(_col_h * 0.22)
        _gapc   = PAD
        _lap_area_top = _col_top + _ch_h + _gapc
        # Two small side-by-side panels (A left, B right)
        _ch_gap = max(6, int(8*s))
        _ch_w   = (LX1 - PAD - _ch_gap) // 2
        _cols_x = [PAD, PAD + _ch_w + _ch_gap]
        for _i, (_lbl, _vstr, _col) in enumerate(_chan_cells):
            _cx0 = _cols_x[_i]; _cx1 = _cx0 + _ch_w
            _box(_cx0, _col_top, _cx1, _col_top+_ch_h, _col, radius=10)
            _cxc = (_cx0+_cx1)//2
            _clf = max(8, int(_ch_h*0.26))
            _cvf = max(12, int(_ch_h*0.46))
            d.text((_cx0+int(10*s), _col_top+int(_ch_h*0.30)), _lbl,
                   font=fb(_clf), fill=_col, anchor="lm")
            _tco((_cxc, _col_top+int(_ch_h*0.68)), _vstr, fb(_cvf), WHITE, BLACK, 2)
    else:
        _lap_area_top = _col_top

    # LAP + LAP TIME fill the remaining column space below the channel row
    lh = ((_col_bot - _lap_area_top) - PAD) // 2
    _box(PAD, _lap_area_top, LX1, _lap_area_top+lh, CYAN, radius=12)
    _tc((lxc, _lap_area_top+int(lh*0.18)), "LAP", fc(int(lh*0.20)), CYAN)
    _lap_num = 0
    for _li, (_st, _et, _lt) in enumerate(laps, 1):
        if _st <= ts: _lap_num = _li
    _tco((lxc, _lap_area_top+int(lh*0.62)),
         str(_lap_num if _lap_num else "—"), fb(int(lh*0.56)), WHITE, BLACK, 3)
    by0l = _lap_area_top + lh + PAD
    _box(PAD, by0l, LX1, _col_bot, GOLD, radius=12)
    lh2 = _col_bot - by0l
    _tc((lxc, by0l+int(lh2*0.18)), "LAP TIME", fc(int(lh2*0.20)), GOLD)
    _lt_str, _lt_col, _ = get_timer_display(ts, laps, P=P)
    _tco((lxc, by0l+int(lh2*0.62)), _lt_str, fb(int(lh2*0.40)), WHITE, BLACK, 2)

    # ── RIGHT: slim TPS + Brake bars, then g-trace below ─────────────────────
    RX0 = GX1 + PAD; RX1 = SW - PAD; RW = RX1 - RX0
    orig_rh = (PH - PAD*5) // 4
    bar_h = max(24, orig_rh // 3)
    brad = max(4, bar_h // 4)
    lbl_fsz = max(8, int(bar_h * 0.50))
    val_fsz = max(7, int(bar_h * 0.45))

    for i, (lbl, pct, col) in enumerate([
            ("TPS",   min(1.0, throttle / 100.0),    GREEN),
            ("Brake", min(1.0, (brake_pct or 0) / 100.0), RED)]):
        by = PY0 + PAD + i * (bar_h + PAD)
        _box(RX0, by, RX1, by+bar_h, col, fill=(12,12,16), radius=brad, stroke=2)
        fill_w = int((RW - brad*2) * pct)
        if fill_w > brad * 2:
            d.rounded_rectangle([RX0, by, RX0+fill_w, by+bar_h],
                                  radius=brad, fill=col)
        midy = by + bar_h // 2
        d.text((RX0+8, midy), lbl, font=fc(lbl_fsz),
               fill=WHITE if pct > 0.35 else col, anchor="lm")
        d.text((RX1-6, midy), f"{int(pct*100)}%",
               font=fc(val_fsz), fill=WHITE, anchor="rm")

    # ── G-TRACE: under the bars ───────────────────────────────────────────────
    gg_y0 = PY0 + PAD + 2*(bar_h + PAD) + PAD//2
    _LBL_BAND5 = int((_h - gg_y0) * 0.18)   # band below plot for big readouts
    gg_x0 = RX0; gg_x1 = RX1
    gsz = min(gg_x1-gg_x0, (_h - PAD - gg_y0) - _LBL_BAND5)
    gg_y1 = gg_y0 + gsz
    gcx = (gg_x0+gg_x1)//2; gcy = (gg_y0+gg_y1)//2
    gu = gsz // 4

    d.rectangle([gg_x0, gg_y0, gg_x1, gg_y1], fill=(10,10,14))
    for gv in [-2,-1,0,1,2]:
        lx=gcx+gv*gu; ly=gcy-gv*gu; cg=WHITE if gv==0 else (40,40,40)
        d.line([(lx,gg_y0+1),(lx,gg_y1-1)], fill=cg, width=1)
        d.line([(gg_x0+1,ly),(gg_x1-1,ly)], fill=cg, width=1)
    for gv in [-1.5,-0.5,0.5,1.5]:
        lx=gcx+int(gv*gu); ly=gcy-int(gv*gu)
        d.line([(lx,gg_y0+1),(lx,gg_y1-1)], fill=(25,25,25), width=1)
        d.line([(gg_x0+1,ly),(gg_x1-1,ly)], fill=(25,25,25), width=1)
    d.rounded_rectangle([gg_x0,gg_y0,gg_x1,gg_y1],
                         radius=8, outline=(60,60,70), width=2, fill=None)
    if trace_glat and len(trace_glat) > 1:
        n = len(trace_glat)
        pts = [(max(gg_x0+2, min(gg_x1-2, gcx+int(trace_glat[i]*gu))),
                max(gg_y0+2, min(gg_y1-2, gcy-int((-trace_glong[i])*gu))))
               for i in range(n)]
        lw = max(3, int(6*s))
        for i in range(n-1):
            if speed_colour and trace_speed and i < len(trace_speed):
                col_t = _speed_colour_fn(trace_speed[i])
            else:
                alpha = 0.3 + 0.7*(i/(n-1))
                col_t = tuple(int(c*alpha) for c in PURPLE)
            d.line([pts[i],pts[i+1]], fill=col_t, width=lw)
    dx = max(gg_x0+4, min(gg_x1-4, gcx+int(g_lat*gu)))
    dy = max(gg_y0+4, min(gg_y1-4, gcy-int((-g_long)*gu)))
    dr = max(4, int(6*s))
    _dot_col2 = _speed_colour_fn(speed) if speed_colour else PURPLE
    d.ellipse([dx-dr,dy-dr,dx+dr,dy+dr], fill=_dot_col2, outline=(120,120,120), width=1)
    # ── Big, clear white Lat / Lon readouts in the band below the plot ───────
    _lbl_cy5 = gg_y1 + (_h - PAD - gg_y1)//2
    _lbl_fsz5 = max(14, int(_LBL_BAND5 * 0.66))
    _half5 = (gg_x1 - gg_x0) // 2
    _wide_panel_text(img, (gg_x0 + (gg_x1-gg_x0)//4, _lbl_cy5),
                     f"Lat {g_lat:+.2f}", _lbl_fsz5, WHITE,
                     _half5 - int(6*s), ow=max(2,int(3*s)))
    _wide_panel_text(img, (gg_x0 + (gg_x1-gg_x0)*3//4, _lbl_cy5),
                     f"Lon {-g_long:+.2f}", _lbl_fsz5, WHITE,
                     _half5 - int(6*s), ow=max(2,int(3*s)))

    return img

def _build_frame_style12(img, d, rpm, throttle, speed, gear,
                          g_lat, g_long, ts, laps, _w, _h, s,
                          brake_pct, rpm_max, peak_rpm, P,
                          trace_glat=None, trace_glong=None,
                          trace_speed=None, speed_colour=False, chan=None):
    """Style 12 — coloured-box race dash (s12b layout with g-trace)."""
    import math as _m

    WHITE  = P["white"];  GREY  = P["grey"];   BLACK = P["panel"]
    CYAN   = P["cyan"];   GOLD  = P["gold"]
    GREEN  = P["green"];  RED   = P["red"];    AMBER = P["amber"]
    PURPLE = P["purple"]; BLUE  = P["blue"]
    PAD    = 14

    CHROMA = P["chroma"]
    # Fill entire frame with chroma (transparent), then paint the strip over it
    d.rectangle([0, 0, _w, _h], fill=CHROMA)

    # G-trace removed from this dash — available as a separate overlay video.
    # The data strip now spans the full frame width.
    PH   = int(_h * 0.70); PY0 = _h - PH

    # Layout content width (panels don't fill the frame). Compute the strip
    # extent up front so the black background ends just past the last column.
    _LAYW = _w - PAD - int((PH - PAD*2) * 0.92)   # ≈ old strip right edge
    GW  = int(_LAYW * 0.26); GCX = _LAYW // 2
    GX0 = GCX - GW // 2;  GX1 = GCX + GW // 2
    _rx0 = GX1 + PAD
    _left_col_w = GX0 - 2*PAD                 # left (lap) column width
    _strip_right = _rx0 + _left_col_w + PAD   # right edge of right column + pad

    # ── STRIP: full height, content width ────────────────────────────────────
    SW = _strip_right
    d.rectangle([0, PY0, SW, _h], fill=BLACK)
    d.line([(0, PY0), (SW, PY0)], fill=(50, 60, 80), width=3)

    def _box(x0, y0, x1, y1, outline, fill=(15,15,22), radius=10, stroke=2):
        d.rounded_rectangle([x0,y0,x1,y1], radius=radius, fill=fill)
        d.rounded_rectangle([x0,y0,x1,y1], radius=radius, outline=outline, width=stroke)

    def _tc_mm(pos, text, font, fill):
        d.text(pos, text, font=font, fill=fill, anchor="mm")

    def _tco_mm(pos, text, font, fill, outline=BLACK, stroke=3):
        x, y = pos
        for dx2 in range(-stroke, stroke+1):
            for dy2 in range(-stroke, stroke+1):
                if dx2 or dy2:
                    d.text((x+dx2, y+dy2), text, font=font, fill=outline, anchor="mm")
        d.text(pos, text, font=font, fill=fill, anchor="mm")

    def _tco_rm(pos, text, font, fill, outline=BLACK, stroke=2):
        x, y = pos
        for dx2 in range(-stroke, stroke+1):
            for dy2 in range(-stroke, stroke+1):
                if dx2 or dy2:
                    d.text((x+dx2, y+dy2), text, font=font, fill=outline, anchor="rm")
        d.text(pos, text, font=font, fill=fill, anchor="rm")

    # ── GEAR + SPEED + RPM: centre column — speed 1/4, gear 1/2, rpm 1/4 ────
    # (Layout width _LAYW and GX0/GX1 computed above with the strip extent.)
    _full_y0 = PY0 + PAD; _full_y1 = _h - PAD
    _total_h = _full_y1 - _full_y0 - PAD*2
    _spd_h  = _total_h // 4
    _gear_h = _total_h // 2
    _rpm_h  = _total_h - _spd_h - _gear_h
    _spd_y0 = _full_y0;              _spd_y1 = _spd_y0 + _spd_h
    _gear_y0 = _spd_y1 + PAD;        _gear_y1 = _gear_y0 + _gear_h
    _rpm_y0  = _gear_y1 + PAD;       _rpm_y1  = _full_y1
    def _ctr_box(x0,y0,x1,y1,col,fill,lbl,lbl_col,val_str,val_col,radius=12,stroke=3):
        _box(x0,y0,x1,y1,col,fill=fill,radius=radius,stroke=stroke)
        bh=y1-y0; bw=x1-x0; bcx=(x0+x1)//2
        lfsz=max(7,int(bh*0.22))
        d.text((x0+PAD,y0+int(bh*0.18)),lbl,font=fc(lfsz),fill=lbl_col,anchor="lm")
        vfsz=max(10,int(bh*0.58))
        while vfsz>8:
            _bb=d.textbbox((0,0),val_str,font=fb(vfsz))
            if (_bb[2]-_bb[0])<bw-PAD*3: break
            vfsz-=1
        for _dx in range(-3,4):
            for _dy in range(-3,4):
                if _dx or _dy: d.text((bcx+_dx,y0+int(bh*0.65)+_dy),val_str,font=fb(vfsz),fill=BLACK,anchor="mm")
        d.text((bcx,y0+int(bh*0.65)),val_str,font=fb(vfsz),fill=val_col,anchor="mm")
    _ctr_box(GX0,_spd_y0,GX1,_spd_y1, CYAN,(5,18,20),"km/h",CYAN,str(int(speed)),WHITE)
    _ctr_box(GX0,_gear_y0,GX1,_gear_y1, AMBER,(20,15,5),"GEAR",GREY,
             str(gear) if gear>0 else "N",AMBER,radius=16,stroke=5)
    _ctr_box(GX0,_rpm_y0,GX1,_rpm_y1, AMBER,(20,10,5),"RPM",AMBER,str(int(rpm)),WHITE)

    # ── LEFT: lap number + lap time stacked ───────────────────────────────────
    LX1 = GX0 - PAD; lh = (PH - PAD*3) // 2; lxc = (PAD + LX1) // 2
    _box(PAD, PY0+PAD, LX1, PY0+PAD+lh, CYAN, radius=12)
    _tc_mm((lxc, PY0+PAD+int(lh*0.18)), "LAP", fc(int(lh*0.20)), CYAN)
    # Current lap number
    _lap_num = 0
    for _li, (_st, _et, _lt) in enumerate(laps, 1):
        if _st <= ts: _lap_num = _li
    _tco_mm((lxc, PY0+PAD+int(lh*0.62)),
             str(_lap_num if _lap_num else "—"), fb(int(lh*0.56)), WHITE, BLACK, 3)

    by0l = PY0 + PAD*2 + lh
    _box(PAD, by0l, LX1, _h-PAD, GOLD, radius=12)
    lh2  = _h - PAD - by0l
    _tc_mm((lxc, by0l+int(lh2*0.18)), "LAP TIME", fc(int(lh2*0.20)), GOLD)
    _lp_timer_str, _lp_timer_col, _ = get_timer_display(ts, laps, P=P)
    _tco_mm((lxc, by0l+int(lh2*0.62)), _lp_timer_str, fb(int(lh2*0.40)), _lp_timer_col, BLACK, 2)

    # ── RIGHT: data items vertical (Lat/Lon G removed — in standalone overlay) ─
    RX0   = _rx0
    RX1   = _rx0 + _left_col_w         # right column matches left column width
    INNER = int(PAD * 1.5)
    ORANGE = P.get("orange", (255, 130, 30))
    # Base items are TPS and Brake; Channel A/B append when selected.
    items = [
        ("TPS",   f"{int(throttle)}%",        GREEN,  (0, 22, 6)),
        ("Brake", f"{int(brake_pct or 0)}%",  RED,    (22, 4, 4)),
    ]
    def _chan_active(_v):
        return _v is not None and not (isinstance(_v,float) and _m.isnan(_v))
    if chan:
        if _chan_active(chan.get("A_val")):
            _albl = chan.get("A_lbl") or "A"
            items.append((_albl, _chan_vstr(chan, "A"), CYAN, (4, 18, 22)))
        if _chan_active(chan.get("B_val")):
            _blbl = chan.get("B_lbl") or "B"
            items.append((_blbl, _chan_vstr(chan, "B"), GOLD, (20, 14, 4)))
    _n_items = len(items)
    rh    = (PH - PAD*(_n_items+1)) // _n_items
    for i, (lbl, val, col, box_fill) in enumerate(items):
        by = PY0 + PAD + i*(rh + PAD)
        _box(RX0, by, RX1, by+rh, col, fill=box_fill, radius=10, stroke=3)
        lfsz = max(10, int(rh * 0.32))
        vfsz = max(12, int(rh * 0.56))
        # Shrink value to fit
        while vfsz > 10:
            _bb2 = d.textbbox((0,0), val, font=fb(vfsz))
            if (_bb2[2]-_bb2[0]) <= (RX1-RX0) - INNER*3: break
            vfsz -= 1
        # Label top-left, bold, in channel colour
        for _dx in range(-2,3):
            for _dy in range(-2,3):
                if _dx or _dy:
                    d.text((RX0+INNER+_dx, by+int(rh*0.28)+_dy), lbl,
                           font=fb(lfsz), fill=BLACK, anchor="lm")
        d.text((RX0+INNER, by+int(rh*0.28)), lbl, font=fb(lfsz), fill=col, anchor="lm")
        # Value right-aligned, white, outlined
        _tco_rm((RX1-INNER, by+int(rh*0.72)), val, fb(vfsz), WHITE, BLACK, 2)

    return img

def _build_frame_style11(img, d, rpm, throttle, speed, gear,
                          g_lat, g_long, ts, laps, _w, _h, s,
                          brake_pct, rpm_max, peak_rpm, P, chan=None):
    """Style 11 — Race dash v3."""
    import math as _m
    _s15 = P.get("style15_layout", False)   # Dash 3 is never the trapezoid layout

    WHITE   = P["white"]
    GREY    = P["grey"]
    BAR_COL = P.get("rpm_bar_col", (200, 15, 15))
    BAR_BG  = P.get("rpm_bar_bg",  (25, 10, 10))
    BLACK   = P["panel"]
    PAD     = int(20*s)

    d.rectangle([0, 0, _w, _h], fill=BLACK)

    # ── ZONE LAYOUT ───────────────────────────────────────────────────────────
    BAR_Y0   = 0
    BAR_Y1   = int(_h * 0.40)
    BAR_H2   = BAR_Y1 - BAR_Y0

    # Right panel for speed — bar ends before this
    RX       = int(_w * 0.63)
    RW       = _w - RX - PAD

    # Bar runs full width but filled area limited to RX
    BAR_FULL_X1 = _w               # background arc goes full width
    BAR_FILL_X1 = RX - int(20*s)   # fill stops before speed panel

    DIV_Y    = int(_h * 0.62)
    RPM_ZONE_Y0 = BAR_Y1 + int(_h * 0.02)
    RPM_ZONE_Y1 = DIV_Y - int(_h * 0.02)
    RPM_ZONE_H  = RPM_ZONE_Y1 - RPM_ZONE_Y0

    DATA_Y0  = DIV_Y + int(_h * 0.03)
    DATA_Y1  = _h - PAD

    # ── RPM BAR — arc spans full width, fill limited ─────────────────────────
    # Shape: quarter-circle sweeps from bottom-left all the way across
    # Radius = BAR_H2 so the arc tip starts at x=0, y=BAR_Y1 and sweeps
    # to x=BAR_H2, y=BAR_Y0 (top-left). Then flat across to x=BAR_FULL_X1.
    _R = BAR_H2

    def _bar_poly(x_right):
        pts = []
        # Top edge
        pts.append((_R, BAR_Y0))
        pts.append((x_right, BAR_Y0))
        # Right edge
        pts.append((x_right, BAR_Y1))
        # Bottom back to arc start
        pts.append((_R, BAR_Y1))
        # Quarter-circle from bottom of arc to top
        N = 48
        for i in range(N+1):
            angle = _m.pi + _m.pi/2 * i/N
            px = _R + _R * _m.cos(angle)
            py = BAR_Y1 + _R * _m.sin(angle)
            pts.append((int(px), int(py)))
        return pts

    # Background (full width)
    d.polygon(_bar_poly(BAR_FULL_X1), fill=BAR_BG)

    # 20 segments — each grows upward from baseline as RPM increases
    N_SEGS    = 20
    SEG_GAP   = max(2, int(3*s))
    SEG_START = 0   # segments start at left edge
    USABLE_W  = BAR_FILL_X1 - SEG_START
    SEG_W     = (USABLE_W - SEG_GAP*(N_SEGS-1)) // N_SEGS
    _rpm_pct  = max(0.0, min(1.0, rpm / max(rpm_max, 1)))
    _lit_segs = int(_rpm_pct * N_SEGS + 0.5)
    _inner_pad = SEG_GAP

    # RPM fraction drives the height curve
    # Heights fixed by segment POSITION only — RPM controls lit/unlit, not height
    _EXP = 2.8

    for seg_i in range(N_SEGS):
        sx0 = SEG_START + seg_i * (SEG_W + SEG_GAP)
        sx1 = sx0 + SEG_W
        t   = seg_i / (N_SEGS - 1)
        # Height always from position, identical whether lit or unlit
        _h_frac = max(0.05, min(1.0, 1.0 - (1.0 - t) ** _EXP))
        seg_h   = max(4, int((BAR_H2 - _inner_pad*2) * _h_frac))
        sy_bot  = BAR_Y1 - _inner_pad
        sy_top  = sy_bot - seg_h
        _rad    = max(2, int(SEG_W * 0.20))
        if seg_i < _lit_segs:
            if t < 0.40:   col = (30, 200, 60)
            elif t < 0.60: col = (180, 220, 0)
            elif t < 0.75: col = (255, 180, 0)
            elif t < 0.88: col = (255, 90, 0)
            else:           col = (220, 15, 15)
        else:
            col = (30, 12, 12)   # dim unlit — same height, dark colour
        d.rounded_rectangle([sx0, sy_top, sx1, sy_bot], radius=_rad, fill=col)

    # ── LAP BADGES (option C) — two pill boxes, top-right of bar zone ────────
    _CYAN = (80, 200, 255); _GOLD = (255, 200, 60)
    _lap_num = 0
    _last_lt = None
    for _li, (_st, _et, _lt) in enumerate(laps, 1):
        if _st <= ts:
            _lap_num = _li
        if ts >= _et:
            _last_lt = _lt
    _badge_h  = int(BAR_H2 * 0.42)
    _badge_y0 = int(BAR_H2 * 0.05)
    _badge_y1 = _badge_y0 + _badge_h
    _badge_pad = int(8*s)
    _num_badge_w = int(130*s)
    _num_badge_x1 = _w - _badge_pad
    _num_badge_x0 = _num_badge_x1 - _num_badge_w
    _time_badge_w = int(220*s)
    _time_badge_x1 = _num_badge_x0 - _badge_pad
    _time_badge_x0 = _time_badge_x1 - _time_badge_w
    # Lap number badge (cyan)
    d.rounded_rectangle([_num_badge_x0,_badge_y0,_num_badge_x1,_badge_y1],
                         radius=int(8*s), fill=(20,20,30))
    d.rounded_rectangle([_num_badge_x0,_badge_y0,_num_badge_x1,_badge_y1],
                         radius=int(8*s), outline=_CYAN, width=max(1,int(2*s)))
    _bcx = (_num_badge_x0 + _num_badge_x1)//2
    _bcy = (_badge_y0 + _badge_y1)//2
    _lbl_fsz = max(7, int(_badge_h * 0.24))
    _val_fsz = max(10, int(_badge_h * 0.52))
    _tc(d, (_bcx, _badge_y0 + int(_badge_h*0.22)), "LAP", fc(_lbl_fsz), _CYAN)
    _tc(d, (_bcx, _bcy + int(_badge_h*0.15)), str(_lap_num if _lap_num else "—"), fb(_val_fsz), WHITE)
    # Lap time badge (gold)
    d.rounded_rectangle([_time_badge_x0,_badge_y0,_time_badge_x1,_badge_y1],
                         radius=int(8*s), fill=(20,20,30))
    d.rounded_rectangle([_time_badge_x0,_badge_y0,_time_badge_x1,_badge_y1],
                         radius=int(8*s), outline=_GOLD, width=max(1,int(2*s)))
    _tcx = (_time_badge_x0 + _time_badge_x1)//2
    _timer11_str, _timer11_col, _timer11_lbl = get_timer_display(ts, laps, P=P)
    _tc(d, (_tcx, _badge_y0 + int(_badge_h*0.22)), _timer11_lbl, fc(_lbl_fsz), _GOLD)
    _tc(d, (_tcx, _bcy + int(_badge_h*0.15)), _timer11_str, fb(max(10,int(_badge_h*0.46))), _timer11_col)

    # ── SPEED (right panel, below badges) ────────────────────────────────────
    _spd_lbl_fsz = max(11, int(23*s))
    _spd_fsz     = max(14, int(BAR_H2 * 0.42))
    _spd_str     = str(int(speed))
    while _spd_fsz > 12:
        _bb = d.textbbox((0,0), _spd_str, font=fb(_spd_fsz))
        if (_bb[2]-_bb[0]) < RW - int(12*s): break
        _spd_fsz -= 2
    _spd_cx = RX + RW//2
    # Position below the badge area
    _spd_top = _badge_y1 + int(8*s)
    _spd_avail = BAR_Y1 - _spd_top
    _tc(d, (_spd_cx, _spd_top + int(_spd_avail*0.22)), "km/h", fc(_spd_lbl_fsz), P.get("data",(215,220,230)))
    _spd_num_y = _spd_top + int(_spd_avail*0.22) + _spd_lbl_fsz + int(4*s) + _spd_fsz//2
    _tc(d, (_spd_cx, _spd_num_y), _spd_str, fb(_spd_fsz), WHITE)

    # ── RPM NUMBER — in zone between bar and data row ─────────────────────────
    if not _s15:
        _rpm_str  = str(int(rpm))
        _rpm_fsz  = max(14, int(RPM_ZONE_H * 0.72))
        while _rpm_fsz > 12:
            _bb = d.textbbox((0,0), _rpm_str, font=fb(_rpm_fsz))
            if (_bb[2]-_bb[0]) < int(_w * 0.35): break
            _rpm_fsz -= 2
        _rpm_cx   = int(_w * 0.28)
        _rpm_cy   = RPM_ZONE_Y0 + RPM_ZONE_H//2
        _tc(d, (_rpm_cx, _rpm_cy), _rpm_str, fb(_rpm_fsz), WHITE)
        _rpm_lbl_fsz = max(10, int(_rpm_fsz * 0.32))
        _rpm_bb = d.textbbox((0,0), _rpm_str, font=fb(_rpm_fsz))
        _rpm_left = _rpm_cx - (_rpm_bb[2]-_rpm_bb[0])//2 - int(8*s)
        _rpm_lbl_col = P.get("data", (215, 220, 230))
        _tc(d, (_rpm_left - int(24*s), _rpm_cy), "RPM", fc(_rpm_lbl_fsz), _rpm_lbl_col)

    # Peak RPM in brackets
    if peak_rpm is not None and not _s15:
        _pk_str  = f"({int(peak_rpm)})"
        _pk_fsz  = max(12, int(_rpm_fsz * 0.62))
        _rpm_right = _rpm_cx + (_rpm_bb[2]-_rpm_bb[0])//2 + int(14*s)
        _pk_bb = d.textbbox((0,0), _pk_str, font=fi(_pk_fsz))
        _pk_x  = _rpm_right + (_pk_bb[2]-_pk_bb[0])//2
        _pk_col = P.get("amber", (255, 175, 60))   # bright accent for peak
        # Make sure peak doesn't reach speed panel
        if _pk_x + (_pk_bb[2]-_pk_bb[0])//2 < RX - int(8*s):
            _tc(d, (_pk_x, _rpm_cy), _pk_str, fi(_pk_fsz), _pk_col)

    # ── DIVIDER ────────────────────────────────────────────────────────────────
    d.line([(0, DIV_Y), (_w, DIV_Y)], fill=P["border"], width=max(2,int(3*s)))

    # ── DATA ROW — TPS, Brake, Lat G, Lon G, Gear (5 columns) ────────────────
    DATA_H   = DATA_Y1 - DATA_Y0
    N_COLS   = 5
    COL_W    = _w // N_COLS
    LBL_FSZ  = max(10, int(min(DATA_H * 0.34, 30*s)))   # larger labels
    LBL_COL  = P.get("data", (215, 220, 230))           # brighter than grey
    VAL_FSZ  = max(10, int(min(DATA_H * 0.52, 52*s)))
    while LBL_FSZ + int(DATA_H*0.08) + VAL_FSZ > DATA_H - PAD:
        VAL_FSZ -= 2

    _gear_str = str(gear) if gear > 0 else "N"
    data_items = [
        ("TPS",   f"{int(throttle)}%",          P["green"]),
        ("Brake", f"{int(brake_pct or 0)}%",    P["red"]),
        ("Lat G", f"{g_lat:+.2f}",              WHITE),
        ("Lon G", f"{-g_long:+.2f}",            WHITE),
        ("Gear",  _gear_str,                     P.get("amber",(255,160,0))),  # gear larger below
    ]
    # Optional channels: A below Lon G, B below Lat G (2×2 with the G readouts)
    def _ch_ok(_v): return _v is not None and not (isinstance(_v,float) and _m.isnan(_v))
    _chanA_cell = None; _chanB_cell = None
    if chan:
        if _ch_ok(chan.get("A_val")):
            _chanA_cell = (chan.get("A_lbl") or "A",
                           f"{int(round(chan['A_val']))}", P.get("cyan",(80,200,255)))
        if _ch_ok(chan.get("B_val")):
            _chanB_cell = (chan.get("B_lbl") or "B",
                           f"{int(round(chan['B_val']))}", P.get("gold",(255,180,40)))
    _lbl_y  = DATA_Y0 + int(DATA_H * 0.04)
    _val_cy = DATA_Y0 + LBL_FSZ + int(DATA_H * 0.08) + VAL_FSZ//2

    # TPS and Brake: horizontal bar graphs instead of numbers
    _bar_col_w = COL_W - int(24*s)
    _bar_row_h = max(8, int(DATA_H * 0.28))
    _bar_row_y = _val_cy - _bar_row_h//2

    for i, (lbl, val, col) in enumerate(data_items):
        cx2 = COL_W*i + COL_W//2
        # Lat/Lon cells draw their own label inside the split layout when a
        # channel is paired below — skip the shared top label for those.
        _is_split = (i == 2 and _chanB_cell is not None) or \
                    (i == 3 and _chanA_cell is not None)
        if not _is_split:
            _tc(d, (cx2, _lbl_y), lbl, fc(LBL_FSZ), LBL_COL)
        if i < 2:  # TPS and Brake — horizontal bar graph
            _pct = float(val.strip("%")) / 100.0
            _bx0 = COL_W*i + int(12*s)
            _bx1 = _bx0 + _bar_col_w
            _by0 = _bar_row_y; _by1 = _by0 + _bar_row_h
            _brad = max(2, _bar_row_h//4)
            # Background track
            d.rounded_rectangle([_bx0,_by0,_bx1,_by1], radius=_brad, fill=(30,30,35))
            # Fill
            _fill_w = int(_bar_col_w * _pct)
            if _fill_w > _brad*2:
                d.rounded_rectangle([_bx0,_by0,_bx0+_fill_w,_by1], radius=_brad, fill=col)
            # Value label at end of bar
            _pct_fsz = max(8, int(DATA_H*0.22))
            _tc(d, (cx2, _by1 + int(6*s) + _pct_fsz//2), val, fc(_pct_fsz), col)
        elif i == 4:  # Gear — as tall as the data row allows
            # Start at 2× VAL_FSZ, shrink to fit column width and row height
            _gear_fsz = min(VAL_FSZ * 2, int(DATA_H * 0.95))
            while _gear_fsz > 10:
                _bb = d.textbbox((0,0), val, font=fb(_gear_fsz))
                _bh = _bb[3]-_bb[1]; _bw = _bb[2]-_bb[0]
                if _bw < COL_W - int(8*s) and _bh < DATA_H - int(8*s): break
                _gear_fsz -= 2
            # Centre vertically in the data row
            _gear_cy = DATA_Y0 + DATA_H//2
            _tc(d, (cx2, _gear_cy), val, fb(_gear_fsz), col)
        elif i in (2, 3):  # Lat G / Lon G — with optional channel below (2×2)
            # Channel pairing: B under Lat G (i=2), A under Lon G (i=3)
            _pair = _chanB_cell if i == 2 else _chanA_cell
            if _pair is not None:
                _half_h = DATA_H // 2
                _g_lf = max(9, int(LBL_FSZ*0.9))
                # value font sized to fit the half-cell minus the label band
                _avail = _half_h - _g_lf - int(10*s)
                _g_vfsz = max(10, min(int(_avail*0.85), VAL_FSZ))
                while _g_vfsz > 9:
                    _bb = d.textbbox((0,0), val, font=fb(_g_vfsz))
                    if (_bb[2]-_bb[0]) < COL_W - int(12*s): break
                    _g_vfsz -= 2
                _clbl, _cval, _ccol = _pair
                _c_vfsz = _g_vfsz
                while _c_vfsz > 9:
                    _bb = d.textbbox((0,0), _cval, font=fb(_c_vfsz))
                    if (_bb[2]-_bb[0]) < COL_W - int(12*s): break
                    _c_vfsz -= 2
                # ── Top half: label+value as a tight centred group ──
                _glabel = lbl.replace(" G", "")   # "Lat"/"Lon" — tighter in split cell
                _gap_lv = int(3*s)                # small gap between label and value
                _grp_h  = _g_lf + _gap_lv + _g_vfsz
                _grp_y0 = DATA_Y0 + (_half_h - _grp_h)//2
                _tc(d, (cx2, _grp_y0 + _g_lf//2), _glabel, fc(_g_lf), LBL_COL)
                _tc(d, (cx2, _grp_y0 + _g_lf + _gap_lv + _g_vfsz//2), val, fb(_g_vfsz), col)
                # ── Divider ──
                d.line([(COL_W*i + int(8*s), DATA_Y0 + _half_h),
                        (COL_W*(i+1) - int(8*s), DATA_Y0 + _half_h)],
                       fill=P["border"], width=max(1,int(1*s)))
                # ── Bottom half: channel label+value as a tight centred group ──
                _by_top = DATA_Y0 + _half_h
                _cgrp_h = _g_lf + _gap_lv + _c_vfsz
                _cgrp_y0 = _by_top + (_half_h - _cgrp_h)//2
                _tc(d, (cx2, _cgrp_y0 + _g_lf//2), _clbl, fc(_g_lf), _ccol)
                _tc(d, (cx2, _cgrp_y0 + _g_lf + _gap_lv + _c_vfsz//2), _cval, fb(_c_vfsz), _ccol)
            else:
                _vfsz = VAL_FSZ
                while _vfsz > 10:
                    _bb = d.textbbox((0,0), val, font=fb(_vfsz))
                    if (_bb[2]-_bb[0]) < COL_W - int(12*s): break
                    _vfsz -= 2
                _tc(d, (cx2, _val_cy), val, fb(_vfsz), col)
        else:
            _vfsz = VAL_FSZ
            while _vfsz > 10:
                _bb = d.textbbox((0,0), val, font=fb(_vfsz))
                if (_bb[2]-_bb[0]) < COL_W - int(12*s): break
                _vfsz -= 2
            _tc(d, (cx2, _val_cy), val, fb(_vfsz), col)
        if i > 0:
            d.line([(COL_W*i, DIV_Y+int(4*s)), (COL_W*i, _h)],
                   fill=P["border"], width=max(1,int(1*s)))

    return img



# ── DELTA WIDGET ──────────────────────────────────────────────────────────────
_DELTA_CACHE = {}   # cached best-lap reference per session

def _delta_widget_pos(P, out_w, out_h):
    """Return (x, y, w, h) for the delta widget — always far right of frame."""
    PAD = int(14 * (out_w / 1920))
    DW  = int(280 * (out_w / 1920))
    DH  = int(140 * (out_h / 750))
    x   = out_w - DW - PAD
    cy  = (out_h - DH) // 2
    return (x, cy, DW, DH)


def build_delta_reference(ts_arr, lat_arr, lon_arr, laps, sf_lat=None, sf_lon=None,
                          min_lap_time=None):
    """Build best-lap reference for delta calculation.

    Stores the reference lap GPS polyline in ENU coordinates so that
    draw_delta_widget can project the current position onto it — giving
    true path-progress-based comparison, not cumulative-distance comparison.

    Reference dict contains:
      ref_e, ref_n   — ENU polyline of best lap (metres from SF point)
      ref_dist       — cumulative distance along polyline at each point (m)
      ref_time       — elapsed time at each point (s)
      best_lap_time  — lap time (s)
      best_lap       — (st, et, lt)
      all_laps       — original lap list
      full_ts        — full session timestamp array
      cos_lat        — cos(sf_lat) for ENU conversion
      sf_lat, sf_lon — S/F coordinates
    """
    import numpy as _np

    # If no S/F coordinate provided, derive from GPS position at first lap boundary
    if sf_lat is None or sf_lon is None:
        if laps and len(ts_arr) and lat_arr is not None and lon_arr is not None:
            _idx0 = int(_np.searchsorted(ts_arr, laps[0][0]))
            _idx0 = min(_idx0, len(lat_arr)-1)
            # Walk forward to find a valid GPS fix
            for _ii in range(_idx0, min(_idx0+50, len(lat_arr))):
                if _np.isfinite(lat_arr[_ii]) and _np.isfinite(lon_arr[_ii]):
                    sf_lat = float(lat_arr[_ii])
                    sf_lon = float(lon_arr[_ii])
                    break
        if sf_lat is None:
            return None

    cos_lat = _np.cos(_np.radians(sf_lat))

    # IQR-based flying lap filter — keeps the tight cluster, rejects anomalies
    _all_lts = _np.array([lt for _,_,lt in laps if lt > 5])
    if len(_all_lts) >= 4:
        _q1 = float(_np.percentile(_all_lts, 25))
        _q3 = float(_np.percentile(_all_lts, 75))
        _iqr = _q3 - _q1
        _lo  = _q1 - 1.5 * _iqr
        _hi  = _q3 + 1.5 * _iqr
        valid_laps = [(st,et,lt) for st,et,lt in laps if _lo <= lt <= _hi]
    else:
        valid_laps = [(st,et,lt) for st,et,lt in laps if lt > 10]
    if not valid_laps:
        valid_laps = [(st,et,lt) for st,et,lt in laps if lt > 5]
    if not valid_laps:
        return None

    best_st, best_et, best_lt = min(valid_laps, key=lambda x: x[2])

    # Extract best lap GPS points
    mask = (ts_arr >= best_st) & (ts_arr <= best_et)
    if not mask.any():
        return None

    ref_lat = lat_arr[mask]
    ref_lon = lon_arr[mask]
    ref_ts  = ts_arr[mask] - best_st   # elapsed time from lap start

    # Convert to ENU metres (tangent plane at SF)
    ref_e = (ref_lon - sf_lon) * 111320.0 * cos_lat
    ref_n = (ref_lat - sf_lat) * 111320.0

    # Cumulative distance along reference polyline
    ref_dist = _np.zeros(len(ref_e))
    for i in range(1, len(ref_e)):
        de = ref_e[i] - ref_e[i-1]; dn = ref_n[i] - ref_n[i-1]
        ref_dist[i] = ref_dist[i-1] + _np.sqrt(de**2 + dn**2)

    return {
        'ref_e':        ref_e,
        'ref_n':        ref_n,
        'ref_dist':     ref_dist,
        'ref_time':     ref_ts,
        'best_lap_time': best_lt,
        'best_lap':     (best_st, best_et, best_lt),
        'all_laps':     laps,
        'valid_laps':   valid_laps,
        'full_ts':      ts_arr,
        'lat_arr':      lat_arr,
        'lon_arr':      lon_arr,
        'cos_lat':      cos_lat,
        'sf_lat':       sf_lat,
        'sf_lon':       sf_lon,
    }


def draw_delta_widget(d, img, x, y, w, h, ts_now, laps,
                      delta_ref, ts_arr,
                      s=1.0, history_s=8.0):
    """Draw the delta vs best lap widget.

    d         — ImageDraw
    img       — PIL Image (for pixel sampling)
    x, y      — top-left corner of widget
    w, h      — widget dimensions
    ts_now    — current timestamp (seconds)
    laps      — list of (start, end, laptime) tuples
    delta_ref — result of build_delta_reference()
    ts_arr    — full session timestamp array
    history_s — how many seconds of delta history to show in trace
    """
    import numpy as _np

    if delta_ref is None:
        return

    # Use the laps the reference was built with
    laps = delta_ref.get('all_laps', laps)

    # Find current lap
    cur_lap = None
    for st, et, lt in laps:
        if st <= ts_now < et:
            cur_lap = (st, et, lt); break
    if cur_lap is None:
        return

    lap_st, lap_et, lap_lt = cur_lap
    cur_time_lap = ts_now - lap_st

    ref_e    = delta_ref['ref_e']
    ref_n    = delta_ref['ref_n']
    ref_dist = delta_ref['ref_dist']
    ref_time = delta_ref['ref_time']
    best_lt  = delta_ref['best_lap_time']
    full_ts  = delta_ref['full_ts']
    lat_arr  = delta_ref['lat_arr']
    lon_arr  = delta_ref['lon_arr']
    cos_lat  = delta_ref['cos_lat']
    sf_lat   = delta_ref['sf_lat']
    sf_lon   = delta_ref['sf_lon']

    def _project_onto_ref(e, n, elapsed_s):
        """Project ENU point onto ref polyline. Returns distance along ref (m)."""
        ef = min(1.0, elapsed_s / max(best_lt, 1.0))
        es = ef * float(ref_dist[-1])
        sr = float(ref_dist[-1]) * 0.25
        klo = int(_np.searchsorted(ref_dist, max(0.0, es - sr)))
        khi = min(int(_np.searchsorted(ref_dist, min(float(ref_dist[-1]), es + sr))) + 1,
                  len(ref_e) - 1)
        min_d = _np.inf; best_s = es
        for k in range(klo, khi):
            ax, ay = ref_e[k], ref_n[k]
            bx, by = ref_e[k+1], ref_n[k+1]
            dx, dy = bx-ax, by-ay; sl2 = dx*dx+dy*dy
            if sl2 < 1e-9: continue
            t2 = max(0.0, min(1.0, ((e-ax)*dx + (n-ay)*dy) / sl2))
            px = ax+t2*dx; py = ay+t2*dy
            _d = _np.sqrt((e-px)**2+(n-py)**2)
            if _d < min_d:
                min_d = _d
                best_s = ref_dist[k] + t2 * _np.sqrt(sl2)
        return max(0.0, min(float(ref_dist[-1]), best_s))

    def _get_ref_pos_at_time(t_elapsed):
        """ENU position of reference car at t_elapsed seconds into lap."""
        s = float(_np.interp(t_elapsed, ref_time, ref_dist))
        # Interpolate position along polyline at distance s
        k = int(_np.searchsorted(ref_dist, s)) - 1
        k = max(0, min(k, len(ref_e)-2))
        seg_len = ref_dist[k+1] - ref_dist[k]
        if seg_len < 1e-6:
            return ref_e[k], ref_n[k]
        frac = (s - ref_dist[k]) / seg_len
        return (ref_e[k] + frac*(ref_e[k+1]-ref_e[k]),
                ref_n[k] + frac*(ref_n[k+1]-ref_n[k]))

    # Current car ENU position
    idx_now = min(int(_np.searchsorted(full_ts, ts_now)), len(full_ts)-1)
    cur_e = (lon_arr[idx_now] - sf_lon) * 111320.0 * cos_lat
    cur_n = (lat_arr[idx_now] - sf_lat) * 111320.0

    # Current car's position along ref path
    cur_s = _project_onto_ref(cur_e, cur_n, cur_time_lap)

    # Reference car's position along ref path at same elapsed time
    ref_s = float(_np.interp(cur_time_lap, ref_time, ref_dist))

    # Distance delta: positive = current car is AHEAD of reference
    delta_m = cur_s - ref_s

    # History trace
    t_hist_start = max(lap_st, ts_now - history_s)
    mask_hist = (full_ts >= t_hist_start) & (full_ts <= ts_now)
    ts_hist = full_ts[mask_hist]
    deltas_hist = []
    for _t in ts_hist:
        _idx = min(int(_np.searchsorted(full_ts, _t)), len(full_ts)-1)
        _ce = (lon_arr[_idx] - sf_lon) * 111320.0 * cos_lat
        _cn = (lat_arr[_idx] - sf_lat) * 111320.0
        _t_lap = _t - lap_st
        _cs = _project_onto_ref(_ce, _cn, _t_lap)
        _rs = float(_np.interp(_t_lap, ref_time, ref_dist))
        deltas_hist.append(_cs - _rs)

    if len(deltas_hist) > 4:
        kernel = _np.array([0.15, 0.2, 0.3, 0.2, 0.15])
        deltas_hist = list(_np.convolve(deltas_hist, kernel, mode='same'))


    # ── Draw widget ────────────────────────────────────────────────────────────
    PAD    = max(4, int(6*s))
    BLACK  = (8, 8, 10)
    GAIN   = (30, 200, 60)    # green = gaining (negative delta)
    LOSS   = (220, 30, 30)    # red = losing (positive delta)
    WHITE  = (240, 240, 245)
    GREY   = (120, 120, 140)
    BORDER = (50, 50, 65)

    # Background
    d.rounded_rectangle([x, y, x+w, y+h], radius=max(4,int(6*s)),
                         fill=BLACK, outline=BORDER, width=max(1,int(1.5*s)))

    # Centre line (zero delta)
    cx = x + w//2
    bar_y0 = y + PAD
    bar_y1 = y + int(h*0.52)
    bar_h2 = bar_y1 - bar_y0
    bar_x0 = x + PAD
    bar_x1 = x + w - PAD
    bar_w  = bar_x1 - bar_x0

    # Zero line
    d.line([(cx, bar_y0), (cx, bar_y1)], fill=GREY, width=max(1,int(1*s)))

    # Delta bar — centred, extends left (gain) or right (loss)
    MAX_DELTA = 100.0   # ±100m = full bar width
    _frac = max(-1.0, min(1.0, delta_m / MAX_DELTA))
    bar_col = GAIN if delta_m >= 0 else LOSS
    if abs(_frac) > 0.01:
        if delta_m >= 0:  # ahead → bar extends right from centre
            bx0 = cx
            bx1 = cx + int(_frac * bar_w//2)
        else:              # behind → bar extends left from centre
            bx0 = cx + int(_frac * bar_w//2)  # _frac negative so this < cx
            bx1 = cx
        _brad = max(2, int(4*s))
        d.rounded_rectangle([bx0, bar_y0+PAD, bx1, bar_y1-PAD],
                             radius=_brad, fill=bar_col)

    # Delta history trace (thin line below bar)
    trace_y0 = bar_y1 + PAD
    trace_y1 = y + h - PAD*3 - int(22*s)
    trace_h  = trace_y1 - trace_y0
    trace_mid = trace_y0 + trace_h//2
    if len(deltas_hist) > 1:
        pts = []
        for i, dv in enumerate(deltas_hist):
            tx = bar_x0 + int(bar_w * i / max(len(deltas_hist)-1, 1))
            ty = trace_mid - int((dv / MAX_DELTA) * trace_h//2)
            ty = max(trace_y0, min(trace_y1, ty))
            pts.append((tx, ty))
        # Draw as coloured segments
        lw = max(2, int(3*s))
        for i in range(len(pts)-1):
            col = GAIN if deltas_hist[i] <= 0 else LOSS
            d.line([pts[i], pts[i+1]], fill=col, width=lw)
        # Zero baseline
        d.line([(bar_x0, trace_mid), (bar_x1, trace_mid)],
               fill=(40,40,55), width=max(1,int(1*s)))

    # Delta value — large, coloured
    _sign_str = "+" if delta_m >= 0 else "-"
    _val_str = f"{_sign_str}{abs(delta_m):.0f}m"
    _val_fsz = max(10, int(h * 0.22))
    _val_col = GAIN if delta_m >= 0 else LOSS
    _val_y   = y + h - PAD - int(_val_fsz*0.6)
    _tc(d, (x + w//2, _val_y), _val_str, fb(_val_fsz), _val_col)

    # Best lap label small
    _blt_m, _blt_s = divmod(best_lt, 60)
    _lbl = f"Δ Best  {int(_blt_m)}:{_blt_s:05.2f}"
    _tc(d, (x + w//2, y + PAD + int(6*s)), _lbl,
        fc(max(8, int(14*s))), GREY)


def build_frame(rpm, throttle, speed, gear, g_lat, g_long, ts,
                trace_glat, trace_glong_raw, laps,
                gauge_bg=None, cx=None, cy=None, radius=None,
                w=None, h=None, scale=1.0, brake_pct=None, rpm_max=9000,
                peak_rpm=None, trace_speed=None, speed_colour=False, P=None,
                trace_throttle=None, trace_brake=None, trace_gear=None,
                chanA=None, chanA_label="", chanB=None, chanB_label="",
                chanA_unit="", chanB_unit=""):
    P = P or STYLES["Style 5"]
    # Stash optional extra channels so individual style builders can read them
    _CHAN = {"A_val": chanA, "A_lbl": chanA_label or "", "A_unit": chanA_unit or "",
             "B_val": chanB, "B_lbl": chanB_label or "", "B_unit": chanB_unit or ""}

    _w = w or W_VID; _h = h or H_VID
    s  = scale
    BAR_H = int(360*s); BAR_Y = (_h - BAR_H)//2

    # ── Style 16/17: Dash 8 circular telemetry gauge ──────────────────────────
    if P.get("dash8_layout") and _dash8 is not None:
        _bg = _dash8_get_bg(P.get("dash8_chroma", False), rpm_max=rpm_max)
        timer_str, _tcol, _tlbl = get_timer_display(ts, laps, P=P)
        _lapno = 1
        if laps:
            for _li, (_st, _et, _lt) in enumerate(laps):
                if _st <= ts <= _et:
                    _lapno = _li + 1; break
            else:
                if ts > laps[-1][1]:
                    _lapno = len(laps)
        _img = _dash8.render_frame(
            rpm=rpm, speed=speed, gear=int(gear),
            lap=_lapno, timer_str=timer_str,
            rpm_max=rpm_max, peak_rpm=peak_rpm if peak_rpm else int(rpm),
            throttle=throttle if throttle is not None else 0,
            brake=brake_pct if brake_pct is not None else 0,
            g_lat=g_lat, g_long=g_long,
            trace_glat=trace_glat, trace_glong=trace_glong_raw,
            trace_speed=trace_speed, speed_colour=speed_colour,
            bg=_bg,
            chanA=_CHAN["A_val"], chanA_label=_CHAN["A_lbl"],
            chanB=_CHAN["B_val"], chanB_label=_CHAN["B_lbl"],
            chanA_unit=_CHAN.get("A_unit",""), chanB_unit=_CHAN.get("B_unit",""),
            bars_inside=P.get("dash8_bars_inside", False))
        if (_img.width, _img.height) != (_w, _h):
            _img = _img.resize((_w, _h), Image.LANCZOS)
        return _img.convert("RGB")

    # Gauge geometry
    GS = int(460*s); GX = int(10*s)
    _cx = cx or GX + GS//2
    _cy = cy or _h//2
    _r  = radius or GS//2 - int(12*s)


    # Style 14: time-trail data panel
    if P.get("style14_layout"):
        img = Image.new("RGB", (_w, _h), P["panel"])
        d   = ImageDraw.Draw(img)
        return _build_frame_style14(img, d, rpm, throttle, speed, gear,
                                    g_lat, g_long, ts, laps, _w, _h, s,
                                    brake_pct, rpm_max, peak_rpm, P,
                                    trace_glat, trace_glong_raw, trace_speed,
                                    trace_throttle, trace_brake, trace_gear, _CHAN)

    # Style 20: Dash 9 — full-width plot + bottom panel row
    if P.get("style20_layout"):
        img = Image.new("RGB", (_w, _h), P["panel"])
        d   = ImageDraw.Draw(img)
        return _build_frame_style20(img, d, rpm, throttle, speed, gear,
                                    g_lat, g_long, ts, laps, _w, _h, s,
                                    brake_pct, rpm_max, peak_rpm, P,
                                    trace_glat, trace_glong_raw, trace_speed,
                                    trace_throttle, trace_brake, trace_gear, _CHAN)

    # Style 13: slim-bar variant of style 12 — skip gauge entirely
    if P.get("style13_layout"):
        img = Image.new("RGB", (_w, _h), P["panel"])
        d   = ImageDraw.Draw(img)
        return _build_frame_style13(img, d, rpm, throttle, speed, gear,
                                    g_lat, g_long, ts, laps, _w, _h, s,
                                    brake_pct, rpm_max, peak_rpm, P,
                                    trace_glat, trace_glong_raw,
                                    trace_throttle, trace_brake,
                                    trace_speed, speed_colour, _CHAN)

    # Style 12: coloured-box race dash — skip gauge entirely
    if P.get("style12_layout"):
        img = Image.new("RGB", (_w, _h), P["panel"])
        d   = ImageDraw.Draw(img)
        return _build_frame_style12(img, d, rpm, throttle, speed, gear,
                                    g_lat, g_long, ts, laps, _w, _h, s,
                                    brake_pct, rpm_max, peak_rpm, P,
                                    trace_glat, trace_glong_raw,
                                    trace_speed, speed_colour, _CHAN)

    # Style 11: full-frame race dash — skip gauge entirely
    if P.get("style11_layout"):
        img = Image.new("RGB", (_w, _h), P["panel"])
        d   = ImageDraw.Draw(img)
        return _build_frame_style11(img, d, rpm, throttle, speed, gear,
                                    g_lat, g_long, ts, laps, _w, _h, s,
                                    brake_pct, rpm_max, peak_rpm, P, _CHAN)

    # Styles 4, 7, 8 are full-frame chroma overlays — skip gauge rendering
    if P.get("style4_layout") or P.get("style7_layout") or P.get("style8_layout"):
        img = Image.new("RGB", (_w, _h), P["chroma"])
        d   = ImageDraw.Draw(img)
        if P.get("style8_layout"):
            return _build_frame_style8(img, d, rpm, throttle, speed, gear,
                                        g_lat, g_long, ts, laps, _w, _h, s,
                                        brake_pct, rpm_max, peak_rpm, P)
        if P.get("style7_layout"):
            return _build_frame_style7(img, d, rpm, throttle, speed, gear,
                                        g_lat, g_long, ts, laps, _w, _h, s,
                                        brake_pct, rpm_max, peak_rpm, P)
        # style4_layout handled below after this block

    # Start from static background (gauge styles)
    if not P.get("style4_layout"):
        if gauge_bg is not None:
            img = _draw_gauge_dynamic(gauge_bg, _cx, _cy, _r, rpm, rpm_max=rpm_max, peak_rpm=peak_rpm, P=P)
        else:
            bg = _build_gauge_bg(_cx, _cy, _r, rpm_max=rpm_max, P=P)
            img = _draw_gauge_dynamic(bg, _cx, _cy, _r, rpm, rpm_max=rpm_max, peak_rpm=peak_rpm, P=P)
        img = img.convert('RGB')
        right = Image.new('RGB', (_w - (GX+GS+int(5*s)), _h), P["chroma"])
        img.paste(right, (GX+GS+int(5*s), 0))
    else:
        img = Image.new("RGB", (_w, _h), P["chroma"])
    d = ImageDraw.Draw(img)


    # ── STYLE 5: Speed/gear inside gauge, bars beside, wider timer panels ────────
    if P.get("style5_layout", False):
        _s15_base = _build_frame_style5(img, d, rpm, throttle, speed, gear,
                                    g_lat, g_long, ts, trace_glat, trace_glong_raw,
                                    laps, _w, _h, s, brake_pct, rpm_max, peak_rpm,
                                    trace_speed, speed_colour, P,
                                    GX, GS, _cx, _cy, _r, BAR_Y, BAR_H, gauge_bg, _CHAN)
        if P.get("style15_layout"):
            _s15_d = ImageDraw.Draw(_s15_base)
            _draw_trapezoid_stack(_s15_d, _s15_base, _cx, _cy, _r, s, speed, rpm,
                                   None, ts, laps, P)
        return _s15_base

    # ── STYLE 4: Full-frame italic column ──────────────────────────────────────
    if P.get("style4_layout", False):
        return _build_frame_style4(img, d, rpm, throttle, speed, gear,
                                    g_lat, g_long, ts, laps, _w, _h, s,
                                    brake_pct, rpm_max, peak_rpm, P)

    # ── STYLE 3: AIM SmartyCam layout ────────────────────────────────────────
    if P.get("style3_layout", False):
        return _build_frame_style3(img, d, rpm, throttle, speed, gear,
                                   g_lat, g_long, ts, trace_glat, trace_glong_raw,
                                   laps, _w, _h, s, brake_pct, rpm_max, peak_rpm,
                                   trace_speed, speed_colour, P)

    # ── TIMER / SPEED / GEAR ─────────────────────────────────────────────────
    if P.get("style15_layout"):
        return img

    timer_str, timer_col, timer_lbl = get_timer_display(ts, laps, P=P)
    SG_X  = GX + GS + int(18*s)
    BOX_W = int(178*s); GAP = int(10*s)
    BOX_H = (BAR_H - GAP*3)//4

    # AIM/RaceStudio-style boxes: sharp rectangle, colour band label strip at bottom,
    # thin 1px bevel (top+left lighter, bottom+right darker)
    _LBL_H = max(int(BOX_H * 0.28), int(20*s))  # label band height

    def _draw_aim_box(x0, y0, x1, y1, accent_col):
        bv = P.get("emboss_light", P["grey"])   # bevel highlight
        bs = P.get("emboss_dark",  (0,0,0))     # bevel shadow
        lh = _LBL_H
        # Main body fill
        d.rectangle([x0, y0, x1, y1], fill=P["panel"])

        # Radial burst — cached per (size, colours) so it only computes once
        if "burst_centre" in P and "burst_edge" in P:
            _bc_t = tuple(P["burst_centre"])
            _be_t = tuple(P["burst_edge"])
            body_y1 = y1 - lh
            bx0, bx1 = x0+1, x1-1
            by0, by1 = y0+1, body_y1-1
            bW = bx1 - bx0; bH = by1 - by0
            if bW > 0 and bH > 0:
                _bkey = (bW, bH, _bc_t, _be_t)
                if _bkey not in _BOX_BURST_CACHE:
                    bc = np.array(_bc_t, dtype=np.float32)
                    be = np.array(_be_t, dtype=np.float32)
                    xs = np.linspace(-1, 1, bW, dtype=np.float32)
                    ys = np.linspace(-1, 1, bH, dtype=np.float32)
                    xx, yy = np.meshgrid(xs, ys)
                    dist = np.clip(np.sqrt(xx**2 + yy**2), 0, 1)
                    t    = dist ** 0.5
                    arr  = np.clip(
                        bc[None,None,:] * (1-t[:,:,None]) + be[None,None,:] * t[:,:,None],
                        0, 255).astype(np.uint8)
                    _BOX_BURST_CACHE[_bkey] = Image.fromarray(arr, mode="RGB")
                img.paste(_BOX_BURST_CACHE[_bkey], (bx0, by0))

        # Colour label strip at bottom
        d.rectangle([x0, y1-lh, x1, y1], fill=accent_col)
        bw = max(2, int(4*s))
        iw = max(1, int(2*s))
        io = bw + iw            # inner offset
        # Outer bevel: top+left highlight
        d.line([x0, y0, x1, y0], fill=bv, width=bw)
        d.line([x0, y0, x0, y1], fill=bv, width=bw)
        # Outer bevel: bottom+right shadow
        d.line([x0, y1, x1, y1], fill=bs, width=bw)
        d.line([x1, y0, x1, y1], fill=bs, width=bw)
        # Inner bevel: top+left highlight
        d.line([x0+io, y0+io, x1-io, y0+io], fill=bv, width=iw)
        d.line([x0+io, y0+io, x0+io, y1-io], fill=bv, width=iw)
        # Inner bevel: bottom+right shadow
        d.line([x0+io, y1-io, x1-io, y1-io], fill=bs, width=iw)
        d.line([x1-io, y0+io, x1-io, y1-io], fill=bs, width=iw)


    # Current lap number
    _lap_num = "—"
    for _li, (_st, _et, _lt) in enumerate(laps, 1):
        if _st <= ts < _et: _lap_num = str(_li)
        elif ts >= _et and ts < _et + 5: _lap_num = str(_li)

    # All four boxes share same max font, shrunk to fit
    _accent_col = P.get("accent_line", P.get("cyan", (200,200,200)))
    _data_col = P.get("data", P["white"])  # same colour for all values
    _box_data = [
        (_lap_num,                          _data_col, "LAP"),
        (timer_str,                         _data_col, timer_lbl),
        (f"{int(speed)}",                   _data_col, "km/h"),
        (str(gear) if gear>0 else "N",      _data_col, "GEAR"),
    ]
    _max_fsz = int(80*s)
    _margin  = int(14*s)
    _outline_col = P.get("dark", (8,8,14))
    _ow = max(1, int(2*s))
    _lbl_fnt = fc(max(10, int(20*s)))   # larger label font
    for i, (val, col, lbl) in enumerate(_box_data):
        by = BAR_Y + (3-i)*(BOX_H+GAP)
        _draw_aim_box(SG_X, by, SG_X+BOX_W, by+BOX_H, _accent_col)
        # Value
        _fsz = _max_fsz
        while _fsz > 8:
            _bb = d.textbbox((0,0), val, font=fc(_fsz))
            if (_bb[2]-_bb[0]) < BOX_W - _margin:
                break
            _fsz -= 1
        _tc_outlined(d, (SG_X+BOX_W//2, by + (BOX_H-_LBL_H)//2),
                     val, fc(_fsz), col, _outline_col, _ow)
        # Label band — use data colour so Style 2 gets dark text on light strip
        _lbl_text_col = P.get("data", (255,255,255))
        _tc_outlined(d, (SG_X+BOX_W//2, by+BOX_H-_LBL_H//2),
                     lbl, _lbl_fnt, _lbl_text_col, _outline_col, _ow)

    # ── THROTTLE + BRAKE BARS ────────────────────────────────────────────────
    BAR_X  = SG_X + BOX_W + 22
    BAR_W  = 52; BAR_GAP = 16
    # brake_pct is passed in pre-normalised 0-100 (or g_long fallback from render_video)
    _brake = float(np.clip(brake_pct if brake_pct is not None else 0.0, 0, 100))

    for ix, (pct100, col) in enumerate([(throttle, P["green"]),(_brake, P["red"])]):
        bx = BAR_X + ix*(BAR_W+BAR_GAP)
        d.rounded_rectangle([bx, BAR_Y, bx+BAR_W, BAR_Y+BAR_H],
                              radius=6, fill=P["panel"], outline=P["grey"], width=1)
        fh = int((BAR_H-4) * pct100 / 100.0)
        if fh > 4:
            d.rounded_rectangle([bx+2, BAR_Y+BAR_H-2-fh,
                                  bx+BAR_W-2, BAR_Y+BAR_H-2],
                                  radius=5, fill=col)

    # ── GG TRACE ─────────────────────────────────────────────────────────────
    TR_X = BAR_X + BAR_W*2 + BAR_GAP + int(20*s)
    TR_W = _w - TR_X - int(15*s)

    # Use square GG box — fill outer strip with chroma so no panel bleed shows
    GG_SZ = min(TR_W, BAR_H)
    GG_X  = TR_X + (TR_W - GG_SZ)//2
    GG_Y  = BAR_Y + (BAR_H - GG_SZ)//2
    # Fill entire TR area with chroma first (removes blank panel sides)
    d.rectangle([TR_X, BAR_Y, TR_X+TR_W, BAR_Y+BAR_H], fill=P["chroma"])
    # Draw the square GG box
    d.rounded_rectangle([GG_X, GG_Y, GG_X+GG_SZ, GG_Y+GG_SZ],
                          radius=4, fill=P["panel"], outline=P["border"], width=1)
    mx = GG_X + GG_SZ//2; my = GG_Y + GG_SZ//2
    GU  = GG_SZ // 4   # pixels per G unit (2G range each side)
    # Grid — 0.5G minor, 1G major, 0 = white
    HU = GU // 2   # half-G in pixels
    for gv in [-2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0]:
        lx = mx + int(gv * GU)
        ly = my - int(gv * GU)
        is_full = (gv == int(gv))   # True for ±1, ±2
        grid_col = (70,70,100) if is_full else (40,40,60)
        grid_w   = 2 if is_full else 1
        d.line([lx, GG_Y+4, lx, GG_Y+GG_SZ-4], fill=grid_col, width=grid_w)
        d.line([GG_X+4, ly, GG_X+GG_SZ-4, ly], fill=grid_col, width=grid_w)
    d.line([mx, GG_Y+4, mx, GG_Y+GG_SZ-4], fill=P["white"], width=2)
    d.line([GG_X+4, my, GG_X+GG_SZ-4, my], fill=P["white"], width=2)

    # Live G readings — use data colour, with panel-coloured backing rectangle
    _fnt_g    = fc(max(16, int(26*s)))
    _data_col = P.get("data", P["white"])
    _live_y   = GG_Y + GG_SZ - int(22*s)
    _pad_x    = int(6*s); _pad_y = int(4*s)
    for _txt, _tx in [(f"Lat {g_lat:+.2f}G", GG_X + GG_SZ//4),
                       (f"Lon {-g_long:+.2f}G", GG_X + GG_SZ*3//4)]:
        _bb = d.textbbox((0,0), _txt, font=_fnt_g)
        _tw = _bb[2]-_bb[0]; _th = _bb[3]-_bb[1]
        # Draw panel-coloured backing rectangle
        d.rectangle([_tx-_tw//2-_pad_x, _live_y-_th//2-_pad_y,
                      _tx+_tw//2+_pad_x, _live_y+_th//2+_pad_y], fill=P["panel"])
        _tc(d, (_tx, _live_y), _txt, _fnt_g, _data_col)

    # Trace line
    trace_glong = [-g for g in trace_glong_raw]  # flip: braking up
    n = len(trace_glat)

    def _speed_colour(spd, min_spd=50.0, max_spd=200.0):
        """Blue at 50 kph → red at 200 kph. Clamps outside that range."""
        t = max(0.0, min(1.0, (spd - min_spd) / (max_spd - min_spd)))
        if t < 0.5:
            # blue → cyan
            tt = t / 0.5
            return (0, int(180*tt), int(255 - 55*tt))
        else:
            # cyan → yellow → red
            tt = (t - 0.5) / 0.5
            if tt < 0.5:
                ttt = tt / 0.5
                return (int(255*ttt), 255, int(255*(1-ttt)))
            else:
                ttt = (tt - 0.5) / 0.5
                return (255, int(255*(1-ttt)), 0)

    if n > 1:
        pts = []
        for i in range(n):
            tx = mx + int(trace_glat[i] * GU)
            ty = my - int(trace_glong[i] * GU)
            tx = max(GG_X+2, min(GG_X+GG_SZ-2, tx))
            ty = max(GG_Y+2, min(GG_Y+GG_SZ-2, ty))
            pts.append((tx, ty))
        lw = max(3, int(6*s))
        for i in range(len(pts)-1):
            alpha = 0.08 + 0.92*(i/(n-1))
            if speed_colour and trace_speed and i < len(trace_speed):
                base = _speed_colour(trace_speed[i])
                col_t = tuple(int(c*alpha) for c in base)
            else:
                col_t = _alpha_col(P["cyan"], alpha)
            d.line([pts[i], pts[i+1]], fill=col_t, width=lw)

    # Current dot
    dx = mx + int(g_lat * GU)
    dy = my + int(g_long * GU)   # note: raw g_long, braking negative = up on screen   # note: raw g_long, braking negative=up on screen
    dx = max(GG_X+5, min(GG_X+GG_SZ-5, dx))
    dy = max(GG_Y+5, min(GG_Y+GG_SZ-5, dy))
    dr = max(6, int(11*s))
    dot_col = _speed_colour(speed) if speed_colour else P["cyan"]
    d.ellipse([dx-dr,dy-dr,dx+dr,dy+dr], fill=dot_col, outline=P["white"], width=max(2,int(3*s)))

    return img


def render_video(rows, t_start, t_end, output_path, fps,
                 progress_cb, done_cb, error_cb, cancel_check=None,
                 filter_rpm=False, filter_speed=False,
                 resolution=(1280, 500), g_trail_secs=5.0,
                 lap_offset=0.0,
                 lap_min_spd=200.0, lap_est_time=60.0,
                 speed_colour=False, style="Style 1",
                 precomputed_laps=None,
                 delta_ref=None,
                 chanA_label="", chanB_label="",
                 chanA_unit="", chanB_unit=""):
    try:
        import pandas as pd
        print(f"render_video: delta_ref={delta_ref is not None}", flush=True)
        print(f"render_video: t_start={t_start:.3f} t_end={t_end:.3f} "
              f"rows_ts=[{rows['ts'].min():.3f}..{rows['ts'].max():.3f}] "
              f"n_rows={len(rows)}", flush=True)
        mask    = (rows['ts'] >= t_start) & (rows['ts'] <= t_end)
        segment = rows[mask].reset_index(drop=True)
        if len(segment) == 0:
            error_cb("No data in selected time range."); return
        print(f"  segment: {len(segment)} frames, first_ts={segment['ts'].iloc[0]:.3f} "
              f"first_rpm={segment['rpm'].iloc[0]:.0f} first_speed={segment['speed'].iloc[0]:.0f}", flush=True)

        # Apply low-pass filter if requested
        if filter_rpm:
            segment = segment.copy()
            segment['rpm'] = apply_lowpass(segment['rpm'])
        if filter_speed:
            segment = segment.copy()
            segment['speed'] = apply_lowpass(segment['speed'])

        # Brake: use dedicated channel if present, else derive from g_long
        has_brake_ch = 'brake' in segment.columns and segment['brake'].notna().any()
        if not has_brake_ch:
            # No brake channel mapped — leave as NaN (bar will show empty)
            segment = segment.copy()
            segment['brake'] = np.nan

        P = STYLES.get(style, STYLES["Style 5"])

        # Detect laps from FULL dataset so mid-session renders still get correct timer
        if precomputed_laps:
            # Use AIM beacon laps — just apply the timer offset
            laps = [(st + lap_offset, et + lap_offset, lt)
                    for (st, et, lt) in precomputed_laps]
            print(f"Using {len(laps)} precomputed AIM laps", flush=True)
        else:
            laps_raw = find_lap_events(rows['speed'].values, rows['ts'].values,
                                       min_spd=float(lap_min_spd), min_gap=float(lap_est_time))
            laps = [(st + lap_offset, et + lap_offset, lt)
                    for (st, et, lt) in laps_raw]
        # ── Interpolate to output fps if needed ───────────────────────────────
        data_dt   = float(np.median(np.diff(segment['ts'].values)))
        data_fps  = 1.0 / data_dt if data_dt > 0 else fps
        if abs(fps - data_fps) > 0.5:   # resample needed
            _t0s = float(segment['ts'].iloc[0])
            _t1s = float(segment['ts'].iloc[-1])
            # Integer frame indexing — eliminates floating-point accumulation
            _frame_count = int(round((_t1s - _t0s) * fps))
            _new_ts = _t0s + np.arange(_frame_count) / fps
            _old_ts = segment['ts'].values.astype(float)
            _interp_cols = [c for c in segment.columns
                            if c != 'ts' and np.issubdtype(segment[c].dtype, np.number)]
            _interp_data = {'ts': _new_ts}
            for _c in _interp_cols:
                _vals = segment[_c].values.astype(float)
                _interp_data[_c] = np.interp(_new_ts, _old_ts, _vals)
            # Gear: nearest-neighbour not linear
            if 'gear' in segment.columns:
                _gear_idx = np.searchsorted(_old_ts, _new_ts).clip(0, len(_old_ts)-1)
                _interp_data['gear'] = segment['gear'].values[_gear_idx].astype(int)
            import pandas as _pd2
            segment = _pd2.DataFrame(_interp_data)
            print(f"Resampled {len(segment)} frames at {fps}fps "
                  f"(data was {data_fps:.1f}fps)", flush=True)

        N    = len(segment)

        # Resolve output dimensions
        out_w, out_h = resolution
        scale = out_w / W_VID

        # Detect peak RPM and set gauge scale to nearest 1000 above
        peak_rpm = float(segment['rpm'].max())
        import math as _math
        rpm_max  = int(_math.ceil(peak_rpm / 1000) * 1000)
        rpm_max  = max(rpm_max, 6000)   # minimum sensible scale
        print(f"Peak RPM: {peak_rpm:.0f}  →  Gauge scale: 0-{rpm_max}", flush=True)

        # Pre-build static gauge background once (scaled, with correct rpm_max).
        # Dash 8 family builds its own background internally — skip here.
        GS  = int(460 * scale); GX = int(10 * scale)
        cx  = GX + GS//2; cy = int(H_VID * scale)//2
        r   = GS//2 - int(12 * scale)
        if P.get("dash8_layout"):
            gauge_bg = None
        else:
            gauge_bg = _build_gauge_bg(cx, cy, r, w=out_w, h=int(H_VID*scale), rpm_max=rpm_max, P=P)

        out_h_actual = int(H_VID * scale)
        ffmpeg_exe = get_ffmpeg_path()
        cmd = [ffmpeg_exe, "-y",
               "-f","rawvideo","-vcodec","rawvideo",
               "-s",f"{out_w}x{out_h_actual}","-pix_fmt","rgb24",
               "-r",str(fps),"-i","pipe:0",
               "-c:v","libx264","-preset","fast",
               "-crf","18","-pix_fmt","yuv420p",
               output_path]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

        # Pre-build RPM array for fast 1s rolling peak lookup
        _seg_ts  = segment['ts'].values
        _seg_rpm = segment['rpm'].values

        # ── Parallel render: build frames in thread pool, write in order ────────
        import concurrent.futures as _cf
        _N_WORKERS = min(4, max(1, __import__('os').cpu_count() - 1))

        def _build_one(args):
            _i, _row = args
            _t_now    = float(_row['ts'])
            _cur_gear = int(_row['gear'])
            _cur_rpm  = float(_row['rpm'])
            _peak_mask = (_seg_ts >= _t_now - 1.5) & (_seg_ts <= _t_now)
            _shown_pk  = int(_seg_rpm[_peak_mask].max()) if _peak_mask.any() else None
            _tr_mask   = (segment['ts'] >= _t_now - g_trail_secs) & (segment['ts'] <= _t_now)
            _tr        = segment[_tr_mask]
            _bv = _row['brake'] if 'brake' in _row.index else float('nan')
            _bval = 0.0 if (not isinstance(_bv, float) or np.isnan(_bv)) else float(np.clip(_bv, 0, 100))
            # Optional extra channels (NaN sentinel when not selected)
            _chA = float(_row['chanA']) if 'chanA' in _row.index else float('nan')
            _chB = float(_row['chanB']) if 'chanB' in _row.index else float('nan')
            _img = build_frame(
                _cur_rpm, float(_row['throttle']), float(_row['speed']),
                _cur_gear, float(_row['g_lat']), float(_row['g_long']),
                _t_now,
                _tr['g_lat'].tolist(), _tr['g_long'].tolist(),
                laps, gauge_bg, cx, cy, r,
                w=out_w, h=out_h_actual, scale=scale,
                brake_pct=_bval, rpm_max=rpm_max,
                peak_rpm=_shown_pk,
                trace_speed=_tr['speed'].tolist() if speed_colour else None,
                speed_colour=speed_colour, P=P,
                trace_throttle=_tr['throttle'].tolist() if 'throttle' in _tr.columns else None,
                trace_brake=_tr['brake'].tolist() if 'brake' in _tr.columns else None,
                trace_gear=_tr['gear'].tolist() if 'gear' in _tr.columns else None,
                chanA=_chA, chanA_label=chanA_label,
                chanB=_chB, chanB_label=chanB_label,
                chanA_unit=chanA_unit, chanB_unit=chanB_unit)
            # Draw delta widget if enabled
            if delta_ref is not None:
                from PIL import ImageDraw as _IDrw
                _dimg = _img if _img.mode == 'RGB' else _img.convert('RGB')
                _dd = _IDrw.Draw(_dimg)
                _dx, _dy, _dw, _dh = _delta_widget_pos(P, out_w, out_h_actual)
                draw_delta_widget(
                    _dd, _dimg,
                    _dx, _dy, _dw, _dh,
                    _t_now, laps, delta_ref,
                    delta_ref["full_ts"],
                    s=scale)
                return _i, _dimg.tobytes()
            return _i, _img.tobytes() if _img.mode == 'RGB' else _img.convert('RGB').tobytes()

        _rows_list = list(segment.iterrows())
        _LOOKAHEAD = _N_WORKERS * 3   # keep this many frames in flight

        with _cf.ThreadPoolExecutor(max_workers=_N_WORKERS) as _pool:
            from collections import deque as _dq
            _in_flight = _dq()   # (future,) in submission order
            _submitted = 0

            # Pre-fill the pipeline
            while _submitted < min(_LOOKAHEAD, len(_rows_list)):
                _in_flight.append(_pool.submit(_build_one, _rows_list[_submitted]))
                _submitted += 1

            for _i in range(len(_rows_list)):
                if cancel_check and cancel_check():
                    ff.stdin.close(); ff.wait()
                    try: os.remove(output_path)
                    except: pass
                    done_cb(); return

                # Get next completed frame (in order)
                _, _raw = _in_flight.popleft().result()
                ff.stdin.write(_raw)

                # Submit one more to keep pipeline full
                if _submitted < len(_rows_list):
                    _in_flight.append(_pool.submit(_build_one, _rows_list[_submitted]))
                    _submitted += 1

                if _i % 10 == 0:
                    progress_cb(int(100 * _i / N))

        ff.stdin.close(); ff.wait()
        progress_cb(100); done_cb()
    except Exception as e:
        import traceback
        error_cb(f"{e}\n{traceback.format_exc()}")
