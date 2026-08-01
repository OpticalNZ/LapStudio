"""
xrk_helper.py — runs as subprocess from the DLL folder.
Tries multiple loading strategies to handle Python 3.8+ DLL restrictions.
"""
import ctypes, os, sys, json

try:
    import numpy as _np          # optional: enables the fast binary transfer
except ImportError:
    _np = None

_DLL_NAMES = [
    'MatLabXRK-2022-64-ReleaseU.dll',
    'MatLabXRK-2022-32-ReleaseU.dll',
    'MatLabXRK-64-ReleaseU.dll',
    'MatLabXRK-32-ReleaseU.dll',
    'MatLabXRK-2017-64-ReleaseU.dll',
]

# The AIM DLL is not self-contained: it imports libxml2-2.dll and
# pthreadVC2_x64.dll, which ship with RaceStudio3 rather than with Windows.
# If they cannot be found the DLL will not load at all, which is the usual
# reason AIM import "does nothing".
_AIM_EXTRA_DEPS = ('libxml2-2.dll', 'pthreadVC2_x64.dll', 'pthreadVC2.dll')

_RS3_DIRS = [
    r'C:\Program Files (x86)\AIM\RaceStudio3',
    r'C:\Program Files\AIM\RaceStudio3',
    r'C:\Program Files (x86)\AIM\RaceStudio2',
    r'C:\Program Files\AIM\RaceStudio2',
    r'C:\Program Files (x86)\AIM',
    r'C:\Program Files\AIM',
]


def _pe_imports(path):
    """Return the list of DLLs a PE file imports (no external deps needed)."""
    import struct
    try:
        b = open(path, 'rb').read()
        e = struct.unpack_from('<I', b, 0x3c)[0]
        if b[e:e+4] != b'PE\0\0':
            return []
        coff = e + 4
        nsec = struct.unpack_from('<H', b, coff + 2)[0]
        opt = coff + 20
        magic = struct.unpack_from('<H', b, opt)[0]
        sh = opt + struct.unpack_from('<H', b, coff + 16)[0]
        secs = []
        for i in range(nsec):
            o = sh + i * 40
            vsz, va, rsz, ptr = struct.unpack_from('<IIII', b, o + 8)
            secs.append((va, vsz, ptr, rsz))
        def r2o(r):
            for va, vsz, ptr, rsz in secs:
                if va <= r < va + max(vsz, rsz):
                    return ptr + (r - va)
            return None
        dd = opt + (112 if magic == 0x20b else 96)
        imp_rva = struct.unpack_from('<I', b, dd + 8)[0]
        io = r2o(imp_rva)
        out = []
        while io is not None:
            if b[io:io+20] == b'\0' * 20:
                break
            nrva = struct.unpack_from('<I', b, io + 12)[0]
            if not nrva:
                break
            no = r2o(nrva)
            if no is None:
                break
            out.append(b[no:b.index(b'\0', no)].decode('latin1'))
            io += 20
        return out
    except Exception:
        return []


# DLLs supplied by Windows itself — never report these as missing.
_WINDOWS_DLLS = {
    'kernel32.dll', 'user32.dll', 'gdi32.dll', 'msimg32.dll', 'advapi32.dll',
    'shlwapi.dll', 'uxtheme.dll', 'ole32.dll', 'oleaut32.dll', 'gdiplus.dll',
    'oleacc.dll', 'imm32.dll', 'winmm.dll', 'oledlg.dll', 'shell32.dll',
    'winspool.drv', 'ws2_32.dll', 'crypt32.dll', 'bcrypt.dll', 'normaliz.dll',
    'wldap32.dll', 'comdlg32.dll', 'comctl32.dll', 'version.dll', 'setupapi.dll',
    'iphlpapi.dll', 'netapi32.dll', 'userenv.dll', 'secur32.dll', 'dbghelp.dll',
    'ntdll.dll', 'rpcrt4.dll', 'msvcrt.dll', 'msvcp_win.dll', 'ucrtbase.dll',
}

