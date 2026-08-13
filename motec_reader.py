"""
motec_reader.py - read MoTeC i2 .ld log files.

MoTeC does not publish the .ld format. This parser was derived by inspecting a
C125 dash log and checking every field against values the logger itself reports
in the companion .ldx file, so the layout below is what the files actually
contain rather than what any specification says.

Layout, all little-endian:

    0x00  u32   0x40, the file marker
    0x08  u32   pointer to the first channel header
    0x0C  u32   pointer to the sample data
    0x24  u32   pointer to the event block
    0x46  u32   logger serial number
    0x4A  8s    logger type, e.g. "C125"
    0x56  u32   channel count
    0x5E  16s   date, dd/mm/yyyy
    0x7E  16s   time, hh:mm:ss
    0x9E  64s   driver
    0xDE  64s   vehicle id

Channel headers form a doubly linked list, each 124 bytes:

    +0    u32   previous header, 0 at the head
    +4    u32   next header, 0 at the tail
    +8    u32   pointer to this channel's samples
    +12   u32   sample count
    +18   u16   storage class (see _NUMPY_TYPE)
    +20   u16   bytes per sample, 2 or 4
    +22   u16   sample rate in Hz
    +24   4×i16 shift, multiplier, divisor, decimal places
    +32   32s   name
    +64   8s    short name
    +72   12s   unit

A stored sample becomes a real value as:

    value = (raw / divisor * 10**-decimals + shift) * multiplier

Channels are logged at their own rates, from 1 Hz for lap counters to 50 Hz for
throttle and brakes, so everything is resampled onto one time base before the
app sees it.

The .ldx beside the .ld is plain XML holding the session details a driver types
into i2 - event, venue, driver, fastest lap. It is read when present because it
is more likely to be correct than the header written at logging time.
"""
import os
import re
import struct
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------

_MARKER = 0x40
_CHAN_HEADER_LEN = 124

# Storage class -> numpy type. 3 and 0 are 16-bit, 5 is 32-bit, 7 is float.
_NUMPY_TYPE = {0: np.int16, 3: np.int16, 5: np.int32, 7: np.float32}

# A channel that has nothing to report stores the largest value its type can
# hold. Left alone these arrive as 3276.7 deg of steering or 32767 kPa of brake
# pressure and wreck every auto-scaled gauge in the app.
_SENTINEL = {np.int16: 32767, np.int32: 2147483647, np.float32: None}

DEFAULT_RATE_HZ = 25.0

# ---------------------------------------------------------------------------
# Which channels are worth showing
# ---------------------------------------------------------------------------
# A dash logs its own health alongside the car: bus utilisation, transmit error
# counters, firmware versions. This log carries 124 channels and roughly a third
# are of that kind. They are hidden by default rather than deleted, because the
# exclusions are patterns and a pattern can always be wrong about someone else's
# channel naming.

_HIDE_PATTERNS = [
    r"\bCAN\b", r"comms", r"diag", r"error count", r"bus utilization",
    r"alarm", r"display", r"backlight", r"\bSLM\b", r"rotary", r"indicator",
    r"camshaft", r"trim knock", r"closed loop", r"aux supply",
    r"^v\d+ ", r"version", r"device up time", r"gps date", r"gps time",
    r"ignition cut", r"knock state", r"fuel cut", r"fuel pump",
    r"throttle aim", r"gear detect", r"engine state", r"limit state",
    r"fuel state", r"timing state", r"mass flow", r"fuel volume",
]

# Always keep these even if a pattern above would hide them.
_ALWAYS_SHOW = {
    "brake state", "engine speed limit", "gps sats used", "gps altitude",
    "gps heading", "gps speed",
}

# Held rather than ramped between samples. Interpolating a gear or a lap number
# would invent a 2.5th gear halfway through every shift.
_STEP_CHANNELS = {
    "gear", "lap number", "lap time", "beacon", "reference lap time",
    "lap time predicted", "fuel used per lap", "brake state",
    "left indicator", "right indicator",
}


def _hidden(name):
    low = name.lower()
    if low in _ALWAYS_SHOW:
        return False
    return any(re.search(p, low, re.I) for p in _HIDE_PATTERNS)


# ---------------------------------------------------------------------------
# Low level parsing
# ---------------------------------------------------------------------------

