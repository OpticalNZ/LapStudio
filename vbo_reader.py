"""
vbo_reader.py — Racelogic/vRacer VBO file reader.

Format details:
  - Sections delimited by [section name]
  - [header]: column names (one per line)
  - [laptiming]: lap start finish laptime (all as HHMMSS.mmm)
  - [session data]: key value pairs
  - [column names]: space-separated column names for data
  - [data]: space-separated values

  Coordinate encoding: decimal minutes (MMMM.MMMMM)
    lat_deg = raw_lat / 60.0          (negative = south)
    lon_deg = abs(raw_lon) / 60.0     (VBO uses negative for east NZ too)

  Time encoding: HHMMSS.mmm
    elapsed = HH*3600 + MM*60 + SS.mmm  (seconds since midnight UTC)
    Convert to session-relative seconds by subtracting session start time.
"""

import os
import re
import pandas as pd
import numpy as np


def _hhmmss_to_s(v):
    """Convert HHMMSS.mmm float to total seconds."""
    v = float(v)
    hh = int(v / 10000)
    mm = int((v % 10000) / 100)
    ss = v % 100
    return hh * 3600 + mm * 60 + ss


def _dec_minutes_lat(raw):
    """Decimal-minutes lat → decimal degrees (negative = south)."""
    return float(raw) / 60.0


def _dec_minutes_lon(raw):
    """Decimal-minutes lon → decimal degrees (positive = east)."""
    return abs(float(raw)) / 60.0


def read_vbo(filepath, progress_cb=None):
    """
    Read a .vbo file and return (df, meta).

    df columns (all standardised):
      ts          — elapsed seconds from session start
      lat         — decimal degrees (negative south)
      lon         — decimal degrees (positive east)
      speed       — km/h
      g_long      — longitudinal G
      g_lat       — lateral G
      heading     — degrees
      height      — metres
      distance    — metres

    meta keys:
      driver, track, vehicle, session_id, date, best_lap, num_laps
      laps: list of (start_s, end_s, laptime_s)
    """
    if progress_cb:
        progress_cb(0, 100, "Reading VBO file…")

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    lines = content.splitlines()

    # ── Parse sections ────────────────────────────────────────────────────────
    sections = {}
    cur_section = None
    cur_lines = []
    for line in lines:
        line = line.strip()
        m = re.match(r'^\[(.+)\]$', line)
        if m:
            if cur_section is not None:
                sections[cur_section] = cur_lines
            cur_section = m.group(1).lower()
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_section:
        sections[cur_section] = cur_lines

    # ── Session metadata ──────────────────────────────────────────────────────
    meta = {}
    for line in sections.get('session data', []):
        parts = line.split(None, 1)
        if len(parts) == 2:
            meta[parts[0].lower().replace('-', '_')] = parts[1].strip()

    # ── Lap timing ────────────────────────────────────────────────────────────
    laps_raw = []
    for line in sections.get('laptiming', []):
        parts = line.split()
        if len(parts) >= 4 and parts[0].isdigit():
            lap_n    = int(parts[0])
            start_ut = _hhmmss_to_s(parts[1])
            end_ut   = _hhmmss_to_s(parts[2])
            lt_s     = _hhmmss_to_s(parts[3])
            laps_raw.append((lap_n, start_ut, end_ut, lt_s))

    # ── Column names ──────────────────────────────────────────────────────────
    col_line = ' '.join(sections.get('column names', []))
    col_names = col_line.split()
    if not col_names:
        # Fall back to [header] section
        col_names = [l for l in sections.get('header', []) if l]

    # ── Data rows ─────────────────────────────────────────────────────────────
    data_rows = []
    for line in sections.get('data', []):
        if not line:
            continue
        parts = line.split()
        if len(parts) >= len(col_names):
            data_rows.append(parts[:len(col_names)])

    if not data_rows:
        raise ValueError("No data rows found in VBO file.")

    raw_df = pd.DataFrame(data_rows, columns=col_names)

    if progress_cb:
        progress_cb(30, 100, "Parsing coordinates…")

    # ── Convert columns ───────────────────────────────────────────────────────
    df = pd.DataFrame()

    # Time → UTC seconds → session-relative
    time_col = next((c for c in col_names if c.lower() == 'time'), None)
    if time_col:
        ut = raw_df[time_col].astype(float).apply(_hhmmss_to_s)
        t0 = ut.iloc[0]
        df['ts'] = (ut - t0).values
    else:
        df['ts'] = np.arange(len(raw_df), dtype=float)

    # Lap timing: also convert to session-relative
    t0_ut = _hhmmss_to_s(sections.get('data', [''])[0].split()[1]) if data_rows else 0.0
    # Use first data row time as session start
    if time_col:
        t0_ut = float(raw_df[time_col].iloc[0])
        t0_s  = _hhmmss_to_s(t0_ut)
    else:
        t0_s = 0.0

    laps = []
    for lap_n, start_ut, end_ut, lt_s in laps_raw:
        st = start_ut - t0_s
        et = end_ut   - t0_s
        laps.append((st, et, lt_s))
    meta['laps'] = laps

    # Latitude
    lat_col = next((c for c in col_names if 'lat' in c.lower()), None)
    if lat_col:
        df['lat'] = raw_df[lat_col].astype(float).apply(_dec_minutes_lat)

    # Longitude
    lon_col = next((c for c in col_names if 'lon' in c.lower() or 'long' in c.lower()), None)
    if lon_col:
        df['lon'] = raw_df[lon_col].astype(float).apply(_dec_minutes_lon)

    # Speed
    spd_col = next((c for c in col_names if 'veloc' in c.lower() or 'speed' in c.lower()), None)
    if spd_col:
        df['speed'] = pd.to_numeric(raw_df[spd_col], errors='coerce').fillna(0)

    # G forces
    long_col = next((c for c in col_names if 'longacc' in c.lower()), None)
    lat_g_col = next((c for c in col_names if 'latacc' in c.lower()), None)
    df['g_long'] = pd.to_numeric(raw_df[long_col], errors='coerce').fillna(0) if long_col else 0.0
    df['g_lat']  = pd.to_numeric(raw_df[lat_g_col], errors='coerce').fillna(0) if lat_g_col else 0.0

    # Heading, height, distance
    for dest, keywords in [('heading', ['heading']), ('height', ['height']), ('distance', ['distance'])]:
        col = next((c for c in col_names if any(k in c.lower() for k in keywords)), None)
        df[dest] = pd.to_numeric(raw_df[col], errors='coerce').fillna(0) if col else 0.0

    # Placeholders for channels not in VBO
    df['rpm']      = 0.0
    df['throttle'] = 0.0
    df['brake']    = 0.0
    df['gear']     = 0

    # Derived: infer throttle/brake from longitudinal G
    if 'g_long' in df.columns:
        df['throttle'] = np.clip(df['g_long'] * 100, 0, 100)
        df['brake']    = np.clip(-df['g_long'] * 100, 0, 100)

    df.reset_index(drop=True, inplace=True)

    if progress_cb:
        progress_cb(100, 100, "VBO loaded.")

    # Metadata extras
    meta['duration_s']  = float(df['ts'].max())
    meta['n_samples']   = len(df)
    meta['has_gps']     = True
    meta['source']      = 'vRacer VBO'

    return df, meta


def is_vbo_file(filepath):
    return os.path.splitext(filepath)[1].lower() == '.vbo'