# Runtimes that must be installed rather than copied (side-by-side assemblies)
_RUNTIME_DLLS = {
    'msvcr90.dll':  'Microsoft Visual C++ 2008 SP1 Redistributable (x64)',
    'msvcp90.dll':  'Microsoft Visual C++ 2008 SP1 Redistributable (x64)',
    'msvcr100.dll': 'Microsoft Visual C++ 2010 Redistributable (x64)',
    'msvcr120.dll': 'Microsoft Visual C++ 2013 Redistributable (x64)',
    'vcruntime140.dll': 'Microsoft Visual C++ 2015-2022 Redistributable (x64)',
    'msvcp140.dll': 'Microsoft Visual C++ 2015-2022 Redistributable (x64)',
}


def _find_in_dirs(name, search_dirs):
    for d in search_dirs:
        try:
            if d and os.path.isfile(os.path.join(d, name)):
                return os.path.join(d, name)
        except Exception:
            pass
    return None


def find_missing_deps(dll_path, search_dirs, _seen=None, _depth=0):
    """Walk the whole dependency tree and return the non-Windows DLLs that
    cannot be found. Recursive, because the AIM DLL's own dependencies pull in
    further libraries (libxml2 needs libiconv-2 and libz; pthreadVC2 needs the
    VC90 runtime), and any one of them missing stops the DLL loading."""
    if _seen is None:
        _seen = {}
    missing = []
    for dep in _pe_imports(dll_path):
        key = dep.lower()
        if key in _seen or key in _WINDOWS_DLLS:
            continue
        _seen[key] = True
        p = _find_in_dirs(dep, search_dirs)
        if p is None:
            missing.append(dep)
        elif _depth < 4:
            missing.extend(find_missing_deps(p, search_dirs, _seen, _depth + 1))
    return missing


def describe_missing(missing):
    """Human-readable guidance for a list of missing dependency DLLs."""
    copyable = [m for m in missing if m.lower() not in _RUNTIME_DLLS]
    runtimes = sorted({_RUNTIME_DLLS[m.lower()] for m in missing
                       if m.lower() in _RUNTIME_DLLS})
    lines = []
    if copyable:
        lines.append("Copy these from your AIM RaceStudio3 folder into the "
                     "LapStudio folder:\n    " + "\n    ".join(copyable))
    if runtimes:
        lines.append("Install (a local copy will not work, these are "
                     "side-by-side runtimes):\n    " + "\n    ".join(runtimes))
    return "\n\n".join(lines)