def _text(buf, off, length):
    """A fixed-width, NUL-padded string field."""
    return buf[off:off + length].split(b"\0")[0].decode("latin-1", "replace").strip()


def _read_header(buf):
    if len(buf) < 0x11E or struct.unpack_from("<I", buf, 0)[0] != _MARKER:
        raise ValueError("not a MoTeC .ld file (missing the 0x40 marker)")
    meta_ptr, data_ptr = struct.unpack_from("<II", buf, 0x08)
    event_ptr = struct.unpack_from("<I", buf, 0x24)[0]
    for label, ptr in (("channel", meta_ptr), ("data", data_ptr)):
        if not 0 < ptr < len(buf):
            raise ValueError(f"{label} pointer {ptr} is outside the file")
    head = {
        "meta_ptr": meta_ptr,
        "data_ptr": data_ptr,
        "serial": struct.unpack_from("<I", buf, 0x46)[0],
        "logger": _text(buf, 0x4A, 8),
        "channel_count": struct.unpack_from("<I", buf, 0x56)[0],
        "date": _text(buf, 0x5E, 16),
        "time": _text(buf, 0x7E, 16),
        "driver": _text(buf, 0x9E, 64),
        "vehicle": _text(buf, 0xDE, 64),
    }
    # The event block holds the session name and the pointer to the venue block.
    if 0 < event_ptr < len(buf) - 1156:
        head["event"] = _text(buf, event_ptr, 64)
        head["session"] = _text(buf, event_ptr + 64, 64)
        head["comment"] = _text(buf, event_ptr + 128, 1024)
        venue_ptr = struct.unpack_from("<I", buf, event_ptr + 1152)[0]
        if 0 < venue_ptr < len(buf) - 74:
            head["venue"] = _text(buf, venue_ptr, 64)
            # Track length is stored in millimetres, best lap in milliseconds.
            length_mm, best_ms = struct.unpack_from("<II", buf, venue_ptr + 66)
            if 0 < length_mm < 100_000_000:
                head["venue_length_m"] = length_mm / 1000.0
            if 0 < best_ms < 3_600_000:
                head["venue_best_lap_s"] = best_ms / 1000.0
    return head


def _read_channels(buf, meta_ptr):
    """Walk the linked list of channel headers."""
    channels, offset, seen = [], meta_ptr, set()
    while offset and offset not in seen and offset + _CHAN_HEADER_LEN <= len(buf):
        seen.add(offset)
        nxt, data_ptr, count = struct.unpack_from("<III", buf, offset + 4)
        storage = struct.unpack_from("<H", buf, offset + 18)[0]
        rate = struct.unpack_from("<H", buf, offset + 22)[0]
        shift, mul, div, decimals = struct.unpack_from("<hhhh", buf, offset + 24)
        channels.append({
            "name": _text(buf, offset + 32, 32),
            "short": _text(buf, offset + 64, 8),
            "unit": _text(buf, offset + 72, 12),
            "data_ptr": data_ptr, "count": count, "storage": storage,
            "rate": rate, "shift": shift, "mul": mul, "div": div,
            "decimals": decimals,
        })
        offset = nxt
    return channels


def _decode(buf, ch):
    """Samples of one channel as float, with unreported values as NaN."""
    dtype = _NUMPY_TYPE.get(ch["storage"])
    if dtype is None or ch["count"] <= 0:
        return None
    end = ch["data_ptr"] + ch["count"] * np.dtype(dtype).itemsize
    if end > len(buf):
        return None
    raw = np.frombuffer(buf, dtype=dtype, count=ch["count"], offset=ch["data_ptr"])
    out = raw.astype(np.float64)
    sentinel = _SENTINEL.get(dtype)
    if sentinel is not None:
        out[raw == sentinel] = np.nan
    div = ch["div"] or 1
    mul = ch["mul"] if ch["mul"] else 1
    return (out / div * 10.0 ** (-ch["decimals"]) + ch["shift"]) * mul


