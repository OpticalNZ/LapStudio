"""
xrk_helper.py — runs as subprocess from the DLL folder.
Tries multiple loading strategies to handle Python 3.8+ DLL restrictions.
"""
import ctypes, os, sys, json

_DLL_NAMES = [
    'MatLabXRK-2022-64-ReleaseU.dll',
    'MatLabXRK-2022-32-ReleaseU.dll',
    'MatLabXRK-64-ReleaseU.dll',
    'MatLabXRK-32-ReleaseU.dll',
    'MatLabXRK-2017-64-ReleaseU.dll',
]

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

    # Add script dir to Windows DLL search path
    if hasattr(os, 'add_dll_directory'):
        try: os.add_dll_directory(script_dir)
        except Exception: pass
        try: os.add_dll_directory(os.path.join(script_dir, 'bin'))
        except Exception: pass

    errors = []
    for p in candidates:
        # Strategy A: winmode=0 (old-style search, finds side-by-side deps)
        try:
            lib = ctypes.CDLL(p, winmode=0)
            print(f"Loaded (winmode=0): {p}", file=sys.stderr)
            return lib
        except (OSError, TypeError) as e:
            errors.append(f"winmode=0 {p}: {e}")

        # Strategy B: bare name from cwd (we're already in the right dir)
        try:
            os.chdir(script_dir)
            lib = ctypes.CDLL(os.path.basename(p), winmode=0)
            print(f"Loaded (bare name winmode=0): {p}", file=sys.stderr)
            return lib
        except (OSError, TypeError) as e:
            errors.append(f"bare winmode=0: {e}")

        # Strategy C: standard CDLL
        try:
            lib = ctypes.CDLL(p)
            print(f"Loaded (CDLL): {p}", file=sys.stderr)
            return lib
        except OSError as e:
            errors.append(f"CDLL {p}: {e}")

        # Strategy D: WinDLL
        try:
            lib = ctypes.WinDLL(p)
            print(f"Loaded (WinDLL): {p}", file=sys.stderr)
            return lib
        except OSError as e:
            errors.append(f"WinDLL {p}: {e}")

    print("All load strategies failed:", file=sys.stderr)
    for e in errors:
        print(f"  {e}", file=sys.stderr)

    # Last resort: check what deps are missing using Windows API
    try:
        import ctypes.wintypes
        k32 = ctypes.WinDLL('kernel32')
        for p in candidates:
            h = k32.LoadLibraryExW(p, None, 0x00000001)  # LOAD_LIBRARY_AS_DATAFILE
            if h:
                print(f"File is readable as data: {p}", file=sys.stderr)
                k32.FreeLibrary(h)
    except Exception as e:
        print(f"LoadLibraryEx check: {e}", file=sys.stderr)

    raise RuntimeError(
        f"Cannot load AIM DLL. Missing Visual C++ runtime or dependency.\n"
        f"Errors: {errors[:3]}")


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

def s(b):
    return b.decode('utf-8','replace').strip() if b else ''

def read_ch(lib, fh, idx, gps=False, raw=False):
    if raw:   n = lib.get_GPS_raw_channel_samples_count(fh,idx)
    elif gps: n = lib.get_GPS_channel_samples_count(fh,idx)
    else:     n = lib.get_channel_samples_count(fh,idx)
    if n<=0: return [],[]
    bt=(ctypes.c_double*n)(); bv=(ctypes.c_double*n)()
    if raw:   lib.get_GPS_raw_channel_samples(fh,idx,bt,bv,n)
    elif gps: lib.get_GPS_channel_samples(fh,idx,bt,bv,n)
    else:     lib.get_channel_samples(fh,idx,bt,bv,n)
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
        print(f'ERROR: open_file returned {fh}'); sys.exit(2)
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
        for i in range(lib.get_channels_count(fh)):
            nm=s(lib.get_channel_name(fh,i)) or f'ch_{i}'
            t,v=read_ch(lib,fh,i)
            if len(t)>1: channels[nm]={'t':t,'v':v}
        for i in range(lib.get_GPS_channels_count(fh)):
            nm=s(lib.get_GPS_channel_name(fh,i)) or f'gps_{i}'
            t,v=read_ch(lib,fh,i,gps=True)
            if len(t)>1: channels[nm]={'t':t,'v':v}
        for i in range(lib.get_GPS_raw_channels_count(fh)):
            nm=s(lib.get_GPS_raw_channel_name(fh,i)) or f'raw_{i}'
            t,v=read_ch(lib,fh,i,raw=True)
            if len(t)>1: channels[nm]={'t':t,'v':v}

        with open(outpath,'w') as f:
            json.dump({'meta':meta,'channels':channels},f)
        print(f"OK:{meta['n_laps']} laps, {len(channels)} channels")
    finally:
        lib.close_file_i(fh)

if __name__=='__main__':
    main()