def load_dll():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Collect candidate DLL paths
    candidates = []
    for name in _DLL_NAMES:
        p = os.path.join(script_dir, name)
        if os.path.isfile(p):
            candidates.append(p)
    # Also scan for any matlabxrk dll
    for f in os.listdir(script_dir):
        if 'matlabxrk' in f.lower() and f.lower().endswith('.dll'):
            p = os.path.join(script_dir, f)
            if p not in candidates:
                candidates.append(p)

    if not candidates:
        raise RuntimeError(f"No MatLabXRK DLL found in {script_dir}")

    # Where the dependent DLLs may live: alongside the AIM DLL, in bin/, in a
    # user-specified folder, or in the RaceStudio install (which ships them).
    dep_dirs = [script_dir, os.path.join(script_dir, 'bin')]
    if os.environ.get('XRK_DLL_PATH'):
        dep_dirs.insert(0, os.environ['XRK_DLL_PATH'])
    dep_dirs.extend([d for d in _RS3_DIRS if os.path.isdir(d)])

    # Add them all to the Windows DLL search path so imports resolve
    if hasattr(os, 'add_dll_directory'):
        for d in dep_dirs:
            try:
                if d and os.path.isdir(d):
                    os.add_dll_directory(d)
            except Exception:
                pass
    # Also prepend to PATH, which helps the legacy search order
    try:
        os.environ['PATH'] = os.pathsep.join(
            [d for d in dep_dirs if d and os.path.isdir(d)] + [os.environ.get('PATH', '')])
    except Exception:
        pass

    # Report up-front if a required dependency (at any depth) isn't present
    if candidates:
        miss = find_missing_deps(candidates[0], dep_dirs)
        if miss:
            print(f"WARNING: dependencies are missing - {', '.join(miss)}",
                  file=sys.stderr)
            print(describe_missing(miss), file=sys.stderr)

    errors = []
    for p in candidates:
        # Strategy A: winmode=0 (old-style search, finds side-by-side deps)
        try:
            lib = ctypes.CDLL(p, winmode=0)
            print(f"Loaded (winmode=0): {p}", file=sys.stderr)
            return lib
        except (OSError, TypeError, AttributeError) as e:
            errors.append(f"winmode=0 {p}: {e}")

        # Strategy B: bare name from cwd (we're already in the right dir)
        try:
            os.chdir(script_dir)
            lib = ctypes.CDLL(os.path.basename(p), winmode=0)
            print(f"Loaded (bare name winmode=0): {p}", file=sys.stderr)
            return lib
        except (OSError, TypeError, AttributeError) as e:
            errors.append(f"bare winmode=0: {e}")

        # Strategy C: standard CDLL
        try:
            lib = ctypes.CDLL(p)
            print(f"Loaded (CDLL): {p}", file=sys.stderr)
            return lib
        except (OSError, AttributeError, TypeError) as e:
            errors.append(f"CDLL {p}: {e}")

        # Strategy D: WinDLL
        try:
            lib = ctypes.WinDLL(p)
            print(f"Loaded (WinDLL): {p}", file=sys.stderr)
            return lib
        except (OSError, AttributeError, TypeError) as e:
            errors.append(f"WinDLL {p}: {e}")

    print("All load strategies failed:", file=sys.stderr)
    for e in errors:
        print(f"  {e}", file=sys.stderr)

    # Last resort: check what deps are missing using Windows API
    try:
        # NOTE: do NOT "import ctypes.wintypes" here — a function-local import
        # would make `ctypes` a local name for the whole function and every
        # earlier ctypes.CDLL call would raise UnboundLocalError.
        k32 = ctypes.WinDLL('kernel32')
        for p in candidates:
            h = k32.LoadLibraryExW(p, None, 0x00000001)  # LOAD_LIBRARY_AS_DATAFILE
            if h:
                print(f"File is readable as data: {p}", file=sys.stderr)
                k32.FreeLibrary(h)
    except Exception as e:
        print(f"LoadLibraryEx check: {e}", file=sys.stderr)

    miss = find_missing_deps(candidates[0], dep_dirs) if candidates else []
    if miss:
        raise RuntimeError(
            "Cannot load the AIM DLL: required dependencies are missing - "
            + ", ".join(miss) + ".\n\n" + describe_missing(miss))
    raise RuntimeError(
        f"Cannot load AIM DLL (dependencies appear present). "
        f"Check that Python and the DLL are both 64-bit and that the Visual C++ "
        f"runtime is installed.\nErrors: {errors[:3]}")


