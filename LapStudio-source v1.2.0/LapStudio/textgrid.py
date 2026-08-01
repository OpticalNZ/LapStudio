"""
textgrid.py — fixed digit-pitch text drawing, shared by every LapStudio renderer.

The display fonts (Big Shoulders, Poppins) are proportional, not tabular: at
size 100 a "1" is 27 px wide but an "8" is 49 px. So "111" measures 81 px and
"888" measures 145 px, and any centre-anchored readout slides sideways by ~30 px
as the value changes. That was the jitter on every dash.

Every digit here gets its own fixed-width cell, so a digit's position depends
only on the slot it occupies - never on its shape or its neighbours.
"""

from PIL import Image, ImageDraw, ImageFont


def _stroke_supported():
    """Pillow >= 6.2 can draw a text outline in a single pass (stroke_width)."""
    try:
        import inspect
        return 'stroke_width' in inspect.signature(ImageDraw.ImageDraw.text).parameters
    except Exception:
        return False


_HAS_STROKE = _stroke_supported()
_SCRATCH = None


def _scratch_draw():
    """One reusable canvas for text measurement."""
    global _SCRATCH
    if _SCRATCH is None:
        _SCRATCH = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    return _SCRATCH


def _vmask(text):
    """Metric reference for a value: every digit becomes '8' (the widest), so a
    bbox taken from it does not change as the value does."""
    return "".join("8" if c.isdigit() else c for c in str(text))


_PITCH_CACHE = {}


def _font_key(fnt):
    return (getattr(fnt, "path", None), getattr(fnt, "size", None), id(fnt))


def _digit_pitch(fnt):
    """Width of the widest digit in `fnt` - the fixed cell width."""
    k = _font_key(fnt)
    v = _PITCH_CACHE.get(k)
    if v is None:
        sd = _scratch_draw()
        try:
            v = max(sd.textlength(str(i), font=fnt) for i in range(10))
        except Exception:
            v = sd.textbbox((0, 0), "8", font=fnt)[2]
        _PITCH_CACHE[k] = v
    return v


def _cells(text, fnt):
    """[(token, cell_x, cell_w)] plus total width.

    Each DIGIT gets its own fixed-pitch cell. Runs of non-digits (words, units,
    ":" and ".") stay together in one cell at their natural width, so kerning
    inside "km/h" or "Lap" is preserved.
    """
    sd = _scratch_draw()
    pitch = _digit_pitch(fnt)
    out = []
    x = 0.0
    run = ""
    def _flush(x):
        nonlocal run
        if run:
            try:
                cw = sd.textlength(run, font=fnt)
            except Exception:
                cw = sd.textbbox((0, 0), run, font=fnt)[2]
            out.append((run, x, cw))
            x += cw
            run = ""
        return x
    for ch in text:
        if ch.isdigit():
            x = _flush(x)
            out.append((ch, x, pitch))
            x += pitch
        else:
            run += ch
    x = _flush(x)
    return out, x