def _resample(values, rate, grid, step):
    """Put one channel on the shared time base.

    Held values (gear, lap number) take the last sample at or before each point;
    everything else is interpolated. NaN gaps are carried across so a dropout
    does not pull a trace down to zero.
    """
    if rate <= 0 or len(values) == 0:
        return np.full(len(grid), np.nan)
    src = np.arange(len(values), dtype=np.float64) / rate
    good = ~np.isnan(values)
    if not good.any():
        return np.full(len(grid), np.nan)
    if step:
        idx = np.searchsorted(src, grid, side="right") - 1
        np.clip(idx, 0, len(values) - 1, out=idx)
        out = values[idx]
        if not good.all():                       # carry the last real value
            fill = np.maximum.accumulate(np.where(good, np.arange(len(values)), 0))
            out = values[fill[idx]]
        return out
    return np.interp(grid, src[good], values[good])


# ---------------------------------------------------------------------------
# The .ldx sidecar
# ---------------------------------------------------------------------------

def read_ldx(path):
    """Session details from the .ldx beside a .ld, or {} if there is none."""
    base = os.path.splitext(path)[0]
    candidates = [base + ".ldx", base + ".LDX"]

    # MoTeC writes the pair with matching names, but people rename log files.
    # Fall back to an .ldx in the same folder whose name is contained in the
    # .ld's, which recovers "Hampton 22 May - 20240522-1905502.ld" ->
    # "20240522-1905502.ldx" without matching an unrelated session.
    folder, stem = os.path.split(base)
    try:
        for entry in os.listdir(folder or "."):
            if entry.lower().endswith(".ldx"):
                other = os.path.splitext(entry)[0]
                if len(other) >= 8 and other in stem:
                    candidates.append(os.path.join(folder, entry))
    except OSError:
        pass

    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        try:
            root = ET.parse(candidate).getroot()
        except (ET.ParseError, OSError):
            continue
        out = {}
        for node in root.iter():
            key, value = node.get("Id"), node.get("Value")
            if key and value not in (None, ""):
                out[key] = value
        if out:
            out["_ldx_path"] = candidate
            return out
    return {}


# ---------------------------------------------------------------------------
# Laps
# ---------------------------------------------------------------------------

