"""
xrk_reader.py — AIM XRK/DRK/XRZ reader using a subprocess helper.

Runs xrk_helper.py as a separate process from the DLL's folder,
completely avoiding ctypes DLL dependency conflicts in the main process.
"""
import os, sys, json, subprocess, tempfile
import numpy as np
import pandas as pd

_DLL_NAMES = [
    'MatLabXRK-2022-64-ReleaseU.dll',
    'MatLabXRK-2022-32-ReleaseU.dll',
    'MatLabXRK-64-ReleaseU.dll',
    'MatLabXRK-32-ReleaseU.dll',
    'MatLabXRK-2017-64-ReleaseU.dll',
]

_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xrk_helper.py')


def find_dll(start_dir=None):
    """Return (dll_path, dll_dir) for the first AIM DLL found, or (None, None)."""
    dirs = []
    if os.environ.get('XRK_DLL_PATH'):
        dirs.append(os.environ['XRK_DLL_PATH'])
    if start_dir:
        dirs.append(os.path.abspath(start_dir))
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    # A frozen build unpacks bundled data to _MEIPASS, and users may also drop
    # the DLL next to the exe itself.
    _mei = getattr(sys, "_MEIPASS", None)
    if _mei:
        dirs.append(_mei)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    # RaceStudio3 install locations
    for p in [r'C:\Program Files (x86)\AIM\RaceStudio3',
              r'C:\Program Files\AIM\RaceStudio3']:
        dirs.append(p)
    dirs.extend(os.environ.get('PATH', '').split(os.pathsep))

    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in _DLL_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p, d
        # Also scan for any MatLabXRK*.dll
        try:
            for entry in sorted(os.listdir(d)):
                if 'matlabxrk' in entry.lower() and entry.lower().endswith('.dll'):
                    p = os.path.join(d, entry)
                    return p, d
        except PermissionError:
            pass
    return None, None


def _load_payload(path):
    """Read the helper's output: .npz (fast binary) or legacy JSON."""
    try:
        with np.load(path, allow_pickle=False) as z:
            if '__meta__' in z:
                meta = json.loads(bytes(z['__meta__']).decode('utf-8'))
                names = json.loads(bytes(z['__names__']).decode('utf-8'))
                chans = {}
                if '__tmap__' in z:                 # de-duplicated time bases
                    tmap = z['__tmap__']
                    tcache = {}
                    for i, nm in enumerate(names):
                        k = int(tmap[i])
                        if k not in tcache:
                            tcache[k] = z[f'tu{k}']
                        chans[nm] = {'t': tcache[k], 'v': z[f'v{i}']}
                else:                                # one time array per channel
                    for i, nm in enumerate(names):
                        chans[nm] = {'t': z[f't{i}'], 'v': z[f'v{i}']}
                return {'meta': meta, 'channels': chans}
    except Exception:
        pass
    with open(path, 'r') as f:
        return json.load(f)


def _cache_path(filepath):
    """Where the parsed result for this exact file version is cached."""
    try:
        import hashlib, tempfile
        st = os.stat(filepath)
        # v2: payload now also carries channel units
        key = hashlib.sha1(
            f"v2|{os.path.abspath(filepath)}|{st.st_mtime_ns}|{st.st_size}".encode()
        ).hexdigest()[:16]
        return os.path.join(tempfile.gettempdir(), 'lapstudio_aim_cache',
                            key + '.npz')
    except Exception:
        return None