def _gtext(draw, xy, text, fnt, fill, anchor="mm", outline=None, ow=0,
           vref=None, slots=None):
    """Draw `text` with digits on a fixed pitch so the value cannot jitter.

    anchor: two chars, horizontal in "lmr" and vertical in "mtb" (as PIL).
    vref:   metric reference for VERTICAL placement (see _vmask) - keeps the
            baseline steady when glyph heights differ between frames.
    slots:  optional template for the widest value the field will ever show
            (e.g. "888" for a 0-999 readout). The field is reserved at that
            width, which keeps the surrounding layout fixed; the value is then
            aligned in it per `anchor`. Reserve + anchor "r" gives a column that
            never moves at all. Without slots the field is sized to the current
            text, which already removes all same-length jitter.
    """
    text = "" if text is None else str(text)
    if not text:
        return
    if not any(c.isdigit() for c in text) and not slots:
        # A label can't jitter - draw it natively so kerning is untouched.
        ow = int(ow or 0)
        if outline is not None and ow > 0 and _HAS_STROKE:
            draw.text(xy, text, font=fnt, fill=fill, anchor=anchor,
                      stroke_width=ow, stroke_fill=outline)
        elif outline is not None and ow > 0:
            for dx in range(-ow, ow + 1):
                for dy in range(-ow, ow + 1):
                    if dx or dy:
                        draw.text((xy[0] + dx, xy[1] + dy), text, font=fnt,
                                  fill=outline, anchor=anchor)
            draw.text(xy, text, font=fnt, fill=fill, anchor=anchor)
        else:
            draw.text(xy, text, font=fnt, fill=fill, anchor=anchor)
        return
    cells, tw = _cells(text, fnt)

    # ── horizontal: place the reserved field box, then the value inside it
    res_w = tw
    if slots:
        _, res_w = _cells(str(slots), fnt)
        res_w = max(res_w, tw)
    ah = anchor[0] if anchor else "m"
    if ah == "l":
        box_x = xy[0]
    elif ah == "r":
        box_x = xy[0] - res_w
    else:
        box_x = xy[0] - res_w / 2.0
    # Align the value INSIDE the reserved field the same way the field itself is
    # anchored: a centred readout stays centred (so a digit-count change steps it
    # by half a cell), while a right-anchored column is pinned and never moves.
    if ah == "l":
        x0 = box_x
    elif ah == "r":
        x0 = box_x + (res_w - tw)
    else:
        x0 = box_x + (res_w - tw) / 2.0

    # ── vertical: measure from vref when given so the row never bobs
    bb = draw.textbbox((0, 0), vref if vref else text, font=fnt)
    av = anchor[1] if len(anchor or "") > 1 else "m"
    if av == "a":            # PIL default: y is the ascender line
        y0 = xy[1]
    elif av == "t":          # top of the inked box
        y0 = xy[1] - bb[1]
    elif av == "b":          # bottom of the inked box
        y0 = xy[1] - bb[3]
    elif av == "s":          # baseline
        try:
            y0 = xy[1] - fnt.getmetrics()[0]
        except Exception:
            y0 = xy[1] - bb[3]
    else:                    # "m" - vertically centred on the inked box
        y0 = xy[1] - (bb[3] - bb[1]) / 2.0 - bb[1]

    ow = int(ow or 0)
    stroke = (outline is not None and ow > 0)
    for ch, cx, cw in cells:
        if not ch.strip():
            continue
        if len(ch) == 1 and ch.isdigit():
            # Centre the glyph's ADVANCE box in the cell, in float. (Centring the
            # ink box instead needs integer bboxes and leaves a 1-2 px wobble.)
            try:
                adv = draw.textlength(ch, font=fnt)
            except Exception:
                adv = cw
            gx = x0 + cx + (cw - adv) / 2.0
        else:
            gx = x0 + cx
        if stroke and _HAS_STROKE:
            draw.text((gx, y0), ch, font=fnt, fill=fill,
                      stroke_width=ow, stroke_fill=outline)
        elif stroke:
            for dx in range(-ow, ow + 1):
                for dy in range(-ow, ow + 1):
                    if dx or dy:
                        draw.text((gx + dx, y0 + dy), ch, font=fnt, fill=outline)
            draw.text((gx, y0), ch, font=fnt, fill=fill)
        else:
            draw.text((gx, y0), ch, font=fnt, fill=fill)


def _vw(text, fnt):
    """Width of the widest same-length rendering of `text` (its digit mask).

    Auto-shrink loops must measure THIS, not the current value: otherwise the
    chosen font size changes with which digits happen to be showing and the
    readout visibly breathes frame to frame.
    """
    return _gwidth(_vmask(str(text)), fnt)


def _gwidth(text, fnt, slots=None):
    """Laid-out width of `text` (or of the reserved field if slots given)."""
    if slots:
        return max(_cells(str(text or ""), fnt)[1], _cells(str(slots), fnt)[1])
    return _cells(str(text or ""), fnt)[1]