def _laps_from_channels(named, rate_hz, duration):
    """Lap boundaries and exact lap times.

    MoTeC holds each completed lap time in the Lap Time channel for the whole of
    the following lap, so lap N's time is read from anywhere inside lap N+1.
    That is the logger's own beacon timing to a hundredth of a second, which
    beats anything derivable from a 20 Hz GPS trace.
    """
    if "Lap Number" not in named:
        return []
    lap_no = named["Lap Number"]
    if np.isnan(lap_no).all():
        return []
    lap_no = pd.Series(lap_no).ffill().bfill().to_numpy()
    lap_time = named.get("Lap Time")

    edges = np.flatnonzero(np.diff(lap_no) != 0) + 1
    bounds = np.concatenate([[0], edges, [len(lap_no)]])
    laps = []
    for i in range(len(bounds) - 1):
        a, b = int(bounds[i]), int(bounds[i + 1])
        if b <= a:
            continue
        number = int(round(lap_no[a]))
        start, end = a / rate_hz, min(b / rate_hz, duration)
        exact = None
        if lap_time is not None and i + 1 < len(bounds) - 1:
            # The value held during the NEXT lap is this lap's finished time.
            nxt = lap_time[int(bounds[i + 1]):int(bounds[i + 2])]
            nxt = nxt[~np.isnan(nxt)]
            if len(nxt):
                candidate = float(np.median(nxt))
                if 5.0 < candidate < 3600.0:
                    exact = candidate
        laps.append((start, end, exact if exact is not None else end - start, number))
    return laps


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def read_ld(filepath, progress_cb=None, rate_hz=DEFAULT_RATE_HZ, all_channels=False):
    """Read a MoTeC .ld and return (df, meta), matching read_vbo's contract.

    rate_hz       the shared time base. 25 Hz is well above any video frame rate
                  and halves the table compared with the 50 Hz channels.
    all_channels  include the logger's own diagnostic channels.
    """
    def report(pct, msg):
        if progress_cb:
            progress_cb(pct, 100, msg)

    report(0, "Reading MoTeC file")
    with open(filepath, "rb") as fh:
        buf = fh.read()

    head = _read_header(buf)
    channels = _read_channels(buf, head["meta_ptr"])
    if not channels:
        raise ValueError("no channels found - the file may be truncated")
    report(15, f"{len(channels)} channels")

    # Decode every channel on its own clock first.
    decoded, duration = {}, 0.0
    for ch in channels:
        values = _decode(buf, ch)
        if values is None or not ch["name"]:
            continue
        name = ch["name"]
        if name in decoded:                       # duplicate names do occur
            name = f"{name} ({ch['short'] or len(decoded)})"
            ch = dict(ch, name=name)
        decoded[name] = (ch, values)
        if ch["rate"]:
            duration = max(duration, len(values) / ch["rate"])
    if duration <= 0:
        raise ValueError("no channel carries a usable sample rate")
    report(45, "Decoded")

    # Drop GPS samples taken before the aerial has a fix, where both coordinates
    # read zero. This has to happen at the channel's own rate: once the shared
    # time base interpolates across a zero it produces a smooth ramp from New
    # Zealand towards the Atlantic, and every intermediate point looks valid.
    gps_dropouts = 0
    lat_ch, lon_ch = decoded.get("GPS Latitude"), decoded.get("GPS Longitude")
    if lat_ch is not None and lon_ch is not None and len(lat_ch[1]) == len(lon_ch[1]):
        no_fix_native = (np.abs(lat_ch[1]) < 1e-4) & (np.abs(lon_ch[1]) < 1e-4)
        gps_dropouts = int(no_fix_native.sum())
        if gps_dropouts and not no_fix_native.all():
            lat_ch[1][no_fix_native] = np.nan
            lon_ch[1][no_fix_native] = np.nan

    native = {n: v for n, (_c, v) in decoded.items()}
    laps_native = _laps_from_channels(
        {n: v for n, v in native.items() if decoded[n][0]["rate"] == 1},
        1.0, duration)

    # Everything onto one clock.
    grid = np.arange(0.0, duration, 1.0 / float(rate_hz))
    table, hidden = {}, []
    for name, (ch, values) in decoded.items():
        if not all_channels and _hidden(name):
            hidden.append(name)
            continue
        step = name.lower() in _STEP_CHANNELS or (
            ch["decimals"] == 0 and len(np.unique(values[~np.isnan(values)])) <= 64)
        table[name] = _resample(values, ch["rate"], grid, step)
    report(70, "Resampled")

    df = pd.DataFrame({"ts": grid})

    def take(*names, default=0.0):
        for n in names:
            if n in table and not np.isnan(table[n]).all():
                return pd.Series(table[n]).ffill().bfill().fillna(default).to_numpy()
        return np.full(len(grid), float(default))

    # ── Core channels the app expects by name ────────────────────────────────
    # Belt and braces: any coordinate that still reads zero after resampling is
    # held at the last good fix rather than drawn.
    lat, lon = take("GPS Latitude"), take("GPS Longitude")
    no_fix = (np.abs(lat) < 1e-4) & (np.abs(lon) < 1e-4)
    if no_fix.any() and not no_fix.all():
        lat[no_fix], lon[no_fix] = np.nan, np.nan
        lat = pd.Series(lat).ffill().bfill().to_numpy()
        lon = pd.Series(lon).ffill().bfill().to_numpy()
    df["lat"], df["lon"] = lat, lon

    # MoTeC stores -1 in a channel it has nothing to report for, which surfaces
    # as a fractionally negative speed or rpm. Clamping the channels that cannot
    # physically go negative is safer than reading -1 as missing everywhere,
    # because for a signed channel such as steering or lateral G it is a real
    # reading.
    df["speed"] = np.maximum(take("Ground Speed", "GPS Speed", "Drive Speed"), 0.0)
    df["rpm"] = np.maximum(take("Engine Speed"), 0.0)
    df["g_lat"] = take("G Force Lat")
    df["g_long"] = take("G Force Long")
    df["heading"] = take("GPS Heading")
    df["height"] = take("GPS Altitude")
    df["gear"] = np.rint(take("Gear")).astype(int).clip(0, 12)

    # Throttle: the butterfly, not the pedal. Both are logged; "Throttle Pedal"
    # stays available as a mappable channel for anyone who wants driver input.
    df["throttle"] = np.clip(take("Throttle Position", "Throttle Pedal"), 0, 100)

    # Brake: MoTeC logs pressure in kPa, not a percentage. Scaling against the
    # session's own peak gives a bar that modulates like the driver's foot.
    # A near-flat trace means the car brakes lightly, not that the scale is
    # wrong, so the peak used is reported in meta for the record.
    brake_kpa = take("Brake Pressure Front", "Brake Pressure Rear")
    brake_ref = float(np.nanpercentile(brake_kpa, 99.5)) if np.any(brake_kpa > 0) else 0.0
    if brake_ref > 1.0:
        df["brake"] = np.clip(brake_kpa / brake_ref * 100.0, 0, 100)
    else:                                    # pressure not logged - fall back
        df["brake"] = np.clip(take("Brake State") * 100.0, 0, 100)
        brake_ref = 0.0

    # Distance travelled, integrated from speed. MoTeC's own Lap Distance resets
    # every lap and is logged at 1 Hz, so it is no use as a session odometer.
    df["distance"] = np.concatenate([[0.0], np.cumsum(
        np.diff(grid) * (df["speed"].to_numpy()[:-1] / 3.6))])

    consumed = {
        "GPS Latitude", "GPS Longitude", "Ground Speed", "G Force Lat",
        "G Force Long", "GPS Heading", "GPS Altitude", "Engine Speed", "Gear",
        "Throttle Position",
    }
    for name, values in table.items():
        if name in consumed or name in df.columns:
            continue
        df[name] = pd.Series(values).ffill().bfill().fillna(0.0).to_numpy()
    report(90, "Assembled")

    # ── Metadata ─────────────────────────────────────────────────────────────
    sidecar = read_ldx(filepath)
    laps = [(s, e, t) for (s, e, t, _n) in laps_native if e > 0]
    meta = {
        "laps": laps,
        "lap_numbers": [n for (_s, _e, _t, n) in laps_native],
        "engine_channels": {
            "rpm": "Engine Speed" in table, "throttle": "Throttle Position" in table,
            "brake": brake_ref > 0, "gear": "Gear" in table,
        },
        "extra_channels": [c for c in df.columns
                           if c not in ("ts", "lat", "lon", "speed", "g_long",
                                        "g_lat", "heading", "height", "distance",
                                        "rpm", "throttle", "brake", "gear")],
        "gps_dropout_samples": gps_dropouts,
        "hidden_channels": hidden,
        "duration_s": float(grid[-1]) if len(grid) else 0.0,
        "n_samples": len(df),
        "has_gps": bool(np.any(np.abs(df["lat"]) > 0.0001)),
        "source": f"MoTeC {head.get('logger') or 'i2'}",
        "rate_hz": float(rate_hz),
        "brake_ref_kpa": brake_ref,
        "channel_count": len(decoded),
        # i2 numbers laps its own way; the sidecar is the version a user sees.
        "driver": sidecar.get("Driver") or head.get("driver", ""),
        "vehicle": sidecar.get("Vehicle Id") or head.get("vehicle", ""),
        "venue": sidecar.get("Venue") or head.get("venue", ""),
        "event": sidecar.get("Event") or head.get("event", ""),
        "session": sidecar.get("Session") or head.get("session", ""),
        "log_date": head.get("date", ""),
        "log_time": head.get("time", ""),
        "ldx": sidecar,
    }
    # Aliases so the shared UI code that reads a VBO's metadata also works here.
    meta["track"] = meta["venue"]
    timed = [t for (_s, _e, t) in laps if 5.0 < t < 3600.0]
    if timed:
        best = min(timed)
        meta["best_lap"] = f"{int(best // 60)}:{best % 60:06.3f}"
        meta["best_lap_s"] = best
    else:
        meta["best_lap"] = sidecar.get("Fastest Time", "")
    report(100, "MoTeC file loaded.")
    return df, meta


def is_motec_file(filepath):
    return os.path.splitext(filepath)[1].lower() in (".ld",)


# Alias, so callers can use the same verb as the other readers.
read_motec = read_ld


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        print("usage: python motec_reader.py <file.ld>")
        raise SystemExit(2)
    frame, info = read_ld(src)
    print(f"{info['source']}  {info['duration_s']:.1f}s  {info['n_samples']} rows "
          f"at {info['rate_hz']:.0f} Hz")
    print(f"{info['event']} / {info['venue']} / {info['driver']} / {info['vehicle']}")
    print(f"{info['channel_count']} channels, {len(info['hidden_channels'])} hidden")
    print(f"laps: {len(info['laps'])}")
    for (start, _end, lap_t), number in zip(info["laps"], info["lap_numbers"]):
        print(f"   lap {number:2d}  start {start:7.1f}s   {int(lap_t // 60)}:{lap_t % 60:06.3f}")