def setup(lib):
    ci=ctypes.c_int; cp=ctypes.c_char_p; pcd=ctypes.POINTER(ctypes.c_double)
    lib.open_file.argtypes=[cp];    lib.open_file.restype=ci
    lib.close_file_i.argtypes=[ci]; lib.close_file_i.restype=ci
    lib.get_vehicle_name.argtypes=[ci];      lib.get_vehicle_name.restype=cp
    lib.get_track_name.argtypes=[ci];        lib.get_track_name.restype=cp
    lib.get_racer_name.argtypes=[ci];        lib.get_racer_name.restype=cp
    lib.get_championship_name.argtypes=[ci]; lib.get_championship_name.restype=cp
    lib.get_date_and_time.argtypes=[ci];     lib.get_date_and_time.restype=ctypes.c_void_p
    lib.get_laps_count.argtypes=[ci];        lib.get_laps_count.restype=ci
    lib.get_lap_info.argtypes=[ci,ci,pcd,pcd]; lib.get_lap_info.restype=ci
    lib.get_channels_count.argtypes=[ci];    lib.get_channels_count.restype=ci
    lib.get_channel_name.argtypes=[ci,ci];   lib.get_channel_name.restype=cp
    lib.get_channel_samples_count.argtypes=[ci,ci]; lib.get_channel_samples_count.restype=ci
    lib.get_channel_samples.argtypes=[ci,ci,pcd,pcd,ci]; lib.get_channel_samples.restype=ci
    lib.get_GPS_channels_count.argtypes=[ci];       lib.get_GPS_channels_count.restype=ci
    lib.get_GPS_channel_name.argtypes=[ci,ci];      lib.get_GPS_channel_name.restype=cp
    lib.get_GPS_channel_samples_count.argtypes=[ci,ci]; lib.get_GPS_channel_samples_count.restype=ci
    lib.get_GPS_channel_samples.argtypes=[ci,ci,pcd,pcd,ci]; lib.get_GPS_channel_samples.restype=ci
    lib.get_GPS_raw_channels_count.argtypes=[ci];        lib.get_GPS_raw_channels_count.restype=ci
    lib.get_GPS_raw_channel_name.argtypes=[ci,ci];       lib.get_GPS_raw_channel_name.restype=cp
    lib.get_GPS_raw_channel_samples_count.argtypes=[ci,ci]; lib.get_GPS_raw_channel_samples_count.restype=ci
    lib.get_GPS_raw_channel_samples.argtypes=[ci,ci,pcd,pcd,ci]; lib.get_GPS_raw_channel_samples.restype=ci
    # Units let the reader convert e.g. m/s -> km/h instead of guessing
    for _fn in ('get_channel_units','get_GPS_channel_units','get_GPS_raw_channel_units'):
        try:
            _f = getattr(lib, _fn)
            _f.argtypes=[ci,ci]; _f.restype=cp
        except Exception:
            pass

def s(b):
    return b.decode('utf-8','replace').strip() if b else ''

def read_ch(lib, fh, idx, gps=False, raw=False):
    """Read one channel. Returns numpy arrays when numpy is available (fast,
    no Python-list conversion), else plain lists."""
    if raw:   n = lib.get_GPS_raw_channel_samples_count(fh,idx)
    elif gps: n = lib.get_GPS_channel_samples_count(fh,idx)
    else:     n = lib.get_channel_samples_count(fh,idx)
    if n<=0: return [],[]
    bt=(ctypes.c_double*n)(); bv=(ctypes.c_double*n)()
    if raw:   lib.get_GPS_raw_channel_samples(fh,idx,bt,bv,n)
    elif gps: lib.get_GPS_channel_samples(fh,idx,bt,bv,n)
    else:     lib.get_channel_samples(fh,idx,bt,bv,n)
    if _np is not None:
        # frombuffer avoids copying 2 x n doubles through Python objects, which
        # is what made loading take minutes.
        t = _np.frombuffer(bt, dtype=_np.float64, count=n).copy()
        v = _np.frombuffer(bv, dtype=_np.float64, count=n).copy()
        return t, v
    return list(bt), list(bv)

def fmt_tm(ptr):
    if not ptr: return ''
    try:
        f=(ctypes.c_int*9).from_address(ptr)
        return f'{f[5]+1900:04d}-{f[4]+1:02d}-{f[3]:02d} {f[2]:02d}:{f[1]:02d}:{f[0]:02d}'
    except: return ''

