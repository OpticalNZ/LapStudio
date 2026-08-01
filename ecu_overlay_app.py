"""
ECU Overlay Video Generator
GUI application for generating chroma-key video overlays from ECU datalogs.
Supports Emtron, Link ECU, and any CSV/TSV structured log file.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading

def _aim_ext(path):
    return os.path.splitext(path)[1].lower() in {'.xdrk','.drk','.xrk','.rrk','.gpk'}
# AIM file reader (optional — needs DLL at runtime)
try:
    import aim_reader as _aim
    _AIM_OK = True
except ImportError:
    _AIM_OK = False
import os
import sys
import subprocess
import re

# ── Try importing data/render deps — show friendly error if missing ───────────
try:
    import pandas as pd
    import numpy as np
    import sys as _sys
    import os as _os
    # Add script directory to path so renderer_pil can be found
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import renderer_pil as _R
    DEPS_OK = True
except ImportError as e:
    DEPS_OK = False
    MISSING_DEP = str(e)

# renderer_multistyle — optional secondary renderer; absence must not disable core
try:
    import renderer_multistyle as _MS
    _MS_OK = True
except ImportError:
    _MS_OK = False
    _MS = None

# trackmap — optional standalone track-map overlay renderer
try:
    import trackmap_render as _TM
    _TM_OK = True
except ImportError:
    _TM_OK = False
    _TM = None

# gtrace — optional standalone G-force trace overlay renderer
try:
    import gtrace_render as _GT
    _GT_OK = True
except ImportError:
    _GT_OK = False
    _GT = None

# lapdata — optional standalone lap-data overlay renderer
try:
    import lapdata_render as _LD
    _LD_OK = True
except ImportError:
    _LD_OK = False
    _LD = None

# VBO reader — optional, loaded separately so missing file gives clear error
try:
    import vbo_reader as _VBO
    _VBO_OK = True
except ImportError:
    _VBO = None
    _VBO_OK = False

# ══════════════════════════════════════════════════════════════════════════════
# COLUMN DETECTION
# ══════════════════════════════════════════════════════════════════════════════

CHANNEL_KEYWORDS = {
    'rpm':      ['ecu_rpm', 'engine_rpm', 'engine speed', 'rpm', 'revs'],
    'speed':    ['gps speed', 'gps_speed', 'vehicle speed', 'ground speed',
                 'speed kph', 'speed mph', 'wheel speed', 'speed', 'kph', 'mph', 'velocity'],
    'gear':     ['ecu_gear', 'gear position', 'gear'],
    'throttle': ['ecu_throttle', 'throttle position', 'tps', 'throttle'],
    'brake':    ['front_brake', 'brake pressure', 'brake', 'brakepressure', 'brake press', 'hydraulic', 'brake psi', 'brake bar'],
    # Both underscore and space forms: a CSV export gives "GPS_LatAcc" while the
    # AIM DLL returns the same channel as "GPS LatAcc".
    'g_lat':    ['gps_latacc', 'gps latacc', 'latacc', 'lat acc', 'lat_acc',
                 'g-lat', 'glat', 'lateral g', 'lat g', 'lateral', 'g lat', 'accel lat'],
    'g_long':   ['gps_lonacc', 'gps lonacc', 'lonacc', 'lon acc', 'lon_acc',
                 'longacc', 'long acc', 'long_acc',
                 'g-long', 'glong', 'longitudinal g', 'long g', 'longitudinal',
                 'g long', 'accel long'],
    # GPS position. Deliberately strict: only true coordinate names, never the
    # acceleration channels (GPS_LatAcc / GPS_LonAcc), which some AIM logs carry
    # instead of coordinates.
    'lat':      ['gps_latitude', 'latitude', 'gps latitude', 'lat_deg', 'gps_lat_deg'],
    'lon':      ['gps_longitude', 'longitude', 'gps longitude', 'lon_deg', 'gps_lon_deg',
                 'lng', 'gps_lng'],
}

# Column names that must never be treated as GPS coordinates even if a keyword
# happens to appear inside them (accelerations, headings, accuracy figures, ...).
_GPS_POS_EXCLUDE = ('acc', 'accel', 'slope', 'heading', 'gyro', 'altitude',
                    'nsat', 'sat', 'speed', 'accuracy', 'quality', 'hdop',
                    'ecef', 'velocity')

# Vehicle speed must not be taken from an ECEF velocity component (those are
# earth-centred cartesian rates, e.g. "ECEF velocity_X", and swing +/- about 0).
_SPEED_EXCLUDE = ('ecef', 'accuracy', 'sat')

CHANNEL_LABELS = {
    'rpm':      'RPM',
    'speed':    'Speed',
    'gear':     'Gear',
    'throttle': 'Throttle',
    'brake':    'Brake',
    'g_lat':    'G Lateral',
    'g_long':   'G Longitudinal',
    'lat':      'GPS Latitude',
    'lon':      'GPS Longitude',
}

def detect_delimiter(filepath):
    """Detect CSV delimiter by sampling first few lines."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        sample = f.read(4096)
    counts = {',': sample.count(','), '\t': sample.count('\t'), ';': sample.count(';')}
    return max(counts, key=counts.get)

def find_header_row(filepath, delimiter):
    """Find the data header row, returning the pandas-compatible row index.
    
    Handles AIM CSVs with multi-line comment fields by reading the file
    properly as CSV and counting logical (not physical) rows.
    """
    import csv as _csv, io

    _meta_keys = {'format','venue','vehicle','user','comment','date',
                  'sample rate','duration','segment','beacon markers',
                  'segment times','data source'}
    _unit_words = {'sec','s','ms','km','m','km/h','m/s','mph','kph',
                   'v','mv','a','ma','rpm','g','bar','psi','c','%',
                   'deg','rad','hz','n','nm','w','kw','l','lt','laps',''}

    def _is_num(s):
        try: float(s.strip().strip('"')); return True
        except: return False

    def _is_data(parts):
        if len(parts) < 3 or not parts[0]: return False
        non_empty = [p for p in parts if p]
        if not non_empty: return False
        n_num = sum(1 for p in non_empty if _is_num(p))
        return _is_num(parts[0]) and n_num / len(non_empty) > 0.8

    def _is_units(parts):
        non_empty = [p.lower().strip() for p in parts if p.strip()]
        if not non_empty: return True
        n = sum(1 for p in non_empty if p in _unit_words or
                p.endswith('/h') or p.endswith('/s') or
                p.startswith('°') or (len(p) <= 5 and not p[0].isdigit()))
        return n / len(non_empty) > 0.65

    # Read file as CSV logical rows (handles multi-line quoted fields)
    with open(filepath, 'r', encoding='latin-1', errors='replace') as f:
        raw = f.read()
    reader = _csv.reader(io.StringIO(raw), delimiter=delimiter)
    rows = []
    try:
        for i, row in enumerate(reader):
            rows.append([p.strip().strip('"') for p in row])
            if i > 100: break
    except Exception:
        pass

    # Find first data row
    first_data_idx = None
    for i, parts in enumerate(rows):
        if _is_data(parts):
            if i+1 < len(rows) and _is_data(rows[i+1]):
                first_data_idx = i; break

    if first_data_idx is None:
        return 0

    # Walk backwards to find column name row
    for i in range(first_data_idx - 1, -1, -1):
        parts = rows[i]
        if not parts: continue
        if parts[0].lower() in _meta_keys: continue
        if _is_data(parts): continue
        if _is_units(parts): continue
        n_str = sum(1 for p in parts if p and not _is_num(p))
        if n_str > 2:
            # If previous row is identical (AIM duplicates header), use that
            if i > 0 and rows[i-1] == parts:
                return i - 1
            return i

    return max(0, first_data_idx - 1)


def parse_aim_metadata(filepath):
    """Parse AIM CSV metadata header. Returns dict with 'laps' list of (start,end,laptime)."""
    import csv as _csv, re as _re
    meta = {}
    try:
        with open(filepath, 'r', encoding='latin-1', errors='replace') as f:
            for _ in range(35):
                line = f.readline()
                if not line:
                    break
                try:
                    parts = next(_csv.reader([line.strip()]))
                except Exception:
                    continue
                parts = [p.strip().strip('"') for p in parts]
                if not parts:
                    continue
                key = parts[0].lower()
                if key == 'beacon markers':
                    rest = ','.join(parts[1:])
                    meta['beacons'] = [float(x.strip()) for x in rest.split(',')
                                       if x.strip()]
                elif key == 'segment times':
                    times = []
                    for p in parts[1:]:
                        p = p.strip()
                        if not p:
                            continue
                        m = _re.match(r'(\d+):(\d+\.\d+)', p)
                        if m:
                            times.append(int(m.group(1))*60 + float(m.group(2)))
                        else:
                            try:
                                times.append(float(p))
                            except ValueError:
                                pass
                    meta['seg_times'] = times
                elif key == 'duration':
                    try:
                        meta['duration'] = float(parts[1])
                    except Exception:
                        pass
                elif key == 'venue':
                    meta['venue'] = parts[1] if len(parts) > 1 else ''
                elif key == 'vehicle':
                    meta['vehicle'] = parts[1] if len(parts) > 1 else ''
    except Exception:
        pass

    # Build laps list: (start_s, end_s, laptime_s)
    beacons = meta.get('beacons', [])
    seg_times = meta.get('seg_times', [])
    if beacons and seg_times and len(beacons) == len(seg_times):
        laps = []
        start = 0.0
        for end, lt in zip(beacons, seg_times):
            laps.append((start, end, lt))
            start = end
        meta['laps'] = laps
    else:
        meta['laps'] = []
    return meta


def detect_laps_gps(ts, lat, lon, sf_lat, sf_lon, radius_m=30.0, min_gap_s=30.0):
    """Detect S/F crossings using signed-distance interpolation.

    Method:
    1. Convert to local ENU metres
    2. Estimate vehicle tangent from motion
    3. Signed distance d = P · tangent detects perpendicular crossing
    4. Interpolate exact crossing timestamp
    5. First valid crossing establishes the canonical heading — all subsequent
       crossings must be within 45° of this to reject false triggers from
       nearby track sections that pass close to the SF point.

    Returns list of (start_ts, end_ts, lap_time_s).
    """
    import numpy as _np

    cos_lat = _np.cos(_np.radians(sf_lat))
    east  = (lon - sf_lon) * 111320.0 * cos_lat
    north = (lat - sf_lat) * 111320.0
    dist  = _np.sqrt(east**2 + north**2)
    n = len(ts)

    TANGENT_WINDOW = 4
    MIN_SPEED_MS   = 15.0   # ~54kph minimum — eliminates slow false triggers
    MAX_CROSS_DIST = 50.0   # interpolated crossing must be within 50m of SF

    crossings = []
    canonical_tx = None   # fixed S/F plane normal — set once, never changed
    canonical_ty = None

    for i in range(1, n):
        # Pre-filter: only look near the SF point
        if dist[i-1] > 300 and dist[i] > 300:
            continue

        dt_seg = float(ts[i] - ts[i-1])
        if dt_seg <= 0:
            continue
        seg_len = _np.sqrt((east[i]-east[i-1])**2 + (north[i]-north[i-1])**2)
        speed_ms = seg_len / dt_seg
        if speed_ms < MIN_SPEED_MS:
            continue

        if canonical_tx is None:
            # ── Phase 1: finding first crossing ──────────────────────────────
            # Use instantaneous tangent to detect the first good crossing
            lo = max(0, i - TANGENT_WINDOW)
            hi = min(n - 1, i + TANGENT_WINDOW)
            de = east[hi] - east[lo]
            dn = north[hi] - north[lo]
            mag = _np.sqrt(de**2 + dn**2)
            if mag < 1e-6:
                continue
            tx = de / mag; ty = dn / mag

            d0 = east[i-1]*tx + north[i-1]*ty
            d1 = east[i]  *tx + north[i]  *ty
            if not ((d0 < 0 and d1 >= 0) or (d0 > 0 and d1 <= 0)):
                continue

            lam = max(0.0, min(1.0, -d0 / (d1 - d0 + 1e-12)))
            t_cross = float(ts[i-1]) + lam * dt_seg
            cross_e = east[i-1] + lam * (east[i] - east[i-1])
            cross_n = north[i-1] + lam * (north[i] - north[i-1])
            cross_dist = _np.sqrt(cross_e**2 + cross_n**2)

            if cross_dist > MAX_CROSS_DIST:
                continue

            # ── First crossing found: freeze the S/F plane ───────────────────
            # Use a wider window for best heading estimate at this moment
            lo2 = max(0, i - TANGENT_WINDOW * 3)
            hi2 = min(n - 1, i + TANGENT_WINDOW * 3)
            de2 = east[hi2] - east[lo2]
            dn2 = north[hi2] - north[lo2]
            mag2 = _np.sqrt(de2**2 + dn2**2)
            if mag2 > 1e-6:
                canonical_tx = de2 / mag2
                canonical_ty = dn2 / mag2
            else:
                canonical_tx = tx
                canonical_ty = ty

            crossings.append((t_cross, cross_dist))

        else:
            # ── Phase 2: use FIXED canonical plane for all subsequent laps ───
            use_tx = canonical_tx
            use_ty = canonical_ty

            # Speed check — same as Phase 1
            dt_seg2 = float(ts[i] - ts[i-1])
            if dt_seg2 <= 0: continue
            seg_len2 = _np.sqrt((east[i]-east[i-1])**2 + (north[i]-north[i-1])**2)
            if seg_len2 / dt_seg2 < MIN_SPEED_MS: continue

            d0 = east[i-1]*use_tx + north[i-1]*use_ty
            d1 = east[i]  *use_tx + north[i]  *use_ty
            if not ((d0 < 0 and d1 >= 0) or (d0 > 0 and d1 <= 0)):
                continue

            lam = max(0.0, min(1.0, -d0 / (d1 - d0 + 1e-12)))
            t_cross = float(ts[i-1]) + lam * dt_seg
            cross_e = east[i-1] + lam * (east[i] - east[i-1])
            cross_n = north[i-1] + lam * (north[i] - north[i-1])
            cross_dist = _np.sqrt(cross_e**2 + cross_n**2)

            if cross_dist > MAX_CROSS_DIST:
                continue

            crossings.append((t_cross, cross_dist))

    # Filter by minimum gap between crossings
    filtered = []
    for t_c, d_c in crossings:
        if not filtered or t_c - filtered[-1][0] >= min_gap_s:
            filtered.append((t_c, d_c))

    if not filtered:
        closest = float(_np.nanmin(dist)) if len(dist) > 0 else 9999
        raise ValueError(
            f"No S/F crossings found near {sf_lat:.6f}, {sf_lon:.6f}. "
            f"Closest GPS approach: {closest:.1f}m. "
            "Check coordinate or use Lap Beacons method.")

    laps = []
    for i in range(1, len(filtered)):
        st = filtered[i-1][0]; et = filtered[i][0]; lt = et - st
        laps.append((st, et, lt))

    return laps


def _nearest_approach_time(ts, east, north, gx, gy, min_speed_ms=3.0):
    """Return the timestamp of the closest approach to point (gx, gy) in local
    ENU metres, using linear interpolation between the two bracketing samples.
    Returns (t_cross, min_dist) or (None, dist) if never within range."""
    import numpy as _np
    d = _np.sqrt((east - gx) ** 2 + (north - gy) ** 2)
    i_min = int(_np.argmin(d))
    return float(ts[i_min]), float(d[i_min])


def detect_stage(ts, lat, lon, start_lat, start_lon, finish_lat, finish_lon,
                 radius_m=50.0):
    """Point-to-point (rally stage / hillclimb) timing.

    The car passes the START line once and the FINISH line once. We find the
    closest approach to each and time the run between them. Unlike lap timing,
    start != finish, so there is exactly one timed 'lap' (the stage run).

    Returns [(start_ts, finish_ts, stage_time_s)].
    """
    import numpy as _np
    ts = _np.asarray(ts, float); lat = _np.asarray(lat, float); lon = _np.asarray(lon, float)
    # ENU relative to the start point for consistent metres
    cos0 = _np.cos(_np.radians(start_lat))
    e = (lon - start_lon) * 111320.0 * cos0
    n = (lat - start_lat) * 111320.0
    # start point is origin (0,0); finish point in same frame
    fe = (finish_lon - start_lon) * 111320.0 * cos0
    fn = (finish_lat - start_lat) * 111320.0

    t_start, d_start = _nearest_approach_time(ts, e, n, 0.0, 0.0)
    t_fin, d_fin = _nearest_approach_time(ts, e, n, fe, fn)

    if d_start > radius_m:
        raise ValueError(
            f"Start line not reached (closest approach {d_start:.1f}m > "
            f"{radius_m:.0f}m). Check the start coordinate.")
    if d_fin > radius_m:
        raise ValueError(
            f"Finish line not reached (closest approach {d_fin:.1f}m > "
            f"{radius_m:.0f}m). Check the finish coordinate.")
    if t_fin <= t_start:
        raise ValueError(
            "Finish crossing is not after the start crossing — check that the "
            "start and finish coordinates aren't swapped.")

    return [(t_start, t_fin, t_fin - t_start)]