def read_xrk(filepath, progress_cb=None):
    """
    Read a DRK/XRK/XRZ file by running xrk_helper.py as a subprocess
    from the DLL's directory. Avoids all ctypes dependency conflicts.
    """
    filepath = os.path.abspath(filepath)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    def prog(pct, msg=''):
        if progress_cb: progress_cb(int(pct), msg)

    # Re-opening a file we have already parsed: skip the DLL entirely
    import shutil
    _cp = _cache_path(filepath)
    if _cp and os.path.isfile(_cp):
        try:
            prog(60, 'Loading cached AIM data…')
            data = _load_payload(_cp)
            print(f"[xrk_reader] using cache {os.path.basename(_cp)}", flush=True)
            return _build_result(data, prog)
        except Exception as _e:
            print(f"[xrk_reader] cache unusable ({_e}), re-reading", flush=True)

    prog(2, 'Locating AIM DLL…')
    dll_path, dll_dir = find_dll(start_dir=os.path.dirname(filepath))
    if dll_path is None:
        raise RuntimeError(
            "AIM DLL not found. Place MatLabXRK-2022-64-ReleaseU.dll "
            "next to xrk_helper.py or set XRK_DLL_PATH.")

    prog(5, f'Found DLL: {os.path.basename(dll_path)}')

    # Copy helper to DLL folder so it runs alongside the DLL
    helper_in_dll_dir = os.path.join(dll_dir, 'xrk_helper.py')
    if not getattr(sys, "frozen", False):
        import shutil
        if not os.path.isfile(helper_in_dll_dir) or \
           os.path.getmtime(_HELPER) > os.path.getmtime(helper_in_dll_dir):
            shutil.copy2(_HELPER, helper_in_dll_dir)

    # Write output to temp file
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='xrk_out_')
    os.close(tmp_fd)

    try:
        prog(8, 'Starting DLL reader process…')
        if getattr(sys, "frozen", False):
            # In a PyInstaller build sys.executable is LapStudio.exe, not python,
            # so running "python xrk_helper.py" is impossible: the old command
            # relaunched the GUI with the helper path as argv[1] and then timed
            # out. The exe re-runs ITSELF in helper mode instead - see the
            # --xrk-helper branch at the top of ecu_overlay_app.py.
            cmd = [sys.executable, "--xrk-helper", filepath, tmp_path]
        else:
            cmd = [sys.executable, helper_in_dll_dir, filepath, tmp_path]
        # Run from DLL dir with DLL dir prepended to PATH
        env = os.environ.copy()
        env['PATH'] = dll_dir + os.pathsep + env.get('PATH', '')
        result = subprocess.run(
            cmd,
            cwd=dll_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            # Missing dependencies are the common case — report just that, in
            # plain language, rather than a Python traceback.
            _miss = []
            for _line in err.splitlines():
                if 'dependencies are missing' in _line:
                    _tail = _line.split('-', 1)[-1]
                    _miss = [x.strip().rstrip('.') for x in _tail.split(',')
                             if x.strip().lower().endswith(('.dll', '.drv'))]
                    break
            if _miss:
                # Re-use the helper's own guidance (copy vs install)
                try:
                    if getattr(sys, "frozen", False):
                        import xrk_helper as _xh          # bundled as a module
                    else:
                        import importlib.util as _ilu
                        _spec = _ilu.spec_from_file_location('_xh', _HELPER)
                        _xh = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_xh)
                    _guide = _xh.describe_missing(_miss)
                except Exception:
                    _guide = ("Copy the missing files from your AIM RaceStudio3 "
                              "folder into the LapStudio folder.")
                raise RuntimeError(
                    "AIM file support needs these, which are not on this PC:\n"
                    "    " + "\n    ".join(_miss) + "\n\n" + _guide + "\n\n"
                    "You can also point the AIM DLL box (File Selection panel) at "
                    "your RaceStudio3 folder.\n"
                    "Until then, export the session from RaceStudio as CSV and "
                    "load that instead.")
            raise RuntimeError(
                f"xrk_helper failed (exit {result.returncode}):\n{err}")

        prog(70, 'Loading channel data…')
        stdout = result.stdout.strip()
        print(f"[xrk_reader] Helper: {stdout}", flush=True)

        # The helper writes .npz (binary) when numpy is available, else JSON.
        data = _load_payload(tmp_path)
        # Keep the parsed result so re-opening the same file is instant
        try:
            _cp = _cache_path(filepath)
            if _cp:
                os.makedirs(os.path.dirname(_cp), exist_ok=True)
                shutil.copy2(tmp_path, _cp)
        except Exception:
            pass

    finally:
        try: os.unlink(tmp_path)
        except OSError: pass

    return _build_result(data, prog)


