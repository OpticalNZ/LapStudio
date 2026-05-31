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

    prog(2, 'Locating AIM DLL…')
    dll_path, dll_dir = find_dll(start_dir=os.path.dirname(filepath))
    if dll_path is None:
        raise RuntimeError(
            "AIM DLL not found. Place MatLabXRK-2022-64-ReleaseU.dll "
            "next to xrk_helper.py or set XRK_DLL_PATH.")

    prog(5, f'Found DLL: {os.path.basename(dll_path)}')

    # Copy helper to DLL folder so it runs alongside the DLL
    import shutil
    helper_in_dll_dir = os.path.join(dll_dir, 'xrk_helper.py')
    if not os.path.isfile(helper_in_dll_dir) or \
       os.path.getmtime(_HELPER) > os.path.getmtime(helper_in_dll_dir):
        shutil.copy2(_HELPER, helper_in_dll_dir)

    # Write output to temp file
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='xrk_out_')
    os.close(tmp_fd)

    try:
        prog(8, 'Starting DLL reader process…')
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
            raise RuntimeError(
                f"xrk_helper failed (exit {result.returncode}):\n{err}")

        prog(70, 'Loading channel data…')
        stdout = result.stdout.strip()
        print(f"[xrk_reader] Helper: {stdout}", flush=True)

        with open(tmp_path, 'r') as f:
            data = json.load(f)

    finally:
        try: os.unlink(tmp_path)
        except OSError: pass

    meta     = data['meta']
    channels = data['channels']

    if not channels:
        raise RuntimeError(
            f"No channel data in file. "
            f"Laps={meta.get('n_laps',0)}, "
            f"Helper output: {result.stdout.strip()}")

    prog(80, f"Building DataFrame ({len(channels)} channels)…")

    # Build unified time grid
    all_t = [ch['t'] for ch in channels.values() if len(ch['t']) > 1]
    t_min = min(t[0]  for t in all_t)
    t_max = max(t[-1] for t in all_t)

    all_dts = []
    for t in all_t:
        arr = np.diff(t)
        pos = arr[arr > 0]
        if len(pos): all_dts.append(float(np.median(pos)))
    dt = float(np.median(all_dts)) if all_dts else 0.1
    dt = max(dt, 0.001)

    n_frames = int(round((t_max - t_min) / dt))
    grid_ts  = t_min + np.arange(n_frames) * dt

    df_dict = {'ts': grid_ts}
    for name, ch in channels.items():
        t = np.array(ch['t']); v = np.array(ch['v'])
        valid = np.isfinite(t) & np.isfinite(v)
        if valid.sum() < 2: continue
        df_dict[name] = np.interp(grid_ts, t[valid], v[valid],
                                  left=v[valid][0], right=v[valid][-1]).astype(np.float32)

    df = pd.DataFrame(df_dict)

    laps_list = [(st, et, lt) for st, et, lt in meta.get('laps', [])]
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