def load_file(filepath):
    """Load log file, return (dataframe, timestamp_col, all_columns)."""
    delim = detect_delimiter(filepath)
    header_row = find_header_row(filepath, delim)

    # Try progressively more lenient parsing strategies
    df = None
    errors = []
    for kwargs in [
        # Strategy 1: strict, modern pandas
        dict(sep=delim, header=header_row, skip_blank_lines=True,
             encoding='utf-8', encoding_errors='replace',
             on_bad_lines='skip', low_memory=False),
        # Strategy 2: python engine, more tolerant of ragged rows
        dict(sep=delim, header=header_row, skip_blank_lines=True,
             encoding='utf-8', encoding_errors='replace',
             on_bad_lines='skip', engine='python', low_memory=False),
        # Strategy 3: legacy pandas (<1.3) keyword
        dict(sep=delim, header=header_row, skip_blank_lines=True,
             encoding='utf-8', error_bad_lines=False, warn_bad_lines=False,
             low_memory=False),
        # Strategy 4: python engine, skip bad, infer everything
        dict(sep=delim, header=header_row, skip_blank_lines=True,
             encoding='latin-1', on_bad_lines='skip', engine='python',
             low_memory=False),
    ]:
        try:
            df = pd.read_csv(filepath, **kwargs)
            break
        except Exception as e:
            errors.append(str(e))
            df = None

    if df is None:
        raise ValueError(
            f"Could not parse CSV file. Tried {len(errors)} strategies.\n"
            + "\n".join(errors[:2]))

    df = df.dropna(how='all')

    # Clean column names
    df.columns = [str(c).strip().strip('"') for c in df.columns]

    # Drop non-data rows (units rows, header duplicates etc.)
    ts_candidate = df.columns[0]
    _ts_num = pd.to_numeric(df[ts_candidate], errors='coerce')
    df = df[_ts_num.notna()].copy()
    df[ts_candidate] = _ts_num[_ts_num.notna()].values
    df.reset_index(drop=True, inplace=True)

    # AIM per-lap format: timestamps reset to 0 at each lap boundary
    # Detect resets and reconstruct monotonic absolute time
    _ts_raw = df[ts_candidate].values.copy().astype(np.float64)
    _ts_out = _ts_raw.copy()
    _offset = np.float64(0.0)
    for _i in range(1, len(_ts_raw)):
        if _ts_raw[_i] < _ts_raw[_i-1] - 0.5:   # reset: new lap starts
            _offset += _ts_raw[_i-1]             # add raw prev (not output)
        _ts_out[_i] = _ts_raw[_i] + _offset
    df[ts_candidate] = _ts_out

    # Drop rows where all values are NaN
    df = df.reset_index(drop=True)

    # Timestamp is always column 0
    ts_col = df.columns[0]

    # Convert timestamp column to numeric elapsed seconds
    ts_raw = df[ts_col]
    ts_sample = ts_raw.dropna().head(10)
    try:
        ts_numeric = pd.to_numeric(ts_sample, errors='raise')
        # Check if milliseconds (values > 1000 for first sample suggests ms)
        if ts_numeric.iloc[0] > 1000 and ts_numeric.iloc[-1] > 1000:
            df[ts_col] = pd.to_numeric(df[ts_col], errors='coerce') / 1000.0
        else:
            df[ts_col] = pd.to_numeric(df[ts_col], errors='coerce')
    except Exception:
        # Try parsing as time string HH:MM:SS.ms
        try:
            def parse_time(s):
                parts = str(s).split(':')
                if len(parts) == 3:
                    return float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                return float(s)
            df[ts_col] = df[ts_col].apply(parse_time)
        except Exception:
            df[ts_col] = pd.to_numeric(df[ts_col], errors='coerce')

    df = df.dropna(subset=[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)
    return df, ts_col, list(df.columns)

#: Column names that ARE a channel outright (already-normalised data)
_EXACT_NAMES = {
    'rpm': ('rpm',), 'speed': ('speed',), 'gear': ('gear',),
    'throttle': ('throttle', 'tps'), 'brake': ('brake',),
    'g_lat': ('g_lat', 'glat', 'lat_g'), 'g_long': ('g_long', 'glong', 'lon_g', 'long_g'),
    'lat': ('lat', 'latitude'), 'lon': ('lon', 'lng', 'longitude'),
}


def auto_detect_channels(columns):
    """Return best-guess column mapping for each channel."""
    mapping = {}
    col_lower = {c: c.lower() for c in columns}
    # Exact names win outright — avoids a substring match stealing them
    _exact = {}
    for ch, names in _EXACT_NAMES.items():
        for col, col_l in col_lower.items():
            if col_l.strip() in names:
                _exact[ch] = col
                break
    for channel, keywords in CHANNEL_KEYWORDS.items():
        if channel in _exact:
            mapping[channel] = _exact[channel]
            continue
        best = None
        best_score = 0
        for col, col_l in col_lower.items():
            # GPS position must not pick up acceleration / heading / accuracy
            # channels (e.g. GPS_LatAcc), which are not coordinates.
            if channel in ('lat', 'lon') and any(x in col_l for x in _GPS_POS_EXCLUDE):
                continue
            # Vehicle speed must not come from an ECEF velocity component
            if channel == 'speed' and any(x in col_l for x in _SPEED_EXCLUDE):
                continue
            for kw in keywords:
                if kw in col_l:
                    score = len(kw)  # longer match = more specific
                    if score > best_score:
                        best_score = score
                        best = col
        mapping[channel] = best
    return mapping


def normalise_coordinates(values, kind='lat'):
    """Convert a coordinate series to decimal degrees, whatever form it is in.

    Loggers report position in several ways: decimal degrees, radians, decimal
    minutes (Racelogic VBO), or scaled integers (degrees x 1e7, as many GPS
    chipsets do). Returns (degrees_array, description) or (None, reason).
    """
    import numpy as _np
    v = _np.asarray(values, dtype=float)
    fin = v[_np.isfinite(v)]
    if fin.size < 2:
        return None, "no finite samples"
    amax = float(_np.nanmax(_np.abs(fin)))
    span = float(_np.nanmax(fin) - _np.nanmin(fin))
    if span <= 0:
        return None, f"constant value ({fin[0]:.6g})"
    limit = 90.0 if kind == 'lat' else 180.0

    # Work out the scale from the magnitude
    if amax <= limit * 1.001:
        # Already degrees — but radians would also land here for latitude, so
        # only treat as radians when it cannot be degrees for BOTH axes.
        return v, "decimal degrees"
    if amax <= limit * 60.0 * 1.001:
        return v / 60.0, "decimal minutes -> degrees"
    if amax <= limit * 1e5:
        return v / 1e4, "degrees x 1e4 -> degrees"
    if amax <= limit * 1e6:
        return v / 1e5, "degrees x 1e5 -> degrees"
    if amax <= limit * 1e7:
        return v / 1e6, "degrees x 1e6 -> degrees"
    if amax <= limit * 1e8:
        return v / 1e7, "degrees x 1e7 -> degrees"
    return None, f"values out of range (max |{amax:.6g}|)"


def looks_like_coordinates(values, kind='lat'):
    """True if a series can be interpreted as GPS coordinates."""
    deg, _why = normalise_coordinates(values, kind)
    if deg is None:
        return False
    import numpy as _np
    d = _np.asarray(deg, dtype=float)
    d = d[_np.isfinite(d)]
    if d.size < 2:
        return False
    # Reject accelerations: they swing about zero within a couple of units
    if float(_np.nanmax(_np.abs(d))) < 0.001:
        return False
    return float(_np.nanmax(d) - _np.nanmin(d)) > 1e-9

# ══════════════════════════════════════════════════════════════════════════════
# RENDERER  (same logic as render_ecu_v3.py, callable from GUI)
# ══════════════════════════════════════════════════════════════════════════════

CHROMA = "#ff00ff"
AMBER  = "#ffaa00"
RED    = "#ff2020"
WHITE  = "#ffffff"
GREY   = "#888899"
BORDER = "#555566"
CYAN   = "#40d0ff"
ORANGE = "#ff7700"
GREEN  = "#39d353"
W_VID, H_VID = 1280, 500
DPI = 100
FPS = 30

# ══════════════════════════════════════════════════════════════════════════════
# GUI
# ══════════════════════════════════════════════════════════════════════════════

# ── COLOUR SCHEMES ──────────────────────────────────────────────────────────
# Two themes, both built on cobalt blue. Chosen with measured contrast rather
# than by eye: the previous single theme had borders at 1.4:1 (invisible), muted
# text at 5.1:1, and the same colour for the window and for input fields, so
# nothing separated from anything else.
#
# Note on cobalt: true cobalt (#0047ab) manages only 2.1:1 on a near-black
# background, so on the dark theme it is used as a FILL behind white text and a
# lighter cobalt carries accent text. On the light theme #0047ab is ideal as text
# at 8.4:1.
THEMES = {
    "dark": {
        "BG":       "#05060a",   # window
        "CARD":     "#1a1e29",   # cards / panels          1.22:1 vs window
        "PANEL":    "#0c0e15",   # input fields / sunken    1.16:1 vs card
        "ACCENT":   "#8ab6ff",   # accent text, headings    8.8:1  on card
        "ACCENT2":  "#9bb8e8",   # secondary accent, stats  9.0:1
        "FILL":     "#1f6feb",   # cobalt fill (buttons, active tab)
        "ON_FILL":  "#ffffff",   # text on the cobalt fill  4.6:1
        "TEXT_PRI": "#f7f9ff",   #                          15.9:1
        "TEXT_SEC": "#aab4c6",   #                          8.3:1
        "BORDER":   "#42506b",   # visible dividers         2.05:1
        "GREEN":    "#3ddc84",
        "RED":      "#ff5470",
    },
    "light": {
        "BG":       "#e2e7f0",
        "CARD":     "#ffffff",
        "PANEL":    "#eff3f9",
        "ACCENT":   "#003d92",   #                          10.2:1 on white
        "ACCENT2":  "#14539b",   #                          7.7:1
        "FILL":     "#0047ab",
        "ON_FILL":  "#f7faff",   #                          8.1:1 on the fill
        "TEXT_PRI": "#0b1220",   #                          18.7:1
        "TEXT_SEC": "#48536b",   #                          7.7:1
        "BORDER":   "#bcc7db",
        "GREEN":    "#0f7a3d",
        "RED":      "#c62828",
    },
}

THEME_NAME = "dark"

# Module-level names the whole UI is written against. _apply_theme() rebinds them.
DARK_BG = DARK_PANEL = DARK_CARD = ACCENT = ACCENT2 = TEXT_PRI = TEXT_SEC = ""
BORDER_COL = GREEN_UI = RED_UI = FILL_COL = ON_FILL = ""


def _theme_colours(name):
    return THEMES.get(name, THEMES["dark"])


def _bind_theme(name):
    """Point the module colour names at one of the THEMES."""
    global THEME_NAME, DARK_BG, DARK_PANEL, DARK_CARD, ACCENT, ACCENT2
    global TEXT_PRI, TEXT_SEC, BORDER_COL, GREEN_UI, RED_UI, FILL_COL, ON_FILL
    T = _theme_colours(name)
    THEME_NAME = name if name in THEMES else "dark"
    DARK_BG    = T["BG"]
    DARK_PANEL = T["PANEL"]
    DARK_CARD  = T["CARD"]
    ACCENT     = T["ACCENT"]
    ACCENT2    = T["ACCENT2"]
    TEXT_PRI   = T["TEXT_PRI"]
    TEXT_SEC   = T["TEXT_SEC"]
    BORDER_COL = T["BORDER"]
    GREEN_UI   = T["GREEN"]
    RED_UI     = T["RED"]
    FILL_COL   = T["FILL"]
    ON_FILL    = T["ON_FILL"]


_PREFS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ui_prefs.json")


def _load_pref(key, default=None):
    try:
        import json
        with open(_PREFS_PATH, encoding="utf-8") as fh:
            return json.load(fh).get(key, default)
    except Exception:
        return default


def _save_pref(key, value):
    try:
        import json
        data = {}
        if os.path.exists(_PREFS_PATH):
            with open(_PREFS_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        data[key] = value
        with open(_PREFS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


# The live theme switch remaps colours BY VALUE, so two roles in one palette
# must never share a value - otherwise the swap becomes ambiguous and, in the
# case that caught this, white cards stayed white when returning to the dark
# theme. Fail loudly at import rather than mysteriously at runtime.
for _tn, _tv in THEMES.items():
    if len(set(v.lower() for v in _tv.values())) != len(_tv):
        from collections import Counter as _C
        _dupes = [c for c, n in _C(v.lower() for v in _tv.values()).items() if n > 1]
        raise AssertionError(
            f"theme '{_tn}' reuses {_dupes} for more than one role; give each "
            f"role its own value so the live theme switch stays unambiguous")

_bind_theme(_load_pref("theme", "dark"))


# 1..4 then every 5 up to 120. Built once: two separate ranges were being
# concatenated at each dropdown and both included 5, so "5" appeared twice.
_TRAIL_SECS = [str(i) for i in range(1, 5)] + [str(i) for i in range(5, 125, 5)]

_UI_PREFS = ["Segoe UI Semibold", "Segoe UI", "Calibri", "Helvetica", "Arial"]
_MONO_PREFS = ["Consolas", "DejaVu Sans Mono", "Courier New", "Liberation Mono",
               "Menlo", "Monaco", "Courier"]


def _pick_font_family(preferred, fallback_named=None):
    """First installed family from `preferred`.

    tkinter.font.families() needs a Tk root, so at import time this always fails
    and falls through. `fallback_named` names a built-in Tk font (TkFixedFont)
    whose actual family is used instead - important for the monospace font,
    because "TkFixedFont" is not itself a family name and Tk silently substitutes
    a PROPORTIONAL default, which is what made the lap table columns drift.
    """
    try:
        import tkinter.font as _tkfont
        avail = set(_tkfont.families())
        for fam in preferred:
            if fam in avail:
                return fam
        if fallback_named:
            return _tkfont.nametofont(fallback_named).actual("family")
    except Exception:
        pass
    return preferred[0]


def _resolve_fonts():
    """Re-pick the font families now that a Tk root exists, and rebuild the
    FONT_* tuples. Called at the very start of _build_ui, before any widget."""
    global _UI_FAMILY, _MONO_FAMILY
    global FONT_TITLE, FONT_HEAD, FONT_BODY, FONT_SMALL, FONT_MONO
    _UI_FAMILY = _pick_font_family(_UI_PREFS, "TkDefaultFont")
    _MONO_FAMILY = _pick_font_family(_MONO_PREFS, "TkFixedFont")
    FONT_TITLE = (_UI_FAMILY, 24, "bold")
    FONT_HEAD = (_UI_FAMILY, 12, "bold")
    FONT_BODY = (_UI_FAMILY, 11, "bold")
    FONT_SMALL = (_UI_FAMILY, 10, "bold")
    FONT_MONO = (_MONO_FAMILY, 11, "bold")

# UI font (clean, readable) and a monospace for data/values
# Provisional: _resolve_fonts() replaces these once the root window exists.
_UI_FAMILY = _UI_PREFS[0]
_MONO_FAMILY = _MONO_PREFS[0]

FONT_TITLE  = (_UI_FAMILY, 24, "bold")
FONT_HEAD   = (_UI_FAMILY, 12, "bold")
FONT_BODY   = (_UI_FAMILY, 11, "bold")
FONT_SMALL  = (_UI_FAMILY, 10, "bold")
FONT_MONO   = (_MONO_FAMILY, 11, "bold")

class App(tk.Tk):
    # ── Help panel content ───────────────────────────────────────────────────
    # The explanations that used to sit inline under each control live here, so
    # the settings column stays short and the lateral space does the work.
    HELP = {
        "file": (
            "FILE SELECTION\n\n"
            "Load a CSV or VBO datalog, then choose where the rendered video is "
            "written.\n\n"
            "AIM DLL: MatLabXRK-2022-64-ReleaseU.dll, only needed for XRK / DRK / "
            "XRZ files."),
        "channels": (
            "CHANNEL MAPPING\n\n"
            "Match each dash channel to a column in your log. Green dot = found, "
            "red = not mapped. Min/Max/Avg helps confirm you picked the right "
            "column, and units are as logged.\n\n"
            "Invert flips a channel's sign (useful when G or throttle is logged "
            "the opposite way round). Smooth applies a light low-pass filter.\n\n"
            "Channels A & B: two extra channels to display on the dash. Give each "
            "a short label (4 characters) and an optional 1-character unit, e.g. "
            "OIL / b. Any numeric column in the log can be selected."),
        "lap": (
            "LAP DETECTION\n\n"
            "Lap Beacons: reads lap markers embedded in the data file (AIM CSV, "
            "VBO, etc.).\n\n"
            "GPS Crossing: detects the start/finish line crossing from GPS. "
            "Auto-filled from your lap data; to set it yourself, right-click the "
            "point in Google Maps, click the coordinates at the top of the menu "
            "to copy them, then paste (format: lat, lon e.g. -40.236, 175.558).\n\n"
            "Speed Peaks: finds the fastest straight-line sections.\n\n"
            "Point-to-Point (Stage): for rally stages and hillclimbs where the "
            "start and finish are different places. Paste each coordinate the "
            "same way; Lat and Lon fill in automatically.\n\n"
            "Timer offset (Speed Peaks only) shifts the timer start/end by "
            "plus/minus N seconds."),
        "output": (
            "OUTPUT\n\n"
            "Start and End set the time range that gets rendered. 'Set start to "
            "first lap' jumps to the first flying lap and ends at the last lap.\n\n"
            "Output FPS is the frame rate of the rendered video. Matching your "
            "footage avoids resampling.\n\n"
            "The Start/End range also drives the separate overlay widgets."),
        "dash": (
            "DASH STYLE\n\n"
            "Visual Style picks the dashboard layout. Output resolution is set "
            "automatically to suit the style.\n\n"
            "Dash trail: how many seconds of history the dashboard's built-in "
            "G-trace / track trail shows, and whether it is coloured by speed.\n\n"
            "Backdrop: the background baked behind the dashboard. Chroma magenta "
            "or green key out in your editor; Dark bakes a solid background "
            "instead. This applies to the dashboard only - the widgets below have "
            "their own."),
        "gplot": (
            "G-PLOT STYLE\n\n"
            "Renders a SEPARATE video file of the G-force trace, so you can place "
            "it anywhere over your footage.\n\n"
            "Form: Target is the circular crosshair; X-Y Graph is the rectangular "
            "grid plot (drawn as an opaque panel, so it needs no chroma keying).\n\n"
            "Trail time: seconds of G history drawn behind the current point.\n\n"
            "Colour: speed colour, black & white, or by one of your mapped extra "
            "channels.\n\n"
            "Max range (G): the outer ring / plot limit. Auto derives it from the "
            "log; a fixed value keeps the scale constant between runs.\n\n"
            "Both forms show the combined-G reading, calculated as the horizontal "
            "resultant: sqrt(lat squared + long squared)."),
        "trackmap": (
            "TRACK MAP STYLE\n\n"
            "Renders a SEPARATE video file of the track outline from GPS with a "
            "moving position dot. Needs GPS lat/lon in the log.\n\n"
            "Speed colour: colours the track by speed. With it off the line is "
            "mono white.\n\n"
            "Map amount: All draws the whole track for the selected time range. "
            "An 'Ns window' draws a moving window centred on the car - half ahead, "
            "half behind - re-scaled to fill the frame so the shape evolves as the "
            "car progresses.\n\n"
            "The map covers the Start/End range set in the Output panel."),
        "lapdata": (
            "LAP DATA STYLE\n\n"
            "Renders a SEPARATE video file of the lap information. Lap times are "
            "shown as M:SS.ss.\n\n"
            "Content: 'Current lap' shows the lap number and its time (running "
            "while the lap is in progress). 'All laps (list)' shows every lap in "
            "the session as a vertical list, highlighting the current lap, with "
            "the best lap in gold. If there are more laps than fit, the list "
            "windows around the current lap.\n\n"
            "Graphic style: each option is taken from an existing dash so the "
            "widget matches the dash you are using."),
        "generate": (
            "GENERATE\n\n"
            "Generate Video renders the main dashboard plus any widgets you have "
            "ticked. Preview Frame renders a single frame at the start time, with "
            "the ticked widgets shown alongside so you can check everything at "
            "once.\n\n"
            "Widgets are written as separate files next to the main video, named "
            "after it (_gtrace, _trackmap, _lapdata)."),
    }

    # Layout constants: every card shares these so labels and controls line up
    # on a common vertical axis across the whole window.
    LABEL_COL_W = 165      # width of the label column (col 0)
    SPACER_COL  = 5        # absorbs slack so controls stay left-justified
    HELP_COL    = 6        # per-panel help text sits here, right of the controls
    HELP_W      = 300
    PREVIEW_MIN_W = 360    # each overlay tab keeps at least this much for its preview

    def _register_help(self, card, key):
        """Place this section's help text inside the panel, to the right of the
        controls, so the settings stay narrow and left-justified."""
        txt = self.HELP.get(key, "")
        if not txt:
            return
        tk.Label(card, text=txt, font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC,
                 justify="left", anchor="nw", wraplength=self.HELP_W - 16).grid(
            row=0, column=self.HELP_COL, rowspan=40, sticky="nw", padx=(20, 0))

    def _wire_help(self):
        """No-op: help now lives inside each panel (see _register_help)."""
        return

    def __init__(self):
        super().__init__()
        self.title("LapStudio")
        self.configure(bg=DARK_BG)
        self.resizable(True, True)
        # Sized for a normal laptop display: fill it, but never propose a window
        # bigger than the screen actually is. Panels are tabs now, so the height
        # is set by the tallest single tab rather than by all of them stacked.
        self.minsize(1100, 620)
        try:
            _sw, _sh = self.winfo_screenwidth(), self.winfo_screenheight()
        except Exception:
            _sw, _sh = 1440, 900
        _w = max(1100, min(1500, _sw - 80))
        _h = max(620, min(920, _sh - 120))
        self.geometry(f"{_w}x{_h}+{max(0, (_sw - _w) // 2)}+{max(0, (_sh - _h) // 3)}")

        # State
        self.df          = None
        self.ts_col      = None
        self.ts_col_var  = tk.StringVar(value='(auto)')
        self.all_cols    = []
        self.col_vars    = {}   # channel -> StringVar
        self.smooth_vars = {}   # channel -> BooleanVar for per-channel smoothing
        self.file_path   = tk.StringVar()
        self.output_dir  = tk.StringVar()
        self.output_name = tk.StringVar(value="ecu_overlay.mp4")
        # The output is named after the loaded log. This tracks the name the app
        # derived, so a name typed by hand is never overwritten by a later load.
        self._auto_output_name = "ecu_overlay.mp4"
        self.t_start_var = tk.StringVar(value="0")
        self.t_end_var   = tk.StringVar(value="0")
        self.max_t_label = tk.StringVar(value="Max: —")
        self.fps_var      = tk.StringVar(value="25")
        self.invert_glat   = tk.BooleanVar(value=False)
        self.invert_glong  = tk.BooleanVar(value=False)
        self.invert_throttle = tk.BooleanVar(value=False)
        # ── Optional extra channels A & B ──────────────────────────────────
        # Each: a source-column selection + a 4-letter display label.
        self.chanA_col   = tk.StringVar(value="(None)")
        self.chanA_label = tk.StringVar(value="")
        self.chanA_unit  = tk.StringVar(value="")
        self.chanB_col   = tk.StringVar(value="(None)")
        self.chanB_label = tk.StringVar(value="")
        self.chanB_unit  = tk.StringVar(value="")
        self._render_start_time = None
        self._preview_window    = None
        self.filter_rpm    = tk.BooleanVar(value=False)
        self.filter_speed  = tk.BooleanVar(value=False)
        self._default_smooth = {"speed": True}  # applied when mapping is built
        self.resolution_var  = tk.StringVar(value="1080p (1920×750)")
        self.g_trail_secs    = tk.StringVar(value="40")
        self.g_trail_speed_colour = tk.BooleanVar(value=True)
        # Optional separate track-map overlay video
        self.gen_trackmap = tk.BooleanVar(value=False)
        # Optional separate G-force trace overlay video
        self.gen_gtrace = tk.BooleanVar(value=False)
        # G-trace widget options
        self.gtrace_form = tk.StringVar(value="Target")
        self.gtrace_trail = tk.StringVar(value="40")   # trail time (s); matches dash trail
        self.gtrace_colour = tk.StringVar(value="Speed colour")
        self.gtrace_gmax   = tk.StringVar(value="Auto")   # plot range cap (G)
        # Per-panel backdrop (chroma-key / background colour). Each of the dash,
        # the G-trace and the track map has its own; all default to magenta.
        self.backdrop_var = tk.StringVar(value="Chroma magenta")   # dashboard
        self.gt_backdrop  = tk.StringVar(value="Chroma magenta")   # G-trace widget
        self.tm_backdrop  = tk.StringVar(value="Chroma magenta")   # track-map widget
        # Track-map widget options (own settings, independent of the dash)
        self.tm_speed_colour = tk.BooleanVar(value=True)
        self.tm_amount   = tk.StringVar(value="All")   # All, or a 10..150s window
        # Optional separate lap-data overlay video
        self.gen_lapdata = tk.BooleanVar(value=False)
        self.ld_mode     = tk.StringVar(value="Current lap")
        self.ld_style    = tk.StringVar(value="Dash 5 · Panels")
        self.ld_backdrop = tk.StringVar(value="Chroma magenta")
        # Optional separate steering-wheel overlay video — removed (renderer
        # dropped). Placeholder vars kept absent; no wheel controls remain.
        self.lap_offset_var  = tk.StringVar(value="0")
        self.lap_min_speed   = tk.StringVar(value="200")
        self.lap_est_time    = tk.StringVar(value="60")
        self.lap_mode_var    = tk.StringVar(value="Lap Beacons")
        self.lap_sf_lat      = tk.StringVar(value="")
        self.lap_sf_lon      = tk.StringVar(value="")
        # Point-to-point (rally stage / hillclimb): separate start & finish lines
        self.stage_start_lat = tk.StringVar(value="")
        self.stage_start_lon = tk.StringVar(value="")
        self.stage_fin_lat   = tk.StringVar(value="")
        self.stage_fin_lon   = tk.StringVar(value="")
        self.stage_radius    = tk.StringVar(value="50")
        self.lap_sf_radius   = tk.StringVar(value="30")
        self.lap_lat_col     = tk.StringVar(value="")
        self.lap_lon_col     = tk.StringVar(value="")
        self.style_var       = tk.StringVar(value=_R.DASH_NAMES[0])
        # Trim the output to the dash panel where the panel does not fill the
        # frame, so the result needs no chroma keying at all.
        self.crop_to_content = tk.BooleanVar(value=True)
        self.style2_var      = tk.StringVar(value="None")
        self._file_info_var  = tk.StringVar(value="")
        self.lap_text        = tk.StringVar(value="Load a file to detect laps.")
        self.progress    = tk.IntVar(value=0)
        self.status_var  = tk.StringVar(value="Load a log file to begin.")
        self._cancel_flag = False
        self._dot_labels  = {}   # channel -> Label widget
        self._all_inputs  = []   # widgets to disable during render
        # Live per-tab preview state
        self._pv_time_lbl = tk.StringVar(value="--:--")
        self._pv_var      = tk.DoubleVar(value=0.0)   # shared 0..1 scrub position
        self._pv_scales   = []
        self._pv_panes    = {}
        self._pv_holders  = {}
        self._pv_kind     = None
        self._pv_job      = None
        self._pv_last_box = None

        self._build_ui()

        if not DEPS_OK:
            messagebox.showerror("Missing Dependencies",
                f"Required library not found: {MISSING_DEP}\n\n"
                "Run:  pip install pandas numpy pillow aggdraw\n"
                "then restart this program.")

    def _style_ttk(self, _sty=None):
        """Apply the current palette to the ttk widgets (combobox, notebook,
        scrollbar, progress bar, scale). Re-run when the theme changes."""
        _sty = _sty or ttk.Style()
        try:
            _sty.theme_use('default')
        except Exception:
            pass
        _sty.configure("TCombobox",
            fieldbackground=DARK_PANEL, background=DARK_PANEL,
            foreground=TEXT_PRI, selectbackground=DARK_PANEL,
            selectforeground=TEXT_PRI, arrowcolor=ACCENT,
            insertcolor=TEXT_PRI, bordercolor=BORDER_COL,
            lightcolor=BORDER_COL, darkcolor=BORDER_COL)
        _sty.map("TCombobox",
            fieldbackground=[('readonly', DARK_PANEL), ('!readonly', DARK_PANEL)],
            foreground=[('readonly', TEXT_PRI), ('!readonly', TEXT_PRI)],
            selectbackground=[('readonly', DARK_PANEL)],
            selectforeground=[('readonly', TEXT_PRI)],
            bordercolor=[('focus', FILL_COL)])
        self.option_add("*TCombobox*Listbox.background", DARK_PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT_PRI)
        self.option_add("*TCombobox*Listbox.selectBackground", FILL_COL)
        self.option_add("*TCombobox*Listbox.selectForeground", ON_FILL)
        _sty.configure("TNotebook", background=DARK_BG, borderwidth=0,
                       tabmargins=(6, 4, 6, 0))
        _sty.configure("TNotebook.Tab", background=DARK_CARD, foreground=TEXT_SEC,
                       padding=(18, 8), font=FONT_HEAD, borderwidth=0)
        _sty.map("TNotebook.Tab",
                 background=[("selected", FILL_COL)],
                 foreground=[("selected", ON_FILL)],
                 expand=[("selected", (0, 0, 0, 0))])
        _sty.configure("TScrollbar", background=DARK_CARD, troughcolor=DARK_BG,
                       bordercolor=DARK_BG, arrowcolor=TEXT_SEC)
        _sty.configure("ECU.Horizontal.TProgressbar",
                       troughcolor=DARK_PANEL, background=FILL_COL,
                       bordercolor=BORDER_COL, lightcolor=FILL_COL,
                       darkcolor=FILL_COL)
        _sty.configure("TScale", background=DARK_CARD, troughcolor=DARK_PANEL)
        return _sty

    def _apply_theme(self, name, save=True):
        """Switch theme live: rebind the palette, restyle ttk, then walk the
        widget tree swapping every old palette colour for its new counterpart.

        A tree walk (rather than rebuilding the UI) keeps all state - loaded log,
        channel mapping, scrub position - exactly where it was.
        """
        old = dict(_theme_colours(THEME_NAME))
        _bind_theme(name)
        new = dict(_theme_colours(THEME_NAME))
        remap = {old[k].lower(): new[k] for k in old if k in new}
        self.configure(bg=DARK_BG)
        self._style_ttk()

        _OPTS = ("background", "bg", "foreground", "fg", "activebackground",
                 "activeforeground", "disabledforeground", "highlightbackground",
                 "highlightcolor", "selectcolor", "insertbackground",
                 "troughcolor", "selectbackground", "selectforeground",
                 "readonlybackground")

        def _walk(w):
            for opt in _OPTS:
                try:
                    cur = str(w.cget(opt))
                except Exception:
                    continue
                if not cur:
                    continue
                tgt = remap.get(cur.lower())
                if tgt and tgt != cur:
                    try:
                        w.configure(**{opt: tgt})
                    except Exception:
                        pass
            for ch in w.winfo_children():
                _walk(ch)

        _walk(self)
        if hasattr(self, "_theme_btn"):
            self._theme_btn.config(
                text="☀ Light theme" if THEME_NAME == "dark" else "☾ Dark theme")
        if save:
            _save_pref("theme", THEME_NAME)
        # The preview backdrops are unrelated to the UI theme, but the panes show
        # placeholder text in theme colours, so refresh whatever is on screen.
        self._schedule_preview(80)

    def _toggle_theme(self):
        self._apply_theme("light" if THEME_NAME == "dark" else "dark")

    def _build_ui(self):
        # Font families can only be enumerated once a root exists, which it now
        # does - do it before any widget is built so everything picks them up.
        _resolve_fonts()
        # ── Apply ttk styles FIRST before any widgets are created ─────────
        _sty = self._style_ttk()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Window chrome: title, tab row, global bar ──────────────────────
        # Panels used to be stacked in one long scrolling column, which made the
        # window very tall once the per-panel help text was added. Each panel is
        # now a tab, and the settings that apply to the whole job (time range,
        # fps, output, render) live in a bar that stays visible on every tab.
        self.rowconfigure(0, weight=0)     # title
        self.rowconfigure(1, weight=1)     # notebook
        self.rowconfigure(2, weight=0)     # global bar

        title_f = tk.Frame(self, bg=DARK_BG, padx=18, pady=(12))
        title_f.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(title_f, text="◈ LAP STUDIO", font=FONT_TITLE,
                 bg=DARK_BG, fg=ACCENT).pack(side="left")
        tk.Label(title_f, text=" TELEMETRY OVERLAY", font=FONT_TITLE,
                 bg=DARK_BG, fg=TEXT_PRI).pack(side="left")

        # Loading indicator — animates while a file (especially an AIM .drk,
        # which goes out to the AIM DLL) is being read.
        self._loading_var = tk.StringVar(value="")
        self._loading_lbl = tk.Label(title_f, textvariable=self._loading_var,
                                     font=FONT_HEAD, bg=DARK_BG, fg=ACCENT)
        self._loading_lbl.pack(side="left", padx=(24, 0))
        self._loading_job = None
        self._loading_msg = ""
        self._loading_i = 0

        nb = ttk.Notebook(self)
        nb.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=12)
        self._nb = nb
        self._tab_canvases = {}
        self._scroll_canvas = None      # set per tab; see _active_canvas

        def _make_tab(label):
            """Add a notebook tab whose content scrolls if it does not fit."""
            holder = tk.Frame(nb, bg=DARK_BG)
            nb.add(holder, text=label)
            holder.columnconfigure(0, weight=1)
            holder.rowconfigure(0, weight=1)
            cv = tk.Canvas(holder, bg=DARK_BG, highlightthickness=0)
            sb = ttk.Scrollbar(holder, orient="vertical", command=cv.yview)
            cv.configure(yscrollcommand=sb.set)
            cv.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            inner = tk.Frame(cv, bg=DARK_BG, padx=12, pady=12)
            inner.columnconfigure(0, weight=1)
            _win = cv.create_window((0, 0), window=inner, anchor="nw")
            cv.bind("<Configure>",
                    lambda e, c=cv, w=_win: c.itemconfig(w, width=e.width))
            inner.bind("<Configure>",
                       lambda e, c=cv: c.configure(scrollregion=c.bbox("all")))
            self._tab_canvases[str(holder)] = cv
            return inner

        def _split_tab(inner):
            """Controls on the left, live preview on the right."""
            inner.columnconfigure(0, weight=0, minsize=self.LABEL_COL_W + self.HELP_W + 200)
            inner.columnconfigure(1, weight=1, minsize=self.PREVIEW_MIN_W)
            inner.rowconfigure(0, weight=1)
            left = tk.Frame(inner, bg=DARK_BG)
            left.grid(row=0, column=0, sticky="nw")
            left.columnconfigure(0, weight=1)
            right = tk.Frame(inner, bg=DARK_BG)
            right.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
            right.columnconfigure(0, weight=1)
            right.rowconfigure(0, weight=1)
            return left, right

        tab_data = _make_tab("  Data  ")
        tab_laps = _make_tab("  Laps  ")
        _dash_in = _make_tab("  Dash  ")
        _gp_in   = _make_tab("  G-Plot  ")
        _tm_in   = _make_tab("  Track Map  ")
        _ld_in   = _make_tab("  Lap Data  ")
        tab_dash, pv_dash = _split_tab(_dash_in)
        tab_gp,   pv_gp   = _split_tab(_gp_in)
        tab_tm,   pv_tm   = _split_tab(_tm_in)
        tab_ld,   pv_ld   = _split_tab(_ld_in)
        self._tab_keys = {}             # notebook tab id -> preview kind
        for _hold, _kind in ((_dash_in, "dash"), (_gp_in, "gplot"),
                             (_tm_in, "trackmap"), (_ld_in, "lapdata")):
            self._tab_keys[str(_hold.master.master)] = _kind

        # Track whether the pointer is over a combobox or its (open) dropdown
        # list, so the mouse wheel scrolls the list rather than the whole app.
        _combo_cls = ('TCombobox', 'Combobox', 'Listbox', 'ComboboxPopdownFrame')
        self._wheel_over_combo = False
        def _cls_is_combo(w):
            _depth = 0
            while w is not None and _depth < 6:
                try:
                    if w.winfo_class() in _combo_cls:
                        return True
                except Exception:
                    break
                w = getattr(w, 'master', None); _depth += 1
            return False
        def _on_enter(e):
            try:
                if e.widget.winfo_class() in _combo_cls:
                    self._wheel_over_combo = True
            except Exception:
                pass
        def _on_leave(e):
            try:
                if e.widget.winfo_class() in _combo_cls:
                    self._wheel_over_combo = False
            except Exception:
                pass
        def _pointer_over_dropdown(e):
            # Prefer the widget actually under the pointer; when found it is
            # authoritative and also clears any stale flag. When it can't be
            # resolved (pointer over the popdown top-level on some platforms),
            # fall back to the Enter/Leave flag.
            try:
                w = self.winfo_containing(e.x_root, e.y_root)
            except Exception:
                w = None
            if w is not None:
                over = _cls_is_combo(w)
                self._wheel_over_combo = over
                return over
            return self._wheel_over_combo
        def _on_mousewheel(e):
            if _pointer_over_dropdown(e):
                return          # let the combobox / its list handle the wheel
            cv = self._active_canvas()
            if cv is not None:
                cv.yview_scroll(int(-1*(e.delta/120)), "units")
        def _on_mousewheel_linux(e):
            if _pointer_over_dropdown(e):
                return
            cv = self._active_canvas()
            if cv is not None:
                cv.yview_scroll(-1 if e.num == 4 else 1, "units")

        self.bind_all("<MouseWheel>", _on_mousewheel)
        self.bind_all("<Button-4>", _on_mousewheel_linux)
        self.bind_all("<Button-5>", _on_mousewheel_linux)
        self.bind_all("<Enter>", _on_enter, add="+")
        self.bind_all("<Leave>", _on_leave, add="+")
        nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab_changed())

        # ── SECTION 1: FILE SELECTION ─────────────────────────────────────────
        self._section(tab_data, 0, "FILE SELECTION")
        fs = self._card(tab_data, 1)
        self._register_help(fs, "file")
        fs.columnconfigure(1, weight=1)

        tk.Label(fs, text="Log File:", font=FONT_HEAD, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=0, column=0, sticky="w", padx=(0,10), pady=4)
        tk.Entry(fs, textvariable=self.file_path, font=FONT_MONO,
                 bg=DARK_PANEL, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                 relief="flat", bd=4).grid(row=0, column=1, sticky="ew", pady=4)
        self._btn(fs, "Browse…", self._browse_file).grid(row=0, column=2, padx=(8,0), pady=4)

        tk.Label(fs, text="Output Folder:", font=FONT_HEAD, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=1, column=0, sticky="w", padx=(0,10), pady=4)
        tk.Entry(fs, textvariable=self.output_dir, font=FONT_MONO,
                 bg=DARK_PANEL, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                 relief="flat", bd=4).grid(row=1, column=1, sticky="ew", pady=4)
        self._btn(fs, "Browse…", self._browse_output).grid(row=1, column=2, padx=(8,0), pady=4)

        tk.Label(fs, text="Output Filename:", font=FONT_HEAD, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=2, column=0, sticky="w", padx=(0,10), pady=4)
        tk.Entry(fs, textvariable=self.output_name, font=FONT_MONO,
                 bg=DARK_PANEL, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                 relief="flat", bd=4).grid(row=2, column=1, sticky="ew", pady=4)

        # DLL path field (shown only for XRK files, hidden otherwise)
        self._dll_row_frame = tk.Frame(fs, bg=DARK_CARD)
        self._dll_row_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(4,0))
        self._dll_row_frame.grid_remove()  # XRK/DRK disabled pending bug fix
        tk.Label(self._dll_row_frame, text="XRK DLL:", font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC).pack(side="left", padx=(0,4))
        self.dll_path_var = tk.StringVar(value="")
        tk.Entry(self._dll_row_frame, textvariable=self.dll_path_var, width=42,
                 font=FONT_MONO, bg=DARK_PANEL, fg=ACCENT2,
                 insertbackground=TEXT_PRI, relief="flat", bd=4).pack(side="left")
        def _browse_dll():
            p = filedialog.askopenfilename(
                title="Select AIM XRK DLL",
                filetypes=[("DLL", "*.dll"), ("All", "*.*")])
            if p: self.dll_path_var.set(p)
        tk.Button(self._dll_row_frame, text="Browse", font=FONT_SMALL,
                  bg=DARK_PANEL, fg=TEXT_PRI, relief="flat",
                  command=_browse_dll).pack(side="left", padx=(6,0))
        self._dll_row_frame.grid_remove()   # hidden until XRK file selected

        # ── SECTION 2: COLUMN MAPPING ─────────────────────────────────────────
        self._section(tab_data, 2, "CHANNEL MAPPING")
        self.col_frame = self._card(tab_data, 3)
        self._register_help(self.col_frame, "channels")
        self._build_col_mapping([])   # empty until file loaded
        # collect all lockable inputs after build
        self.after(100, self._collect_inputs)

        # ── SECTION 3: LAP DETECTION ──────────────────────────────────────────
        lap_card = self._card(tab_laps, 0)
        self._register_help(lap_card, "lap")
        lap_card.columnconfigure(3, weight=1)

        # Mode selector
        tk.Label(lap_card, text='Method:', font=FONT_HEAD,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=0, column=0, sticky='w', padx=(0,8), pady=(0,6))
        _radio_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _radio_frame.grid(row=0, column=1, columnspan=3, sticky='w', pady=(0,6))
        for _mi, _mv in enumerate(['Lap Beacons', 'GPS Crossing', 'Speed Peaks',
                                    'Point-to-Point (Stage)']):
            tk.Radiobutton(_radio_frame, text=_mv,
                           variable=self.lap_mode_var, value=_mv,
                           font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_PRI,
                           activebackground=DARK_CARD, activeforeground=ACCENT,
                           selectcolor=DARK_PANEL, relief='flat',
                           cursor='hand2').pack(side='left', padx=(0,16))

        # Separator
        tk.Frame(lap_card, bg=BORDER_COL, height=1).grid(
            row=1, column=0, columnspan=4, sticky='ew', pady=(0,6))

        # ── Dynamic sub-panels (shown based on mode) ──────────────────────────
        # Speed Peaks panel
        _spd_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _spd_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
        def _lbl(f, t, r, c, **kw):
            tk.Label(f, text=t, font=kw.get('font',FONT_HEAD), bg=DARK_CARD,
                     fg=kw.get('fg',TEXT_SEC)).grid(row=r,column=c,sticky='w',padx=(0,8),pady=2)
        def _ent(f, v, r, c, w=7):
            e = tk.Entry(f, textvariable=v, width=w, font=FONT_MONO,
                         bg=DARK_PANEL, fg=ACCENT2, insertbackground=TEXT_PRI,
                         relief='flat', bd=4)
            e.grid(row=r, column=c, sticky='w', padx=(0,8), pady=2)
        _lbl(_spd_frame, 'Min speed (kph):', 0, 0)
        _ent(_spd_frame, self.lap_min_speed,  0, 1)
        _lbl(_spd_frame, 'Est. lap time (s):', 1, 0)
        _ent(_spd_frame, self.lap_est_time,    1, 1)

        # GPS Crossing panel
        _gps_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _gps_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
        _gps_frame.grid_remove()
        _lbl(_gps_frame, 'Lat col:', 0, 0)
        _lat_col_cb = ttk.Combobox(_gps_frame, textvariable=self.lap_lat_col,
                                    values=[], width=24, font=FONT_MONO, state='readonly')
        _lat_col_cb.grid(row=0, column=1, sticky='w', padx=(0,8), pady=2)
        _lbl(_gps_frame, 'Lon col:', 1, 0)
        _lon_col_cb = ttk.Combobox(_gps_frame, textvariable=self.lap_lon_col,
                                    values=[], width=24, font=FONT_MONO, state='readonly')
        _lon_col_cb.grid(row=1, column=1, sticky='w', padx=(0,8), pady=2)
        self._fix_combo(_lat_col_cb); self._fix_combo(_lon_col_cb)
        # Trigger lap display update when GPS columns change
        for _gcv in (self.lap_lat_col, self.lap_lon_col):
            _gcv.trace_add('write', lambda *_: self._schedule_lap_update())
        # Paste field — accepts Google Maps format "-40.236, 175.558"
        _lbl(_gps_frame, 'Paste lat, lon:', 3, 0)
        self._sf_paste_var = tk.StringVar(value="")
        _paste_ent = tk.Entry(_gps_frame, textvariable=self._sf_paste_var,
                              width=28, font=FONT_MONO, bg=DARK_PANEL,
                              fg=ACCENT2, insertbackground=TEXT_PRI,
                              relief="flat", bd=4)
        _paste_ent.grid(row=3, column=1, columnspan=2, sticky="w", pady=2)
        def _on_paste_change(*_):
            raw = self._sf_paste_var.get().strip()
            # Accept "lat, lon" or "lat lon" with optional degree symbols
            import re as _re
            nums = _re.findall(r"[-+]?\d+\.\d+", raw)
            if len(nums) >= 2:
                self.lap_sf_lat.set(nums[0])
                self.lap_sf_lon.set(nums[1])
        self._sf_paste_var.trace_add("write", _on_paste_change)
        _lbl(_gps_frame, 'S/F Latitude:',  4, 0)
        _ent(_gps_frame, self.lap_sf_lat,    4, 1, w=16)
        _lbl(_gps_frame, 'S/F Longitude:', 5, 0)
        _ent(_gps_frame, self.lap_sf_lon,    5, 1, w=16)
        _lbl(_gps_frame, 'Min lap time (s):', 6, 0)
        _ent(_gps_frame, self.lap_est_time,  6, 1, w=7)

        # Point-to-Point (Stage) panel — rally stages, hillclimbs (start != finish)
        _stage_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _stage_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
        _stage_frame.grid_remove()
        # Start line
        _lbl(_stage_frame, 'Paste start (lat, lon):', 1, 0)
        self._stage_start_paste = tk.StringVar(value="")
        tk.Entry(_stage_frame, textvariable=self._stage_start_paste, width=28,
                 font=FONT_MONO, bg=DARK_PANEL, fg=ACCENT2,
                 insertbackground=TEXT_PRI, relief="flat", bd=4).grid(
            row=1, column=1, columnspan=2, sticky="w", pady=2)
        _lbl(_stage_frame, 'Start Lat:', 2, 0)
        _ent(_stage_frame, self.stage_start_lat, 2, 1, w=16)
        _lbl(_stage_frame, 'Start Lon:', 3, 0)
        _ent(_stage_frame, self.stage_start_lon, 3, 1, w=16)
        # Finish line
        _lbl(_stage_frame, 'Paste finish (lat, lon):', 4, 0)
        self._stage_fin_paste = tk.StringVar(value="")
        tk.Entry(_stage_frame, textvariable=self._stage_fin_paste, width=28,
                 font=FONT_MONO, bg=DARK_PANEL, fg=ACCENT2,
                 insertbackground=TEXT_PRI, relief="flat", bd=4).grid(
            row=4, column=1, columnspan=2, sticky="w", pady=2)
        _lbl(_stage_frame, 'Finish Lat:', 5, 0)
        _ent(_stage_frame, self.stage_fin_lat, 5, 1, w=16)
        _lbl(_stage_frame, 'Finish Lon:', 6, 0)
        _ent(_stage_frame, self.stage_fin_lon, 6, 1, w=16)
        _lbl(_stage_frame, 'Line radius (m):', 7, 0)
        _ent(_stage_frame, self.stage_radius, 7, 1, w=7)
        def _on_stage_start_paste(*_):
            import re as _re
            nums = _re.findall(r"[-+]?\d+\.\d+", self._stage_start_paste.get())
            if len(nums) >= 2:
                self.stage_start_lat.set(nums[0]); self.stage_start_lon.set(nums[1])
        def _on_stage_fin_paste(*_):
            import re as _re
            nums = _re.findall(r"[-+]?\d+\.\d+", self._stage_fin_paste.get())
            if len(nums) >= 2:
                self.stage_fin_lat.set(nums[0]); self.stage_fin_lon.set(nums[1])
        self._stage_start_paste.trace_add("write", _on_stage_start_paste)
        self._stage_fin_paste.trace_add("write", _on_stage_fin_paste)
        for _sv in (self.stage_start_lat, self.stage_start_lon,
                    self.stage_fin_lat, self.stage_fin_lon):
            _sv.trace_add("write", lambda *_: self._schedule_lap_update(delay=400))

        # AIM beacons panel
        _aim_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _aim_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
        _aim_frame.grid_remove()
        _aim_info_lbl = tk.Label(_aim_frame, text='Load a data file with lap beacon markers (AIM CSV, VBO, etc.).',
                                  font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC,
                                  justify='left', anchor='w', wraplength=400)
        _aim_info_lbl.grid(row=0, column=0, sticky='w')

        # Timer offset (Speed Peaks only)
        _sep2 = tk.Frame(lap_card, bg=BORDER_COL, height=1)
        _sep2.grid(row=3, column=0, columnspan=4, sticky='ew', pady=(6,4))
        _off_lbl = tk.Label(lap_card, text='Timer offset (s):', font=FONT_HEAD,
                             bg=DARK_CARD, fg=TEXT_SEC)
        _off_lbl.grid(row=4, column=0, sticky='w', padx=(0,8))
        _off_ent = tk.Entry(lap_card, textvariable=self.lap_offset_var, width=7,
                             font=FONT_MONO, bg=DARK_PANEL, fg=ACCENT2,
                             insertbackground=TEXT_PRI, relief='flat', bd=4)
        _off_ent.grid(row=4, column=1, sticky='w', padx=(0,8))
        _off_hint = tk.Label(lap_card, text='± seconds',
                              font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC)
        _off_hint.grid(row=4, column=2, columnspan=2, sticky='w')
        # Show/hide offset with mode
        _offset_widgets = [_sep2, _off_lbl, _off_ent, _off_hint]

        # Mode switch logic
        _mode_hints = {
            'Lap Beacons':  'Reads lap beacon markers embedded in the data file (AIM CSV, VBO, etc.)',
            'GPS Crossing': 'Detects S/F line crossing from GPS position',
            'Speed Peaks':  'Detects high-speed sections on the straight',
            'Point-to-Point (Stage)': 'Times a run between a separate start and finish line (rally/hillclimb)',
        }
        def _on_mode_change(*_):
            m = self.lap_mode_var.get()
            # Hide all sub-panels first
            _spd_frame.grid_remove()
            _gps_frame.grid_remove()
            _aim_frame.grid_remove()
            _stage_frame.grid_remove()
            # Show relevant panel — Speed Peaks only shows params for that method
            if m == 'Speed Peaks':
                _spd_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
            elif m == 'GPS Crossing':
                _gps_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
            elif m == 'Lap Beacons':
                _aim_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
            elif m == 'Point-to-Point (Stage)':
                _stage_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
            # Timer offset only for Speed Peaks
            for _w in _offset_widgets:
                if m == 'Speed Peaks':
                    _w.grid()
                else:
                    _w.grid_remove()
            try:
                self._schedule_lap_update(msg="Detecting laps")
            except Exception:
                pass
        self.lap_mode_var.trace_add('write', _on_mode_change)
        # Hide speed panel initially (default is Auto)
        _spd_frame.grid_remove()
        _on_mode_change()  # apply initial state

        # Store refs for later updates
        self._aim_info_lbl = _aim_info_lbl
        self._lat_col_cb   = _lat_col_cb
        self._lon_col_cb   = _lon_col_cb

        # Debounced refresh — wait for typing to settle, then recompute with the
        # loading sign shown (see _schedule_lap_update).
        self._lap_update_job = None
        def _debounced_update(*_):
            try:
                if self._lap_update_job:
                    self.after_cancel(self._lap_update_job)
                self._lap_update_job = self.after(
                    600, lambda: self._schedule_lap_update(delay=60))
            except Exception:
                pass
        for _lv in (self.lap_min_speed, self.lap_est_time, self.lap_offset_var,
                    self.lap_sf_lat, self.lap_sf_lon, self.lap_sf_radius,
                    self.lap_lat_col, self.lap_lon_col):
            _lv.trace_add('write', _debounced_update)

        _lap_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _lap_frame.grid(row=5, column=0, columnspan=5, sticky="nsew", pady=(6,0))
        _lap_sb = tk.Scrollbar(_lap_frame, orient="vertical", bg=DARK_PANEL)
        _lap_sb.pack(side="right", fill="y")
        # Single narrow column now, so make the box tall rather than wide.
        self.lap_info = tk.Text(_lap_frame, height=24, width=32, font=FONT_MONO,
                                 bg=DARK_CARD, fg=ACCENT2, relief="flat",
                                 wrap="none", state="disabled",
                                 yscrollcommand=_lap_sb.set)
        self.lap_info.pack(side="left", fill="both", expand=True)
        _lap_sb.config(command=self.lap_info.yview)

        # ── GLOBAL BAR: output range, fps, preview scrubber, render ─────────
        # These apply to the whole job rather than one overlay, so they sit below
        # the tabs and stay visible whichever tab is open.
        bar = tk.Frame(self, bg=DARK_CARD, padx=14, pady=10,
                       highlightbackground=BORDER_COL, highlightthickness=1)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 12))
        bar.columnconfigure(0, weight=0)
        bar.columnconfigure(1, weight=1)
        self._global_bar = bar

        tr = tk.Frame(bar, bg=DARK_CARD)
        tr.grid(row=0, column=0, sticky="nw")

        tk.Label(tr, text="Start (s):", font=FONT_HEAD, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=0, column=0, sticky="w", padx=(0,8))
        tk.Entry(tr, textvariable=self.t_start_var, width=10, font=FONT_MONO,
                 bg=DARK_PANEL, fg=ACCENT2, insertbackground=TEXT_PRI,
                 relief="flat", bd=4).grid(row=0, column=1, sticky="w")

        tk.Label(tr, text="End (s):", font=FONT_HEAD, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=0, column=2, sticky="w", padx=(20,8))
        tk.Entry(tr, textvariable=self.t_end_var, width=10, font=FONT_MONO,
                 bg=DARK_PANEL, fg=ACCENT2, insertbackground=TEXT_PRI,
                 relief="flat", bd=4).grid(row=0, column=3, sticky="w")

        tk.Label(tr, textvariable=self.max_t_label, font=FONT_SMALL,
                 bg=DARK_CARD, fg=ACCENT).grid(row=0, column=4, padx=(20,0), sticky="w")

        # Button: set t_start to first lap boundary
        def _set_start_to_first_lap():
            try:
                lps = self._compute_laps()
            except Exception as _e:
                self.status_var.set(f"Could not compute laps: {_e}")
                return
            if not lps:
                self.status_var.set("No laps found — check lap detection settings")
                return
            # Identify flying laps using median lap time as reference
            # The median filters out outliers (out-lap, in-lap, safety car)
            import statistics as _stat
            all_lts = [lt for st,et,lt in lps]
            median_lt = _stat.median(all_lts)
            # Flying lap = within 40% of median
            flying = [(st,et,lt) for st,et,lt in lps
                      if abs(lt - median_lt) <= median_lt * 0.40]
            if not flying:
                flying = lps  # last resort
            # Sort by start time — first flying lap
            flying_sorted = sorted(flying, key=lambda x: x[0])
            first_t = float(flying_sorted[0][0])
            last_t  = float(sorted(lps, key=lambda x: x[1])[-1][1])
            self.t_start_var.set(f"{first_t:.3f}")
            self.t_end_var.set(f"{last_t:.3f}")
            m,s = divmod(flying_sorted[0][2], 60)
            self.status_var.set(
                f"Range: {first_t:.2f}s → {last_t:.2f}s  "
                f"({len(flying)} flying laps, first: {int(m)}:{s:05.2f})")
        tk.Button(tr, text="⟩ Set start to first lap",
                  font=FONT_SMALL, bg=DARK_PANEL, fg=ACCENT,
                  relief="flat", cursor="hand2",
                  command=_set_start_to_first_lap).grid(
            row=0, column=5, sticky="w", padx=(12,0))

        tk.Label(tr, text="Output FPS:", font=FONT_HEAD, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=1, column=0, sticky="w", padx=(0,8), pady=(8,0))
        self._fps_info_var = tk.StringVar(value="")
        fps_combo = ttk.Combobox(tr, textvariable=self.fps_var,
                                  values=[str(f) for f in [10,12,15,20,24,25,30,48,50,60]],
                                  width=6, font=FONT_MONO, state="readonly")
        fps_combo.grid(row=1, column=1, sticky="w", pady=(8,0))
        self._fix_combo(fps_combo)
        tk.Label(tr, text="fps", font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=1, column=2, sticky="w", padx=(4,0), pady=(8,0))
        tk.Label(tr, textvariable=self._fps_info_var, font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=1, column=3, sticky="w", padx=(12,0), pady=(8,0))

        # ── ⑤ DASH STYLE ──────────────────────────────────────────────────────
        # Everything that shapes the MAIN dashboard render lives here, including
        # the dash's built-in G-trace / track trail (trail length + speed colour).
        sty = self._card(tab_dash, 0)
        self._register_help(sty, "dash")
        tk.Label(sty, text="Visual Style:", font=FONT_HEAD,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=0, column=0, sticky="w", padx=(0,10))
        _style_cb = ttk.Combobox(sty, textvariable=self.style_var,
                      values=list(_R.DASH_NAMES),
                      width=38, font=FONT_MONO, state="readonly")
        _style_cb.grid(row=0, column=1, sticky="w")
        self._fix_combo(_style_cb)

        # Main-dash built-in trace trail: length + speed colour
        _trail_lbl = tk.Label(sty, text="Dash trail:", font=FONT_HEAD, bg=DARK_CARD,
                              fg=TEXT_SEC)
        _trail_lbl.grid(row=1, column=0, sticky="w", padx=(0,10), pady=(10,0))
        _trail_wrap = tk.Frame(sty, bg=DARK_CARD)
        _trail_wrap.grid(row=1, column=1, columnspan=3, sticky="w", pady=(10,0))
        trail_combo = ttk.Combobox(_trail_wrap, textvariable=self.g_trail_secs,
                                    values=_TRAIL_SECS,
                                    width=6, font=FONT_MONO, state="readonly")
        trail_combo.pack(side="left")
        trail_combo.set("40")
        self._fix_combo(trail_combo)
        tk.Label(_trail_wrap, text="seconds trail time", font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC).pack(side="left", padx=(8,0))
        tk.Checkbutton(_trail_wrap, text="Speed colour", variable=self.g_trail_speed_colour,
                        bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
                        activeforeground=ACCENT2, selectcolor=DARK_PANEL,
                        font=FONT_SMALL, relief="flat", cursor="hand2").pack(
            side="left", padx=(20,0))

        # Remember these so they can be hidden for dashes with no trail feature.
        self._trail_lbl_w = _trail_lbl
        self._trail_row_w = _trail_wrap

        # Dashboard backdrop - the chroma-key / background colour of the dash.
        self._bd_lbl = tk.Label(sty, text="Backdrop:", font=FONT_HEAD, bg=DARK_CARD,
                                fg=TEXT_SEC)
        self._bd_lbl.grid(row=3, column=0, sticky="w", padx=(0,10), pady=(10,0))
        _bd_cb = ttk.Combobox(sty, textvariable=self.backdrop_var, state="readonly",
                              values=["Chroma magenta", "Chroma green", "Dark"],
                              width=18, font=FONT_MONO)
        _bd_cb.grid(row=3, column=1, sticky="w", pady=(10,0))
        self._fix_combo(_bd_cb)
        self._bd_cb = _bd_cb

        # Crop to the panel: for dashes whose graphic does not fill the frame,
        # the output can be trimmed to the panel so no chroma keying is needed.
        self._crop_chk = tk.Checkbutton(
            sty, text="Crop frame to the panel (no chroma key needed)",
            variable=self.crop_to_content,
            bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
            activeforeground=ACCENT2, selectcolor=DARK_PANEL,
            font=FONT_SMALL, relief="flat", cursor="hand2")
        self._crop_chk.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8,0))

        # ── ⑥ G-PLOT STYLE (standalone G-force trace overlay) ──────────────────
        gp = self._card(tab_gp, 0)
        self._register_help(gp, "gplot")
        tk.Checkbutton(gp, text="Generate G-force trace graphic",
                        variable=self.gen_gtrace,
                        bg=DARK_CARD, fg=ACCENT, activebackground=DARK_CARD,
                        activeforeground=ACCENT, selectcolor=DARK_PANEL,
                        font=FONT_BODY, relief="flat", cursor="hand2").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0,0))
        tk.Label(gp, text="Form:", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=2, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _gtf = ttk.Combobox(gp, textvariable=self.gtrace_form, state="readonly",
                            values=["Target", "X-Y Graph"],
                            width=22, font=FONT_MONO)
        _gtf.grid(row=2, column=1, columnspan=2, sticky="w", pady=(2,2))
        self._fix_combo(_gtf)
        tk.Label(gp, text="Trail time (s):", font=FONT_SMALL, bg=DARK_CARD,
                 fg=TEXT_SEC).grid(row=3, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _gp_trail = ttk.Combobox(gp, textvariable=self.gtrace_trail, state="readonly",
                     values=_TRAIL_SECS,
                     width=8, font=FONT_MONO)
        _gp_trail.grid(row=3, column=1, sticky="w", pady=(2,2))
        _gp_trail.set("40")
        self._fix_combo(_gp_trail)
        tk.Label(gp, text="Colour:", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=4, column=0, sticky="w", padx=(0,10), pady=(2,2))
        self._gtrace_colour_cb = ttk.Combobox(gp, textvariable=self.gtrace_colour,
                            state="readonly", values=self._gtrace_colour_options(),
                            width=22, font=FONT_MONO)
        self._gtrace_colour_cb.grid(row=4, column=1, columnspan=2, sticky="w", pady=(2,4))
        self._fix_combo(self._gtrace_colour_cb)
        tk.Label(gp, text="Max range (G):", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=5, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _gp_gm = ttk.Combobox(gp, textvariable=self.gtrace_gmax, state="readonly",
                              values=["Auto", "2.5", "2", "1.5", "1"],
                              width=8, font=FONT_MONO)
        _gp_gm.grid(row=5, column=1, sticky="w", pady=(2,2))
        self._fix_combo(_gp_gm)
        self._gp_bd_lbl = tk.Label(gp, text="Backdrop:", font=FONT_SMALL,
                                   bg=DARK_CARD, fg=TEXT_SEC)
        self._gp_bd_lbl.grid(row=7, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _gp_bd = ttk.Combobox(gp, textvariable=self.gt_backdrop, state="readonly",
                              values=["Chroma magenta", "Chroma green", "Dark"],
                              width=18, font=FONT_MONO)
        _gp_bd.grid(row=7, column=1, columnspan=2, sticky="w", pady=(2,4))
        self._fix_combo(_gp_bd)
        self._gp_bd_cb = _gp_bd

        # ── ⑦ TRACK MAP STYLE (standalone track-map overlay) ───────────────────
        tm = self._card(tab_tm, 0)
        self._register_help(tm, "trackmap")
        tk.Checkbutton(tm, text="Generate track map graphic",
                        variable=self.gen_trackmap,
                        bg=DARK_CARD, fg=ACCENT, activebackground=DARK_CARD,
                        activeforeground=ACCENT, selectcolor=DARK_PANEL,
                        font=FONT_BODY, relief="flat", cursor="hand2").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0,0))
        tk.Checkbutton(tm, text="Speed colour", variable=self.tm_speed_colour,
                        bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
                        activeforeground=ACCENT2, selectcolor=DARK_PANEL,
                        font=FONT_SMALL, relief="flat", cursor="hand2").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=(0,10), pady=(4,2))
        tk.Label(tm, text="Backdrop:", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=3, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _tm_bd = ttk.Combobox(tm, textvariable=self.tm_backdrop, state="readonly",
                              values=["Chroma magenta", "Chroma green", "Dark"],
                              width=18, font=FONT_MONO)
        _tm_bd.grid(row=3, column=1, columnspan=2, sticky="w", pady=(2,2))
        self._fix_combo(_tm_bd)
        tk.Label(tm, text="Map amount:", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=5, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _tm_amt = ttk.Combobox(tm, textvariable=self.tm_amount, state="readonly",
                               values=(["All"]
                                       + [f"{n}s window" for n in range(10, 61, 10)]
                                       + [f"{n}s window" for n in (75, 90, 105, 120, 135, 150)]),
                               width=18, font=FONT_MONO)
        _tm_amt.grid(row=5, column=1, columnspan=2, sticky="w", pady=(2,4))
        self._fix_combo(_tm_amt)

        # ── ⑧ LAP DATA STYLE (standalone lap-data overlay) ────────────────────
        ld = self._card(tab_ld, 0)
        self._register_help(ld, "lapdata")
        tk.Checkbutton(ld, text="Generate lap data graphic",
                        variable=self.gen_lapdata,
                        bg=DARK_CARD, fg=ACCENT, activebackground=DARK_CARD,
                        activeforeground=ACCENT, selectcolor=DARK_PANEL,
                        font=FONT_BODY, relief="flat", cursor="hand2").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0,0))
        tk.Label(ld, text="Content:", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=2, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _ld_mode = ttk.Combobox(ld, textvariable=self.ld_mode, state="readonly",
                                values=["Current lap", "All laps (list)"],
                                width=22, font=FONT_MONO)
        _ld_mode.grid(row=2, column=1, columnspan=2, sticky="w", pady=(2,2))
        self._fix_combo(_ld_mode)
        tk.Label(ld, text="Graphic style:", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=3, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _ld_sty = ttk.Combobox(ld, textvariable=self.ld_style, state="readonly",
                               values=["Dash 1 · Cells, light",
                                       "Dash 2 · Cells, dark",
                                       "Dash 5 · Panels",
                                       "Dash 6 · Logger rows",
                                       "Dash 7 · HUD bar",
                                       "Dash 8b · Slanted panels",
                                       "Dash 10 · Plain text"],
                               width=22, font=FONT_MONO)
        _ld_sty.grid(row=3, column=1, columnspan=2, sticky="w", pady=(2,2))
        self._fix_combo(_ld_sty)
        tk.Label(ld, text="Backdrop:", font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=4, column=0, sticky="w", padx=(0,10), pady=(2,2))
        _ld_bd = ttk.Combobox(ld, textvariable=self.ld_backdrop, state="readonly",
                              values=["Chroma magenta", "Chroma green", "Dark"],
                              width=18, font=FONT_MONO)
        _ld_bd.grid(row=4, column=1, columnspan=2, sticky="w", pady=(2,4))
        self._fix_combo(_ld_bd)

        # Resolution / data info line (updates with style + fps)
        self._style_info_lbl = tk.Label(sty, textvariable=self._file_info_var,
                 font=FONT_SMALL, bg=DARK_CARD, fg=ACCENT)
        self._style_info_lbl.grid(row=5, column=0, columnspan=4, sticky="w", pady=(4,0))
        def _on_style_change(*_):
            _sn1 = self.style_var.get()
            _fw, _fh = _R.style_frame_size(_R.STYLES.get(_sn1))
            _r = f"{_fw}×{_fh}"
            _data_fps2 = getattr(self, "_data_fps", None)
            _sel_fps = self.fps_var.get() if hasattr(self, "fps_var") else ""
            if _data_fps2 and hasattr(self, "_file_info_var"):
                _dur = getattr(self, "df", None)
                _rows = len(_dur) if _dur is not None else 0
                _t0 = float(self.t_start_var.get() or 0)
                _t1 = float(self.t_end_var.get() or 0)
                self._file_info_var.set(
                    f"data: {_data_fps2} fps  ·  render: {_sel_fps} fps  ·  {_rows:,} rows  ·  "
                    f"{_t1-_t0:.1f}s  ·  output: {_r}")
        self.style_var.trace_add("write", _on_style_change)
        if hasattr(self, "fps_var"):
            self.fps_var.trace_add("write", _on_style_change)

        # ── Render controls, in the global bar ──────────────────────────────
        gen = tk.Frame(bar, bg=DARK_CARD)
        gen.grid(row=0, column=1, sticky="new", padx=(24, 0))
        gen.columnconfigure(0, weight=1)

        btn_row = tk.Frame(gen, bg=DARK_CARD)
        btn_row.grid(row=0, column=0, sticky="ew", pady=(0,8))
        btn_row.columnconfigure(0, weight=1)
        self.gen_btn = self._btn(btn_row, "▶  GENERATE VIDEO", self._start_render,
                                  big=True, color=ACCENT)
        self.gen_btn.grid(row=0, column=0, sticky="ew", padx=(0,8))
        self.preview_btn = self._btn(btn_row, "🖼  PREVIEW ALL", self._preview_frame,
                                      big=True, color=ACCENT2)
        self.preview_btn.grid(row=0, column=1, padx=(8,8))
        self.cancel_btn = self._btn(btn_row, "✕  CANCEL", self._cancel_render,
                                     big=True, color=RED_UI)
        self.cancel_btn.grid(row=0, column=2, padx=(8,0))
        self.cancel_btn.config(state="disabled", fg=TEXT_SEC)

        # Progress bar
        self.pbar = ttk.Progressbar(gen, variable=self.progress, maximum=100,
                                     style="ECU.Horizontal.TProgressbar", length=400)
        self.pbar.grid(row=1, column=0, sticky="ew", pady=(0,8))

        tk.Label(gen, textvariable=self.status_var, font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC, wraplength=760, justify="left").grid(
            row=2, column=0, sticky="w")

        # Bottom-left corner: theme toggle, and the bar's help behind a "?"
        # (the bar has no room for a help column of its own).
        _corner = tk.Frame(bar, bg=DARK_CARD)
        _corner.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._theme_btn = tk.Button(
            _corner,
            text="☀ Light theme" if THEME_NAME == "dark" else "☾ Dark theme",
            font=FONT_SMALL, bg=DARK_PANEL, fg=ACCENT, activebackground=BORDER_COL,
            activeforeground=ACCENT, relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4, highlightbackground=BORDER_COL, highlightthickness=1,
            command=self._toggle_theme)
        self._theme_btn.pack(side="left")
        tk.Button(_corner, text=" ? ", font=FONT_SMALL, bg=DARK_PANEL, fg=ACCENT,
                  activebackground=BORDER_COL, activeforeground=ACCENT,
                  relief="flat", cursor="hand2",
                  command=lambda: messagebox.showinfo(
                      "Output and Generate",
                      self.HELP.get("output", "") + "\n\n" +
                      self.HELP.get("generate", ""))).pack(side="left", padx=(8, 0))

        # ── Live preview panes, one per overlay tab. Each carries its own copy
        #    of the time scrubber directly under the picture; they all drive one
        #    shared value, so scrubbing on one tab moves them all. ────────────
        self._pv_panes = {}
        self._pv_holders = {}
        self._pv_scales = []
        for _par, _kind, _cap in ((pv_dash, "dash", "DASH PREVIEW"),
                                  (pv_gp, "gplot", "G-PLOT PREVIEW"),
                                  (pv_tm, "trackmap", "TRACK MAP PREVIEW"),
                                  (pv_ld, "lapdata", "LAP DATA PREVIEW")):
            self._make_preview_pane(_par, _kind, _cap)

        # Any setting that shapes an overlay repreviews the visible tab. Only the
        # tab on show renders, so a change on another tab costs nothing.
        for _vn in ("style_var", "style2_var", "g_trail_secs", "g_trail_speed_colour",
                    "backdrop_var",
                    "gtrace_form", "gtrace_trail", "gtrace_colour", "gtrace_gmax",
                    "gt_backdrop", "gen_gtrace",
                    "tm_speed_colour", "tm_amount", "tm_backdrop", "gen_trackmap",
                    "ld_mode", "ld_style", "ld_backdrop", "gen_lapdata",
                    "t_start_var", "t_end_var"):
            _v = getattr(self, _vn, None)
            if _v is not None:
                try:
                    _v.trace_add("write", lambda *_a: self._schedule_preview())
                except Exception:
                    pass

        # Inverting a channel changes the working data itself, so rebuild rather
        # than just repreviewing (the rebuild schedules a preview when it lands).
        for _vn in ("invert_glat", "invert_glong", "invert_throttle"):
            _v = getattr(self, _vn, None)
            if _v is not None:
                try:
                    _v.trace_add("write", lambda *_a: self._schedule_rebuild(120))
                except Exception:
                    pass

        for _vn in ("style_var", "crop_to_content"):
            _v = getattr(self, _vn, None)
            if _v is not None:
                try:
                    _v.trace_add("write", self._sync_dash_controls)
                except Exception:
                    pass
        try:
            self.gtrace_form.trace_add("write", self._sync_gplot_controls)
        except Exception:
            pass

        # Context-sensitive help: bind every registered section (and children)
        self._wire_help()
        self._sync_dash_controls()
        self._sync_gplot_controls()
        self._on_tab_changed()

    # ── UI helpers ─────────────────────────────────────────────────────────────
    def _section(self, parent, row, text):
        f = tk.Frame(parent, bg=DARK_BG)
        f.grid(row=row, column=0, sticky="ew", pady=(12,2))
        tk.Label(f, text=text, font=FONT_HEAD, bg=DARK_BG, fg=ACCENT).pack(side="left")
        tk.Frame(f, bg=BORDER_COL, height=1).pack(side="left", fill="x", expand=True, padx=(10,0))

    def _card(self, parent, row):
        f = tk.Frame(parent, bg=DARK_CARD, padx=14, pady=10,
                     highlightbackground=BORDER_COL, highlightthickness=1)
        f.grid(row=row, column=0, sticky="ew")
        # Labels col 0 (fixed width) → controls col 1..4 → spacer → help column,
        # so every panel lines its controls up on a common vertical axis.
        f.columnconfigure(0, minsize=self.LABEL_COL_W, weight=0)
        f.columnconfigure(self.SPACER_COL, weight=1)
        f.columnconfigure(self.HELP_COL, minsize=self.HELP_W, weight=0)
        return f

    def _btn(self, parent, text, cmd, big=False, color=ACCENT2):
        size = 11 if big else 10
        pad  = (14,10) if big else (10,6)
        b = tk.Button(parent, text=text, command=cmd,
                      font=("Courier New", size, "bold"),
                      bg=DARK_PANEL, fg=color, activebackground=BORDER_COL,
                      activeforeground=color, relief="flat", bd=0,
                      cursor="hand2", padx=pad[0], pady=pad[1],
                      highlightbackground=color, highlightthickness=1)
        return b

    def _build_col_mapping(self, columns, preset=None):
        # Preserve smooth state across rebuilds
        _saved_smooth = {ch: v.get() for ch, v in self.smooth_vars.items()}
        for w in self.col_frame.winfo_children():
            w.destroy()
        self.col_vars = {}

        if not columns:
            tk.Label(self.col_frame, text="No file loaded yet.",
                     font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(row=0, column=0, sticky="w")
            return

        mapping = preset if preset is not None else auto_detect_channels(columns)

        # ── Time channel selector ──────────────────────────────────────────
        tk.Label(self.col_frame, text="Time:", font=FONT_HEAD,
                 bg=DARK_CARD, fg=ACCENT).grid(row=0, column=0, sticky='w', padx=(0,12), pady=(0,8))
        _ts_vals = ['(auto)'] + columns
        _cur_ts  = self.ts_col if self.ts_col in columns else '(auto)'
        self.ts_col_var.set(_cur_ts)
        _ts_combo = ttk.Combobox(self.col_frame, textvariable=self.ts_col_var,
                                  values=_ts_vals, width=34, font=FONT_MONO, state='readonly')
        _ts_combo.grid(row=0, column=1, columnspan=2, sticky='w', pady=(0,8))
        self._fix_combo(_ts_combo)
        def _on_ts_change(event):
            if self.df is not None:
                col_map = {ch: var.get() for ch, var in self.col_vars.items()
                           if var.get() not in ('(not detected)', '(None)', '')}
                self._reload_with_ts(col_map)
        _ts_combo.bind('<<ComboboxSelected>>', _on_ts_change)
        tk.Label(self.col_frame, text='Time axis column (seconds)',
                 font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=0, column=3, columnspan=2, sticky='w', padx=(8,0), pady=(0,8))

        tk.Frame(self.col_frame, bg=BORDER_COL, height=1).grid(
            row=1, column=0, columnspan=5, sticky='ew', pady=(0,6))

        headers = ["Channel", "Detected Column", "Min / Max / Avg", "Invert", "Smooth"]
        for c, h in enumerate(headers):
            tk.Label(self.col_frame, text=h, font=FONT_HEAD,
                     bg=DARK_CARD, fg=ACCENT).grid(row=2, column=c, sticky="w",
                                                    padx=(0,20), pady=(0,6))
        # Separator
        tk.Frame(self.col_frame, bg=BORDER_COL, height=1).grid(
            row=3, column=0, columnspan=5, sticky="ew", pady=(0,6))

        for r, (ch, label) in enumerate(CHANNEL_LABELS.items(), start=4):
            tk.Label(self.col_frame, text=label, font=FONT_HEAD,
                     bg=DARK_CARD, fg=TEXT_PRI, width=16, anchor="w").grid(
                row=r, column=0, sticky="w", pady=3)

            var = tk.StringVar(value=mapping.get(ch) or "(not detected)")
            self.col_vars[ch] = var

            # Colour indicator — green if detected, red if not
            detected = mapping.get(ch) is not None
            ind_col = GREEN_UI if detected else RED_UI

            dot_lbl = tk.Label(self.col_frame, text="●", font=FONT_SMALL,
                               bg=DARK_CARD, fg=ind_col)
            dot_lbl.grid(row=r, column=1, sticky="w")
            self._dot_labels[ch] = dot_lbl

            combo = ttk.Combobox(self.col_frame, textvariable=var,
                                  values=["(None)"] + columns, width=34,
                                  font=FONT_MONO, state="readonly")
            combo.grid(row=r, column=1, sticky="w", padx=(16,20), pady=3)
            self._fix_combo(combo)

            def _on_combo_change(event, ch=ch, var=var, lbl=dot_lbl, _cs=None):
                val = var.get()
                lbl.config(fg=GREEN_UI if val and val not in ('(not detected)', '(None)') else RED_UI)
                if _cs: _cs(val)
            # _cs will be bound after stats_var is created — use a list as mutable cell
            _cs_cell = [None]

            # Min / Max / Avg stats
            stats_var = tk.StringVar(value="—")

            def _compute_stats(col_name, sv=stats_var):
                if not col_name or col_name in ("(not detected)", "(None)") or self.df is None:
                    sv.set("—"); return
                try:
                    import pandas as _pd
                    col_data = pd.to_numeric(self.df.get(col_name, pd.Series()), errors="coerce").dropna()
                    if len(col_data) > 0:
                        # Decimals from the RANGE, not the magnitude: GPS
                        # coordinates vary by ~0.003 deg over a lap, so 1 dp
                        # made real data look frozen (-40.2 / -40.2 / -40.2).
                        _rng = float(col_data.max() - col_data.min())
                        _dp = (0 if _rng >= 100 else 1 if _rng >= 10 else
                               2 if _rng >= 1 else 3 if _rng >= 0.1 else
                               4 if _rng >= 0.01 else 6)
                        sv.set(f"{col_data.min():.{_dp}f} / {col_data.max():.{_dp}f}"
                               f" / {col_data.mean():.{_dp}f}")
                    else:
                        sv.set("—")
                except Exception:
                    sv.set("—")

            _compute_stats(mapping.get(ch))   # populate on load
            _cs_cell[0] = _compute_stats     # wire into combo callback

            # Re-bind with stats updater now that _cs_cell is wired
            def _on_combo_change2(event, var=var, lbl=dot_lbl, cs=_compute_stats):
                val = var.get()
                lbl.config(fg=GREEN_UI if val and val not in ('(not detected)', '(None)') else RED_UI)
                cs(val)
                # Rebuild working rows (debounced so the UI does not freeze)
                self._schedule_rebuild()
            combo.bind('<<ComboboxSelected>>', _on_combo_change2)

            tk.Label(self.col_frame, textvariable=stats_var, font=FONT_SMALL,
                     bg=DARK_CARD, fg=ACCENT2, width=26, anchor="w").grid(
                row=r, column=2, sticky="w")

            # Invert checkbox — G channels and throttle
            if ch in ("g_lat", "g_long", "throttle"):
                if ch == "g_lat":      ivar = self.invert_glat
                elif ch == "g_long":   ivar = self.invert_glong
                else:                  ivar = self.invert_throttle
                tk.Checkbutton(
                    self.col_frame, variable=ivar,
                    bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
                    activeforeground=ACCENT2, selectcolor=DARK_PANEL,
                    relief="flat", bd=0, cursor="hand2").grid(
                    row=r, column=3, sticky="w", padx=(4,0))

            # Smooth checkbox — restore saved state, or apply default if first load
            _default_val = self._default_smooth.get(ch, False)
            saved_val = _saved_smooth.get(ch, _default_val)
            if ch not in self.smooth_vars:
                self.smooth_vars[ch] = tk.BooleanVar(value=saved_val)
            else:
                self.smooth_vars[ch].set(saved_val)
            tk.Checkbutton(
                self.col_frame, variable=self.smooth_vars[ch],
                bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
                activeforeground=ACCENT2, selectcolor=DARK_PANEL,
                relief="flat", bd=0, cursor="hand2").grid(
                row=r, column=4, sticky="w", padx=(4,0))

        self.col_frame.columnconfigure(1, weight=1)

    # ── Actions ────────────────────────────────────────────────────────────────
    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select ECU Log File",
            filetypes=[("Log files", "*.csv *.vbo *.xrk *.drk *.xrz"),
                       ("AIM XRK/DRK/XRZ", "*.xrk *.drk *.xrz *.XRK *.DRK"),
                       ("CSV files", "*.csv *.CSV"),
                       ("VBO files", "*.vbo *.VBO"),
                       ("All files", "*.*")])
        if not path:
            return
        self.file_path.set(path)
        # Auto-set output dir to same folder
        if not self.output_dir.get():
            self.output_dir.set(os.path.dirname(path))
        self._load_file(path)

    def _set_output_name_from_log(self, path):
        """Name the output video after the log: "Taupo R1.csv" -> "Taupo R1.mp4".

        Only replaces a name this app derived (or the default), so if the name has
        been typed by hand it survives loading another log.
        """
        try:
            # Split on both separators: a path can carry the other platform's
            # slashes (pasted, or a log path saved on another machine).
            base = str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            stem = os.path.splitext(base)[0].strip()
        except Exception:
            return
        if not stem:
            return
        cur = (self.output_name.get() or "").strip()
        if cur and cur != getattr(self, "_auto_output_name", "ecu_overlay.mp4"):
            return
        self._auto_output_name = f"{stem}.mp4"
        self.output_name.set(self._auto_output_name)

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_dir.set(path)


    def _load_xrk(self, path):
        """Load AIM XRK/DRK/XRZ file via MatLabXRK DLL."""
        import threading
        self.status_var.set("Opening AIM file…")
        self._loading_start("Reading AIM file")
        self._working_rows = None

        # Set DLL path from UI field — accept full path to DLL or folder
        dll_path = self.dll_path_var.get().strip()
        if dll_path:
            if os.path.isfile(dll_path):
                # Full path to DLL — add its folder AND copy the DLL name hint
                os.environ["XRK_DLL_PATH"] = os.path.dirname(dll_path)
                # Also put the exact file in the search list by copying to script dir
                # (simplest approach: just set hint_dir to its folder)
            elif os.path.isdir(dll_path):
                os.environ["XRK_DLL_PATH"] = dll_path

        def _worker():
            try:
                import xrk_reader as _xr
                _hint = os.path.dirname(path)
                if dll_path and os.path.isfile(dll_path):
                    _hint = os.path.dirname(dll_path)
                # Reset cached DLL so new path is picked up
                _xr._dll = None
                def _p(p, m):
                    def _u():
                        self.status_var.set(f"AIM: {m} ({p}%)")
                        self._loading_msg_set(f"{m} {p}%" if m else f"Reading AIM file {p}%")
                    self.after(0, _u)
                result = _xr.read_xrk(path, progress_cb=_p)

                df   = result["df"]
                meta = result["meta"]

                self.df       = df
                self.ts_col   = result["ts_col"]
                self._aim_laps = meta.get("laps", [])

                cols = list(df.columns)

                def _ui():
                    self.status_var.set(
                        f"Loaded XRK: {len(df)} rows, {len(cols)} channels  "
                        f"| {meta.get('vehicle','')} | {meta.get('track','')} "
                        f"| {meta.get('date','')} | {meta.get('n_laps',0)} laps")

                    # Time range from the data
                    _dur = float(pd.to_numeric(df[self.ts_col], errors='coerce').max())
                    self.t_start_var.set("0.0")
                    self.t_end_var.set(f"{_dur:.1f}")
                    if hasattr(self, 'max_t_label'):
                        self.max_t_label.set(f"max {_dur:.1f}s")

                    # Sample rate
                    _d = pd.to_numeric(df[self.ts_col], errors='coerce').diff()
                    _d = _d[_d > 0]
                    _dt = float(_d.median()) if len(_d) else 0.1
                    self._data_fps = max(1, min(60, int(round(1.0 / _dt)))) if _dt > 0 else 10

                    # Build the Channel Mapping table (auto-detected) and use it
                    _map = auto_detect_channels(cols)
                    self._build_col_mapping(cols, preset=_map)
                    _col_map = {ch: c for ch, c in _map.items() if c}
                    self._prepare_working_df(_col_map)

                    # GPS columns for lap detection (strict: never accelerations)
                    for _kws, _var in ((['gps_latitude', 'latitude', 'lat_deg'], self.lap_lat_col),
                                       (['gps_longitude', 'longitude', 'lon_deg', 'lng'], self.lap_lon_col)):
                        if _var.get():
                            continue
                        for _kw in _kws:
                            if _var.get():
                                break
                            for c in cols:
                                _cl = c.lower()
                                if (_kw in _cl and not any(x in _cl for x in _GPS_POS_EXCLUDE)
                                        and not _var.get()):
                                    _var.set(c)
                    if hasattr(self, '_lat_col_cb'):
                        self._lat_col_cb['values'] = cols
                        self._lon_col_cb['values'] = cols

                    # AIM beacon laps drive lap detection
                    n_laps = meta.get("n_laps", 0)
                    if n_laps and hasattr(self, '_aim_info_lbl'):
                        self._aim_info_lbl.config(
                            text=f"{n_laps} laps from AIM beacon markers.")
                    self._update_lap_display()
                    self._loading_stop()
                self.after(0, _ui)
            except Exception as e:
                self.after(0, lambda: (self._loading_stop("load failed"),
                                       self.status_var.set(f"AIM load error: {e}")))
                import traceback; traceback.print_exc()

        threading.Thread(target=_worker, daemon=True).start()

    def _load_vbo(self, path):
        """Load a VBO file and populate the app."""
        try:
            try:
                import vbo_reader as _vbo_mod
            except ImportError:
                self.after(0, lambda: self.status_var.set(
                    "vbo_reader.py not found — place it in the same folder as ecu_overlay_app.py"))
                return
            self.after(0, lambda: (self._loading_start("Reading VBO"),
                                   self.status_var.set("Loading VBO…")))
            df, meta = _vbo_mod.read_vbo(path)

            # Map VBO df to app expectations
            # VBO uses lat/lon directly; create GPS columns in df
            df['GPS_Latitude']  = df['lat']
            df['GPS_Longitude'] = df['lon']
            df['GPS_Speed']     = df['speed']

            # ts column — already in seconds
            self.ts_col = 'ts'

            # Store
            self.df             = df
            self._working_rows  = df.copy()
            self._aim_laps      = meta.get('laps', [])
            self._vbo_meta      = meta

            # Sample rate
            diffs = df['ts'].diff().dropna()
            diffs = diffs[diffs > 0]
            dt = float(diffs.median()) if len(diffs) else 0.1
            fps_resample = min(60, max(1, round(1.0 / dt)))
            self._data_fps = fps_resample
            # VBO often has variable sample rate — clamp to avoid 0 fps
            if fps_resample < 1: fps_resample = 1

            # Populate UI on main thread
            self.after(0, lambda: (self._on_vbo_loaded(path, df, meta, fps_resample),
                                   self._loading_stop()))

        except Exception as e:
            import traceback
            msg = f"VBO load error: {e}\n{traceback.format_exc()}"
            self.after(0, lambda m=msg: (self._loading_stop("load failed"),
                                         self.status_var.set(m[:200])))

    def _on_vbo_loaded(self, path, df, meta, fps_resample):
        """Called on main thread after VBO load completes."""
        cols = list(df.columns)

        # Update file info
        self.file_path.set(path)
        dur = float(df['ts'].max())
        self.t_start_var.set("0.0")
        self.t_end_var.set(f"{dur:.1f}")
        if hasattr(self, 'max_t_label'):
            self.max_t_label.set(f"max {dur:.1f}s")

        # Set ts_col before building mapping so combo default is correct
        self.ts_col = 'ts'

        # Rebuild channel mapping UI for VBO columns. Newer VBOs carry real
        # rpm / throttle / brake / gear — map them when read_vbo found them.
        _ec = meta.get('engine_channels', {}) if isinstance(meta, dict) else {}
        vbo_map = {
            'speed':    'speed',
            'g_lat':    'g_lat',
            'g_long':   'g_long',
            'rpm':      'rpm'      if _ec.get('rpm')      else None,
            'throttle': 'throttle' if _ec.get('throttle') else None,
            'brake':    'brake'    if _ec.get('brake')    else None,
            'gear':     'gear'     if _ec.get('gear')     else None,
        }
        self._build_col_mapping(cols, preset=vbo_map)

        # Prepare working df using VBO channel mapping
        self._prepare_working_df(vbo_map)

        # GPS columns — VBO uses 'lat'/'lon'
        self.lap_lat_col.set('lat')
        self.lap_lon_col.set('lon')
        if hasattr(self, '_lat_col_cb'):
            self._lat_col_cb['values'] = cols
            self._lon_col_cb['values'] = cols

        # File info label
        if hasattr(self, '_file_info_var'):
            sn = self.style_var.get()
            res = '1920×1080' if sn in ('Style 7','Style 8') else ('1920×365' if sn == 'Dash 7 (HUD)' else ('1920×405' if sn == 'Dash 9 (Triple)' else ('2560×750' if sn == 'Dash 10 (Arc)' else '1920×750')))
            self._file_info_var.set(
                f"data: {fps_resample} fps  ·  render: {self.fps_var.get() if hasattr(self,'fps_var') else fps_resample} fps  ·  "
                f"{len(df):,} rows  ·  {dur:.1f}s  ·  "
                f"Driver: {meta.get('driver','')}  ·  Track: {meta.get('track','')}  ·  "
                f"Best: {meta.get('best_lap','')}  ·  output: {res} (auto)")

        # AIM beacons info (use VBO lap timing)
        n_laps = len(self._aim_laps)
        if hasattr(self, '_aim_info_lbl') and n_laps:
            self._aim_info_lbl.config(
                text=f"{n_laps} laps from VBO [laptiming]  (best: {meta.get('best_lap','')})")

        # Auto-populate S/F from lap timing, then update display
        self._auto_detect_sf()
        self._update_lap_display()

        self.status_var.set(
            f"Loaded VBO: {meta.get('driver','')} @ {meta.get('track','')}  "
            f"— {n_laps} laps, best {meta.get('best_lap','')}")

    def _load_file(self, path):
        self.status_var.set("Loading file…")
        self._loading_start("Loading file")
        self.update()
        _ext = os.path.splitext(path)[1].lower()
        if _ext in ('.xrk', '.drk', '.xrz'):
            self._load_xrk(path)
            return
        if _ext == '.vbo':
            import threading
            threading.Thread(target=self._load_vbo, args=(path,), daemon=True).start()
            return
        try:
            df, ts_col, cols = load_file(path)
            self.df     = df
            self.ts_col = ts_col
            self.all_cols = cols

            # Prepare normalised working columns
            col_map_raw = auto_detect_channels(cols)
            self._prepare_working_df(col_map_raw)

            t_max = float(df[ts_col].max())
            t0    = float(df[ts_col].min())
            self.t_start_var.set(f"{t0:.1f}")
            self.t_end_var.set(f"{t_max:.1f}")
            # Parse AIM metadata for lap beacons
            _aim_meta = parse_aim_metadata(path)
            self._aim_laps = _aim_meta.get('laps', [])
            if self._aim_laps:
                n_laps = len(self._aim_laps)
                self.status_var.set(f"Loaded - {n_laps} laps from beacon markers")
                if self.lap_mode_var.get() == 'Auto':
                    pass  # Auto will use beacons
                if hasattr(self, '_aim_info_lbl'):
                    self._aim_info_lbl.config(
                        text=f"{n_laps} laps from beacon markers.")
            # Auto-detect GPS lat/lon columns and populate dropdowns
            self.lap_lat_col.set("")
            self.lap_lon_col.set("")
            # Priority order: most specific first to avoid GPS_LatAcc matching before GPS_Latitude
            _lat_keywords = ['gps_latitude', 'latitude', 'lat_deg']
            _lon_keywords = ['gps_longitude', 'longitude', 'lon_deg', 'lng']
            for _kw in _lat_keywords:
                if self.lap_lat_col.get(): break
                for c in cols:
                    if (_kw in c.lower() and not any(x in c.lower() for x in _GPS_POS_EXCLUDE)
                            and not self.lap_lat_col.get()):
                        self.lap_lat_col.set(c)
            for _kw in _lon_keywords:
                if self.lap_lon_col.get(): break
                for c in cols:
                    if (_kw in c.lower() and not any(x in c.lower() for x in _GPS_POS_EXCLUDE)
                            and not self.lap_lon_col.get()):
                        self.lap_lon_col.set(c)
            if hasattr(self, '_lat_col_cb'):
                self._lat_col_cb['values'] = cols
                self._lon_col_cb['values'] = cols
            self.max_t_label.set(f"Max: {t_max:.1f} s")

            self._build_col_mapping(cols)
            self._update_lap_display()
            self._loading_stop()
            self.status_var.set(
                f"Loaded {len(df):,} rows  ·  {t_max:.1f}s  ·  {len(cols)} channels")
        except Exception as e:
            self._loading_stop("load failed")
            messagebox.showerror("Load Error", str(e))
            self.status_var.set("Error loading file.")

    def _prepare_working_df(self, col_map):
        """Build a clean normalised dataframe with standard column names."""
        if self.df is None:
            return
        df = self.df.copy()

        # Sanitise timestamp column — drop NaN, sort, ensure monotonic
        ts_vals = pd.to_numeric(df[self.ts_col], errors='coerce')
        df[self.ts_col] = ts_vals
        df = df.dropna(subset=[self.ts_col])
        df = df.sort_values(self.ts_col).reset_index(drop=True)
        df = df.drop_duplicates(subset=[self.ts_col])  # remove duplicate timestamps

        if len(df) < 2:
            self.status_var.set("Error: not enough valid timestamp rows to resample.")
            return

        t0 = float(df[self.ts_col].min())
        t1 = float(df[self.ts_col].max())

        import math as _math
        if not (_math.isfinite(t0) and _math.isfinite(t1) and t1 > t0):
            self.status_var.set(
                f"Error: invalid time range ({t0:.3f}..{t1:.3f}). "
                "Check the Time channel selection.")
            return

        # Auto-detect sample rate from data
        _dt = pd.to_numeric(df[self.ts_col], errors='coerce').diff().median()
        fps_resample = int(round(1.0 / _dt)) if _dt and _dt > 0 else 30
        fps_resample = max(1, min(fps_resample, 60))   # cap at 60fps
        self._data_fps = fps_resample
        # Set fps_var to match data rate (user can override)
        if hasattr(self, "_file_info_var"):
            _style_now = self.style_var.get() if hasattr(self, "style_var") else ""
            _fw, _fh = _R.style_frame_size(_R.STYLES.get(_style_now))
            _res = f"{_fw}×{_fh}"
            _sel_fps_now = self.fps_var.get() if hasattr(self,"fps_var") else fps_resample
            self._file_info_var.set(
                f"data: {fps_resample} fps  ·  render: {_sel_fps_now} fps  ·  "
                f"{len(df):,} rows  ·  {t1-t0:.1f}s  ·  output: {_res}")
        # Use render fps for the working grid — eliminates FPS mismatch drift
        try:
            _render_fps = int(self.fps_var.get())
        except Exception:
            _render_fps = fps_resample
        # Integer frame indexing avoids np.arange float accumulation
        _frame_count = int(round((t1 - t0) * _render_fps))
        tt = t0 + np.arange(_frame_count) / _render_fps
        if len(tt) == 0:
            tt = np.array([t0, t1])
        rows = pd.DataFrame({'ts': tt})
        # Per-channel expected ranges for outlier clipping
        _ch_ranges = {
            'rpm':      (0,    20000),
            'throttle': (0,    100),
            'speed':    (0,    400),
            'g_lat':    (-5,   5),
            'g_long':   (-5,   5),
        }
        for ch in ['rpm','throttle','speed','g_lat','g_long']:
            src2 = col_map.get(ch)
            if src2 and src2 in df.columns:
                vals = pd.to_numeric(df[src2], errors='coerce')
                lo, hi = _ch_ranges[ch]
                # Clip hard limits
                vals = vals.clip(lo, hi)
                # Despike: replace values that jump >50% of range in one step
                _rng = hi - lo
                _spike_thresh = _rng * 0.4
                _diff = vals.diff().abs()
                vals[_diff > _spike_thresh] = np.nan
                vals = vals.interpolate(method='linear').ffill().bfill().fillna(0)
                rows[ch] = np.interp(tt, df[self.ts_col].values, vals.values)
            else:
                rows[ch] = 0.0
        # Brake: raw channel, normalised to 0-100% by peak in session
        brake_src = col_map.get('brake')
        if brake_src and brake_src in df.columns:
            bvals = pd.to_numeric(df[brake_src], errors='coerce').fillna(0).values
            braw = pd.to_numeric(df[brake_src], errors='coerce')
            # Despike brake: massive jumps are sensor noise
            _bdiff = braw.diff().abs()
            _bspike = _bdiff > (_bdiff.quantile(0.99) * 3)
            braw[_bspike] = np.nan
            braw = braw.interpolate(method='linear').ffill().bfill().fillna(0)
            braw = braw.clip(braw.quantile(0.005), braw.quantile(0.995))
            bvals = braw.values
            bvals = np.interp(tt, df[self.ts_col].values, bvals)
            # Auto-detect if this looks like a G-force channel (range < 5)
            # vs a pressure channel (range >> 5)
            brange = bvals.max() - bvals.min()
            if brange < 5.0:
                # G-force channel: only use negative values as braking
                # Clip positives to 0, negate, so decel becomes positive
                bvals  = np.clip(-bvals, 0, None)   # -(-1.3) = 1.3, -(0.8) = 0 (clipped)
                bpeak  = bvals.max()
                rows['brake'] = np.clip(bvals / bpeak * 100, 0, 100) if bpeak > 0 else np.zeros(len(bvals))
            else:
                # Pressure channel: shift baseline so min = 0, normalise to peak
                bmin  = bvals.min()
                bvals = bvals - bmin
                bpeak = bvals.max()
                rows['brake'] = np.clip(bvals / bpeak * 100, 0, 100) if bpeak > 0 else np.zeros(len(bvals))
        else:
            rows['brake'] = np.nan   # sentinel: bar shows empty
        # Gear: nearest neighbour
        src = col_map.get('gear')
        if src and src in df.columns:
            vals = pd.to_numeric(df[src], errors='coerce').fillna(0).values
            # Gear: nearest-neighbour not linear — avoid fractional gears
            _gear_idx = np.searchsorted(df[self.ts_col].values, tt).clip(0, len(vals)-1)
            rows['gear'] = np.round(vals[_gear_idx]).astype(int)
        else:
            rows['gear'] = 0

        # Clamp gear: values > 8 are typically sensor no-signal sentinels
        rows['gear'] = rows['gear'].clip(0, 8)

        # GPS lat/lon — needed for GPS lap detection
        _lat_col = self.lap_lat_col.get() if hasattr(self, 'lap_lat_col') else ''
        _lon_col = self.lap_lon_col.get() if hasattr(self, 'lap_lon_col') else ''
        # The Channel Mapping selections win, so a wrong guess can be overridden
        _map_lat = (col_map or {}).get('lat')
        _map_lon = (col_map or {}).get('lon')
        if _map_lat and _map_lat in df.columns:
            _lat_col = _map_lat
            try: self.lap_lat_col.set(_map_lat)      # keep GPS lap detection in sync
            except Exception: pass
        if _map_lon and _map_lon in df.columns:
            _lon_col = _map_lon
            try: self.lap_lon_col.set(_map_lon)
            except Exception: pass
        # Otherwise auto-find, skipping acceleration / heading / accuracy channels
        if not _lat_col:
            for _kw in ['gps_latitude', 'latitude', 'lat_deg']:
                for _c in df.columns:
                    _cl = _c.lower()
                    if _kw in _cl and not any(x in _cl for x in _GPS_POS_EXCLUDE):
                        _lat_col = _c; break
                if _lat_col: break
        if not _lon_col:
            for _kw in ['gps_longitude', 'longitude', 'lon_deg', 'lng']:
                for _c in df.columns:
                    _cl = _c.lower()
                    if _kw in _cl and not any(x in _cl for x in _GPS_POS_EXCLUDE):
                        _lon_col = _c; break
                if _lon_col: break
        # Convert whatever form the coordinates are in (degrees / radians /
        # decimal minutes / scaled integers) to decimal degrees, and say what
        # was found so a bad channel choice is obvious.
        for _nm, _cc, _kind in (('lat', _lat_col, 'lat'), ('lon', _lon_col, 'lon')):
            if not _cc or _cc not in df.columns:
                continue
            _raw = pd.to_numeric(df[_cc], errors='coerce')
            _deg, _why = normalise_coordinates(_raw, _kind)
            if _deg is None:
                print(f"GPS {_nm}: '{_cc}' rejected - {_why}", flush=True)
                if _nm == 'lat': _lat_col = ''
                else: _lon_col = ''
            else:
                _f = np.asarray(_deg, dtype=float)
                _f = _f[np.isfinite(_f)]
                print(f"GPS {_nm}: '{_cc}' {_why}  range "
                      f"{np.nanmin(_f):.5f}..{np.nanmax(_f):.5f} deg", flush=True)
                _dser = pd.Series(np.asarray(_deg, dtype=float), index=df.index)
                if _nm == 'lat':
                    _lat_deg = _dser
                else:
                    _lon_deg = _dser
        if _lat_col and _lat_col in df.columns:
            _lv = _lat_deg.interpolate().bfill().ffill().values
            rows['lat'] = np.interp(tt, df[self.ts_col].values, _lv)
        else:
            rows['lat'] = np.full(len(tt), np.nan)
        if _lon_col and _lon_col in df.columns:
            _lv = _lon_deg.interpolate().bfill().ffill().values
            rows['lon'] = np.interp(tt, df[self.ts_col].values, _lv)
        else:
            rows['lon'] = np.full(len(tt), np.nan)

        # ── Optional channels A & B ─────────────────────
        for _ck, _cv in [('chanA', self.chanA_col), ('chanB', self.chanB_col)]:
            _src = _cv.get() if hasattr(_cv, 'get') else None
            if _src and _src not in ('(None)', '(not detected)', '') and _src in df.columns:
                _vv = pd.to_numeric(df[_src], errors='coerce')
                _vv = _vv.interpolate(method='linear').ffill().bfill().fillna(0)
                rows[_ck] = np.interp(tt, df[self.ts_col].values, _vv.values)
            else:
                rows[_ck] = np.nan   # sentinel: not selected → not displayed

        self._working_rows = rows


    def _update_lap_display(self):
        """Update lap display using current method."""
        if not hasattr(self, '_working_rows') or self._working_rows is None:
            self.lap_text.set("Load a file to detect laps.")
            return
        try:
            laps = self._compute_laps()
            if not laps:
                self.lap_text.set("No laps detected.")
                return
            best_lt = min(lt for _,_,lt in laps)
            mode = self.lap_mode_var.get()
            _txt = self._format_lap_table(laps, best_lt, mode)
            self.lap_text.set(_txt)
            if hasattr(self.lap_info, "config") and self.lap_info.winfo_class() == "Text":
                self.lap_info.config(state="normal")
                self.lap_info.delete("1.0", "end")
                self.lap_info.insert("1.0", _txt)
                self.lap_info.config(state="disabled")
        except Exception as e:
            _err = f"Detection error: {e}"
            self.lap_text.set(_err)
            if hasattr(self.lap_info, "config") and self.lap_info.winfo_class() == "Text":
                self.lap_info.config(state="normal")
                self.lap_info.delete("1.0", "end")
                self.lap_info.insert("1.0", _err)
                self.lap_info.config(state="disabled")


    def _set_arc_track_path(self, rows):
        """For Dash 10 (Arc): supply the full-lap GPS path to the renderer for
        its mini track map. Safe no-op if no GPS columns are available."""
        try:
            if not (_R.STYLES.get(self.style_var.get()) or {}).get("style32_layout"):
                return
            latc = self.lap_lat_col.get(); lonc = self.lap_lon_col.get()
            if latc and lonc and latc in rows.columns and lonc in rows.columns:
                spd = rows['speed'].tolist() if 'speed' in rows.columns else None
                _R.set_track_path(lat=rows[latc].tolist(),
                                  lon=rows[lonc].tolist(), speed=spd)
            else:
                _R.set_track_path(None, None, None)
        except Exception:
            _R.set_track_path(None, None, None)

    def _colour_label_to_col(self, label):
        """Map a friendly colour-channel label back to a dataframe column name."""
        _nice = {"RPM": "rpm", "Speed": "speed", "Gear": "gear",
                 "Throttle": "throttle", "Brake": "brake",
                 "Lat G": "g_lat", "Lon G": "g_long"}
        ch = _nice.get(label)
        try:
            if ch and ch in self.col_vars and self.col_vars[ch].get() not in ("(None)", ""):
                return self.col_vars[ch].get()
            # extra channels A/B matched by their label
            if label == (self.chanA_label.get() or "A") and self.chanA_col.get() not in ("(None)", ""):
                return self.chanA_col.get()
            if label == (self.chanB_label.get() or "B") and self.chanB_col.get() not in ("(None)", ""):
                return self.chanB_col.get()
        except Exception:
            pass
        return None

    def _gtrace_colour_options(self):
        """Colour choices for the G-trace/lap-trace widgets: Speed, B&W, then any
        mapped data channels from the loaded file."""
        opts = ["Speed colour", "Black & white"]
        try:
            mapped = [ch for ch, var in self.col_vars.items()
                      if var.get() and var.get() not in ("(None)", "")]
        except Exception:
            mapped = []
        _nice = {"rpm": "RPM", "speed": "Speed", "gear": "Gear",
                 "throttle": "Throttle", "brake": "Brake",
                 "g_lat": "Lat G", "g_long": "Lon G"}
        for ch in mapped:
            opts.append("Channel: " + _nice.get(ch, ch))
        try:
            if self.chanA_col.get() not in ("(None)", ""):
                opts.append("Channel: " + (self.chanA_label.get() or "A"))
            if self.chanB_col.get() not in ("(None)", ""):
                opts.append("Channel: " + (self.chanB_label.get() or "B"))
        except Exception:
            pass
        return opts

    def _refresh_gtrace_colours(self):
        """Repopulate the colour dropdown after a file loads / mapping changes."""
        try:
            cb = getattr(self, "_gtrace_colour_cb", None)
            if cb is not None:
                cb["values"] = self._gtrace_colour_options()
        except Exception:
            pass


    def _compute_laps(self):
        """Return lap list based on current mode setting."""
        _np = np
        mode = self.lap_mode_var.get()
        aim_laps = getattr(self, '_aim_laps', [])
        df = self.df
        wr = self._working_rows

        if mode == 'Lap Beacons':
            if not aim_laps:
                raise ValueError("No beacon data found. Load a data file with lap beacon markers (AIM CSV, VBO, etc.).")
            offset = float(self.lap_offset_var.get() or 0)
            return [(st+offset, et+offset, lt) for st,et,lt in aim_laps]

        elif mode == 'GPS Crossing':
            lat_col = self.lap_lat_col.get()
            lon_col = self.lap_lon_col.get()
            if not lat_col or not lon_col:
                raise ValueError("No GPS columns detected. Load a file with GPS data.")
            try: sf_lat = float(self.lap_sf_lat.get())
            except: raise ValueError("Enter S/F Latitude.")
            try: sf_lon = float(self.lap_sf_lon.get())
            except: raise ValueError("Enter S/F Longitude.")
            try: min_gap = float(self.lap_est_time.get())
            except: min_gap = 30.0
            # Use the already-monotonic df timestamps directly (load_file already
            # reconstructed them — do NOT re-apply rollover offsets)
            _mono_ts = pd.to_numeric(df[self.ts_col], errors='coerce').values
            lat_v    = pd.to_numeric(df[lat_col], errors='coerce').values
            lon_v    = pd.to_numeric(df[lon_col], errors='coerce').values
            _valid   = ~(_np.isnan(lat_v) | _np.isnan(lon_v) | _np.isnan(_mono_ts))
            # Run detection on original GPS samples (no interpolation artefacts)
            laps   = detect_laps_gps(_mono_ts[_valid], lat_v[_valid], lon_v[_valid],
                                      sf_lat, sf_lon, min_gap_s=min_gap)
            offset = float(self.lap_offset_var.get() or 0)
            return [(st+offset, et+offset, lt) for st,et,lt in laps]

        elif mode == 'Point-to-Point (Stage)':
            lat_col = self.lap_lat_col.get()
            lon_col = self.lap_lon_col.get()
            if not lat_col or not lon_col:
                raise ValueError("No GPS columns detected. Load a file with GPS data.")
            try:
                s_lat = float(self.stage_start_lat.get()); s_lon = float(self.stage_start_lon.get())
            except: raise ValueError("Enter the start line Lat/Lon.")
            try:
                f_lat = float(self.stage_fin_lat.get()); f_lon = float(self.stage_fin_lon.get())
            except: raise ValueError("Enter the finish line Lat/Lon.")
            try: rad = float(self.stage_radius.get())
            except: rad = 50.0
            _mono_ts = pd.to_numeric(df[self.ts_col], errors='coerce').values
            lat_v    = pd.to_numeric(df[lat_col], errors='coerce').values
            lon_v    = pd.to_numeric(df[lon_col], errors='coerce').values
            _valid   = ~(_np.isnan(lat_v) | _np.isnan(lon_v) | _np.isnan(_mono_ts))
            laps = detect_stage(_mono_ts[_valid], lat_v[_valid], lon_v[_valid],
                                s_lat, s_lon, f_lat, f_lon, radius_m=rad)
            offset = float(self.lap_offset_var.get() or 0)
            return [(st+offset, et+offset, lt) for st,et,lt in laps]

        else:  # Speed Peaks
            if wr is None:
                raise ValueError("No data loaded.")
            try: min_spd = float(self.lap_min_speed.get())
            except: min_spd = 150.0
            try: min_gap = float(self.lap_est_time.get())
            except: min_gap = 30.0
            offset = float(self.lap_offset_var.get() or 0)
            laps = _R.find_lap_events(wr['speed'].values, wr['ts'].values,
                                       min_spd=min_spd, min_gap=min_gap)
            if not laps:
                raise ValueError(f"No laps found above {min_spd}kph with >{min_gap}s gap. "
                                  "Try lowering Min speed or Est. lap time.")
            return [(st+offset, et+offset, lt) for st,et,lt in laps]

    def _auto_detect_sf(self):
        """Auto-detect S/F line from AIM beacons or GPS speed minima."""
        _np = np
        try:
            aim_laps = getattr(self, '_aim_laps', [])
            lat_col = self.lap_lat_col.get()
            lon_col = self.lap_lon_col.get()
            df = self.df
            wr = self._working_rows
            if wr is None or not lat_col or not lon_col:
                self.lap_text.set("Select Lat/Lon columns first, then auto-detect.")
                return
            _raw_ts = pd.to_numeric(df[self.ts_col], errors='coerce').values.copy().astype(float)
            _off2 = 0.0
            for _ii in range(1, len(_raw_ts)):
                if _raw_ts[_ii] < _raw_ts[_ii-1] - 0.5: _off2 += _raw_ts[_ii-1]
                _raw_ts[_ii] += _off2
            lat_v  = pd.to_numeric(df[lat_col], errors='coerce').values
            lon_v  = pd.to_numeric(df[lon_col], errors='coerce').values
            ts = wr['ts'].values
            _valid = ~(_np.isnan(lat_v) | _np.isnan(lon_v))
            lat_i = _np.interp(ts, _raw_ts[_valid], lat_v[_valid])
            lon_i = _np.interp(ts, _raw_ts[_valid], lon_v[_valid])

            # GPS-only: find the median position at crossing points
            # Uses the signed-distance GPS lap detection against current SF coordinate
            # (bootstrapped from any existing SF coordinate, or track centre)
            try:
                _cur_lat = float(self.lap_sf_lat.get())
                _cur_lon = float(self.lap_sf_lon.get())
            except Exception:
                self.status_var.set("Enter an approximate S/F coordinate first.")
                return
            try:
                _detected = detect_laps_gps(ts, lat_i, lon_i, _cur_lat, _cur_lon)
                sf_lats = []; sf_lons = []
                for _st, _et, _lt in _detected:
                    _idx = int(_np.argmin(_np.abs(ts - _et)))
                    sf_lats.append(float(lat_i[_idx]))
                    sf_lons.append(float(lon_i[_idx]))
                if not sf_lats:
                    self.status_var.set("No GPS crossings found near current coordinate.")
                    return
                sf_lat = float(_np.mean(sf_lats))
            except Exception as _e:
                self.status_var.set(f"GPS S/F detection failed: {_e}")
                return
            if False:  # dead branch placeholder
                sf_lat = 0.0
                sf_lon = float(_np.mean(lon_i[mins]))

            self.lap_sf_lat.set(f"{sf_lat:.6f}")
            self.lap_sf_lon.set(f"{sf_lon:.6f}")
            self.lap_text.set(f"S/F set to {sf_lat:.6f}, {sf_lon:.6f}")
            self._update_lap_display()
        except Exception as e:
            self.lap_text.set(f"Auto-detect error: {e}")

    def _collect_inputs(self):
        """Collect all input widgets for locking during render."""
        self._all_inputs = []
        def _recurse(w):
            wclass = w.winfo_class()
            if wclass in ('Entry', 'TCombobox', 'Button'):
                self._all_inputs.append(w)
            for child in w.winfo_children():
                _recurse(child)
        _recurse(self)

    def _lock_ui(self):
        self._collect_inputs()
        for w in self._all_inputs:
            try:
                if w != self.cancel_btn:
                    w.config(state="disabled")
            except Exception:
                pass
        self.cancel_btn.config(state="normal", fg=RED_UI)
        self.gen_btn.config(state="disabled", fg=TEXT_SEC)

    def _unlock_ui(self):
        for w in self._all_inputs:
            try:
                wclass = w.winfo_class()
                if wclass == 'TCombobox':
                    w.config(state="readonly")
                else:
                    w.config(state="normal")
            except Exception:
                pass
        self.cancel_btn.config(state="disabled", fg=TEXT_SEC)
        self.gen_btn.config(state="normal", fg=ACCENT)

    def _cancel_render(self):
        self._cancel_flag = True
        self.status_var.set("Cancelling… please wait for current frame to finish.")
        self.cancel_btn.config(state="disabled", fg=TEXT_SEC)


    def _preview_frame(self):
        """Render a single frame at t_start and display it in a popup window."""
        if not DEPS_OK:
            messagebox.showerror("Missing Dependencies", f"Cannot render: {MISSING_DEP}")
            return
        if self.df is None:
            messagebox.showwarning("No File", "Please load a log file first.")
            return

        # Rebuild working data with current settings
        _T = self._t0()
        col_map = {ch: var.get() for ch, var in self.col_vars.items()
                   if var.get() not in ("(not detected)", "(None)", "")}
        self._prepare_working_df(col_map)
        _T = self._tlog("preview: prepare working data", _T)

        try:
            t_preview = float(self.t_start_var.get())
        except ValueError:
            t_preview = float(self._working_rows['ts'].min())

        try:
            g_trail = float(self.g_trail_secs.get())
        except ValueError:
            g_trail = 5.0

        try:
            lap_min_spd  = float(self.lap_min_speed.get())
            lap_est_time = float(self.lap_est_time.get())
            lap_offset   = float(self.lap_offset_var.get())
        except ValueError:
            lap_min_spd = 200.0; lap_est_time = 60.0; lap_offset = 0.0

        _style_now = self.style_var.get()

        # Mosaic preview — composite all style cells at reduced resolution
        _style2_now = self.style2_var.get() if hasattr(self,"style2_var") else "None"
        if _style2_now and _style2_now != "None":
            self._preview_dual()
            return

        resolution = _R.style_frame_size(_R.STYLES.get(_style_now))
        out_w, out_h = resolution
        _nat_h = int(_R.H_VID * (out_w / _R.W_VID))
        if out_h < _nat_h - 2:
            out_h_actual = out_h
            scale = out_h_actual / _R.H_VID
        else:
            scale = out_w / _R.W_VID
            out_h_actual = int(_R.H_VID * scale)

        P = self._apply_backdrop_to_P(_R.STYLES.get(_style_now, _R.STYLES[_R.DASH_NAMES[0]]))

        import math as _math
        rows = self._working_rows
        # Safety net: ensure selected channel columns are present in working rows
        try:
            _need_chan = (self.chanA_col.get() not in ('(None)','(not detected)','') or
                          self.chanB_col.get() not in ('(None)','(not detected)',''))
            if _need_chan and (rows is None or 'chanA' not in rows.columns):
                _cmap = {ch2: v2.get() for ch2, v2 in self.col_vars.items()
                         if v2.get() not in ('(not detected)', '(None)', '')}
                self._prepare_working_df(_cmap)
                rows = self._working_rows
        except Exception:
            pass
        # Apply inversions
        if self.invert_glat.get():
            rows = rows.copy(); rows["g_lat"] = -rows["g_lat"]
        if self.invert_glong.get():
            rows = rows.copy(); rows["g_long"] = -rows["g_long"]
        if self.invert_throttle.get():
            rows = rows.copy(); rows["throttle"] = 100.0 - rows["throttle"].clip(0,100)

        try:
            laps = self._compute_laps()
        except Exception:
            laps = []
        _T = self._tlog("preview: lap detection", _T)
        self._set_arc_track_path(rows)
        _T = self._tlog("preview: arc track path", _T)

        peak_rpm = float(rows['rpm'].max())
        rpm_max  = int(_math.ceil(peak_rpm/1000)*1000)
        rpm_max  = max(rpm_max, 6000)
        _peak_spd = float(rows['speed'].max()) if 'speed' in rows.columns else 0.0
        speed_max = max(60, int(_math.ceil(_peak_spd/10.0)*10))

        _R._GAUGE_BG_CACHE.clear()
        GS = int(460*scale); GX = int(10*scale)
        cx = GX+GS//2; cy = out_h_actual//2; r = GS//2-int(12*scale)
        # Full-frame / box-grid / dash8 styles build their own background — skip gauge bg.
        _no_gauge = (P.get("dash8_layout") or P.get("style7_layout") or P.get("style8_layout")
                     or P.get("style11_layout") or P.get("style12_layout")
                     or P.get("style13_layout") or P.get("style14_layout") or P.get("style20_layout"))
        bg = None if _no_gauge else _R._build_gauge_bg(cx, cy, r, w=out_w, h=out_h_actual, rpm_max=rpm_max, P=P)
        _T = self._tlog("preview: gauge background", _T)

        # Find frame at t_preview
        idx = (rows['ts'] - t_preview).abs().idxmin()
        row = rows.iloc[idx]
        t_now = float(row['ts'])
        trace = rows[(rows['ts'] >= t_now - g_trail) & (rows['ts'] <= t_now)]
        # Style 14 uses fixed 10s trail regardless of g_trail setting
        if _R.style_trail_fixed_secs(_R.STYLES.get(_style_now)):
            trace = rows[(rows['ts'] >= t_now - 10.0) & (rows['ts'] <= t_now)]

        # 1s peak RPM
        peak_mask = (rows['ts'] >= t_now-1.0) & (rows['ts'] <= t_now)
        shown_peak = int(rows.loc[peak_mask,'rpm'].max()) if peak_mask.any() else None

        brake_val = float(row['brake']) if 'brake' in row.index and not np.isnan(row.get('brake',float('nan'))) else float(np.clip(-row['g_long']/1.2*100,0,100))

        speed_col = self.g_trail_speed_colour.get()
        img = _R.build_frame(
            float(row['rpm']), float(row['throttle']), float(row['speed']),
            int(row['gear']), float(row['g_lat']), float(row['g_long']), t_now,
            trace['g_lat'].tolist(), trace['g_long'].tolist(),
            laps, bg, cx, cy, r,
            w=out_w, h=out_h_actual, scale=scale,
            brake_pct=brake_val, rpm_max=rpm_max, peak_rpm=shown_peak,
            trace_speed=trace['speed'].tolist() if speed_col else None,
            speed_colour=speed_col, speed_max=speed_max, P=P,
            trace_throttle=trace['throttle'].tolist() if 'throttle' in trace.columns else None,
            trace_brake=trace['brake'].tolist() if 'brake' in trace.columns else None,
            trace_gear=trace['gear'].tolist() if 'gear' in trace.columns else None,
            chanA=float(row['chanA']) if 'chanA' in row.index else float('nan'),
            chanA_label=self.chanA_label.get(),
            chanB=float(row['chanB']) if 'chanB' in row.index else float('nan'),
            chanB_label=self.chanB_label.get(),
            chanA_unit=self.chanA_unit.get(), chanB_unit=self.chanB_unit.get(),
            backdrop_colour=self._backdrop_rgb())
    def _section(self, parent, row, text):
        f = tk.Frame(parent, bg=DARK_BG)
        f.grid(row=row, column=0, sticky="ew", pady=(12,2))
        tk.Label(f, text=text, font=FONT_HEAD, bg=DARK_BG, fg=ACCENT).pack(side="left")
        tk.Frame(f, bg=BORDER_COL, height=1).pack(side="left", fill="x", expand=True, padx=(10,0))

    def _card(self, parent, row):
        f = tk.Frame(parent, bg=DARK_CARD, padx=14, pady=10,
                     highlightbackground=BORDER_COL, highlightthickness=1)
        f.grid(row=row, column=0, sticky="ew")
        # Labels col 0 (fixed width) → controls col 1..4 → spacer → help column,
        # so every panel lines its controls up on a common vertical axis.
        f.columnconfigure(0, minsize=self.LABEL_COL_W, weight=0)
        f.columnconfigure(self.SPACER_COL, weight=1)
        f.columnconfigure(self.HELP_COL, minsize=self.HELP_W, weight=0)
        return f

    def _btn(self, parent, text, cmd, big=False, color=ACCENT2):
        size = 11 if big else 10
        pad  = (14,10) if big else (10,6)
        b = tk.Button(parent, text=text, command=cmd,
                      font=("Courier New", size, "bold"),
                      bg=DARK_PANEL, fg=color, activebackground=BORDER_COL,
                      activeforeground=color, relief="flat", bd=0,
                      cursor="hand2", padx=pad[0], pady=pad[1],
                      highlightbackground=color, highlightthickness=1)
        return b

    def _build_col_mapping(self, columns, preset=None):
        # Preserve smooth state across rebuilds
        _saved_smooth = {ch: v.get() for ch, v in self.smooth_vars.items()}
        for w in self.col_frame.winfo_children():
            w.destroy()
        self.col_vars = {}

        if not columns:
            tk.Label(self.col_frame, text="No file loaded yet.",
                     font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(row=0, column=0, sticky="w")
            return

        mapping = preset if preset is not None else auto_detect_channels(columns)

        # ── Time channel selector ──────────────────────────────────────────
        tk.Label(self.col_frame, text="Time:", font=FONT_HEAD,
                 bg=DARK_CARD, fg=ACCENT).grid(row=0, column=0, sticky='w', padx=(0,12), pady=(0,8))
        _ts_vals = ['(auto)'] + columns
        _cur_ts  = self.ts_col if self.ts_col in columns else '(auto)'
        self.ts_col_var.set(_cur_ts)
        _ts_combo = ttk.Combobox(self.col_frame, textvariable=self.ts_col_var,
                                  values=_ts_vals, width=34, font=FONT_MONO, state='readonly')
        _ts_combo.grid(row=0, column=1, columnspan=2, sticky='w', pady=(0,8))
        self._fix_combo(_ts_combo)
        def _on_ts_change(event):
            if self.df is not None:
                col_map = {ch: var.get() for ch, var in self.col_vars.items()
                           if var.get() not in ('(not detected)', '(None)', '')}
                self._reload_with_ts(col_map)
        _ts_combo.bind('<<ComboboxSelected>>', _on_ts_change)
        tk.Label(self.col_frame, text='Time axis column (seconds)',
                 font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=0, column=3, columnspan=2, sticky='w', padx=(8,0), pady=(0,8))

        tk.Frame(self.col_frame, bg=BORDER_COL, height=1).grid(
            row=1, column=0, columnspan=5, sticky='ew', pady=(0,6))

        headers = ["Channel", "Detected Column", "Min / Max / Avg", "Invert", "Smooth"]
        for c, h in enumerate(headers):
            tk.Label(self.col_frame, text=h, font=FONT_HEAD,
                     bg=DARK_CARD, fg=ACCENT).grid(row=2, column=c, sticky="w",
                                                    padx=(0,20), pady=(0,6))
        # Separator
        tk.Frame(self.col_frame, bg=BORDER_COL, height=1).grid(
            row=3, column=0, columnspan=5, sticky="ew", pady=(0,6))

        for r, (ch, label) in enumerate(CHANNEL_LABELS.items(), start=4):
            tk.Label(self.col_frame, text=label, font=FONT_HEAD,
                     bg=DARK_CARD, fg=TEXT_PRI, width=16, anchor="w").grid(
                row=r, column=0, sticky="w", pady=3)

            var = tk.StringVar(value=mapping.get(ch) or "(not detected)")
            self.col_vars[ch] = var

            # Colour indicator — green if detected, red if not
            detected = mapping.get(ch) is not None
            ind_col = GREEN_UI if detected else RED_UI

            dot_lbl = tk.Label(self.col_frame, text="●", font=FONT_SMALL,
                               bg=DARK_CARD, fg=ind_col)
            dot_lbl.grid(row=r, column=1, sticky="w")
            self._dot_labels[ch] = dot_lbl

            combo = ttk.Combobox(self.col_frame, textvariable=var,
                                  values=["(None)"] + columns, width=34,
                                  font=FONT_MONO, state="readonly")
            combo.grid(row=r, column=1, sticky="w", padx=(16,20), pady=3)
            self._fix_combo(combo)

            def _on_combo_change(event, ch=ch, var=var, lbl=dot_lbl, _cs=None):
                val = var.get()
                lbl.config(fg=GREEN_UI if val and val not in ('(not detected)', '(None)') else RED_UI)
                if _cs: _cs(val)
            # _cs will be bound after stats_var is created — use a list as mutable cell
            _cs_cell = [None]

            # Min / Max / Avg stats
            stats_var = tk.StringVar(value="—")

            def _compute_stats(col_name, sv=stats_var):
                if not col_name or col_name in ("(not detected)", "(None)") or self.df is None:
                    sv.set("—"); return
                try:
                    import pandas as _pd
                    col_data = pd.to_numeric(self.df.get(col_name, pd.Series()), errors="coerce").dropna()
                    if len(col_data) > 0:
                        # Decimals from the RANGE, not the magnitude: GPS
                        # coordinates vary by ~0.003 deg over a lap, so 1 dp
                        # made real data look frozen (-40.2 / -40.2 / -40.2).
                        _rng = float(col_data.max() - col_data.min())
                        _dp = (0 if _rng >= 100 else 1 if _rng >= 10 else
                               2 if _rng >= 1 else 3 if _rng >= 0.1 else
                               4 if _rng >= 0.01 else 6)
                        sv.set(f"{col_data.min():.{_dp}f} / {col_data.max():.{_dp}f}"
                               f" / {col_data.mean():.{_dp}f}")
                    else:
                        sv.set("—")
                except Exception:
                    sv.set("—")

            _compute_stats(mapping.get(ch))   # populate on load
            _cs_cell[0] = _compute_stats     # wire into combo callback

            # Re-bind with stats updater now that _cs_cell is wired
            def _on_combo_change2(event, var=var, lbl=dot_lbl, cs=_compute_stats):
                val = var.get()
                lbl.config(fg=GREEN_UI if val and val not in ('(not detected)', '(None)') else RED_UI)
                cs(val)
                # Rebuild working rows (debounced so the UI does not freeze)
                self._schedule_rebuild()
            combo.bind('<<ComboboxSelected>>', _on_combo_change2)

            tk.Label(self.col_frame, textvariable=stats_var, font=FONT_SMALL,
                     bg=DARK_CARD, fg=ACCENT2, width=26, anchor="w").grid(
                row=r, column=2, sticky="w")

            # Invert checkbox — G channels and throttle
            if ch in ("g_lat", "g_long", "throttle"):
                if ch == "g_lat":      ivar = self.invert_glat
                elif ch == "g_long":   ivar = self.invert_glong
                else:                  ivar = self.invert_throttle
                tk.Checkbutton(
                    self.col_frame, variable=ivar,
                    bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
                    activeforeground=ACCENT2, selectcolor=DARK_PANEL,
                    relief="flat", bd=0, cursor="hand2").grid(
                    row=r, column=3, sticky="w", padx=(4,0))

            # Smooth checkbox — restore saved state, or apply default if first load
            _default_val = self._default_smooth.get(ch, False)
            saved_val = _saved_smooth.get(ch, _default_val)
            if ch not in self.smooth_vars:
                self.smooth_vars[ch] = tk.BooleanVar(value=saved_val)
            else:
                self.smooth_vars[ch].set(saved_val)
            tk.Checkbutton(
                self.col_frame, variable=self.smooth_vars[ch],
                bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
                activeforeground=ACCENT2, selectcolor=DARK_PANEL,
                relief="flat", bd=0, cursor="hand2").grid(
                row=r, column=4, sticky="w", padx=(4,0))

        self.col_frame.columnconfigure(1, weight=1)

        # ── Optional extra channels A & B ──────────────────
        _opt_start = r + 2
        tk.Frame(self.col_frame, bg=BORDER_COL, height=1).grid(
            row=_opt_start-1, column=0, columnspan=5, sticky="ew", pady=(12,8))
        tk.Label(self.col_frame, text="Optional Channels",
                 font=FONT_HEAD, bg=DARK_CARD, fg=ACCENT).grid(
            row=_opt_start, column=0, columnspan=3, sticky="w", pady=(0,2))
        _oh = ["Channel", "Source Column", "Label"]
        for c, h in enumerate(_oh):
            tk.Label(self.col_frame, text=h, font=FONT_HEAD,
                     bg=DARK_CARD, fg=ACCENT).grid(
                row=_opt_start+2, column=c, sticky="w", padx=(0,20), pady=(0,4))
        def _mk_optrow(rr, name, col_var, lbl_var, unit_var=None):
            tk.Label(self.col_frame, text=name, font=FONT_HEAD,
                     bg=DARK_CARD, fg=TEXT_PRI, width=16, anchor="w").grid(
                row=rr, column=0, sticky="w", pady=3)
            _cb = ttk.Combobox(self.col_frame, textvariable=col_var,
                               values=["(None)"] + columns, width=34,
                               font=FONT_MONO, state="readonly")
            _cb.grid(row=rr, column=1, sticky="w", padx=(0,20), pady=3)
            self._fix_combo(_cb)
            def _limit_label(*_a, v=lbl_var):
                s = v.get()
                if len(s) > 4: v.set(s[:4])
            lbl_var.trace_add("write", _limit_label)
            # label + unit live in a small sub-frame so the unit is a tight 1-char box
            _lf = tk.Frame(self.col_frame, bg=DARK_CARD)
            _lf.grid(row=rr, column=2, sticky="w", pady=3)
            tk.Entry(_lf, textvariable=lbl_var, width=6,
                     font=FONT_MONO, bg=DARK_PANEL, fg=TEXT_PRI,
                     insertbackground=TEXT_PRI, relief="flat",
                     highlightthickness=1, highlightbackground=BORDER_COL,
                     highlightcolor=ACCENT).pack(side="left")
            if unit_var is not None:
                def _limit_unit(*_a, v=unit_var):
                    s = v.get()
                    if len(s) > 1: v.set(s[:1])   # single character only
                unit_var.trace_add("write", _limit_unit)
                tk.Label(_lf, text="unit:", font=FONT_SMALL,
                         bg=DARK_CARD, fg=TEXT_SEC).pack(side="left", padx=(8,2))
                tk.Entry(_lf, textvariable=unit_var, width=2,
                         font=FONT_MONO, bg=DARK_PANEL, fg=TEXT_PRI,
                         insertbackground=TEXT_PRI, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER_COL,
                         highlightcolor=ACCENT, justify="center").pack(side="left")
            def _on_opt_change(_e=None):
                self._schedule_rebuild()
            _cb.bind('<<ComboboxSelected>>', _on_opt_change)
        _mk_optrow(_opt_start+3, "Channel A", self.chanA_col, self.chanA_label, self.chanA_unit)
        _mk_optrow(_opt_start+4, "Channel B", self.chanB_col, self.chanB_label, self.chanB_unit)

    # ── Actions ────────────────────────────────────────────────────────────────
    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select ECU Log File",
            filetypes=[("Log files", "*.csv *.vbo *.xrk *.drk *.xrz"),
                       ("AIM XRK/DRK/XRZ", "*.xrk *.drk *.xrz *.XRK *.DRK"),
                       ("CSV files", "*.csv *.CSV"),
                       ("VBO files", "*.vbo *.VBO"),
                       ("All files", "*.*")])
        if not path:
            return
        self.file_path.set(path)
        # Auto-set output dir to same folder
        if not self.output_dir.get():
            self.output_dir.set(os.path.dirname(path))
        self._load_file(path)

    def _set_output_name_from_log(self, path):
        """Name the output video after the log: "Taupo R1.csv" -> "Taupo R1.mp4".

        Only replaces a name this app derived (or the default), so if the name has
        been typed by hand it survives loading another log.
        """
        try:
            # Split on both separators: a path can carry the other platform's
            # slashes (pasted, or a log path saved on another machine).
            base = str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            stem = os.path.splitext(base)[0].strip()
        except Exception:
            return
        if not stem:
            return
        cur = (self.output_name.get() or "").strip()
        if cur and cur != getattr(self, "_auto_output_name", "ecu_overlay.mp4"):
            return
        self._auto_output_name = f"{stem}.mp4"
        self.output_name.set(self._auto_output_name)

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_dir.set(path)


    def _load_xrk(self, path):
        """Load AIM XRK/DRK/XRZ file via MatLabXRK DLL."""
        import threading
        self.status_var.set("Opening AIM file…")
        self._loading_start("Reading AIM file")
        self._working_rows = None

        # Set DLL path from UI field — accept full path to DLL or folder
        dll_path = self.dll_path_var.get().strip()
        if dll_path:
            if os.path.isfile(dll_path):
                # Full path to DLL — add its folder AND copy the DLL name hint
                os.environ["XRK_DLL_PATH"] = os.path.dirname(dll_path)
                # Also put the exact file in the search list by copying to script dir
                # (simplest approach: just set hint_dir to its folder)
            elif os.path.isdir(dll_path):
                os.environ["XRK_DLL_PATH"] = dll_path

        def _worker():
            try:
                import xrk_reader as _xr
                _hint = os.path.dirname(path)
                if dll_path and os.path.isfile(dll_path):
                    _hint = os.path.dirname(dll_path)
                # Reset cached DLL so new path is picked up
                _xr._dll = None
                def _p(p, m):
                    def _u():
                        self.status_var.set(f"AIM: {m} ({p}%)")
                        self._loading_msg_set(f"{m} {p}%" if m else f"Reading AIM file {p}%")
                    self.after(0, _u)
                result = _xr.read_xrk(path, progress_cb=_p)

                df   = result["df"]
                meta = result["meta"]

                self.df       = df
                self.ts_col   = result["ts_col"]
                self._aim_laps = meta.get("laps", [])

                cols = list(df.columns)

                def _ui():
                    self.status_var.set(
                        f"Loaded XRK: {len(df)} rows, {len(cols)} channels  "
                        f"| {meta.get('vehicle','')} | {meta.get('track','')} "
                        f"| {meta.get('date','')} | {meta.get('n_laps',0)} laps")

                    # Time range from the data
                    _dur = float(pd.to_numeric(df[self.ts_col], errors='coerce').max())
                    self.t_start_var.set("0.0")
                    self.t_end_var.set(f"{_dur:.1f}")
                    if hasattr(self, 'max_t_label'):
                        self.max_t_label.set(f"max {_dur:.1f}s")

                    # Sample rate
                    _d = pd.to_numeric(df[self.ts_col], errors='coerce').diff()
                    _d = _d[_d > 0]
                    _dt = float(_d.median()) if len(_d) else 0.1
                    self._data_fps = max(1, min(60, int(round(1.0 / _dt)))) if _dt > 0 else 10

                    # Build the Channel Mapping table (auto-detected) and use it
                    _map = auto_detect_channels(cols)
                    self._build_col_mapping(cols, preset=_map)
                    _col_map = {ch: c for ch, c in _map.items() if c}
                    self._prepare_working_df(_col_map)

                    # GPS columns for lap detection (strict: never accelerations)
                    for _kws, _var in ((['gps_latitude', 'latitude', 'lat_deg'], self.lap_lat_col),
                                       (['gps_longitude', 'longitude', 'lon_deg', 'lng'], self.lap_lon_col)):
                        if _var.get():
                            continue
                        for _kw in _kws:
                            if _var.get():
                                break
                            for c in cols:
                                _cl = c.lower()
                                if (_kw in _cl and not any(x in _cl for x in _GPS_POS_EXCLUDE)
                                        and not _var.get()):
                                    _var.set(c)
                    if hasattr(self, '_lat_col_cb'):
                        self._lat_col_cb['values'] = cols
                        self._lon_col_cb['values'] = cols

                    # AIM beacon laps drive lap detection
                    n_laps = meta.get("n_laps", 0)
                    if n_laps and hasattr(self, '_aim_info_lbl'):
                        self._aim_info_lbl.config(
                            text=f"{n_laps} laps from AIM beacon markers.")
                    self._update_lap_display()
                    self._loading_stop()
                self.after(0, _ui)
            except Exception as e:
                self.after(0, lambda: (self._loading_stop("load failed"),
                                       self.status_var.set(f"AIM load error: {e}")))
                import traceback; traceback.print_exc()

        threading.Thread(target=_worker, daemon=True).start()

    def _load_vbo(self, path):
        """Load a VBO file and populate the app."""
        try:
            try:
                import vbo_reader as _vbo_mod
            except ImportError:
                self.after(0, lambda: self.status_var.set(
                    "vbo_reader.py not found — place it in the same folder as ecu_overlay_app.py"))
                return
            self.after(0, lambda: (self._loading_start("Reading VBO"),
                                   self.status_var.set("Loading VBO…")))
            df, meta = _vbo_mod.read_vbo(path)

            # Map VBO df to app expectations
            # VBO uses lat/lon directly; create GPS columns in df
            df['GPS_Latitude']  = df['lat']
            df['GPS_Longitude'] = df['lon']
            df['GPS_Speed']     = df['speed']

            # ts column — already in seconds
            self.ts_col = 'ts'

            # Store
            self.df             = df
            self._working_rows  = df.copy()
            self._aim_laps      = meta.get('laps', [])
            self._vbo_meta      = meta

            # Sample rate
            diffs = df['ts'].diff().dropna()
            diffs = diffs[diffs > 0]
            dt = float(diffs.median()) if len(diffs) else 0.1
            fps_resample = min(60, max(1, round(1.0 / dt)))
            self._data_fps = fps_resample
            # VBO often has variable sample rate — clamp to avoid 0 fps
            if fps_resample < 1: fps_resample = 1

            # Populate UI on main thread
            self.after(0, lambda: (self._on_vbo_loaded(path, df, meta, fps_resample),
                                   self._loading_stop()))

        except Exception as e:
            import traceback
            msg = f"VBO load error: {e}\n{traceback.format_exc()}"
            self.after(0, lambda m=msg: (self._loading_stop("load failed"),
                                         self.status_var.set(m[:200])))

    def _on_vbo_loaded(self, path, df, meta, fps_resample):
        """Called on main thread after VBO load completes."""
        cols = list(df.columns)

        # Update file info
        self.file_path.set(path)
        dur = float(df['ts'].max())
        self.t_start_var.set("0.0")
        self.t_end_var.set(f"{dur:.1f}")
        if hasattr(self, 'max_t_label'):
            self.max_t_label.set(f"max {dur:.1f}s")

        # Set ts_col before building mapping so combo default is correct
        self.ts_col = 'ts'

        # Rebuild channel mapping UI for VBO columns. Newer VBOs carry real
        # rpm / throttle / brake / gear — map them when read_vbo found them.
        _ec = meta.get('engine_channels', {}) if isinstance(meta, dict) else {}
        vbo_map = {
            'speed':    'speed',
            'g_lat':    'g_lat',
            'g_long':   'g_long',
            'rpm':      'rpm'      if _ec.get('rpm')      else None,
            'throttle': 'throttle' if _ec.get('throttle') else None,
            'brake':    'brake'    if _ec.get('brake')    else None,
            'gear':     'gear'     if _ec.get('gear')     else None,
        }
        self._build_col_mapping(cols, preset=vbo_map)

        # Prepare working df using VBO channel mapping
        self._prepare_working_df(vbo_map)

        # GPS columns — VBO uses 'lat'/'lon'
        self.lap_lat_col.set('lat')
        self.lap_lon_col.set('lon')
        if hasattr(self, '_lat_col_cb'):
            self._lat_col_cb['values'] = cols
            self._lon_col_cb['values'] = cols

        # File info label
        if hasattr(self, '_file_info_var'):
            sn = self.style_var.get()
            res = '1920×1080' if sn in ('Style 7','Style 8') else ('1920×365' if sn == 'Dash 7 (HUD)' else ('1920×405' if sn == 'Dash 9 (Triple)' else ('2560×750' if sn == 'Dash 10 (Arc)' else '1920×750')))
            self._file_info_var.set(
                f"data: {fps_resample} fps  ·  render: {self.fps_var.get() if hasattr(self,'fps_var') else fps_resample} fps  ·  "
                f"{len(df):,} rows  ·  {dur:.1f}s  ·  "
                f"Driver: {meta.get('driver','')}  ·  Track: {meta.get('track','')}  ·  "
                f"Best: {meta.get('best_lap','')}  ·  output: {res} (auto)")

        # AIM beacons info (use VBO lap timing)
        n_laps = len(self._aim_laps)
        if hasattr(self, '_aim_info_lbl') and n_laps:
            self._aim_info_lbl.config(
                text=f"{n_laps} laps from VBO [laptiming]  (best: {meta.get('best_lap','')})")

        # Auto-populate S/F from lap timing, then update display
        self._auto_detect_sf()
        self._update_lap_display()

        self.status_var.set(
            f"Loaded VBO: {meta.get('driver','')} @ {meta.get('track','')}  "
            f"— {n_laps} laps, best {meta.get('best_lap','')}")

    def _load_file(self, path):
        self._set_output_name_from_log(path)
        self.status_var.set("Loading file…")
        self._loading_start("Loading file")
        self.update()
        _ext = os.path.splitext(path)[1].lower()
        if _ext in ('.xrk', '.drk', '.xrz'):
            self._load_xrk(path)
            return
        if _ext == '.vbo':
            import threading
            threading.Thread(target=self._load_vbo, args=(path,), daemon=True).start()
            return
        try:
            df, ts_col, cols = load_file(path)
            self.df     = df
            self.ts_col = ts_col
            self.all_cols = cols

            # Prepare normalised working columns
            col_map_raw = auto_detect_channels(cols)
            self._prepare_working_df(col_map_raw)

            t_max = float(df[ts_col].max())
            t0    = float(df[ts_col].min())
            self.t_start_var.set(f"{t0:.1f}")
            self.t_end_var.set(f"{t_max:.1f}")
            # Parse AIM metadata for lap beacons
            _aim_meta = parse_aim_metadata(path)
            self._aim_laps = _aim_meta.get('laps', [])
            if self._aim_laps:
                n_laps = len(self._aim_laps)
                self.status_var.set(f"Loaded - {n_laps} laps from beacon markers")
                if self.lap_mode_var.get() == 'Auto':
                    pass  # Auto will use beacons
                if hasattr(self, '_aim_info_lbl'):
                    self._aim_info_lbl.config(
                        text=f"{n_laps} laps from beacon markers.")
            # Auto-detect GPS lat/lon columns and populate dropdowns
            self.lap_lat_col.set("")
            self.lap_lon_col.set("")
            # Priority order: most specific first to avoid GPS_LatAcc matching before GPS_Latitude
            _lat_keywords = ['gps_latitude', 'latitude', 'lat_deg']
            _lon_keywords = ['gps_longitude', 'longitude', 'lon_deg', 'lng']
            for _kw in _lat_keywords:
                if self.lap_lat_col.get(): break
                for c in cols:
                    if (_kw in c.lower() and not any(x in c.lower() for x in _GPS_POS_EXCLUDE)
                            and not self.lap_lat_col.get()):
                        self.lap_lat_col.set(c)
            for _kw in _lon_keywords:
                if self.lap_lon_col.get(): break
                for c in cols:
                    if (_kw in c.lower() and not any(x in c.lower() for x in _GPS_POS_EXCLUDE)
                            and not self.lap_lon_col.get()):
                        self.lap_lon_col.set(c)
            if hasattr(self, '_lat_col_cb'):
                self._lat_col_cb['values'] = cols
                self._lon_col_cb['values'] = cols
            self.max_t_label.set(f"Max: {t_max:.1f} s")

            self._build_col_mapping(cols)
            self._update_lap_display()
            self._loading_stop()
            self.status_var.set(
                f"Loaded {len(df):,} rows  ·  {t_max:.1f}s  ·  {len(cols)} channels")
        except Exception as e:
            self._loading_stop("load failed")
            messagebox.showerror("Load Error", str(e))
            self.status_var.set("Error loading file.")

    def _prepare_working_df(self, col_map):
        """Build a clean normalised dataframe with standard column names."""
        if self.df is None:
            return
        df = self.df.copy()

        # Sanitise timestamp column — drop NaN, sort, ensure monotonic
        ts_vals = pd.to_numeric(df[self.ts_col], errors='coerce')
        df[self.ts_col] = ts_vals
        df = df.dropna(subset=[self.ts_col])
        df = df.sort_values(self.ts_col).reset_index(drop=True)
        df = df.drop_duplicates(subset=[self.ts_col])  # remove duplicate timestamps

        if len(df) < 2:
            self.status_var.set("Error: not enough valid timestamp rows to resample.")
            return

        t0 = float(df[self.ts_col].min())
        t1 = float(df[self.ts_col].max())

        import math as _math
        if not (_math.isfinite(t0) and _math.isfinite(t1) and t1 > t0):
            self.status_var.set(
                f"Error: invalid time range ({t0:.3f}..{t1:.3f}). "
                "Check the Time channel selection.")
            return

        # Auto-detect sample rate from data
        _dt = pd.to_numeric(df[self.ts_col], errors='coerce').diff().median()
        fps_resample = int(round(1.0 / _dt)) if _dt and _dt > 0 else 30
        fps_resample = max(1, min(fps_resample, 60))   # cap at 60fps
        self._data_fps = fps_resample
        # Set fps_var to match data rate (user can override)
        if hasattr(self, "_file_info_var"):
            _style_now = self.style_var.get() if hasattr(self, "style_var") else ""
            _fw, _fh = _R.style_frame_size(_R.STYLES.get(_style_now))
            _res = f"{_fw}×{_fh}"
            _sel_fps_now = self.fps_var.get() if hasattr(self,"fps_var") else fps_resample
            self._file_info_var.set(
                f"data: {fps_resample} fps  ·  render: {_sel_fps_now} fps  ·  "
                f"{len(df):,} rows  ·  {t1-t0:.1f}s  ·  output: {_res}")
        # Use render fps for the working grid — eliminates FPS mismatch drift
        try:
            _render_fps = int(self.fps_var.get())
        except Exception:
            _render_fps = fps_resample
        # Integer frame indexing avoids np.arange float accumulation
        _frame_count = int(round((t1 - t0) * _render_fps))
        tt = t0 + np.arange(_frame_count) / _render_fps
        if len(tt) == 0:
            tt = np.array([t0, t1])
        rows = pd.DataFrame({'ts': tt})
        # Per-channel expected ranges for outlier clipping
        _ch_ranges = {
            'rpm':      (0,    20000),
            'throttle': (0,    100),
            'speed':    (0,    400),
            'g_lat':    (-5,   5),
            'g_long':   (-5,   5),
        }
        for ch in ['rpm','throttle','speed','g_lat','g_long']:
            src2 = col_map.get(ch)
            if src2 and src2 in df.columns:
                vals = pd.to_numeric(df[src2], errors='coerce')
                lo, hi = _ch_ranges[ch]
                # Clip hard limits
                vals = vals.clip(lo, hi)
                # Despike: replace values that jump >50% of range in one step
                _rng = hi - lo
                _spike_thresh = _rng * 0.4
                _diff = vals.diff().abs()
                vals[_diff > _spike_thresh] = np.nan
                vals = vals.interpolate(method='linear').ffill().bfill().fillna(0)
                rows[ch] = np.interp(tt, df[self.ts_col].values, vals.values)
            else:
                rows[ch] = 0.0
        # Brake: raw channel, normalised to 0-100% by peak in session
        brake_src = col_map.get('brake')
        if brake_src and brake_src in df.columns:
            bvals = pd.to_numeric(df[brake_src], errors='coerce').fillna(0).values
            braw = pd.to_numeric(df[brake_src], errors='coerce')
            # Despike brake: massive jumps are sensor noise
            _bdiff = braw.diff().abs()
            _bspike = _bdiff > (_bdiff.quantile(0.99) * 3)
            braw[_bspike] = np.nan
            braw = braw.interpolate(method='linear').ffill().bfill().fillna(0)
            braw = braw.clip(braw.quantile(0.005), braw.quantile(0.995))
            bvals = braw.values
            bvals = np.interp(tt, df[self.ts_col].values, bvals)
            # Auto-detect if this looks like a G-force channel (range < 5)
            # vs a pressure channel (range >> 5)
            brange = bvals.max() - bvals.min()
            if brange < 5.0:
                # G-force channel: only use negative values as braking
                # Clip positives to 0, negate, so decel becomes positive
                bvals  = np.clip(-bvals, 0, None)   # -(-1.3) = 1.3, -(0.8) = 0 (clipped)
                bpeak  = bvals.max()
                rows['brake'] = np.clip(bvals / bpeak * 100, 0, 100) if bpeak > 0 else np.zeros(len(bvals))
            else:
                # Pressure channel: shift baseline so min = 0, normalise to peak
                bmin  = bvals.min()
                bvals = bvals - bmin
                bpeak = bvals.max()
                rows['brake'] = np.clip(bvals / bpeak * 100, 0, 100) if bpeak > 0 else np.zeros(len(bvals))
        else:
            rows['brake'] = np.nan   # sentinel: bar shows empty
        # Gear: nearest neighbour
        src = col_map.get('gear')
        if src and src in df.columns:
            vals = pd.to_numeric(df[src], errors='coerce').fillna(0).values
            # Gear: nearest-neighbour not linear — avoid fractional gears
            _gear_idx = np.searchsorted(df[self.ts_col].values, tt).clip(0, len(vals)-1)
            rows['gear'] = np.round(vals[_gear_idx]).astype(int)
        else:
            rows['gear'] = 0

        # Clamp gear: values > 8 are typically sensor no-signal sentinels
        rows['gear'] = rows['gear'].clip(0, 8)

        # GPS lat/lon — needed for GPS lap detection
        _lat_col = self.lap_lat_col.get() if hasattr(self, 'lap_lat_col') else ''
        _lon_col = self.lap_lon_col.get() if hasattr(self, 'lap_lon_col') else ''
        # If not set, try to auto-find
        if not _lat_col:
            for _kw in ['gps_latitude','latitude','gps_lat']:
                for _c in df.columns:
                    if _kw in _c.lower():
                        _lat_col = _c; break
                if _lat_col: break
        if not _lon_col:
            for _kw in ['gps_longitude','longitude','gps_lon']:
                for _c in df.columns:
                    if _kw in _c.lower():
                        _lon_col = _c; break
                if _lon_col: break
        if _lat_col and _lat_col in df.columns:
            _lv = pd.to_numeric(df[_lat_col], errors='coerce').interpolate().bfill().ffill().values
            rows['lat'] = np.interp(tt, df[self.ts_col].values, _lv)
        else:
            rows['lat'] = np.full(len(tt), np.nan)
        if _lon_col and _lon_col in df.columns:
            _lv = pd.to_numeric(df[_lon_col], errors='coerce').interpolate().bfill().ffill().values
            rows['lon'] = np.interp(tt, df[self.ts_col].values, _lv)
        else:
            rows['lon'] = np.full(len(tt), np.nan)

        # ── Optional channels A & B ─────────────────────
        for _ck, _cv in [('chanA', self.chanA_col), ('chanB', self.chanB_col)]:
            _src = _cv.get() if hasattr(_cv, 'get') else None
            if _src and _src not in ('(None)', '(not detected)', '') and _src in df.columns:
                _vv = pd.to_numeric(df[_src], errors='coerce')
                _vv = _vv.interpolate(method='linear').ffill().bfill().fillna(0)
                rows[_ck] = np.interp(tt, df[self.ts_col].values, _vv.values)
            else:
                rows[_ck] = np.nan   # sentinel: not selected → not displayed

        # Channel inversions belong here, not at each consumer: the dash preview
        # used to invert on its own while the widget previews did not, so a
        # toggle appeared to do nothing on the G-plot tab.
        for _icol, _ivar in (("g_lat", self.invert_glat),
                             ("g_long", self.invert_glong)):
            if _ivar.get() and _icol in rows.columns:
                rows[_icol] = -rows[_icol]
        if self.invert_throttle.get() and "throttle" in rows.columns:
            rows["throttle"] = 100.0 - rows["throttle"].clip(0, 100)

        self._working_rows = rows
        # Fresh working data: refresh the scrub readout and the visible preview.
        try:
            self._on_scrub()
        except Exception:
            pass


    def _update_lap_display(self):
        """Update lap display using current method."""
        if not hasattr(self, '_working_rows') or self._working_rows is None:
            self.lap_text.set("Load a file to detect laps.")
            return
        try:
            laps = self._compute_laps()
            if not laps:
                self.lap_text.set("No laps detected.")
                return
            best_lt = min(lt for _,_,lt in laps)
            mode = self.lap_mode_var.get()
            _txt = self._format_lap_table(laps, best_lt, mode)
            self.lap_text.set(_txt)
            if hasattr(self.lap_info, "config") and self.lap_info.winfo_class() == "Text":
                self.lap_info.config(state="normal")
                self.lap_info.delete("1.0", "end")
                self.lap_info.insert("1.0", _txt)
                self.lap_info.config(state="disabled")
        except Exception as e:
            _err = f"Detection error: {e}"
            self.lap_text.set(_err)
            if hasattr(self.lap_info, "config") and self.lap_info.winfo_class() == "Text":
                self.lap_info.config(state="normal")
                self.lap_info.delete("1.0", "end")
                self.lap_info.insert("1.0", _err)
                self.lap_info.config(state="disabled")


    def _compute_laps(self):
        """Return lap list based on current mode setting."""
        _np = np
        mode = self.lap_mode_var.get()
        aim_laps = getattr(self, '_aim_laps', [])
        df = self.df
        wr = self._working_rows

        if mode == 'Lap Beacons':
            if not aim_laps:
                raise ValueError("No beacon data found. Load a data file with lap beacon markers (AIM CSV, VBO, etc.).")
            offset = float(self.lap_offset_var.get() or 0)
            return [(st+offset, et+offset, lt) for st,et,lt in aim_laps]

        elif mode == 'GPS Crossing':
            lat_col = self.lap_lat_col.get()
            lon_col = self.lap_lon_col.get()
            if not lat_col or not lon_col:
                raise ValueError("No GPS columns detected. Load a file with GPS data.")
            try: sf_lat = float(self.lap_sf_lat.get())
            except: raise ValueError("Enter S/F Latitude.")
            try: sf_lon = float(self.lap_sf_lon.get())
            except: raise ValueError("Enter S/F Longitude.")
            try: min_gap = float(self.lap_est_time.get())
            except: min_gap = 30.0
            # Use the already-monotonic df timestamps directly (load_file already
            # reconstructed them — do NOT re-apply rollover offsets)
            _mono_ts = pd.to_numeric(df[self.ts_col], errors='coerce').values
            lat_v    = pd.to_numeric(df[lat_col], errors='coerce').values
            lon_v    = pd.to_numeric(df[lon_col], errors='coerce').values
            _valid   = ~(_np.isnan(lat_v) | _np.isnan(lon_v) | _np.isnan(_mono_ts))
            # Run detection on original GPS samples (no interpolation artefacts)
            laps   = detect_laps_gps(_mono_ts[_valid], lat_v[_valid], lon_v[_valid],
                                      sf_lat, sf_lon, min_gap_s=min_gap)
            offset = float(self.lap_offset_var.get() or 0)
            return [(st+offset, et+offset, lt) for st,et,lt in laps]

        elif mode == 'Point-to-Point (Stage)':
            lat_col = self.lap_lat_col.get()
            lon_col = self.lap_lon_col.get()
            if not lat_col or not lon_col:
                raise ValueError("No GPS columns detected. Load a file with GPS data.")
            try:
                s_lat = float(self.stage_start_lat.get()); s_lon = float(self.stage_start_lon.get())
            except: raise ValueError("Enter the start line Lat/Lon.")
            try:
                f_lat = float(self.stage_fin_lat.get()); f_lon = float(self.stage_fin_lon.get())
            except: raise ValueError("Enter the finish line Lat/Lon.")
            try: rad = float(self.stage_radius.get())
            except: rad = 50.0
            _mono_ts = pd.to_numeric(df[self.ts_col], errors='coerce').values
            lat_v    = pd.to_numeric(df[lat_col], errors='coerce').values
            lon_v    = pd.to_numeric(df[lon_col], errors='coerce').values
            _valid   = ~(_np.isnan(lat_v) | _np.isnan(lon_v) | _np.isnan(_mono_ts))
            laps = detect_stage(_mono_ts[_valid], lat_v[_valid], lon_v[_valid],
                                s_lat, s_lon, f_lat, f_lon, radius_m=rad)
            offset = float(self.lap_offset_var.get() or 0)
            return [(st+offset, et+offset, lt) for st,et,lt in laps]

        else:  # Speed Peaks
            if wr is None:
                raise ValueError("No data loaded.")
            try: min_spd = float(self.lap_min_speed.get())
            except: min_spd = 150.0
            try: min_gap = float(self.lap_est_time.get())
            except: min_gap = 30.0
            offset = float(self.lap_offset_var.get() or 0)
            laps = _R.find_lap_events(wr['speed'].values, wr['ts'].values,
                                       min_spd=min_spd, min_gap=min_gap)
            if not laps:
                raise ValueError(f"No laps found above {min_spd}kph with >{min_gap}s gap. "
                                  "Try lowering Min speed or Est. lap time.")
            return [(st+offset, et+offset, lt) for st,et,lt in laps]

    def _auto_detect_sf(self):
        """Auto-detect S/F line from AIM beacons or GPS speed minima."""
        _np = np
        try:
            aim_laps = getattr(self, '_aim_laps', [])
            lat_col = self.lap_lat_col.get()
            lon_col = self.lap_lon_col.get()
            df = self.df
            wr = self._working_rows
            if wr is None or not lat_col or not lon_col:
                self.lap_text.set("Select Lat/Lon columns first, then auto-detect.")
                return
            _raw_ts = pd.to_numeric(df[self.ts_col], errors='coerce').values.copy().astype(float)
            _off2 = 0.0
            for _ii in range(1, len(_raw_ts)):
                if _raw_ts[_ii] < _raw_ts[_ii-1] - 0.5: _off2 += _raw_ts[_ii-1]
                _raw_ts[_ii] += _off2
            lat_v  = pd.to_numeric(df[lat_col], errors='coerce').values
            lon_v  = pd.to_numeric(df[lon_col], errors='coerce').values
            ts = wr['ts'].values
            _valid = ~(_np.isnan(lat_v) | _np.isnan(lon_v))
            lat_i = _np.interp(ts, _raw_ts[_valid], lat_v[_valid])
            lon_i = _np.interp(ts, _raw_ts[_valid], lon_v[_valid])

            # GPS-only: find the median position at crossing points
            # Uses the signed-distance GPS lap detection against current SF coordinate
            # (bootstrapped from any existing SF coordinate, or track centre)
            try:
                _cur_lat = float(self.lap_sf_lat.get())
                _cur_lon = float(self.lap_sf_lon.get())
            except Exception:
                self.status_var.set("Enter an approximate S/F coordinate first.")
                return
            try:
                _detected = detect_laps_gps(ts, lat_i, lon_i, _cur_lat, _cur_lon)
                sf_lats = []; sf_lons = []
                for _st, _et, _lt in _detected:
                    _idx = int(_np.argmin(_np.abs(ts - _et)))
                    sf_lats.append(float(lat_i[_idx]))
                    sf_lons.append(float(lon_i[_idx]))
                if not sf_lats:
                    self.status_var.set("No GPS crossings found near current coordinate.")
                    return
                sf_lat = float(_np.mean(sf_lats))
            except Exception as _e:
                self.status_var.set(f"GPS S/F detection failed: {_e}")
                return
            if False:  # dead branch placeholder
                sf_lat = 0.0
                sf_lon = float(_np.mean(lon_i[mins]))

            self.lap_sf_lat.set(f"{sf_lat:.6f}")
            self.lap_sf_lon.set(f"{sf_lon:.6f}")
            self.lap_text.set(f"S/F set to {sf_lat:.6f}, {sf_lon:.6f}")
            self._update_lap_display()
        except Exception as e:
            self.lap_text.set(f"Auto-detect error: {e}")

    def _collect_inputs(self):
        """Collect all input widgets for locking during render."""
        self._all_inputs = []
        def _recurse(w):
            wclass = w.winfo_class()
            if wclass in ('Entry', 'TCombobox', 'Button'):
                self._all_inputs.append(w)
            for child in w.winfo_children():
                _recurse(child)
        _recurse(self)

    def _lock_ui(self):
        self._collect_inputs()
        for w in self._all_inputs:
            try:
                if w != self.cancel_btn:
                    w.config(state="disabled")
            except Exception:
                pass
        self.cancel_btn.config(state="normal", fg=RED_UI)
        self.gen_btn.config(state="disabled", fg=TEXT_SEC)

    def _unlock_ui(self):
        for w in self._all_inputs:
            try:
                wclass = w.winfo_class()
                if wclass == 'TCombobox':
                    w.config(state="readonly")
                else:
                    w.config(state="normal")
            except Exception:
                pass
        self.cancel_btn.config(state="disabled", fg=TEXT_SEC)
        self.gen_btn.config(state="normal", fg=ACCENT)

    def _cancel_render(self):
        self._cancel_flag = True
        self.status_var.set("Cancelling… please wait for current frame to finish.")
        self.cancel_btn.config(state="disabled", fg=TEXT_SEC)


    def _render_dash_pil(self, t_preview, laps=None, max_w=None):
        """Build ONE main-dash frame as a PIL image, from the live settings.

        Shared by the Dash tab's live preview and the popup preview so the two
        can never disagree about what the render will look like.
        """
        import math as _math
        _style_now = self.style_var.get()
        resolution = _R.style_frame_size(_R.STYLES.get(_style_now))
        out_w, out_h = resolution
        if max_w:
            # Render smaller for an on-screen preview: much faster, and the
            # layout is resolution-independent (everything scales off height).
            _f = max(0.25, min(1.0, float(max_w) / out_w))
            out_w = max(320, int(out_w * _f))
            out_h = max(120, int(out_h * _f))
        _nat_h = int(_R.H_VID * (out_w / _R.W_VID))
        if out_h < _nat_h - 2:
            out_h_actual = out_h
            scale = out_h_actual / _R.H_VID
        else:
            scale = out_w / _R.W_VID
            out_h_actual = int(_R.H_VID * scale)

        P = self._apply_backdrop_to_P(
            _R.STYLES.get(_style_now, _R.STYLES[_R.DASH_NAMES[0]]))

        _bg_key = (_style_now, self.backdrop_var.get(), out_w, out_h)
        if getattr(self, "_pv_bg_key", None) != _bg_key:
            _R._GAUGE_BG_CACHE.clear()
            self._pv_bg_key = _bg_key

        rows = self._working_rows   # already inverted, see _prepare_working_df

        if laps is None:
            try:
                laps = self._compute_laps()
            except Exception:
                laps = []
        self._set_arc_track_path(rows)

        try:
            g_trail = float(self.g_trail_secs.get())
        except (ValueError, AttributeError):
            g_trail = 5.0

        peak_rpm = float(rows['rpm'].max())
        rpm_max = max(int(_math.ceil(peak_rpm / 1000) * 1000), 6000)
        # Shift-light bands come off the redline, not the gauge scale (see
        # renderer_pil.auto_redline).
        _redline = _R.auto_redline(rows['rpm'].to_numpy())
        if _redline:
            rpm_max = max(rpm_max, int(_math.ceil(_redline / 1000.0) * 1000))
        _peak_spd = float(rows['speed'].max()) if 'speed' in rows.columns else 0.0
        speed_max = max(60, int(_math.ceil(_peak_spd / 10.0) * 10))

        GS = int(460 * scale); GX = int(10 * scale)
        cx = GX + GS // 2; cy = out_h_actual // 2; r = GS // 2 - int(12 * scale)
        _no_gauge = (P.get("dash8_layout") or P.get("style7_layout") or P.get("style8_layout")
                     or P.get("style11_layout") or P.get("style12_layout")
                     or P.get("style13_layout") or P.get("style14_layout")
                     or P.get("style20_layout"))
        bg = None if _no_gauge else _R._build_gauge_bg(
            cx, cy, r, w=out_w, h=out_h_actual, rpm_max=rpm_max, P=P)

        idx = (rows['ts'] - t_preview).abs().idxmin()
        row = rows.iloc[idx]
        t_now = float(row['ts'])
        trace = rows[(rows['ts'] >= t_now - g_trail) & (rows['ts'] <= t_now)]
        if _R.style_trail_fixed_secs(_R.STYLES.get(_style_now)):
            trace = rows[(rows['ts'] >= t_now - 10.0) & (rows['ts'] <= t_now)]

        peak_mask = (rows['ts'] >= t_now - 1.0) & (rows['ts'] <= t_now)
        shown_peak = int(rows.loc[peak_mask, 'rpm'].max()) if peak_mask.any() else None
        brake_val = (float(row['brake']) if 'brake' in row.index
                     and not np.isnan(row.get('brake', float('nan')))
                     else float(np.clip(-row['g_long'] / 1.2 * 100, 0, 100)))
        speed_col = self.g_trail_speed_colour.get()
        _img = _R.build_frame(
            float(row['rpm']), float(row['throttle']), float(row['speed']),
            int(row['gear']), float(row['g_lat']), float(row['g_long']), t_now,
            trace['g_lat'].tolist(), trace['g_long'].tolist(),
            laps, bg, cx, cy, r,
            w=out_w, h=out_h_actual, scale=scale,
            brake_pct=brake_val, rpm_max=rpm_max, peak_rpm=shown_peak,
            trace_speed=trace['speed'].tolist() if speed_col else None,
            speed_colour=speed_col, speed_max=speed_max, P=P,
            trace_throttle=trace['throttle'].tolist() if 'throttle' in trace.columns else None,
            trace_brake=trace['brake'].tolist() if 'brake' in trace.columns else None,
            trace_gear=trace['gear'].tolist() if 'gear' in trace.columns else None,
            chanA=float(row['chanA']) if 'chanA' in row.index else float('nan'),
            chanA_label=self.chanA_label.get(),
            chanB=float(row['chanB']) if 'chanB' in row.index else float('nan'),
            chanB_label=self.chanB_label.get(),
            chanA_unit=self.chanA_unit.get(), chanB_unit=self.chanB_unit.get(),
            backdrop_colour=self._backdrop_rgb(), redline=_redline)
        # Same crop the render will apply, so the preview is honest about the
        # output frame. The rect comes from this frame only; the panel is a solid
        # rectangle, so it does not move with the data.
        if self.crop_to_content.get() and _R.style_can_crop(P):
            _rect = _R.content_crop_rect([_img], self._backdrop_rgb()
                                         or P.get("chroma", (255, 0, 255)))
            if _rect:
                _img = _img.crop(_rect)
        return _img

    def _preview_frame(self):
        """Render a single frame at t_start and display it in a popup window."""
        if not DEPS_OK:
            messagebox.showerror("Missing Dependencies", f"Cannot render: {MISSING_DEP}")
            return
        if self.df is None:
            messagebox.showwarning("No File", "Please load a log file first.")
            return

        # Rebuild working data with current settings
        _T = self._t0()
        col_map = {ch: var.get() for ch, var in self.col_vars.items()
                   if var.get() not in ("(not detected)", "(None)", "")}
        self._prepare_working_df(col_map)
        _T = self._tlog("preview: prepare working data", _T)

        try:
            t_preview = float(self.t_start_var.get())
        except ValueError:
            t_preview = float(self._working_rows['ts'].min())

        try:
            g_trail = float(self.g_trail_secs.get())
        except ValueError:
            g_trail = 5.0

        try:
            lap_min_spd  = float(self.lap_min_speed.get())
            lap_est_time = float(self.lap_est_time.get())
            lap_offset   = float(self.lap_offset_var.get())
        except ValueError:
            lap_min_spd = 200.0; lap_est_time = 60.0; lap_offset = 0.0

        _style_now = self.style_var.get()

        # Mosaic preview — composite all style cells at reduced resolution
        _style2_now = self.style2_var.get() if hasattr(self,"style2_var") else "None"
        if _style2_now and _style2_now != "None":
            self._preview_dual()
            return

        resolution = _R.style_frame_size(_R.STYLES.get(_style_now))
        out_w, out_h = resolution
        rows = self._working_rows
        # Safety net: ensure selected channel columns are present in working rows
        try:
            _need_chan = (self.chanA_col.get() not in ('(None)','(not detected)','') or
                          self.chanB_col.get() not in ('(None)','(not detected)',''))
            if _need_chan and (rows is None or 'chanA' not in rows.columns):
                _cmap = {ch2: v2.get() for ch2, v2 in self.col_vars.items()
                         if v2.get() not in ('(not detected)', '(None)', '')}
                self._prepare_working_df(_cmap)
                rows = self._working_rows
        except Exception:
            pass
        try:
            laps = self._compute_laps()
        except Exception:
            laps = []
        _T = self._tlog("preview: lap detection", _T)
        img = self._render_dash_pil(t_preview, laps)
        out_h_actual = img.height
        out_w = img.width
        t_now = float(rows.iloc[(rows['ts'] - t_preview).abs().idxmin()]['ts'])
        _T = self._tlog("preview: dash frame", _T)
        from PIL import ImageTk, Image as _PILImage, ImageDraw as _PILDraw

        # ── Side previews of the standalone widgets (best-effort) ─────────────
        # These are the SAME graphics generated as separate overlay videos,
        # shown here so the whole output can be checked from one preview. They
        # sit beside the dash and are not integrated into it.
        # G-trace colour mode/channel (mirror the render job)
        _csel = self.gtrace_colour.get()
        if _csel == "Black & white":
            _cmode, _cchan = "bw", None
        elif _csel.startswith("Channel: "):
            _cmode = "channel"; _cchan = self._colour_label_to_col(_csel[len("Channel: "):])
        else:
            _cmode, _cchan = "speed", None
        _tm_line = (235, 235, 245)     # track-map line (mono white when speed colour off)
        # Widgets follow the Output panel's Start/End time range
        try:
            _t_lo = float(self.t_start_var.get())
        except (ValueError, AttributeError):
            _t_lo = None
        try:
            _t_hi = float(self.t_end_var.get())
        except (ValueError, AttributeError):
            _t_hi = None
        if _t_hi is not None and _t_lo is not None and _t_hi <= _t_lo:
            _t_lo = _t_hi = None

        _side_imgs = []   # (caption, PIL image)
        # Only preview widgets that are actually ticked for rendering.
        _gt_form = "target" if self.gtrace_form.get().startswith("Target") else "classic"
        try:
            _gt_trail_prev = float(self.gtrace_trail.get())   # G-plot's own trail
        except (ValueError, AttributeError):
            _gt_trail_prev = 40.0
        try:
            if (self.gen_gtrace.get() and _GT_OK
                    and 'g_lat' in rows.columns and 'g_long' in rows.columns):
                _side_imgs.append(("G-TRACE", _GT.render_preview_frame(
                    rows, t_now, resolution=(480, 480),
                    chroma=self._bd_rgb(self.gt_backdrop.get()),
                    transparent=False, g_trail_secs=_gt_trail_prev, form=_gt_form,
                    colour_mode=_cmode, colour_channel=_cchan,
                    g_max=self._gt_gmax())))
        except Exception as _e:
            print(f"g-trace preview skipped: {_e}", flush=True)
        try:
            if (self.gen_trackmap.get() and _TM_OK
                    and 'lat' in rows.columns and 'lon' in rows.columns):
                _side_imgs.append(("TRACK MAP", _TM.render_preview_frame(
                    rows, t_now, resolution=(480, 480),
                    chroma=self._bd_rgb(self.tm_backdrop.get()),
                    transparent=False, speed_colour=self.tm_speed_colour.get(),
                    line_colour=_tm_line, segment_secs=self._tm_segment_secs(),
                    t_start=_t_lo, t_end=_t_hi, laps=laps)))
        except Exception as _e:
            print(f"track-map preview skipped: {_e}", flush=True)
        try:
            if self.gen_lapdata.get() and _LD_OK and laps:
                _ld_mode_p = "list" if self.ld_mode.get().startswith("All") else "current"
                _ld_res_p = (300, 520) if _ld_mode_p == "list" else (300, 190)
                _side_imgs.append(("LAP DATA", _LD.render_preview_frame(
                    rows, t_now, laps, resolution=_ld_res_p,
                    chroma=self._bd_rgb(self.ld_backdrop.get()), transparent=False,
                    mode=_ld_mode_p, style=self._ld_style_key())))
        except Exception as _e:
            print(f"lap-data preview skipped: {_e}", flush=True)

        _T = self._tlog("preview: widget frames", _T)
        # Dash panel across the top, at full preview width
        disp_w = min(960, out_w)
        disp_h = int(out_h_actual * disp_w / out_w)
        dash_disp = img.convert("RGB").resize((disp_w, disp_h))

        # Widgets in a row UNDERNEATH the dash, scaled to share the width
        _gap = 14
        _cap_h = 22
        thumbs = []
        if _side_imgs:
            _n = len(_side_imgs)
            _slot_w = max(120, (disp_w - _gap * (_n - 1)) // _n)
            _row_h = 0
            for cap, im in _side_imgs:
                # fit inside the slot, preserving aspect, capped in height
                _sc = min(_slot_w / im.width, 300 / im.height)
                _tw, _th = max(1, int(im.width * _sc)), max(1, int(im.height * _sc))
                tim = im.convert("RGB").resize((_tw, _th))
                thumbs.append((cap, tim))
                _row_h = max(_row_h, _th)
        else:
            _row_h = 0

        total_w = disp_w
        total_h = disp_h + ((_gap + _cap_h + _row_h) if thumbs else 0)
        canvas = _PILImage.new("RGB", (total_w, total_h), (0, 0, 0))
        canvas.paste(dash_disp, (0, 0))
        _cd = _PILDraw.Draw(canvas)
        _x = 0
        _y0 = disp_h + _gap
        for cap, tim in thumbs:
            _cd.text((_x, _y0), cap, fill=(180, 210, 255))
            canvas.paste(tim, (_x, _y0 + _cap_h))
            _x += tim.width + _gap

        if self._preview_window and tk.Toplevel.winfo_exists(self._preview_window):
            self._preview_window.destroy()
        self._preview_window = tk.Toplevel(self)
        self._preview_window.title(f"Preview — t={t_now:.1f}s")
        self._preview_window.configure(bg="#000000")
        tk_img = ImageTk.PhotoImage(canvas)
        lbl = tk.Label(self._preview_window, image=tk_img, bg="#000000")
        lbl.image = tk_img   # keep reference
        lbl.pack()
        _n = len(_side_imgs)
        self.status_var.set(
            f"Preview rendered at t={t_now:.1f}s"
            + (f"  (+{_n} widget preview{'s' if _n != 1 else ''})" if _n else ""))


    def _preview_dual(self):
        """Render a single dual-style frame (1920x1080) for preview."""
        import numpy as _np
        from PIL import Image as _PI, ImageTk as _IT
        if self._working_rows is None:
            self.status_var.set("Load a file first."); return
        wr = self._working_rows
        try: t_now = float(self.t_start_var.get())
        except: t_now = float(wr["ts"].iloc[len(wr)//2])
        laps = self._compute_laps()
        self._set_arc_track_path(wr)
        rpm_max = int(wr["rpm"].max()) if "rpm" in wr.columns else 9000
        rpm_max = max(1000, (rpm_max//1000+1)*1000)
        idx = int(_np.searchsorted(wr["ts"].values, t_now))
        idx = min(idx, len(wr)-1)
        row = wr.iloc[idx]
        trail_mask = (wr["ts"] >= t_now - float(self.g_trail_secs.get() or 5)) & (wr["ts"] <= t_now)
        trail = wr[trail_mask]
        frame = self._build_dual_frame(row, trail, t_now, laps, rpm_max, replace_chroma=False)
        if frame is None: return
        dw = min(1280, frame.width)
        dh = int(frame.height * dw / frame.width)
        disp = frame.resize((dw, dh))
        import tkinter as _tk
        _pw = getattr(self, "_preview_window", None)
        if _pw is None or not _pw.winfo_exists():
            self._preview_window = _tk.Toplevel(self)
            self._preview_label = None
        self._preview_window.title(f"Dual Preview — t={t_now:.1f}s")
        photo = _IT.PhotoImage(disp)
        _pl = getattr(self, "_preview_label", None)
        if _pl is None or not _pl.winfo_exists():
            self._preview_label = _tk.Label(self._preview_window, image=photo)
            self._preview_label.pack()
        else:
            self._preview_label.configure(image=photo)
        self._preview_label.image = photo

    def _build_dual_frame(self, row, trail, t_now, laps, rpm_max, replace_chroma=True):
        """Render two styles side by side into a 1920x1080 frame.
        replace_chroma=False keeps magenta visible (for preview).
        replace_chroma=True replaces with black (for video export).
        """
        from PIL import Image as _PI
        import numpy as _np
        sn1 = self.style_var.get()
        sn2 = self.style2_var.get()
        OUT_W, OUT_H = 1920, 1080
        HALF_W = OUT_W // 2
        # Fill with chroma so unpainted regions are transparent in final video
        # For preview (replace_chroma=False) this shows as magenta
        # For export (replace_chroma=True) chroma gets replaced with black afterwards
        result = _PI.new("RGB", (OUT_W, OUT_H), self._backdrop_rgb())
        for slot, sn in enumerate([sn1, sn2]):
            if not sn or sn == "None": continue
            P = self._apply_backdrop_to_P(_R.STYLES.get(sn))
            if not P: continue
            # Determine natural resolution for this style
            is_full = P.get("style7_layout") or P.get("style8_layout")
            no_gauge = is_full or P.get("style11_layout") or P.get("style12_layout") or P.get("style13_layout") or P.get("style14_layout") or P.get("style20_layout") or P.get("dash8_layout")
            nat_w = HALF_W
            nat_h = OUT_H if is_full else int(HALF_W * 750 / 1920)
            s_scale = nat_w / _R.W_VID
            GS = int(460*s_scale); GX = int(10*s_scale)
            cx = GX+GS//2; cy = nat_h//2; r = GS//2-int(12*s_scale)
            _R._GAUGE_BG_CACHE.clear()
            bg = None if no_gauge else _R._build_gauge_bg(cx,cy,r,w=nat_w,h=nat_h,rpm_max=rpm_max,P=P)
            frame = _R.build_frame(
                float(row.get("rpm",0)), float(row.get("throttle",0)),
                float(row.get("speed",0)), int(row.get("gear",0)),
                float(row.get("g_lat",0)), float(row.get("g_long",0)),
                t_now, trail["g_lat"].tolist(), trail["g_long"].tolist(),
                laps, bg, cx, cy, r, w=nat_w, h=nat_h, scale=s_scale,
                brake_pct=float(row.get("brake",0)),
                rpm_max=rpm_max,
                speed_max=max(60, int(((float(row.get("speed",0))+20)//10+1)*10)),
                peak_rpm=int(trail["rpm"].max()) if "rpm" in trail.columns and len(trail) else None,
                trace_speed=None, speed_colour=False, P=P,
                chanA=float(row.get("chanA")) if "chanA" in row.index else float('nan'),
                chanA_label=self.chanA_label.get(),
                chanB=float(row.get("chanB")) if "chanB" in row.index else float('nan'),
                chanB_label=self.chanB_label.get(),
                chanA_unit=self.chanA_unit.get(), chanB_unit=self.chanB_unit.get(),
            backdrop_colour=self._backdrop_rgb())
            # Replace chroma with black for export, keep for preview
            if replace_chroma:
                chroma = tuple(P["chroma"])
                import numpy as np_
                arr = np_.array(frame)
                mask = (arr[:,:,0]==chroma[0])&(arr[:,:,1]==chroma[1])&(arr[:,:,2]==chroma[2])
                arr[mask] = [0,0,0]
                frame = _PI.fromarray(arr)
            # Centre vertically in the half-frame
            y_off = (OUT_H - nat_h) // 2
            result.paste(frame, (slot * HALF_W, y_off))
        return result

    def _preview_mosaic(self):
        """Render a single mosaic frame at reduced size for preview."""
        if self._working_rows is None:
            self.status_var.set("Load a file first.")
            return
        if not _MS_OK:
            self.status_var.set("renderer_multistyle.py not found.")
            return
        try:
            t_preview = float(self.t_start_var.get())
        except ValueError:
            t_preview = float(self._working_rows['ts'].min())
        try:
            laps = self._compute_laps()
        except Exception:
            laps = []
        import math as _math
        rows = self._working_rows
        # Safety net: ensure selected channel columns are present in working rows
        try:
            _need_chan = (self.chanA_col.get() not in ('(None)','(not detected)','') or
                          self.chanB_col.get() not in ('(None)','(not detected)',''))
            if _need_chan and (rows is None or 'chanA' not in rows.columns):
                _cmap = {ch2: v2.get() for ch2, v2 in self.col_vars.items()
                         if v2.get() not in ('(not detected)', '(None)', '')}
                self._prepare_working_df(_cmap)
                rows = self._working_rows
        except Exception:
            pass
        rpm_max = max(int(_math.ceil(float(rows['rpm'].max())/1000)*1000), 6000)
        speed_max = max(60, int(_math.ceil(float(rows['speed'].max())/10.0)*10)) if 'speed' in rows.columns else 200
        g_trail = float(self.g_trail_secs.get() or 5)
        speed_col = self.g_trail_speed_colour.get()

        _MS._build_all_gauge_bgs(rpm_max)
        idx = (rows['ts'] - t_preview).abs().idxmin()
        row = rows.iloc[idx]
        t_now = float(row['ts'])
        trail_mask = (rows['ts'] >= t_now - g_trail) & (rows['ts'] <= t_now)

        import numpy as np
        rows_np = {c: rows[c].to_numpy(np.float32)
                   for c in ['rpm','speed','throttle','brake','g_lat','g_long']}
        rows_np['gear'] = rows['gear'].to_numpy(np.int32)
        ts_arr = rows['ts'].to_numpy(np.float64)
        trail_idx = np.where(trail_mask)[0]

        fs = _MS.FrameState()
        fs.ts = t_now; fs.rpm = float(row['rpm']); fs.speed = float(row['speed'])
        fs.throttle = float(row['throttle']); fs.brake = float(row.get('brake', 0))
        fs.gear = int(row['gear']); fs.g_lat = float(row['g_lat'])
        fs.g_long = float(row['g_long'])
        fs.trace_glat  = rows_np['g_lat'][trail_idx].tolist()
        fs.trace_glong = rows_np['g_long'][trail_idx].tolist()
        fs.trace_speed = rows_np['speed'][trail_idx].tolist()

        from PIL import Image as _Img
        cells = [_MS._render_cell(sn, fs, laps, rpm_max, speed_col, g_trail, speed_max)
                 for sn in _MS.STYLE_ORDER]
        mosaic = _MS._composite_mosaic(cells)

        from PIL import ImageTk
        if self._preview_window and tk.Toplevel.winfo_exists(self._preview_window):
            self._preview_window.destroy()
        self._preview_window = tk.Toplevel(self)
        self._preview_window.title(f"Mosaic Preview — t={t_now:.1f}s")
        self._preview_window.configure(bg="#000000")
        disp_w = min(1280, mosaic.width)
        disp_h = int(mosaic.height * disp_w / mosaic.width)
        disp_img = mosaic.resize((disp_w, disp_h))
        tk_img = ImageTk.PhotoImage(disp_img)
        lbl = tk.Label(self._preview_window, image=tk_img, bg="#000000")
        lbl.image = tk_img
        lbl.pack()
        self.status_var.set(f"Mosaic preview at t={t_now:.1f}s")

    def _reload_with_ts(self, col_map=None):
        """Re-process working data using the selected time channel."""
        if self.df is None:
            return
        sel = self.ts_col_var.get()
        if sel and sel != '(auto)' and sel in self.df.columns:
            # Re-run the time conversion on the chosen column
            import pandas as _pd
            ts_raw = self.df[sel]
            ts_sample = ts_raw.dropna().head(10)
            try:
                ts_n = pd.to_numeric(ts_sample, errors='raise')
                if ts_n.iloc[0] > 1000:
                    self.df['_ts_recomputed'] = pd.to_numeric(self.df[sel], errors='coerce') / 1000.0
                else:
                    self.df['_ts_recomputed'] = pd.to_numeric(self.df[sel], errors='coerce')
            except Exception:
                self.df['_ts_recomputed'] = pd.to_numeric(self.df[sel], errors='coerce')
            self.ts_col = '_ts_recomputed'
        else:
            # Restore auto-detected column
            self.ts_col = self.df.columns[0]

        t_max = float(self.df[self.ts_col].max())
        self.t_end_var.set(f"{t_max:.1f}")
        self.max_t_label.set(f"Max: {t_max:.1f} s")

        if col_map is None:
            col_map = {ch: var.get() for ch, var in self.col_vars.items()
                       if var.get() not in ('(not detected)', '(None)', '')}
        self._prepare_working_df(col_map)
        self._update_lap_display()
        self.status_var.set(f"Time channel: {self.ts_col}  |  Max: {t_max:.1f}s")


    # ── Tabs and live previews ───────────────────────────────────────────────
    def _active_canvas(self):
        """The scrolling canvas of the tab currently on show (wheel target)."""
        try:
            return self._tab_canvases.get(self._nb.select())
        except Exception:
            return None

    def _make_preview_pane(self, parent, kind, caption):
        """A framed image area that shows this tab's overlay at the scrub time."""
        f = tk.Frame(parent, bg=DARK_CARD, padx=10, pady=8,
                     highlightbackground=BORDER_COL, highlightthickness=1)
        f.grid(row=0, column=0, sticky="nsew")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        tk.Label(f, text=caption, font=FONT_HEAD, bg=DARK_CARD,
                 fg=ACCENT).grid(row=0, column=0, sticky="w")
        holder = tk.Frame(f, bg=DARK_PANEL)
        holder.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        holder.grid_propagate(False)     # the image must not resize the pane
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        img = tk.Label(holder, bg=DARK_PANEL, fg=TEXT_SEC, font=FONT_SMALL,
                       text="Load a log file to see a preview",
                       anchor="center", justify="center")
        img.grid(row=0, column=0, sticky="nsew")
        self._pv_panes[kind] = img
        self._pv_holders[kind] = holder

        # Scrubber directly beneath the picture, so the time control sits with
        # the thing it controls. All tabs share self._pv_var.
        scrub = tk.Frame(f, bg=DARK_CARD)
        scrub.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        scrub.columnconfigure(1, weight=1)
        tk.Label(scrub, text="Time:", font=FONT_SMALL, bg=DARK_CARD,
                 fg=TEXT_SEC).grid(row=0, column=0, sticky="w", padx=(0, 8))
        sc = ttk.Scale(scrub, from_=0.0, to=1.0, orient="horizontal",
                       variable=self._pv_var, command=self._on_scrub)
        sc.grid(row=0, column=1, sticky="ew")
        tk.Label(scrub, textvariable=self._pv_time_lbl, font=FONT_MONO,
                 bg=DARK_CARD, fg=ACCENT2, width=9).grid(row=0, column=2,
                                                         sticky="w", padx=(8, 0))
        self._pv_scales.append(sc)
        holder.bind("<Configure>", lambda e, k=kind: self._on_pane_resize(k, e))
        return f

    def _sync_gplot_controls(self, *_a):
        """The X-Y graph is an opaque panel that needs no chroma key, so its
        backdrop choice does nothing - hide it for that form."""
        if not hasattr(self, "_gp_bd_cb"):
            return
        _xy = not self.gtrace_form.get().startswith("Target")
        for w in (self._gp_bd_lbl, self._gp_bd_cb):
            try:
                w.grid_remove() if _xy else w.grid()
            except Exception:
                pass

    def _on_pane_resize(self, kind, event=None):
        """Re-render after the pane is given a new size (window resize).

        Only when the box actually changes, so a run of Configure events during a
        drag does not queue a render per pixel.
        """
        if kind != getattr(self, "_pv_kind", None):
            return
        box = self._preview_box()
        if box == getattr(self, "_pv_last_box", None):
            return
        self._pv_last_box = box
        self._schedule_preview(250)

    def _sync_dash_controls(self, *_a):
        """Show only the controls the selected dash actually uses.

        Most dashes draw no trailing trace, so the trail length and speed-colour
        options do nothing for them; and only the panel dashes can be cropped out
        of their chroma frame.
        """
        if not hasattr(self, "_trail_row_w"):
            return
        P = _R.STYLES.get(self.style_var.get())
        has_trail = _R.style_has_trail(P)
        can_crop = _R.style_can_crop(P)
        for w, shown in ((self._trail_lbl_w, has_trail),
                         (self._trail_row_w, has_trail),
                         (self._crop_chk, can_crop)):
            if w is None:
                continue
            try:
                w.grid() if shown else w.grid_remove()
            except Exception:
                pass
        # Some dashes fill the frame opaquely (Dash 3, Dash 6b), so there is no
        # backdrop to choose - and a cropped dash has no chroma either.
        _bd_used = (not _R.style_is_opaque(P)
                    and not (can_crop and self.crop_to_content.get()))
        for w in (getattr(self, "_bd_lbl", None), getattr(self, "_bd_cb", None)):
            if w is None:
                continue
            try:
                w.grid() if _bd_used else w.grid_remove()
            except Exception:
                pass

    def _on_tab_changed(self):
        """Refresh the preview for the tab that just came into view."""
        try:
            kind = self._tab_keys.get(self._nb.select())
        except Exception:
            kind = None
        self._pv_kind = kind
        if kind:
            self._schedule_preview(120)

    def _on_scrub(self, _val=None):
        """Scrubber moved: update the readout and repreview."""
        t = self._scrub_time()
        if t is None:
            self._pv_time_lbl.set("--:--")
        else:
            _m, _s = divmod(max(0.0, t), 60)
            self._pv_time_lbl.set(f"{int(_m)}:{_s:05.2f}")
        self._schedule_preview(180)

    def _scrub_range(self):
        """(t_lo, t_hi) the scrubber spans: the selected output range."""
        lo = hi = None
        try:
            lo = float(self.t_start_var.get())
        except (ValueError, AttributeError, tk.TclError):
            lo = None
        try:
            hi = float(self.t_end_var.get())
        except (ValueError, AttributeError, tk.TclError):
            hi = None
        if self._working_rows is not None and len(self._working_rows):
            _ts = self._working_rows["ts"]
            if lo is None:
                lo = float(_ts.min())
            if hi is None:
                hi = float(_ts.max())
        if lo is None or hi is None or hi <= lo:
            return None, None
        return lo, hi

    def _scrub_time(self):
        """Absolute time the scrubber currently points at, or None."""
        lo, hi = self._scrub_range()
        if lo is None:
            return None
        try:
            frac = float(self._pv_var.get())
        except Exception:
            frac = 0.0
        return lo + max(0.0, min(1.0, frac)) * (hi - lo)

    def _schedule_preview(self, delay=300):
        """Debounced preview refresh - settings often change several at a time."""
        if getattr(self, "_pv_job", None):
            try:
                self.after_cancel(self._pv_job)
            except Exception:
                pass
        self._pv_job = self.after(delay, self._refresh_tab_preview)

    def _preview_box(self):
        """Pixel box the preview image must fit inside.

        Measured from the NOTEBOOK, never from the label holding the image: the
        label grows to fit whatever it was last given, so sizing off it fed back
        on itself and the preview changed size on every refresh. The notebook is
        sized by the window, so this is stable. Rounded to 20px steps so a stray
        pixel of resize does not trigger a different render.
        """
        hold = (getattr(self, "_pv_holders", None) or {}).get(
            getattr(self, "_pv_kind", None))
        hw = hh = 0
        if hold is not None:
            try:
                hw, hh = hold.winfo_width(), hold.winfo_height()
            except Exception:
                hw = hh = 0
        if hw > 80 and hh > 80:
            # 4px of slack so the image never lands exactly on the edge
            return (int((hw - 4) // 20 * 20), int((hh - 4) // 20 * 20))
        # Not laid out yet: estimate from the window.
        try:
            nb_w = self._nb.winfo_width() or 0
            nb_h = self._nb.winfo_height() or 0
        except Exception:
            nb_w = nb_h = 0
        if nb_w < 400:
            nb_w, nb_h = 1400, 640
        controls_w = self.LABEL_COL_W + self.HELP_W + 200
        box_w = max(self.PREVIEW_MIN_W, nb_w - controls_w - 90)
        box_h = max(220, nb_h - 150)
        return (int(box_w // 20 * 20), int(box_h // 20 * 20))

    def _pv_show_msg(self, kind, msg):
        lbl = (getattr(self, "_pv_panes", None) or {}).get(kind)
        if lbl is not None:
            lbl.config(image="", text=msg)
            lbl.image = None

    def _refresh_tab_preview(self):
        """Render the visible tab's overlay from the live settings."""
        self._pv_job = None
        kind = getattr(self, "_pv_kind", None)
        lbl = (getattr(self, "_pv_panes", None) or {}).get(kind)
        if kind is None or lbl is None:
            return
        if not DEPS_OK:
            self._pv_show_msg(kind, f"Missing dependency: {MISSING_DEP}")
            return
        if self.df is None:
            self._pv_show_msg(kind, "Load a log file to see a preview")
            return
        if self._working_rows is None:
            try:
                col_map = {ch: v.get() for ch, v in self.col_vars.items()
                           if v.get() not in ("(not detected)", "(None)", "")}
                self._prepare_working_df(col_map)
            except Exception as e:
                self._pv_show_msg(kind, f"Could not prepare data: {e}")
                return
        t_now = self._scrub_time()
        if t_now is None:
            self._pv_show_msg(kind, "Set a valid Start / End range")
            return
        box_w, box_h = self._preview_box()
        self._loading_start("Rendering preview")
        try:
            img = self._render_preview_image(kind, t_now, box_w, box_h)
        except Exception as e:
            self._pv_show_msg(kind, f"Preview unavailable:\n{e}")
            return
        finally:
            self._loading_stop()
        if img is None:
            return
        from PIL import ImageTk
        tk_img = ImageTk.PhotoImage(img)
        lbl.config(image=tk_img, text="")
        lbl.image = tk_img          # keep a reference or Tk discards it

    def _render_preview_image(self, kind, t_now, box_w, box_h):
        """PIL image of one overlay at t_now, scaled to fit (box_w, box_h)."""
        from PIL import Image as _PI
        rows = self._working_rows
        try:
            laps = self._compute_laps()
        except Exception:
            laps = []
        _t_lo, _t_hi = self._scrub_range()

        if kind == "dash":
            # Render at roughly preview size: the layout scales off the frame
            # height, so this is the same picture, just cheaper.
            img = self._render_dash_pil(t_now, laps, max_w=max(480, int(box_w * 1.4)))
        elif kind == "gplot":
            if not _GT_OK:
                raise RuntimeError("G-plot renderer not available")
            if "g_lat" not in rows.columns or "g_long" not in rows.columns:
                raise RuntimeError("No G channels mapped")
            _csel = self.gtrace_colour.get()
            if _csel == "Black & white":
                _cmode, _cchan = "bw", None
            elif _csel.startswith("Channel: "):
                _cmode = "channel"
                _cchan = self._colour_label_to_col(_csel[len("Channel: "):])
            else:
                _cmode, _cchan = "speed", None
            try:
                _trail = float(self.gtrace_trail.get())
            except (ValueError, AttributeError):
                _trail = 40.0
            img = _GT.render_preview_frame(
                rows, t_now, resolution=(600, 600),
                chroma=self._bd_rgb(self.gt_backdrop.get()), transparent=False,
                g_trail_secs=_trail,
                form="target" if self.gtrace_form.get().startswith("Target") else "classic",
                colour_mode=_cmode, colour_channel=_cchan, g_max=self._gt_gmax())
        elif kind == "trackmap":
            if not _TM_OK:
                raise RuntimeError("Track map renderer not available")
            if "lat" not in rows.columns or "lon" not in rows.columns:
                raise RuntimeError("No GPS lat / lon channels mapped")
            img = _TM.render_preview_frame(
                rows, t_now, resolution=(600, 600),
                chroma=self._bd_rgb(self.tm_backdrop.get()), transparent=False,
                speed_colour=self.tm_speed_colour.get(),
                line_colour=(235, 235, 245),
                segment_secs=self._tm_segment_secs(),
                t_start=_t_lo, t_end=_t_hi, laps=laps)
        elif kind == "lapdata":
            if not _LD_OK:
                raise RuntimeError("Lap data renderer not available")
            if not laps:
                raise RuntimeError("No laps detected yet")
            _mode = "list" if self.ld_mode.get().startswith("All") else "current"
            _res = (400, 700) if _mode == "list" else (400, 220)
            img = _LD.render_preview_frame(
                rows, t_now, laps, resolution=_res,
                chroma=self._bd_rgb(self.ld_backdrop.get()), transparent=False,
                mode=_mode, style=self._ld_style_key())
        else:
            return None

        sc = min(box_w / img.width, box_h / img.height, 1.6)
        if abs(sc - 1.0) > 0.001:
            img = img.resize((max(1, int(img.width * sc)),
                              max(1, int(img.height * sc))), _PI.LANCZOS)
        # Belt and braces: the Tk label clips anything larger than its cell, so a
        # preview must never come back bigger than the box it was asked for.
        if img.width > box_w or img.height > box_h:
            _s2 = min(box_w / img.width, box_h / img.height)
            img = img.resize((max(1, int(img.width * _s2)),
                              max(1, int(img.height * _s2))), _PI.LANCZOS)
        return img.convert("RGB")

    def _fix_combo(self, combo):
        """Stop the mouse wheel from silently changing a dropdown's value.

        ttk comboboxes step through their values on wheel events, so scrolling
        the page with the pointer over a closed dropdown would quietly remap a
        channel (e.g. a G channel jumping to an ECEF column). We consume the
        event and scroll the page instead; an OPEN dropdown list still scrolls
        normally because that popdown is a separate window.
        """
        def _wheel(e):
            cv = self._active_canvas()
            if cv is not None:
                try:
                    if getattr(e, 'num', None) == 4:
                        cv.yview_scroll(-1, "units")
                    elif getattr(e, 'num', None) == 5:
                        cv.yview_scroll(1, "units")
                    else:
                        cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
                except Exception:
                    pass
            return "break"          # never let ttk change the selection
        for _seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                combo.bind(_seq, _wheel, add="+")
            except Exception:
                pass

    # ── Loading indicator ────────────────────────────────────────────────────
    _SPIN = ("|", "/", "-", "\\")

    def _loading_start(self, msg="Loading"):
        """Show an animated loading sign (thread-safe: call via self.after)."""
        self._loading_msg = msg
        self._loading_i = 0
        if getattr(self, "_loading_job", None):
            try:
                self.after_cancel(self._loading_job)
            except Exception:
                pass
            self._loading_job = None

        def _tick():
            self._loading_i = (self._loading_i + 1) % len(self._SPIN)
            dots = "." * (1 + (self._loading_i % 3))
            self._loading_var.set(
                f"{self._SPIN[self._loading_i]}  {self._loading_msg}{dots}")
            self._loading_job = self.after(180, _tick)

        _tick()
        try:
            self.config(cursor="watch")
        except Exception:
            pass

    def _loading_msg_set(self, msg):
        """Update the text without restarting the animation."""
        self._loading_msg = msg

    def _loading_stop(self, final=""):
        if getattr(self, "_loading_job", None):
            try:
                self.after_cancel(self._loading_job)
            except Exception:
                pass
        self._loading_job = None
        self._loading_var.set(final)
        try:
            self.config(cursor="")
        except Exception:
            pass

    @staticmethod
    def _format_lap_table(laps, best_lt, mode, box_w=96):
        """Lay the lap list out in columns so it uses the panel width instead of
        one long scrolling column."""
        def _mmss(t, dp):
            m, s = divmod(max(0.0, float(t)), 60.0)
            return f"{int(m)}:{s:0{3 + dp}.{dp}f}"

        # Every field is padded to the widest value in the session, so the digits
        # line up down the page whatever the lap count or the times.
        # The "at" column stays in seconds: it is what you type into Start / End.
        cells = [(str(i), _mmss(lt, 3), "*" if lt == best_lt else " ", f"{st:.2f}")
                 for i, (st, _et, lt) in enumerate(laps, 1)]
        w_no = max([len(c[0]) for c in cells] + [3])
        w_lt = max([len(c[1]) for c in cells] + [4])
        w_at = max([len(c[3]) for c in cells] + [6])
        blk_w = w_no + 2 + w_lt + 1 + 2 + w_at
        sep = "   |   "
        entries = [f"{c[0]:>{w_no}}  {c[1]:>{w_lt}}{c[2]}  {c[3]:>{w_at}}"
                   for c in cells]
        # 3 spaces after "Time" to clear the best-lap star in the rows below.
        header = f"{'Lap':>{w_no}}  {'Time':>{w_lt}}   {'At (s)':>{w_at}}"

        # One column. Side-by-side blocks only line up in a true monospace font,
        # and it is not worth betting the readability of the lap list on which
        # fonts a machine happens to have installed.
        n_cols = 1
        n_rows = len(entries)
        lines = [f"  {len(laps)} lap(s) [{mode}]   best: {_mmss(best_lt, 3)}"
                 f"   (* = best)", ""]
        lines.append("  " + sep.join([header] * n_cols))
        lines.append("  " + sep.join(["-" * blk_w] * n_cols))
        for r in range(n_rows):
            row = []
            for c in range(n_cols):
                k = c * n_rows + r                   # fill down each column
                row.append(entries[k] if k < len(entries) else " " * blk_w)
            lines.append("  " + sep.join(row).rstrip())
        return "\n".join(lines)

    def _t0(self):
        """Start a phase timer (diagnostics printed to the console)."""
        import time
        return time.time()

    def _tlog(self, label, t0):
        """Print how long a phase took, so slow steps are identifiable."""
        import time
        dt = time.time() - t0
        if dt >= 0.05:                      # only report anything noticeable
            print(f"[timing] {label:34s} {dt*1000:7.0f} ms", flush=True)
        return time.time()

    def _schedule_lap_update(self, delay=120, msg="Detecting laps"):
        """Debounced lap detection with a visible loading sign.

        Lap detection (GPS crossing / speed peaks) walks the whole sample array,
        so it blocks briefly. Showing the sign and letting Tk paint it *before*
        the work starts means the window no longer looks frozen.
        """
        job = getattr(self, "_lap_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._loading_start(msg)
        try:
            self.update_idletasks()      # paint the sign before we block
        except Exception:
            pass

        def _run():
            self._lap_job = None
            try:
                self._update_lap_display()
            except Exception as _e:
                self.status_var.set(f"Lap detection failed: {_e}")
            finally:
                self._loading_stop()

        self._lap_job = self.after(delay, _run)

    def _schedule_rebuild(self, delay=250):
        """Coalesce rapid channel-mapping changes into a single rebuild, so the
        UI stays responsive instead of resampling on every keystroke/selection."""
        job = getattr(self, "_rebuild_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass

        def _run():
            self._rebuild_job = None
            if self.df is None:
                self._loading_stop()
                return
            col_map = {ch: v.get() for ch, v in self.col_vars.items()
                       if v.get() not in ('(not detected)', '(None)', '')}
            try:
                self.status_var.set("Updating channels…")
                self._prepare_working_df(col_map)
                self._update_lap_display()
                self.status_var.set("Channels updated.")
            except Exception as _e:
                self.status_var.set(f"Channel update failed: {_e}")
            finally:
                self._loading_stop()

        # Show the sign now and let Tk paint it before the blocking work starts
        self._loading_start("Updating channels")
        try:
            self.update_idletasks()
        except Exception:
            pass
        self._rebuild_job = self.after(delay, _run)

    def _bd_rgb(self, name):
        """Backdrop selection name -> RGB fill / chroma-key colour."""
        return {"Chroma magenta": (255, 0, 255),
                "Chroma green":   (0, 255, 0),
                "Dark":           (22, 22, 28)}.get(name, (255, 0, 255))

    def _backdrop_rgb(self):
        """Dashboard backdrop -> RGB (used by the main dash render/preview)."""
        return self._bd_rgb(self.backdrop_var.get())

    def _gt_gmax(self):
        """G-plot 'Max range' -> float G, or None for auto."""
        v = self.gtrace_gmax.get() if hasattr(self, "gtrace_gmax") else "Auto"
        if v == "Auto":
            return None
        try:
            return float(v)
        except ValueError:
            return None

    def _ld_style_key(self):
        """Lap-data 'Graphic style' label -> lapdata_render style key."""
        v = (self.ld_style.get() if hasattr(self, "ld_style") else "") or ""
        # Matched on the dash the graphic is lifted from, so the wording of the
        # label can change without breaking this. Split on the separator and
        # compare the whole token: a prefix test made "Dash 10" match "Dash 1".
        _tag = v.split("·")[0].strip()
        _BY_DASH = {"Dash 1": "cells_light", "Dash 2": "cells_dark",
                    "Dash 5": "panels", "Dash 6": "logger", "Dash 7": "hud",
                    "Dash 8b": "para", "Dash 10": "plain"}
        if _tag in _BY_DASH:
            return _BY_DASH[_tag]
        # legacy labels
        for _tag, _key in (("HUD", "hud"), ("Logger", "logger"), ("Plain", "plain"),
                           ("Cells dark", "cells_dark"), ("Cells light", "cells_light"),
                           ("Slanted", "para")):
            if v.startswith(_tag):
                return _key
        return "panels"

    def _tm_segment_secs(self):
        """Track-map 'Map amount' -> total window length in seconds (centred on
        the car), or None for All."""
        v = self.tm_amount.get() if hasattr(self, "tm_amount") else "All"
        if v == "All":
            return None
        try:
            return float(v.split("s")[0])      # "30s window" -> 30
        except Exception:
            return None

    def _apply_backdrop_to_P(self, P):
        """Return a copy of style params P with the hard-coded magenta chroma
        recoloured to the chosen master backdrop. Leaves P untouched for the
        default (magenta)."""
        col = self._backdrop_rgb()
        if tuple(col) == (255, 0, 255) or P is None:
            return P
        P2 = dict(P)
        for k, v in list(P2.items()):
            if (isinstance(v, tuple) and len(v) >= 3
                    and v[0] == 255 and v[1] == 0 and v[2] == 255):
                P2[k] = tuple(col) + tuple(v[3:])
        return P2

    def _render_overlays(self, t_start, t_end, main_output_path, fps):
        """Render the optional standalone overlay videos (track map and/or
        G-force trace) sequentially on a background thread, after the main render."""
        import os as _os
        import numpy as _np
        rows = self._working_rows
        _base, _ext = _os.path.splitext(main_output_path)

        # Build the job queue based on what's ticked + data availability
        jobs = []   # each: (label, module, out_path, kwargs, skip_reason)
        if self.gen_trackmap.get():
            has_gps = (_TM_OK and rows is not None and 'lat' in rows.columns
                       and 'lon' in rows.columns
                       and not _np.isnan(rows['lat'].to_numpy()).all()
                       and not _np.isnan(rows['lon'].to_numpy()).all())
            if not _TM_OK:
                jobs.append(("track map", None, None, None, "trackmap_render.py not found"))
            elif not has_gps:
                jobs.append(("track map", None, None, None, "no usable GPS lat/lon"))
            else:
                # Lap boundaries let the track map colour the current lap and
                # show all laps as the dark ghost outline.
                try:
                    _laps_tm = self._compute_laps()
                except Exception:
                    _laps_tm = None
                # Track map's own backdrop
                _tm_transparent = False
                _tm_chroma = self._bd_rgb(self.tm_backdrop.get())
                # Line is mono white when Speed colour is off
                _tm_show_lap = True
                _tm_line_col = (235, 235, 245)
                jobs.append(("track map", _TM, f"{_base}_trackmap.mp4",
                             dict(resolution=(720, 720),
                                  speed_colour=self.tm_speed_colour.get(),
                                  transparent=_tm_transparent,
                                  chroma=_tm_chroma,
                                  line_colour=_tm_line_col,
                                  show_current_lap=_tm_show_lap,
                                  segment_secs=self._tm_segment_secs(),
                                  laps=_laps_tm, map_fps=10.0), None))
        if self.gen_gtrace.get():
            import numpy as _np2
            _has_cols = (_GT_OK and rows is not None and 'g_lat' in rows.columns
                         and 'g_long' in rows.columns)
            # The working df fills unmapped channels with 0.0, so "columns exist"
            # isn't enough — require some non-zero G data, else the trace is empty.
            _has_data = False
            if _has_cols:
                _gl = _np2.asarray(rows['g_lat'], dtype=float)
                _gg = _np2.asarray(rows['g_long'], dtype=float)
                _has_data = bool(_np2.nanmax(_np2.abs(_gl)) > 0.01
                                 or _np2.nanmax(_np2.abs(_gg)) > 0.01)
            if not _GT_OK:
                jobs.append(("G-trace", None, None, None, "gtrace_render.py not found"))
            elif not _has_data:
                jobs.append(("G-trace", None, None, None,
                             "no G-force data (map G-Lat / G-Long columns first)"))
            else:
                try:
                    _gt_trail = float(self.gtrace_trail.get())
                except (ValueError, AttributeError):
                    _gt_trail = 2.0
                # form
                _form = "target" if self.gtrace_form.get().startswith("Target") else "classic"
                # colour mode + channel
                _csel = self.gtrace_colour.get()
                if _csel == "Black & white":
                    _cmode, _cchan = "bw", None
                elif _csel.startswith("Channel: "):
                    _cmode = "channel"
                    _cchan = self._colour_label_to_col(_csel[len("Channel: "):])
                else:
                    _cmode, _cchan = "speed", None
                # target form fills a large square; classic keeps its wide plot
                _res = (1000, 1000) if _form == "target" else (1280, 1040)
                jobs.append(("G-trace", _GT, f"{_base}_gtrace.mp4",
                             dict(resolution=_res,
                                  form=_form,
                                  speed_colour=(_cmode == "speed"),
                                  colour_mode=_cmode, colour_channel=_cchan,
                                  chroma=self._bd_rgb(self.gt_backdrop.get()), transparent=False,
                                  g_max=self._gt_gmax(),
                                  g_trail_secs=_gt_trail), None))

        if self.gen_lapdata.get():
            try:
                _laps_ld = self._compute_laps()
            except Exception:
                _laps_ld = None
            if not _LD_OK:
                jobs.append(("lap data", None, None, None, "lapdata_render.py not found"))
            elif not _laps_ld:
                jobs.append(("lap data", None, None, None,
                             "no laps detected (check the lap detection settings)"))
            else:
                _ld_mode = "list" if self.ld_mode.get().startswith("All") else "current"
                _ld_style = self._ld_style_key()
                _ld_res = (560, 760) if _ld_mode == "list" else (560, 320)
                jobs.append(("lap data", _LD, f"{_base}_lapdata.mp4",
                             dict(resolution=_ld_res,
                                  chroma=self._bd_rgb(self.ld_backdrop.get()),
                                  transparent=False, laps=_laps_ld,
                                  mode=_ld_mode, style=_ld_style, map_fps=10.0), None))

        app_ref = self
        made = []
        skipped = []

        def _run_jobs():
            for (label, module, out_path, kwargs, skip) in jobs:
                if app_ref._cancel_flag:
                    break
                if skip is not None:
                    skipped.append(f"{label}: {skip}")
                    continue
                _done = {}
                def _prog(pct, _lbl=label):
                    self.progress.set(int(pct*100))
                    self.status_var.set(f"Rendering {_lbl}… {int(pct*100)}%")
                def _dn(path, _lbl=label):
                    _done['path'] = path
                def _er(msg, _lbl=label):
                    _done['err'] = msg
                self.status_var.set(f"Rendering {label}…")
                module.render_video(rows, t_start, t_end, out_path, fps,
                                    _prog, _dn, _er,
                                    lambda: app_ref._cancel_flag, **kwargs)
                if 'path' in _done:
                    made.append(_done['path'])
                elif 'err' in _done:
                    skipped.append(f"{label}: {_done['err']}")
            # Finished — report on the UI thread
            self._unlock_ui()
            if app_ref._cancel_flag:
                self.status_var.set("Cancelled.")
                return
            _msg = f"Overlay video:\n{main_output_path}\n"
            if made:
                _msg += "\nAlso created:\n" + "\n".join(made)
            if skipped:
                _msg += "\n\nSkipped:\n" + "\n".join(skipped)
            self.status_var.set("✓ Done!  All graphics saved.")
            messagebox.showinfo("Complete", _msg)

        threading.Thread(target=_run_jobs, daemon=True).start()

    def _start_render(self):
        if not DEPS_OK:
            messagebox.showerror("Missing Dependencies",
                f"Cannot render: {MISSING_DEP}\nRun: pip install pandas numpy pillow aggdraw")
            return
        if self.df is None:
            messagebox.showwarning("No File", "Please load a log file first.")
            return

        # Rebuild working df from current dropdown selections
        col_map = {ch: var.get() for ch, var in self.col_vars.items()
                   if var.get() not in ("(not detected)", "(None)", "")}
        self._prepare_working_df(col_map)
        # Apply per-channel smoothing if selected
        if self._working_rows is not None and hasattr(self, 'smooth_vars'):
            for ch2, svar in self.smooth_vars.items():
                if svar.get() and ch2 in self._working_rows.columns:
                    self._working_rows = self._working_rows.copy()
                    self._working_rows[ch2] = _R.apply_lowpass(self._working_rows[ch2])

        # (Inversions are applied in _prepare_working_df, so preview and render
        #  always agree.)

        # Resample working rows to selected fps
        import numpy as _np
        try:
            sel_fps = int(self.fps_var.get())
        except Exception:
            sel_fps = getattr(self, '_data_fps', 25)
        if sel_fps < 200 and self._working_rows is not None:
            rows30 = self._working_rows
            t0 = rows30['ts'].min(); t1 = rows30['ts'].max()
            # Snap grid to t_start so first rendered frame aligns exactly
            try:
                _t_snap = float(self.t_start_var.get())
            except ValueError:
                _t_snap = t0
            # Build grid that passes through _t_snap
            # Integer frame indexing from session start — no float drift
            _n_before = int(round((_t_snap - t0) * sel_fps))
            _grid_start = t0 + _n_before / sel_fps
            _frame_count = int(round((t1 - t0) * sel_fps))
            tt = t0 + _np.arange(_frame_count) / sel_fps
            tt = tt[(tt >= t0) & (tt <= t1 + 1e-9)]
            import pandas as _pd
            rs = pd.DataFrame({'ts': tt})
            for col2 in ['rpm','throttle','speed','g_lat','g_long']:
                rs[col2] = _np.interp(tt, rows30['ts'].values, rows30[col2].values)
            rs['gear'] = _np.round(_np.interp(tt, rows30['ts'].values, rows30['gear'].values)).astype(int)
            if 'brake' in rows30.columns:
                rs['brake'] = _np.interp(tt, rows30['ts'].values, rows30['brake'].values.astype(float))
            for _gc in ['lat','lon']:
                if _gc in rows30.columns:
                    rs[_gc] = _np.interp(tt, rows30['ts'].values, rows30[_gc].values)
            self._working_rows = rs

        # Validate time range
        try:
            t_start = float(self.t_start_var.get())
            t_end   = float(self.t_end_var.get())
        except ValueError:
            messagebox.showerror("Invalid Time", "Start and End times must be numbers.")
            return
        if t_end <= t_start:
            messagebox.showerror("Invalid Range", "End time must be greater than start time.")
            return

        # Output path
        out_dir  = self.output_dir.get()
        out_name = self.output_name.get()
        if not out_dir:
            messagebox.showwarning("No Output Folder", "Please select an output folder.")
            return
        if not out_name.endswith('.mp4'):
            out_name += '.mp4'
        output_path = os.path.join(out_dir, out_name)

        # Check ffmpeg
        try:
            _R.get_ffmpeg_path()
        except FileNotFoundError as e:
            messagebox.showerror("ffmpeg Not Found", str(e))
            return

        # Lock all inputs
        self._cancel_flag = False
        self._lock_ui()
        self.progress.set(0)
        self.status_var.set("Rendering… this may take a while for long sessions.")

        import time as _time
        self._render_start_time = _time.time()

        def progress_cb(pct):
            self.progress.set(pct)
            elapsed = _time.time() - self._render_start_time
            if pct > 2:
                eta_s = int(elapsed / pct * (100 - pct))
                eta_str = f"{eta_s//60}m {eta_s%60:02d}s" if eta_s >= 60 else f"{eta_s}s"
                self.status_var.set(f"Rendering… {pct}%  ·  ETA {eta_str}")
            else:
                self.status_var.set(f"Rendering… {pct}%")
            self.update_idletasks()

        def done_cb():
            self.progress.set(100)
            # Render optional overlay graphics as separate files, in sequence.
            if not self._cancel_flag and (self.gen_trackmap.get() or self.gen_gtrace.get()):
                self._render_overlays(t_start, t_end, output_path, fps)
                return
            self._unlock_ui()
            if self._cancel_flag:
                self.status_var.set("Cancelled.")
            else:
                self.status_var.set(f"✓ Done!  Saved to: {output_path}")
                messagebox.showinfo("Complete", f"Video saved to:\n{output_path}")

        def error_cb(msg):
            self._unlock_ui()
            self.status_var.set(f"Error: {msg}")
            messagebox.showerror("Render Error", msg)

        style = self.style_var.get()

        try:
            fps = int(self.fps_var.get())
            fps = max(1, min(120, fps))
        except (ValueError, AttributeError):
            fps = 30

        _style2_res = self.style2_var.get() if hasattr(self,"style2_var") else "None"
        if _style2_res and _style2_res != "None":
            resolution = (1920, 1080)
        else:
            resolution = _R.style_frame_size(_R.STYLES.get(style))
        try:
            g_trail = float(self.g_trail_secs.get())
        except ValueError:
            g_trail = 5.0
        try:
            lap_offset = float(self.lap_offset_var.get())
        except ValueError:
            lap_offset = 0.0
        try:
            lap_min_spd = float(self.lap_min_speed.get())
        except ValueError:
            lap_min_spd = 200.0
        try:
            lap_est_time = float(self.lap_est_time.get())
        except ValueError:
            lap_est_time = 60.0
        # Compute laps using selected method — no fallback
        try:
            _precomputed_laps = self._compute_laps()
        except Exception as _e:
            self.status_var.set(f"Lap detection error: {_e}")
            return
        if self._working_rows is not None:
            self._set_arc_track_path(self._working_rows)

        # Build delta reference if enabled

        app_ref = self
        style2 = self.style2_var.get() if hasattr(self,"style2_var") else "None"

        # Dual style mode — two styles side by side at 1920x1080
        if style2 and style2 != "None":
            def _dual_render_worker():
                try:
                    import numpy as _np, subprocess as _sp
                    wr = self._working_rows
                    t0 = float(self.t_start_var.get() or wr["ts"].min())
                    t1 = float(self.t_end_var.get() or wr["ts"].max())
                    mask = (wr["ts"] >= t0) & (wr["ts"] <= t1)
                    seg = wr[mask].reset_index(drop=True)
                    if len(seg) < 2:
                        self.after(0, lambda: error_cb("No data in time range."))
                        return
                    rpm_max_d = max(1000,(int(seg["rpm"].max())//1000+1)*1000)
                    total = len(seg)
                    g_trail = float(self.g_trail_secs.get() or 5)
                    OUT_W, OUT_H = 1920, 1080
                    ffmpeg_exe = _R.get_ffmpeg_path()
                    cmd = [ffmpeg_exe, "-y",
                           "-f","rawvideo","-vcodec","rawvideo",
                           "-s",f"{OUT_W}x{OUT_H}","-pix_fmt","rgb24",
                           "-r",str(fps),"-i","pipe:0",
                           "-c:v","libx264","-preset","fast",
                           "-crf","18","-pix_fmt","yuv420p",
                           output_path]
                    ff = _sp.Popen(cmd, stdin=_sp.PIPE, stderr=_sp.DEVNULL)
                    for fi, (_, row) in enumerate(seg.iterrows()):
                        if app_ref._cancel_flag: break
                        t_now = float(row["ts"])
                        trail_mask = (wr["ts"] >= t_now-g_trail) & (wr["ts"] <= t_now)
                        trail = wr[trail_mask]
                        frame = self._build_dual_frame(row, trail, t_now,
                                                        _precomputed_laps, rpm_max_d,
                                                        replace_chroma=True)
                        if frame:
                            ff.stdin.write(_np.array(frame).tobytes())
                        if fi % 30 == 0:
                            pct = int(fi/total*100)
                            self.after(0, lambda p=pct: progress_cb(p))
                    ff.stdin.close()
                    ff.wait()
                    self.after(0, done_cb)
                except Exception as _e:
                    _emsg = str(_e)
                    self.after(0, lambda m=_emsg: error_cb(m))
            threading.Thread(target=_dual_render_worker, daemon=True).start()
            return

        # Mosaic removed — dual-style handled above
        if False and style == "All Styles (Mosaic)":
            if not _MS_OK:
                self.status_var.set("renderer_multistyle.py not found.")
                return
            # Use already-computed laps (same ones used for delta_ref)
            t = threading.Thread(
                target=_MS.render_multistyle_video,
                args=(self._working_rows, t_start, t_end, output_path,
                      fps, _precomputed_laps),
                kwargs=dict(
                    progress_cb=progress_cb,
                    done_cb=done_cb,
                    error_cb=error_cb,
                    cancel_check=lambda: app_ref._cancel_flag,
                    g_trail_secs=g_trail,
                    speed_colour=self.g_trail_speed_colour.get(),
                ),
                daemon=True)
            t.start()
            return

        t = threading.Thread(
            target=_R.render_video,
            args=(self._working_rows, t_start, t_end,
                  output_path, fps, progress_cb, done_cb, error_cb,
                  lambda: app_ref._cancel_flag,
                  self.filter_rpm.get(), self.filter_speed.get(),
                  resolution, g_trail, lap_offset,
                  lap_min_spd, lap_est_time,
                  self.g_trail_speed_colour.get(), style,
                  _precomputed_laps),
            kwargs={
                "chanA_label": self.chanA_label.get(),
                "chanB_label": self.chanB_label.get(),
                "chanA_unit": self.chanA_unit.get(),
                "chanB_unit": self.chanB_unit.get(),
                "backdrop_colour": self._backdrop_rgb(),
                "crop_to_content": self.crop_to_content.get(),
            },
            daemon=True)
        t.start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