def _build_result(data, prog):
    meta     = data['meta']
    channels = data['channels']

    if not channels:
        raise RuntimeError(
            f"No channel data in file. "
            f"Laps={meta.get('n_laps',0)}, "
            f"Helper output: {result.stdout.strip()}")

    prog(80, f"Building DataFrame ({len(channels)} channels)…")

    # ── Build a unified time grid, robustly ──────────────────────────────────
    # Some AIM channels come back on a completely different time base (e.g. an
    # absolute/GPS clock), which would stretch the session to weeks. Take the
    # consensus (median) window and ignore channels far outside it.
    starts, ends, spans = [], [], []
    for ch in channels.values():
        t = ch['t']
        if len(t) > 1 and np.isfinite(t[0]) and np.isfinite(t[-1]):
            starts.append(float(t[0])); ends.append(float(t[-1]))
            spans.append(float(t[-1]) - float(t[0]))
    if not ends:
        raise RuntimeError("No usable channel time data in file.")

    # Work out the true session length in SECONDS. A plain median is unsafe:
    # if most channels are timestamped in milliseconds (as AIM does for GPS),
    # the median follows the wrong unit. Instead let every channel vote for a
    # session length under each plausible unit, and take the best-supported.
    _UNIT_F = (1.0, 1e-3, 1e-6, 60.0)
    _votes = {}
    for sp in spans:
        for _f in _UNIT_F:
            s = sp * _f
            if s <= 1.0:
                continue
            key = round(s, 1)
            _votes[key] = _votes.get(key, 0) + 1
    ref_span = None
    if meta.get('laps'):
        try:
            _l = meta['laps']
            ref_span = float(_l[-1][1]) - float(_l[0][0])
        except Exception:
            ref_span = None
    if _votes:
        if ref_span and ref_span > 1.0:
            # prefer the candidate closest to the lap-derived session length
            med_span = min(_votes, key=lambda k: (abs(k - ref_span), -_votes[k]))
        else:
            # most votes wins; ties go to the shorter (seconds beats ms)
            med_span = min(_votes, key=lambda k: (-_votes[k], k))
    else:
        med_span = float(np.median(spans))
    med_start = 0.0

    # GPS channels are often logged against an absolute (GPS) clock: same
    # duration as the session but offset by a huge amount. Those must be
    # RE-BASED onto the session window, not discarded — dropping them lost
    # GPS speed, the G channels and the position data.
    _tol_start = max(5.0, 0.02 * max(med_span, 1.0))
    _tol_span  = max(5.0, 0.10 * max(med_span, 1.0))
    # Channels may also be timestamped in different UNITS: AIM commonly returns
    # GPS channels in milliseconds while the ECU channels are in seconds, so the
    # span looks ~1000x too long. Try the usual unit scales before giving up.
    _SCALES = ((1.0, ''), (1e-3, 'ms -> s'), (1e-6, 'us -> s'), (60.0, 'min -> s'))
    kept, rebased, rescaled, dropped = {}, [], [], []
    for nm, ch in channels.items():
        t = ch['t']
        if len(t) <= 1:
            continue
        t_arr = np.asarray(t, dtype=np.float64)
        t0, t1 = float(t_arr[0]), float(t_arr[-1])
        raw_span = t1 - t0
        # Pick the unit scale whose span best matches the session
        best = None
        for _f, _lbl in _SCALES:
            if abs(raw_span * _f - med_span) <= _tol_span:
                best = (_f, _lbl); break
        if best is None:
            dropped.append((nm, t0, t1))
            continue
        _f, _lbl = best
        tt2 = t_arr * _f if _f != 1.0 else t_arr
        if _lbl:
            rescaled.append((nm, _lbl))
        s0 = float(tt2[0])
        if abs(s0 - med_start) > _tol_start:        # different origin -> shift
            tt2 = tt2 - (s0 - med_start)
            rebased.append((nm, s0))
        kept[nm] = {'t': tt2, 'v': ch['v']} if (_lbl or tt2 is not t_arr) else ch
    if rescaled:
        print(f"[xrk_reader] rescaled the time base of {len(rescaled)} channel(s): "
              + ", ".join(f"{n} ({l})" for n, l in rescaled[:6])
              + (" …" if len(rescaled) > 6 else ""), flush=True)
    if rebased:
        print(f"[xrk_reader] re-based {len(rebased)} channel(s) from an absolute "
              f"clock onto the session start: "
              + ", ".join(n for n, _ in rebased[:6])
              + (" …" if len(rebased) > 6 else ""), flush=True)
    if dropped:
        print(f"[xrk_reader] ignoring {len(dropped)} channel(s) whose time span "
              f"does not match the session ({med_span:.1f}s): "
              + ", ".join(f"{n} [{a:.0f}..{b:.0f}s]" for n, a, b in dropped[:6])
              + (" …" if len(dropped) > 6 else ""), flush=True)
    if kept:
        channels = kept

    all_t = [ch['t'] for ch in channels.values() if len(ch['t']) > 1]
    t_min = min(t[0]  for t in all_t)
    t_max = max(t[-1] for t in all_t)

    all_dts = []
    for t in all_t:
        arr = np.diff(t)
        pos = arr[arr > 0]
        if len(pos): all_dts.append(float(np.median(pos)))
    dt = float(np.median(all_dts)) if all_dts else 0.1
    # Cap the working rate. AIM logs some channels very fast; resampling every
    # channel onto a 500-1000 Hz grid produces millions of rows, which makes the
    # app crawl. Video output is 25-60 fps, so 50 Hz is ample.
    dt = max(dt, 0.02)
    # Hard ceiling on rows as a backstop for very long sessions
    _span = max(1e-6, t_max - t_min)
    _max_rows = 400_000
    if _span / dt > _max_rows:
        dt = _span / _max_rows

    n_frames = max(2, int(round(_span / dt)))
    grid_ts  = t_min + np.arange(n_frames) * dt

    # Present the session as starting at t=0 so the Start/End boxes and the lap
    # times share one origin (AIM time bases are not necessarily zero-based).
    df_dict = {'ts': (grid_ts - t_min).astype(np.float64)}
    for name, ch in channels.items():
        t = np.asarray(ch['t'], dtype=np.float64)
        v = np.asarray(ch['v'], dtype=np.float64)
        valid = np.isfinite(t) & np.isfinite(v)
        if valid.sum() < 2: continue
        tv, vv = t[valid], v[valid]
        df_dict[name] = np.interp(grid_ts, tv, vv,
                                  left=vv[0], right=vv[-1]).astype(np.float32)

    df = pd.DataFrame(df_dict)

    # ── Speed units ──────────────────────────────────────────────────────────
    # AIM reports GPS speed in m/s on some loggers. The dashes label speed as
    # KPH, so convert using the units the DLL reports (a 204 km/h car otherwise
    # shows 57).
    _units = meta.get('units') or {}
    for _c in list(df.columns):
        _u = str(_units.get(_c, '')).strip().lower().replace(' ', '')
        _cl = _c.lower()
        if not any(k in _cl for k in ('speed', 'velocity', 'vel')):
            continue
        if 'ecef' in _cl:
            continue                      # cartesian components, not road speed
        if _u in ('m/s', 'ms', 'meter/second', 'metres/second', 'm.s-1'):
            df[_c] = (df[_c].to_numpy(dtype=np.float64) * 3.6).astype(np.float32)
            print(f"[xrk_reader] '{_c}' converted m/s -> km/h "
                  f"(now {df[_c].min():.1f}..{df[_c].max():.1f})", flush=True)
        elif _u in ('mph',):
            df[_c] = (df[_c].to_numpy(dtype=np.float64) * 1.609344).astype(np.float32)
            print(f"[xrk_reader] '{_c}' converted mph -> km/h "
                  f"(now {df[_c].min():.1f}..{df[_c].max():.1f})", flush=True)

    # Some AIM/XRK logs carry position only as ECEF cartesian coordinates
    # ("ECEF position_X/Y/Z", metres) rather than latitude/longitude. Derive
    # geodetic lat/lon so the track map and GPS lap detection can be used.
    _ecef = {}
    for _c in df.columns:
        _cl = _c.lower().replace('-', ' ').replace('_', ' ')
        if 'ecef' in _cl and 'position' in _cl:
            for _ax in ('x', 'y', 'z'):
                if _cl.rstrip().endswith(' ' + _ax) or _cl.replace(' ', '').endswith('position' + _ax):
                    _ecef[_ax] = _c
    if len(_ecef) == 3:
        try:
            X = df[_ecef['x']].to_numpy(dtype=np.float64)
            Y = df[_ecef['y']].to_numpy(dtype=np.float64)
            Z = df[_ecef['z']].to_numpy(dtype=np.float64)
            if np.nanmax(np.abs(X)) > 1e5:          # plausible ECEF metres
                a = 6378137.0                        # WGS84 semi-major axis
                f = 1.0 / 298.257223563
                b = a * (1.0 - f)
                e2 = f * (2.0 - f)
                ep2 = (a * a - b * b) / (b * b)
                p = np.sqrt(X * X + Y * Y)
                th = np.arctan2(a * Z, b * p)
                lon = np.arctan2(Y, X)
                lat = np.arctan2(Z + ep2 * b * np.sin(th) ** 3,
                                 p - e2 * a * np.cos(th) ** 3)
                _dlat = np.degrees(lat).astype(np.float32)
                _dlon = np.degrees(lon).astype(np.float32)
                print(f"[xrk_reader] ECEF position -> lat/lon: "
                      f"{np.nanmin(_dlat):.5f}..{np.nanmax(_dlat):.5f}, "
                      f"{np.nanmin(_dlon):.5f}..{np.nanmax(_dlon):.5f} deg "
                      f"(from {_ecef['x']}, {_ecef['y']}, {_ecef['z']})", flush=True)
                # If the log also carries its own lat/lon, keep whichever agrees
                # with the ECEF position; some loggers report a broken pair.
                for _axis, _vals, _names in (
                        ('Latitude', _dlat, ('GPS Latitude', 'Latitude', 'GPS_Latitude')),
                        ('Longitude', _dlon, ('GPS Longitude', 'Longitude', 'GPS_Longitude'))):
                    _own = next((c for c in _names if c in df.columns), None)
                    if _own is not None:
                        _o = df[_own].to_numpy(dtype=np.float64)
                        _d = np.abs(_o - _vals.astype(np.float64))
                        _off = float(np.nanmedian(_d[np.isfinite(_d)])) if np.isfinite(_d).any() else 9e9
                        if _off > 0.01:            # ~1 km disagreement
                            print(f"[xrk_reader] '{_own}' disagrees with the ECEF "
                                  f"position by {_off:.4f} deg - using ECEF instead "
                                  f"(original kept as '{_own} (raw)')", flush=True)
                            df[f'{_own} (raw)'] = df[_own]
                            df[_own] = _vals
                        else:
                            print(f"[xrk_reader] '{_own}' agrees with ECEF "
                                  f"({_off:.6f} deg) - keeping it", flush=True)
                    else:
                        df[f'GPS {_axis}'] = _vals
        except Exception as _e:
            print(f"[xrk_reader] ECEF conversion skipped: {_e}", flush=True)

    print(f"[xrk_reader] resampled to {len(df)} rows @ {1.0/dt:.0f} Hz "
          f"x {len(df.columns)-1} channels", flush=True)

    # Lap times share the channel time base, so shift them to the same origin.
    # Only shift when the laps clearly sit on that base (not already 0-based).
    _raw_laps = [(float(st), float(et), float(lt)) for st, et, lt in meta.get('laps', [])]
    _shift = float(t_min)
    if _raw_laps and _shift > 1.0:
        _first = min(st for st, _, _ in _raw_laps)
        if _first >= _shift - 1.0:            # laps are on the absolute base
            _raw_laps = [(st - _shift, et - _shift, lt) for st, et, lt in _raw_laps]
    laps_list = [(st, et, lt) for st, et, lt in _raw_laps]
    beacons   = [et for _, et, _ in laps_list]
    lap_times = [lt for _, _, lt in laps_list]

    prog(100, 'Done.')
    return {
        'df':     df,
        'ts_col': 'ts',
        'meta': {
            'beacons':      beacons,
            'seg_times':    lap_times,
            'laps':         laps_list,
            'vehicle':      meta.get('vehicle', ''),
            'racer':        meta.get('racer', ''),
            'track':        meta.get('track', ''),
            'date':         meta.get('date', ''),
            'championship': meta.get('championship', ''),
            'n_laps':       meta.get('n_laps', 0),
        },
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python xrk_reader.py <file.drk|.xrk|.xrz>")
        sys.exit(1)
    r = read_xrk(sys.argv[1], progress_cb=lambda p,m: print(f"  {p:3d}% {m}"))
    df = r['df']; meta = r['meta']
    print(f"\nLoaded: {len(df)} rows x {len(df.columns)} channels")
    print(f"Vehicle: {meta['vehicle']}  Track: {meta['track']}  Date: {meta['date']}")
    for i,(st,et,lt) in enumerate(meta['laps'],1):
        m,s=divmod(lt,60); print(f"  Lap {i:2d}: {st:.3f}→{et:.3f}  {int(m)}:{s:05.2f}")
