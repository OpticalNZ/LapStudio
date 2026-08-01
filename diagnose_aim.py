"""
diagnose_aim.py — report exactly what LapStudio sees in an AIM (or CSV/VBO) log.

Usage (from the LapStudio folder):
    py diagnose_aim.py "my session.drk"

Prints: every channel with its range, what auto-detection chooses for each dash
channel, and how the GPS coordinate columns are interpreted. Paste the output
back so the mapping problem can be pinned down without guesswork.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd


def _load_app_helpers():
    """Pull the detection helpers out of ecu_overlay_app without importing it
    (avoids needing tkinter just to run diagnostics)."""
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, 'ecu_overlay_app.py'), encoding='utf-8').read()
    ns = {'np': np, 'pd': pd, 'os': os, 'sys': sys, 're': __import__('re')}
    # module-level constants
    for blk, endtok in (("CHANNEL_KEYWORDS = {", "}\n"),
                        ("CHANNEL_LABELS = {", "}\n"),
                        ("_EXACT_NAMES = {", "}\n"),
                        ("_GPS_POS_EXCLUDE = (", ")\n"),
                        ("_SPEED_EXCLUDE = (", ")\n")):
        i = src.find(blk)
        if i >= 0:
            j = src.index(endtok, i) + len(endtok)
            exec(compile(src[i:j], '<app>', 'exec'), ns)
    # functions
    for fn in ('detect_delimiter', 'find_header_row', 'auto_detect_channels',
               'normalise_coordinates', 'looks_like_coordinates'):
        i = src.find(f"def {fn}(")
        if i < 0:
            continue
        j = src.find("\ndef ", i + 5)
        k = src.find("\nclass ", i + 5)
        end = min(x for x in (j, k, len(src)) if x > 0)
        exec(compile(src[i:end], '<app>', 'exec'), ns)
    return type('APP', (), ns)


APP = _load_app_helpers()


def load(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.drk', '.xrk', '.xrz'):
        import xrk_reader as XR
        r = XR.read_xrk(path, progress_cb=lambda p, m: None)
        return r['df'], r['ts_col'], r['meta']
    if ext == '.vbo':
        import vbo_reader as VB
        df, meta = VB.read_vbo(path)
        return df, 'ts', meta
    # CSV
    delim = APP.detect_delimiter(path)
    hdr = APP.find_header_row(path, delim)
    df = pd.read_csv(path, delimiter=delim, skiprows=hdr, low_memory=False,
                     encoding='utf-8', encoding_errors='replace')
    df = df.apply(pd.to_numeric, errors='coerce')
    df = df.dropna(axis=1, how='all')
    ts = df.columns[0]
    return df, ts, {}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        # default to the AIM files present
        cands = [f for f in os.listdir('.') if f.lower().endswith(('.drk', '.xrk'))]
        if not cands:
            return
        path = cands[0]
        print(f"No file given — using {path}\n")
    else:
        path = sys.argv[1]

    if not os.path.isfile(path):
        print(f"File not found: {path}")
        return

    print("=" * 78)
    print(f"FILE: {path}")
    print("=" * 78)
    df, ts_col, meta = load(path)
    print(f"rows={len(df):,}  columns={len(df.columns)}  time column='{ts_col}'")
    if meta:
        print(f"vehicle={meta.get('vehicle','')}  track={meta.get('track','')}  "
              f"laps={meta.get('n_laps', len(meta.get('laps', [])))}")
    t = pd.to_numeric(df[ts_col], errors='coerce')
    print(f"time range: {t.min():.2f} .. {t.max():.2f} s")

    print("\n" + "-" * 78)
    print("ALL CHANNELS (min / max / mean)")
    print("-" * 78)
    for c in df.columns:
        v = pd.to_numeric(df[c], errors='coerce')
        fin = v[np.isfinite(v)]
        if len(fin) == 0:
            print(f"  {c:26s}  (no numeric data)")
        else:
            print(f"  {c:26s}  {fin.min():14.4f} {fin.max():14.4f} {fin.mean():14.4f}")

    print("\n" + "-" * 78)
    print("AUTO-DETECTION RESULT")
    print("-" * 78)
    cols = list(df.columns)
    m = APP.auto_detect_channels(cols)
    for ch, label in APP.CHANNEL_LABELS.items():
        pick = m.get(ch)
        print(f"  {label:16s} -> {pick if pick else '(not detected)'}")

    print("\n" + "-" * 78)
    print("GPS COORDINATE INTERPRETATION")
    print("-" * 78)
    cand = [c for c in cols
            if any(k in c.lower() for k in ('lat', 'lon', 'ecef', 'position'))]
    if not cand:
        print("  no candidate coordinate columns found")
    for c in cand:
        v = pd.to_numeric(df[c], errors='coerce')
        kind = 'lat' if 'lat' in c.lower() else 'lon'
        deg, why = APP.normalise_coordinates(v, kind)
        if deg is None:
            print(f"  {c:26s} REJECTED: {why}")
        else:
            d = np.asarray(deg, dtype=float)
            d = d[np.isfinite(d)]
            print(f"  {c:26s} {why:28s} -> {d.min():11.5f} .. {d.max():11.5f} deg")

    print("\nDone. Please paste everything above.")


if __name__ == '__main__':
    main()