def main():
    if len(sys.argv)<3:
        print('Usage: xrk_helper.py <file> <out.json>'); sys.exit(1)
    filepath = sys.argv[1]
    outpath  = sys.argv[2]

    # Change to script dir so Windows can find DLL dependencies
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    lib = load_dll()
    setup(lib)

    fh = lib.open_file(filepath.encode())
    if fh<=0:
        # Ask the DLL why, and retry as a wide string in case this is the
        # Unicode build expecting wchar_t* (the "ReleaseU" DLLs sometimes are).
        _why = ''
        try:
            lib.get_last_open_error.restype = ctypes.c_char_p
            _why = s(lib.get_last_open_error())
        except Exception:
            pass
        try:
            lib.open_file.argtypes = [ctypes.c_wchar_p]
            fh = lib.open_file(filepath)
        except Exception:
            fh = 0
        if fh <= 0:
            print(f'ERROR: open_file returned {fh}'
                  + (f' ({_why})' if _why else '')); sys.exit(2)
    try:
        meta = {
            'vehicle':     s(lib.get_vehicle_name(fh)),
            'track':       s(lib.get_track_name(fh)),
            'racer':       s(lib.get_racer_name(fh)),
            'championship':s(lib.get_championship_name(fh)),
            'date':        fmt_tm(lib.get_date_and_time(fh)),
            'n_laps':      lib.get_laps_count(fh),
        }
        laps=[]
        for i in range(meta['n_laps']):
            st=ctypes.c_double(0); dur=ctypes.c_double(0)
            lib.get_lap_info(fh,i,ctypes.byref(st),ctypes.byref(dur))
            laps.append([float(st.value),float(st.value+dur.value),float(dur.value)])
        meta['laps']=laps

        channels={}
        units={}
        def _u(fn, idx):
            try:
                return s(getattr(lib, fn)(fh, idx))
            except Exception:
                return ''
        for i in range(lib.get_channels_count(fh)):
            nm=s(lib.get_channel_name(fh,i)) or f'ch_{i}'
            t,v=read_ch(lib,fh,i)
            if len(t)>1:
                channels[nm]={'t':t,'v':v}; units[nm]=_u('get_channel_units', i)
        for i in range(lib.get_GPS_channels_count(fh)):
            nm=s(lib.get_GPS_channel_name(fh,i)) or f'gps_{i}'
            t,v=read_ch(lib,fh,i,gps=True)
            if len(t)>1:
                channels[nm]={'t':t,'v':v}; units[nm]=_u('get_GPS_channel_units', i)
        for i in range(lib.get_GPS_raw_channels_count(fh)):
            nm=s(lib.get_GPS_raw_channel_name(fh,i)) or f'raw_{i}'
            t,v=read_ch(lib,fh,i,raw=True)
            if len(t)>1:
                channels[nm]={'t':t,'v':v}; units[nm]=_u('get_GPS_raw_channel_units', i)
        meta['units']=units

        # Write the samples in binary (.npz) when numpy is available — writing
        # them as JSON text took minutes for a full session. Metadata rides
        # along as a JSON string inside the archive.
        if _np is not None:
            arrays = {'__meta__': _np.frombuffer(
                json.dumps(meta).encode('utf-8'), dtype=_np.uint8)}
            order = []
            # Most channels share an identical time base, so store each unique
            # time array once and reference it — roughly halves the transfer.
            t_uniq = []          # list of arrays
            t_keys = {}          # (len, first, last, checksum) -> index
            t_map = []           # channel index -> time array index
            for i, (nm, ch) in enumerate(channels.items()):
                order.append(nm)
                t = _np.asarray(ch['t'], dtype=_np.float64)
                key = (int(t.size), float(t[0]), float(t[-1]),
                       float(t.sum()) if t.size < 4_000_000 else 0.0)
                idx = t_keys.get(key)
                if idx is None or not _np.array_equal(t_uniq[idx], t):
                    idx = len(t_uniq)
                    t_uniq.append(t)
                    t_keys[key] = idx
                t_map.append(idx)
                arrays[f'v{i}'] = _np.asarray(ch['v'], dtype=_np.float32)
            for k, t in enumerate(t_uniq):
                arrays[f'tu{k}'] = t
            arrays['__tmap__'] = _np.asarray(t_map, dtype=_np.int32)
            arrays['__names__'] = _np.frombuffer(
                json.dumps(order).encode('utf-8'), dtype=_np.uint8)
            print(f"time bases: {len(t_uniq)} unique for {len(order)} channels",
                  file=sys.stderr)
            _np.savez(outpath, **arrays)      # uncompressed = fastest
            # np.savez appends .npz unless the name already ends with it
            if not outpath.endswith('.npz') and os.path.isfile(outpath + '.npz'):
                os.replace(outpath + '.npz', outpath)
            print(f"OK:{meta['n_laps']} laps, {len(channels)} channels (npz)")
        else:
            with open(outpath,'w') as f:
                json.dump({'meta':meta,'channels':channels},f)
            print(f"OK:{meta['n_laps']} laps, {len(channels)} channels")

    finally:
        lib.close_file_i(fh)

if __name__=='__main__':
    main()
