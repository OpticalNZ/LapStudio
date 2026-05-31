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
    import renderer_multistyle as _MS
    _MS_OK = True
    import renderer_pil as _R
    DEPS_OK = True
except ImportError as e:
    DEPS_OK = False
    _MS_OK = False
    _MS = None
    MISSING_DEP = str(e)

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
    'speed':    ['gps_speed', 'vehicle speed', 'ground speed', 'speed kph', 'speed mph', 'speed', 'kph', 'mph', 'velocity'],
    'gear':     ['ecu_gear', 'gear position', 'gear'],
    'throttle': ['ecu_throttle', 'throttle position', 'tps', 'throttle'],
    'brake':    ['front_brake', 'brake pressure', 'brake', 'brakepressure', 'brake press', 'hydraulic', 'brake psi', 'brake bar'],
    'g_lat':    ['gps_latacc', 'g-lat', 'glat', 'lateral g', 'lat g', 'lateral', 'g lat', 'accel lat'],
    'g_long':   ['gps_lonacc', 'g-long', 'glong', 'longitudinal g', 'long g', 'longitudinal', 'g long', 'accel long'],
}

CHANNEL_LABELS = {
    'rpm':      'RPM',
    'speed':    'Speed',
    'gear':     'Gear',
    'throttle': 'Throttle',
    'brake':    'Brake',
    'g_lat':    'G Lateral',
    'g_long':   'G Longitudinal',
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

def auto_detect_channels(columns):
    """Return best-guess column mapping for each channel."""
    mapping = {}
    col_lower = {c: c.lower() for c in columns}
    for channel, keywords in CHANNEL_KEYWORDS.items():
        best = None
        best_score = 0
        for col, col_l in col_lower.items():
            for kw in keywords:
                if kw in col_l:
                    score = len(kw)  # longer match = more specific
                    if score > best_score:
                        best_score = score
                        best = col
        mapping[channel] = best
    return mapping

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

# ── THEMES ────────────────────────────────────────────────────────────────
THEMES = {
    "Dark": {
        "DARK_BG":    "#0d0d14", "DARK_PANEL": "#14141f", "DARK_CARD": "#1a1a28",
        "ACCENT":     "#ff8c00", "ACCENT2":    "#32ade6",
        "TEXT_PRI":   "#f0f0ff", "TEXT_SEC":   "#888899", "BORDER_COL": "#2a2a40",
    },
    "Slate": {
        "DARK_BG":    "#1e2130", "DARK_PANEL": "#262a3a", "DARK_CARD": "#2e3248",
        "ACCENT":     "#f0a500", "ACCENT2":    "#4fc3f7",
        "TEXT_PRI":   "#e8eaf6", "TEXT_SEC":   "#9fa8c0", "BORDER_COL": "#3d4160",
    },
    "White": {
        "DARK_BG":    "#f0f2f5", "DARK_PANEL": "#ffffff", "DARK_CARD": "#e8eaed",
        "ACCENT":     "#d35400", "ACCENT2":    "#1565c0",
        "TEXT_PRI":   "#1a1a2e", "TEXT_SEC":   "#555577", "BORDER_COL": "#c0c4cc",
    },
}
# Load saved theme preference
try:
    import json as _json
    _pref_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ecu_theme")
    _ACTIVE_THEME = _json.load(open(_pref_file)).get("theme", "Slate")
    if _ACTIVE_THEME not in THEMES: _ACTIVE_THEME = "Slate"
except Exception:
    _ACTIVE_THEME = "Slate"
_T = THEMES[_ACTIVE_THEME]
DARK_BG    = _T["DARK_BG"]
DARK_PANEL = _T["DARK_PANEL"]
DARK_CARD  = _T["DARK_CARD"]
ACCENT     = _T["ACCENT"]
ACCENT2    = _T["ACCENT2"]
TEXT_PRI   = _T["TEXT_PRI"]
TEXT_SEC   = _T["TEXT_SEC"]
BORDER_COL = _T["BORDER_COL"]
GREEN_UI   = "#39d353"
RED_UI     = "#ff3b5c"

FONT_TITLE  = ("Courier New", 22, "bold")
FONT_HEAD   = ("Courier New", 11, "bold")
FONT_BODY   = ("Courier New", 10)
FONT_SMALL  = ("Courier New", 9)
FONT_MONO   = ("Courier New", 10)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LapStudio")
        self.configure(bg=DARK_BG)
        self.resizable(True, True)
        self.minsize(860, 680)

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
        self.t_start_var = tk.StringVar(value="0")
        self.t_end_var   = tk.StringVar(value="0")
        self.max_t_label = tk.StringVar(value="Max: —")
        self.fps_var      = tk.StringVar(value="25")
        self.invert_glat   = tk.BooleanVar(value=False)
        self.invert_glong  = tk.BooleanVar(value=False)
        self.invert_throttle = tk.BooleanVar(value=False)
        self._render_start_time = None
        self._preview_window    = None
        self.filter_rpm    = tk.BooleanVar(value=False)
        self.filter_speed  = tk.BooleanVar(value=False)
        self._default_smooth = {"speed": True}  # applied when mapping is built
        self.resolution_var  = tk.StringVar(value="1080p (1920×750)")
        self.g_trail_secs    = tk.StringVar(value="120")
        self.g_trail_speed_colour = tk.BooleanVar(value=True)
        self.lap_offset_var  = tk.StringVar(value="0")
        self.lap_min_speed   = tk.StringVar(value="200")
        self.lap_est_time    = tk.StringVar(value="60")
        self.lap_mode_var    = tk.StringVar(value="Lap Beacons")
        self.lap_sf_lat      = tk.StringVar(value="")
        self.lap_sf_lon      = tk.StringVar(value="")
        self.lap_sf_radius   = tk.StringVar(value="30")
        self.lap_lat_col     = tk.StringVar(value="")
        self.lap_lon_col     = tk.StringVar(value="")
        self.style_var       = tk.StringVar(value="Dash 1 (white gauge)")
        self.style2_var      = tk.StringVar(value="None")
        self._file_info_var  = tk.StringVar(value="")
        self.lap_text        = tk.StringVar(value="Load a file to detect laps.")
        self.progress    = tk.IntVar(value=0)
        self.status_var  = tk.StringVar(value="Load a log file to begin.")
        self._cancel_flag = False
        self._dot_labels  = {}   # channel -> Label widget
        self._all_inputs  = []   # widgets to disable during render

        self._build_ui()

        if not DEPS_OK:
            messagebox.showerror("Missing Dependencies",
                f"Required library not found: {MISSING_DEP}\n\n"
                "Run:  pip install pandas numpy pillow aggdraw\n"
                "then restart this program.")

    def _build_ui(self):
        # ── Apply ttk styles FIRST before any widgets are created ─────────
        _sty = ttk.Style()
        _sty.theme_use('default')
        _sty.configure("TCombobox",
            fieldbackground=DARK_PANEL, background=DARK_PANEL,
            foreground=TEXT_PRI, selectbackground=DARK_PANEL,
            selectforeground=TEXT_PRI, arrowcolor=TEXT_PRI,
            insertcolor=TEXT_PRI, bordercolor=BORDER_COL,
            lightcolor=BORDER_COL, darkcolor=BORDER_COL)
        _sty.map("TCombobox",
            fieldbackground=[('readonly', DARK_PANEL), ('!readonly', DARK_PANEL)],
            foreground=[('readonly', TEXT_PRI), ('!readonly', TEXT_PRI)],
            selectbackground=[('readonly', DARK_PANEL)],
            selectforeground=[('readonly', TEXT_PRI)])
        self.option_add("*TCombobox*Listbox.background", DARK_PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT_PRI)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", DARK_BG)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Scrollable canvas wrapper ──────────────────────────────────────
        canvas = tk.Canvas(self, bg=DARK_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.grid(row=0, column=0, sticky="nsew")

        outer = tk.Frame(canvas, bg=DARK_BG, padx=18, pady=18)
        outer.columnconfigure(0, weight=1)
        _canvas_window = canvas.create_window((0, 0), window=outer, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(_canvas_window, width=e.width)
        def _on_mousewheel(e):
            try:
                _wgt = e.widget
                if isinstance(_wgt, str): raise TypeError
                _cls = _wgt.winfo_class()
                if _cls in ('TCombobox', 'Listbox', 'ComboboxPopdownFrame'):
                    return "break"
                if _wgt.master and _wgt.master.winfo_class() in ('TCombobox',):
                    return "break"
            except Exception:
                pass
            canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        def _on_mousewheel_linux(e):
            try:
                _wgt = e.widget
                if isinstance(_wgt, str): raise TypeError
                if _wgt.winfo_class() in ('TCombobox', 'Listbox', 'ComboboxPopdownFrame'):
                    return "break"
            except Exception:
                pass
            canvas.yview_scroll(-1 if e.num==4 else 1, "units")

        outer.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel_linux)
        canvas.bind_all("<Button-5>", _on_mousewheel_linux)

        # ── TITLE ─────────────────────────────────────────────────────────────
        title_f = tk.Frame(outer, bg=DARK_BG)
        title_f.grid(row=0, column=0, sticky="ew", pady=(0,16))
        tk.Label(title_f, text="◈ LAP STUDIO", font=FONT_TITLE,
                 bg=DARK_BG, fg=ACCENT).pack(side="left")
        tk.Label(title_f, text=" TELEMETRY OVERLAY", font=FONT_TITLE,
                 bg=DARK_BG, fg=TEXT_PRI).pack(side="left")

        # ── SECTION 1: FILE SELECTION ─────────────────────────────────────────
        self._section(outer, 1, "① FILE SELECTION")
        fs = self._card(outer, 2)
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
        tk.Label(self._dll_row_frame,
                 text="(MatLabXRK-2022-64-ReleaseU.dll — only needed for XRK/DRK/XRZ files)",
                 font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).pack(side="left", padx=(8,0))
        self._dll_row_frame.grid_remove()   # hidden until XRK file selected

        # ── SECTION 2: COLUMN MAPPING ─────────────────────────────────────────
        self._section(outer, 3, "② CHANNEL MAPPING")
        self.col_frame = self._card(outer, 4)
        self._build_col_mapping([])   # empty until file loaded
        # collect all lockable inputs after build
        self.after(100, self._collect_inputs)

        # ── SECTION 3: LAP DETECTION ──────────────────────────────────────────
        self._section(outer, 5, "③ LAP DETECTION")
        lap_card = self._card(outer, 6)
        lap_card.columnconfigure(3, weight=1)

        # Mode selector
        tk.Label(lap_card, text='Method:', font=FONT_HEAD,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=0, column=0, sticky='w', padx=(0,8), pady=(0,6))
        _radio_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _radio_frame.grid(row=0, column=1, columnspan=3, sticky='w', pady=(0,6))
        for _mi, _mv in enumerate(['Lap Beacons', 'GPS Crossing', 'Speed Peaks']):
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
        # Trigger lap display update when GPS columns change
        for _gcv in (self.lap_lat_col, self.lap_lon_col):
            _gcv.trace_add('write', lambda *_: self._update_lap_display())
        # Paste field — accepts Google Maps format "-40.236, 175.558"
        tk.Label(_gps_frame, text='Auto-filled from lap data. Override by pasting from Maps:',
                 font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=2, column=0, columnspan=3, sticky='w', pady=(4,2))
        _lbl(_gps_frame, 'Paste from Maps:', 3, 0)
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

        # AIM beacons panel
        _aim_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _aim_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
        _aim_frame.grid_remove()
        _aim_info_lbl = tk.Label(_aim_frame, text='Load an AIM CSV to use beacon markers.',
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
        _off_hint = tk.Label(lap_card, text='shift timer start/end ±N seconds',
                              font=FONT_SMALL, bg=DARK_CARD, fg=TEXT_SEC)
        _off_hint.grid(row=4, column=2, columnspan=2, sticky='w')
        # Show/hide offset with mode
        _offset_widgets = [_sep2, _off_lbl, _off_ent, _off_hint]

        # Mode switch logic
        _mode_hints = {
            'Lap Beacons':  'Reads lap markers from AIM CSV or VRacer VBO file',
            'GPS Crossing': 'Detects S/F line crossing from GPS position',
            'Speed Peaks':  'Detects high-speed sections on the straight',
        }
        def _on_mode_change(*_):
            m = self.lap_mode_var.get()
            # Hide all sub-panels first
            _spd_frame.grid_remove()
            _gps_frame.grid_remove()
            _aim_frame.grid_remove()
            # Show relevant panel — Speed Peaks only shows params for that method
            if m == 'Speed Peaks':
                _spd_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
            elif m == 'GPS Crossing':
                _gps_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
            elif m == 'Lap Beacons':
                _aim_frame.grid(row=2, column=0, columnspan=4, sticky='ew')
            # Timer offset only for Speed Peaks
            for _w in _offset_widgets:
                if m == 'Speed Peaks':
                    _w.grid()
                else:
                    _w.grid_remove()
            try:
                self._update_lap_display()
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

        # Debounced refresh — wait 600ms after last change before updating
        self._lap_update_job = None
        def _debounced_update(*_):
            try:
                if self._lap_update_job:
                    self.after_cancel(self._lap_update_job)
                self._lap_update_job = self.after(600, self._update_lap_display)
            except Exception:
                pass
        for _lv in (self.lap_min_speed, self.lap_est_time, self.lap_offset_var,
                    self.lap_sf_lat, self.lap_sf_lon, self.lap_sf_radius,
                    self.lap_lat_col, self.lap_lon_col):
            _lv.trace_add('write', _debounced_update)

        _lap_frame = tk.Frame(lap_card, bg=DARK_CARD)
        _lap_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(6,0))
        _lap_sb = tk.Scrollbar(_lap_frame, orient="vertical", bg=DARK_PANEL)
        _lap_sb.pack(side="right", fill="y")
        self.lap_info = tk.Text(_lap_frame, height=6, font=FONT_MONO,
                                 bg=DARK_CARD, fg=ACCENT2, relief="flat",
                                 wrap="none", state="disabled",
                                 yscrollcommand=_lap_sb.set)
        self.lap_info.pack(side="left", fill="both", expand=True)
        _lap_sb.config(command=self.lap_info.yview)

        # ── SECTION 5: STYLE ──────────────────────────────────────────────
        # ── SECTION 4: TIME RANGE ────────────────────────────────────────────
        self._section(outer, 7, "④ TIME RANGE")
        tr = self._card(outer, 8)
        tr.columnconfigure(1, weight=1); tr.columnconfigure(3, weight=1)

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
            row=0, column=4, sticky="w", padx=(12,0))

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

        tk.Label(tr, text="Trail Time:", font=FONT_HEAD, bg=DARK_CARD, fg=TEXT_SEC).grid(
            row=2, column=0, sticky="w", padx=(0,8), pady=(8,0))
        trail_combo = ttk.Combobox(tr, textvariable=self.g_trail_secs,
                                    values=[str(i) for i in range(1,6)] + [str(i) for i in range(5,125,5)],
                                    width=6, font=FONT_MONO, state="readonly")
        trail_combo.grid(row=2, column=1, sticky="w", pady=(8,0))
        trail_combo.set("120")
        self._fix_combo(trail_combo)
        tk.Label(tr, text="s trail", font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=2, column=2, sticky="w",
                                                  padx=(8,0), pady=(8,0))
        tk.Checkbutton(tr, text="Speed colour", variable=self.g_trail_speed_colour,
                        bg=DARK_CARD, fg=ACCENT2, activebackground=DARK_CARD,
                        activeforeground=ACCENT2, selectcolor=DARK_PANEL,
                        font=FONT_SMALL, relief="flat", cursor="hand2").grid(
            row=2, column=3, sticky="w", padx=(16,0), pady=(8,0))

        self._section(outer, 9, "⑥ STYLE")
        sty = self._card(outer, 10)
        sty.columnconfigure(1, weight=1)
        tk.Label(sty, text="Visual Style:", font=FONT_HEAD,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=0, column=0, sticky="w", padx=(0,10))
        ttk.Combobox(sty, textvariable=self.style_var,
                      values=["Dash 1 (white gauge)", "Dash 2 (black gauge)", "Dash 3", "Dash 4", "Dash 5", "Dash 6 (Logger)", "Vertical Text", "Horizontal Text"],
                      width=14, font=FONT_MONO, state="readonly").grid(
            row=0, column=1, sticky="w")
        def _on_style_change(*_):
            _sn1 = self.style_var.get()
            if _sn1 in ("Vertical Text", "Horizontal Text"): _r="1920×1080"
            else: _r="1920×750"
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
        tk.Label(sty, textvariable=self._file_info_var, font=FONT_SMALL,
                 bg=DARK_CARD, fg=ACCENT).grid(row=2, column=0, columnspan=4,
                                                sticky="w", pady=(4,0))
        # Theme selector
        tk.Label(sty, text="App Theme:", font=FONT_HEAD,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=3, column=0, sticky="w",
                                                  padx=(0,10), pady=(8,0))
        self._theme_var = tk.StringVar(value=_ACTIVE_THEME)
        _theme_cb = ttk.Combobox(sty, textvariable=self._theme_var,
                                  values=list(THEMES.keys()),
                                  width=10, font=FONT_MONO, state="readonly")
        _theme_cb.grid(row=3, column=1, sticky="w", pady=(8,0))
        self._fix_combo(_theme_cb)
        def _apply_theme(*_):
            _tn = self._theme_var.get()
            try:
                import json as _json
                _pf = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ecu_theme")
                _json.dump({"theme": _tn}, open(_pf, "w"))
            except Exception:
                pass
            self.status_var.set(f"Theme '{_tn}' saved — restart to apply")
        self._theme_var.trace_add("write", _apply_theme)
        tk.Label(sty, text="(restart to apply)", font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC).grid(row=3, column=2, sticky="w",
                                                   padx=(8,0), pady=(8,0))



        # ── SECTION 6: GENERATE ───────────────────────────────────────────
        self._section(outer, 11, "⑦ GENERATE")
        gen = self._card(outer, 12)

        btn_row = tk.Frame(gen, bg=DARK_CARD)
        btn_row.grid(row=0, column=0, sticky="ew", pady=(0,12))
        btn_row.columnconfigure(0, weight=1)
        self.gen_btn = self._btn(btn_row, "▶  GENERATE VIDEO", self._start_render,
                                  big=True, color=ACCENT)
        self.gen_btn.grid(row=0, column=0, sticky="ew", padx=(0,8))
        self.preview_btn = self._btn(btn_row, "🖼  PREVIEW FRAME", self._preview_frame,
                                      big=True, color=ACCENT2)
        self.preview_btn.grid(row=0, column=1, padx=(8,8))
        self.cancel_btn = self._btn(btn_row, "✕  CANCEL", self._cancel_render,
                                     big=True, color=RED_UI)
        self.cancel_btn.grid(row=0, column=2, padx=(8,0))
        self.cancel_btn.config(state="disabled", fg=TEXT_SEC)

        # Progress bar
        style = ttk.Style()
        style.configure("ECU.Horizontal.TProgressbar",
                        troughcolor=DARK_PANEL, background=ACCENT,
                        bordercolor=BORDER_COL, lightcolor=ACCENT, darkcolor=ACCENT)
        self.pbar = ttk.Progressbar(gen, variable=self.progress, maximum=100,
                                     style="ECU.Horizontal.TProgressbar", length=400)
        self.pbar.grid(row=1, column=0, sticky="ew", pady=(0,8))

        tk.Label(gen, textvariable=self.status_var, font=FONT_SMALL,
                 bg=DARK_CARD, fg=TEXT_SEC, wraplength=700, justify="left").grid(
            row=2, column=0, sticky="w")

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
        f.columnconfigure(0, weight=1)
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
                        sv.set(f"{col_data.min():.1f} / {col_data.max():.1f} / {col_data.mean():.1f}")
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
                # Rebuild working rows with updated channel mapping
                if self.df is not None:
                    col_map = {ch2: v2.get() for ch2, v2 in self.col_vars.items()
                               if v2.get() not in ('(not detected)', '(None)', '')}
                    self._prepare_working_df(col_map)
                    self._update_lap_display()
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
            filetypes=[("Log files", "*.csv *.vbo"),
                       # AIM XRK/DRK: disabled pending bug fix
                       # ("AIM XRK/DRK/XRZ", "*.xrk *.drk *.xrz"),
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

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_dir.set(path)


    def _load_xrk(self, path):
        """Load AIM XRK/DRK/XRZ file via MatLabXRK DLL."""
        import threading
        self.status_var.set("Opening XRK file (may take 10-20s)…")
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
                result = _xr.read_xrk(path, progress_cb=lambda p, m:
                    self.after(0, lambda: self.status_var.set(f"XRK: {m} ({p})")))

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
                    # Auto-detect channels using existing keyword logic
                    detected = auto_detect_channels(cols)
                    for ch, col in detected.items():
                        var = getattr(self, f"ch_{ch}", None)
                        if var and col:
                            var.set(col)
                    # Populate column dropdowns
                    for cb in getattr(self, "_ch_combos", []):
                        cb["values"] = cols
                    # GPS lat/lon for lap detection
                    _lat_kws = ["gps_latitude", "latitude", "gps_lat"]
                    _lon_kws = ["gps_longitude", "longitude", "gps_lon"]
                    for _kw in _lat_kws:
                        if self.lap_lat_col.get(): break
                        for c in cols:
                            if _kw in c.lower() and not self.lap_lat_col.get():
                                self.lap_lat_col.set(c)
                    for _kw in _lon_kws:
                        if self.lap_lon_col.get(): break
                        for c in cols:
                            if _kw in c.lower() and not self.lap_lon_col.get():
                                self.lap_lon_col.set(c)
                    # Show AIM lap times if beacons present
                    n_laps = meta.get("n_laps", 0)
                    if n_laps:
                        lines = [f"AIM laps ({n_laps}):"]
                        for i, (st, et, lt) in enumerate(meta["laps"][:30], 1):
                            m2, s2 = divmod(lt, 60)
                            star = " ★" if lt == min(l[2] for l in meta["laps"]) else ""
                            lines.append(f"  Lap {i:2d}: {st:9.3f}s → {et:9.3f}s   {int(m2)}m {s2:.3f}s{star}")
                        self.lap_text.set("\n".join(lines))
                    self._prepare_working_df()
                self.after(0, _ui)
            except Exception as e:
                self.after(0, lambda: self.status_var.set(f"XRK load error: {e}"))
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
            self.after(0, lambda: self.status_var.set("Loading VBO…"))
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
            self.after(0, lambda: self._on_vbo_loaded(path, df, meta, fps_resample))

        except Exception as e:
            import traceback
            msg = f"VBO load error: {e}\n{traceback.format_exc()}"
            self.after(0, lambda m=msg: self.status_var.set(m[:200]))

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

        # Rebuild channel mapping UI for VBO columns
        vbo_map = {
            'speed':    'speed',
            'g_lat':    'g_lat',
            'g_long':   'g_long',
            'rpm':      None,
            'throttle': None,
            'brake':    None,
            'gear':     None,
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
            res = '1920×1080' if sn in ('Style 7','Style 8') else '1920×750'
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
                self.status_var.set(f"Loaded — {n_laps} laps from AIM beacons")
                if self.lap_mode_var.get() == 'Auto':
                    pass  # Auto will use beacons
                if hasattr(self, '_aim_info_lbl'):
                    self._aim_info_lbl.config(
                        text=f"{n_laps} laps from AIM beacon markers.")
            # Auto-detect GPS lat/lon columns and populate dropdowns
            self.lap_lat_col.set("")
            self.lap_lon_col.set("")
            # Priority order: most specific first to avoid GPS_LatAcc matching before GPS_Latitude
            _lat_keywords = ['gps_latitude', 'latitude', 'gps_lat']
            _lon_keywords = ['gps_longitude', 'longitude', 'gps_lon']
            for _kw in _lat_keywords:
                if self.lap_lat_col.get(): break
                for c in cols:
                    if _kw in c.lower() and not self.lap_lat_col.get():
                        self.lap_lat_col.set(c)
            for _kw in _lon_keywords:
                if self.lap_lon_col.get(): break
                for c in cols:
                    if _kw in c.lower() and not self.lap_lon_col.get():
                        self.lap_lon_col.set(c)
            if hasattr(self, '_lat_col_cb'):
                self._lat_col_cb['values'] = cols
                self._lon_col_cb['values'] = cols
            self.max_t_label.set(f"Max: {t_max:.1f} s")

            self._build_col_mapping(cols)
            self._update_lap_display()
            self.status_var.set(
                f"Loaded {len(df):,} rows  ·  {t_max:.1f}s  ·  {len(cols)} channels")
        except Exception as e:
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
            if _style_now in ("Style 7", "Style 8"): _res="1920×1080"
            elif self.style2_var.get() not in ("None","") and hasattr(self,"style2_var"): _res="1920×1080"
            else: _res="1920×750"
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
            lines = [f"  {len(laps)} lap(s) [{mode}]   (best: {int(best_lt//60)}m {best_lt%60:.2f}s):"]
            for i, (st, et, lt) in enumerate(laps, 1):
                m2, s2 = divmod(lt, 60)
                star = " ★" if lt == best_lt else ""
                lines.append(f"  Lap {i:2d}: {st:9.3f}s → {et:9.3f}s   {int(m2)}m {s2:.3f}s{star}")
            _txt = "\n".join(lines)
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
                raise ValueError("No AIM beacon data. Load an AIM CSV file.")
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
        col_map = {ch: var.get() for ch, var in self.col_vars.items()
                   if var.get() not in ("(not detected)", "(None)", "")}
        self._prepare_working_df(col_map)

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

        if _style_now in ("Style 7", "Style 8"):
            resolution = (1920, 1080)
        else:
            resolution = (1920, 750)
        out_w, out_h = resolution
        scale = out_w / _R.W_VID
        out_h_actual = int(_R.H_VID * scale)

        P = _R.STYLES.get(_style_now, _R.STYLES["Dash 1 (white gauge)"])

        import math as _math
        rows = self._working_rows
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

        peak_rpm = float(rows['rpm'].max())
        rpm_max  = int(_math.ceil(peak_rpm/1000)*1000)
        rpm_max  = max(rpm_max, 6000)

        _R._GAUGE_BG_CACHE.clear()
        GS = int(460*scale); GX = int(10*scale)
        cx = GX+GS//2; cy = out_h_actual//2; r = GS//2-int(12*scale)
        bg = _R._build_gauge_bg(cx, cy, r, w=out_w, h=out_h_actual, rpm_max=rpm_max, P=P)

        # Find frame at t_preview
        idx = (rows['ts'] - t_preview).abs().idxmin()
        row = rows.iloc[idx]
        t_now = float(row['ts'])
        trace = rows[(rows['ts'] >= t_now - g_trail) & (rows['ts'] <= t_now)]
        # Style 14 uses fixed 10s trail regardless of g_trail setting
        if _style_now in ("Style 14", "Dash 6 (Logger)"):
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
            speed_colour=speed_col, P=P,
            trace_throttle=trace['throttle'].tolist() if 'throttle' in trace.columns else None,
            trace_brake=trace['brake'].tolist() if 'brake' in trace.columns else None,
            trace_gear=trace['gear'].tolist() if 'gear' in trace.columns else None)
    def _section(self, parent, row, text):
        f = tk.Frame(parent, bg=DARK_BG)
        f.grid(row=row, column=0, sticky="ew", pady=(12,2))
        tk.Label(f, text=text, font=FONT_HEAD, bg=DARK_BG, fg=ACCENT).pack(side="left")
        tk.Frame(f, bg=BORDER_COL, height=1).pack(side="left", fill="x", expand=True, padx=(10,0))

    def _card(self, parent, row):
        f = tk.Frame(parent, bg=DARK_CARD, padx=14, pady=10,
                     highlightbackground=BORDER_COL, highlightthickness=1)
        f.grid(row=row, column=0, sticky="ew")
        f.columnconfigure(0, weight=1)
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
                        sv.set(f"{col_data.min():.1f} / {col_data.max():.1f} / {col_data.mean():.1f}")
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
                # Rebuild working rows with updated channel mapping
                if self.df is not None:
                    col_map = {ch2: v2.get() for ch2, v2 in self.col_vars.items()
                               if v2.get() not in ('(not detected)', '(None)', '')}
                    self._prepare_working_df(col_map)
                    self._update_lap_display()
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
            filetypes=[("Log files", "*.csv *.vbo"),
                       # AIM XRK/DRK: disabled pending bug fix
                       # ("AIM XRK/DRK/XRZ", "*.xrk *.drk *.xrz"),
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

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_dir.set(path)


    def _load_xrk(self, path):
        """Load AIM XRK/DRK/XRZ file via MatLabXRK DLL."""
        import threading
        self.status_var.set("Opening XRK file (may take 10-20s)…")
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
                result = _xr.read_xrk(path, progress_cb=lambda p, m:
                    self.after(0, lambda: self.status_var.set(f"XRK: {m} ({p})")))

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
                    # Auto-detect channels using existing keyword logic
                    detected = auto_detect_channels(cols)
                    for ch, col in detected.items():
                        var = getattr(self, f"ch_{ch}", None)
                        if var and col:
                            var.set(col)
                    # Populate column dropdowns
                    for cb in getattr(self, "_ch_combos", []):
                        cb["values"] = cols
                    # GPS lat/lon for lap detection
                    _lat_kws = ["gps_latitude", "latitude", "gps_lat"]
                    _lon_kws = ["gps_longitude", "longitude", "gps_lon"]
                    for _kw in _lat_kws:
                        if self.lap_lat_col.get(): break
                        for c in cols:
                            if _kw in c.lower() and not self.lap_lat_col.get():
                                self.lap_lat_col.set(c)
                    for _kw in _lon_kws:
                        if self.lap_lon_col.get(): break
                        for c in cols:
                            if _kw in c.lower() and not self.lap_lon_col.get():
                                self.lap_lon_col.set(c)
                    # Show AIM lap times if beacons present
                    n_laps = meta.get("n_laps", 0)
                    if n_laps:
                        lines = [f"AIM laps ({n_laps}):"]
                        for i, (st, et, lt) in enumerate(meta["laps"][:30], 1):
                            m2, s2 = divmod(lt, 60)
                            star = " ★" if lt == min(l[2] for l in meta["laps"]) else ""
                            lines.append(f"  Lap {i:2d}: {st:9.3f}s → {et:9.3f}s   {int(m2)}m {s2:.3f}s{star}")
                        self.lap_text.set("\n".join(lines))
                    self._prepare_working_df()
                self.after(0, _ui)
            except Exception as e:
                self.after(0, lambda: self.status_var.set(f"XRK load error: {e}"))
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
            self.after(0, lambda: self.status_var.set("Loading VBO…"))
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
            self.after(0, lambda: self._on_vbo_loaded(path, df, meta, fps_resample))

        except Exception as e:
            import traceback
            msg = f"VBO load error: {e}\n{traceback.format_exc()}"
            self.after(0, lambda m=msg: self.status_var.set(m[:200]))

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

        # Rebuild channel mapping UI for VBO columns
        vbo_map = {
            'speed':    'speed',
            'g_lat':    'g_lat',
            'g_long':   'g_long',
            'rpm':      None,
            'throttle': None,
            'brake':    None,
            'gear':     None,
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
            res = '1920×1080' if sn in ('Style 7','Style 8') else '1920×750'
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
                self.status_var.set(f"Loaded — {n_laps} laps from AIM beacons")
                if self.lap_mode_var.get() == 'Auto':
                    pass  # Auto will use beacons
                if hasattr(self, '_aim_info_lbl'):
                    self._aim_info_lbl.config(
                        text=f"{n_laps} laps from AIM beacon markers.")
            # Auto-detect GPS lat/lon columns and populate dropdowns
            self.lap_lat_col.set("")
            self.lap_lon_col.set("")
            # Priority order: most specific first to avoid GPS_LatAcc matching before GPS_Latitude
            _lat_keywords = ['gps_latitude', 'latitude', 'gps_lat']
            _lon_keywords = ['gps_longitude', 'longitude', 'gps_lon']
            for _kw in _lat_keywords:
                if self.lap_lat_col.get(): break
                for c in cols:
                    if _kw in c.lower() and not self.lap_lat_col.get():
                        self.lap_lat_col.set(c)
            for _kw in _lon_keywords:
                if self.lap_lon_col.get(): break
                for c in cols:
                    if _kw in c.lower() and not self.lap_lon_col.get():
                        self.lap_lon_col.set(c)
            if hasattr(self, '_lat_col_cb'):
                self._lat_col_cb['values'] = cols
                self._lon_col_cb['values'] = cols
            self.max_t_label.set(f"Max: {t_max:.1f} s")

            self._build_col_mapping(cols)
            self._update_lap_display()
            self.status_var.set(
                f"Loaded {len(df):,} rows  ·  {t_max:.1f}s  ·  {len(cols)} channels")
        except Exception as e:
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
            if _style_now in ("Style 7", "Style 8"): _res="1920×1080"
            elif self.style2_var.get() not in ("None","") and hasattr(self,"style2_var"): _res="1920×1080"
            else: _res="1920×750"
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
            lines = [f"  {len(laps)} lap(s) [{mode}]   (best: {int(best_lt//60)}m {best_lt%60:.2f}s):"]
            for i, (st, et, lt) in enumerate(laps, 1):
                m2, s2 = divmod(lt, 60)
                star = " ★" if lt == best_lt else ""
                lines.append(f"  Lap {i:2d}: {st:9.3f}s → {et:9.3f}s   {int(m2)}m {s2:.3f}s{star}")
            _txt = "\n".join(lines)
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
                raise ValueError("No AIM beacon data. Load an AIM CSV file.")
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
        col_map = {ch: var.get() for ch, var in self.col_vars.items()
                   if var.get() not in ("(not detected)", "(None)", "")}
        self._prepare_working_df(col_map)

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

        if _style_now in ("Style 7", "Style 8"):
            resolution = (1920, 1080)
        else:
            resolution = (1920, 750)
        out_w, out_h = resolution
        scale = out_w / _R.W_VID
        out_h_actual = int(_R.H_VID * scale)

        P = _R.STYLES.get(_style_now, _R.STYLES["Dash 1 (white gauge)"])

        import math as _math
        rows = self._working_rows
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

        peak_rpm = float(rows['rpm'].max())
        rpm_max  = int(_math.ceil(peak_rpm/1000)*1000)
        rpm_max  = max(rpm_max, 6000)

        _R._GAUGE_BG_CACHE.clear()
        GS = int(460*scale); GX = int(10*scale)
        cx = GX+GS//2; cy = out_h_actual//2; r = GS//2-int(12*scale)
        bg = _R._build_gauge_bg(cx, cy, r, w=out_w, h=out_h_actual, rpm_max=rpm_max, P=P)

        # Find frame at t_preview
        idx = (rows['ts'] - t_preview).abs().idxmin()
        row = rows.iloc[idx]
        t_now = float(row['ts'])
        trace = rows[(rows['ts'] >= t_now - g_trail) & (rows['ts'] <= t_now)]
        # Style 14 uses fixed 10s trail regardless of g_trail setting
        if _style_now in ("Style 14", "Dash 6 (Logger)"):
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
            speed_colour=speed_col, P=P,
            trace_throttle=trace['throttle'].tolist() if 'throttle' in trace.columns else None,
            trace_brake=trace['brake'].tolist() if 'brake' in trace.columns else None,
            trace_gear=trace['gear'].tolist() if 'gear' in trace.columns else None)


        # Show in popup
        from PIL import ImageTk
        if self._preview_window and tk.Toplevel.winfo_exists(self._preview_window):
            self._preview_window.destroy()
        self._preview_window = tk.Toplevel(self)
        self._preview_window.title(f"Preview — t={t_now:.1f}s")
        self._preview_window.configure(bg="#000000")
        # Scale down for display (max 960px wide)
        disp_w = min(960, out_w)
        disp_h = int(out_h_actual * disp_w / out_w)
        disp_img = img.resize((disp_w, disp_h))
        tk_img = ImageTk.PhotoImage(disp_img)
        lbl = tk.Label(self._preview_window, image=tk_img, bg="#000000")
        lbl.image = tk_img   # keep reference
        lbl.pack()
        self.status_var.set(f"Preview rendered at t={t_now:.1f}s")


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
        result = _PI.new("RGB", (OUT_W, OUT_H), (255, 0, 255))
        for slot, sn in enumerate([sn1, sn2]):
            if not sn or sn == "None": continue
            P = _R.STYLES.get(sn)
            if not P: continue
            # Determine natural resolution for this style
            is_full = P.get("style7_layout") or P.get("style8_layout")
            no_gauge = is_full or P.get("style11_layout") or P.get("style12_layout") or P.get("style13_layout") or P.get("style14_layout")
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
                peak_rpm=int(trail["rpm"].max()) if "rpm" in trail.columns and len(trail) else None,
                trace_speed=None, speed_colour=False, P=P)
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
        rpm_max = max(int(_math.ceil(float(rows['rpm'].max())/1000)*1000), 6000)
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
        cells = [_MS._render_cell(sn, fs, laps, rpm_max, speed_col, g_trail)
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


    def _fix_combo(self, combo):
        """Fix combobox dropdown position when inside a scrolled canvas."""
        # No override needed — let ttk handle dropdown natively
        pass

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

        # Apply G-force and throttle inversion if selected
        if self._working_rows is not None:
            if self.invert_glat.get():
                self._working_rows = self._working_rows.copy()
                self._working_rows["g_lat"] = -self._working_rows["g_lat"]
            if self.invert_glong.get():
                if "g_lat" not in self._working_rows.columns or not self.invert_glat.get():
                    self._working_rows = self._working_rows.copy()
                self._working_rows["g_long"] = -self._working_rows["g_long"]
            if self.invert_throttle.get() and "throttle" in self._working_rows.columns:
                self._working_rows = self._working_rows.copy()
                self._working_rows["throttle"] = 100.0 - self._working_rows["throttle"].clip(0,100)

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
        elif style in ("Style 7", "Style 8"):
            resolution = (1920, 1080)
        else:
            resolution = (1920, 750)
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
            kwargs={},
            daemon=True)
        t.start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
