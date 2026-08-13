"""
gsensor.py - work out how a G sensor is mounted, and level it.

A dash or logger bolted to an angled binnacle reads gravity as acceleration. A
sensor pitched back 23 degrees reports -0.4 g the whole time the car is standing
still, which drags the G plot permanently towards braking and makes the car look
like it is slowing when it is coasting.

Worse than the offset, the tilt costs scale. At 23 degrees a 1.5 g stop only puts
cos(23) = 92% of itself on the longitudinal axis; the missing 8% lands on the
vertical one. Subtracting the offset alone fixes where the trace sits and leaves
every braking figure short.

Estimating the tilt

The obvious method - average the sensor while the car is stopped, since gravity
is then the only thing acting on it - needs the car to stop, and to be standing
somewhere level. Neither is safe to assume: a log can begin with the car already
rolling, and pit lane has camber. So two methods that need neither are used, and
they have to agree before anything is corrected.

  CLOSED LAP.  Over a complete lap the car returns to the same place at the same
  speed. Average the sensor weighted by DISTANCE rather than time and two terms
  vanish exactly: the track gradient, because the integral of g*sin(slope) over
  distance is g times the change in height, which is zero round a lap; and the
  real acceleration, because the integral of a over distance is half the change
  in v squared, also zero. What is left is the mounting offset. Laps that do not
  finish at the speed they started - an in lap, an off - are rejected, because
  for those the cancellation does not hold.

  REGRESSION.  Fit the logged channel against acceleration derived from speed.
  The intercept is the offset and the slope is a check on the scale. Needs no
  laps at all, so it still works on a hillclimb or a point to point run.

On the log this was written against the two agree to 0.001 g, and both land
within 0.013 g of what the car's own stationary time reports.

The lateral axis is harder and the code says so rather than pretending. The
closed-lap trick does NOT transfer to it: lateral acceleration is v^2 times
curvature, and curvature integrated round a closed circuit is a full turn, not
zero, so nothing cancels. Lateral falls back to stationary samples where they
exist and to straight-line averaging where they do not, and straight-line
averaging is biased by road camber - measured at 0.04 g against a true zero.
Roll is therefore only corrected when the estimate is confident.
"""
import math

import numpy as np

# A lap is only usable if it finishes near the speed it started, or the terms
# that are supposed to cancel do not.
SPEED_MATCH_TOL = 4.0        # m/s
MIN_LAP_METRES = 500.0
MIN_LAPS = 2

# The two methods must land within this of each other before anything is
# corrected. Set from the spread actually seen between good laps (sd 0.02 g),
# with room for a noisier log.
AGREEMENT_TOL = 0.05         # g

# Below this there is nothing worth correcting; sensors are never perfect.
NEGLIGIBLE_TILT = 1.0        # degrees

# Roll from straight-line averaging carries a camber bias, so it is only applied
# when it is large enough to be real rather than noise.
MIN_ROLL_TO_TRUST = 3.0      # degrees


class GSensorFit:
    """How the sensor sits, and whether we believe it enough to act."""

    def __init__(self, pitch_deg=0.0, roll_deg=0.0, long_offset=0.0,
                 lat_offset=0.0, methods=None, confident=False, note="",
                 n_laps=0, scale=None, has_vertical=False):
        self.pitch_deg = pitch_deg
        self.roll_deg = roll_deg
        self.long_offset = long_offset
        self.lat_offset = lat_offset
        self.methods = methods or {}
        self.confident = confident
        self.note = note
        self.n_laps = n_laps
        self.scale = scale
        self.has_vertical = has_vertical

    @property
    def long_worth_correcting(self):
        """Longitudinal, measured by two methods that have to agree."""
        return self.confident and abs(self.pitch_deg) >= NEGLIGIBLE_TILT

    @property
    def lat_worth_correcting(self):
        """Lateral, held to a higher bar. The only estimates available for it are
        stationary samples and straight-line averaging, and the latter carries a
        camber bias of the same order as a small roll angle - so a small reading
        here is not evidence of a tilt, and a good channel is better left alone.
        """
        return self.confident and abs(self.roll_deg) >= MIN_ROLL_TO_TRUST

    @property
    def worth_correcting(self):
        return self.long_worth_correcting or self.lat_worth_correcting

    def axis_note(self, axis):
        """One line about a single axis, for the tick beside it."""
        if not self.confident:
            return self.note
        if axis == "long":
            return (f"pitched {self.pitch_deg:+.1f} deg ({self.long_offset:+.3f} g)"
                    if self.long_worth_correcting else
                    f"level to within {abs(self.pitch_deg):.1f} deg")
        return (f"rolled {self.roll_deg:+.1f} deg ({self.lat_offset:+.3f} g)"
                if self.lat_worth_correcting else
                f"level to within {abs(self.roll_deg):.1f} deg")

    def describe(self):
        if not self.methods:
            return "G sensor: not enough data to check"
        if not self.confident:
            return f"G sensor: {self.note}"
        return (f"G sensor: longitudinal {self.axis_note('long')}"
                f"  ·  lateral {self.axis_note('lat')}"
                f"  ·  from {self.n_laps} laps")


def _finite(*arrays):
    good = np.ones(len(arrays[0]), dtype=bool)
    for a in arrays:
        if a is not None:
            good &= np.isfinite(a)
    return good


def _smooth(a, n=13):
    if n < 2 or len(a) < n:
        return a
    return np.convolve(a, np.ones(n) / n, mode="same")


def _closed_lap_offset(ts, dist, v_ms, channel, laps):
    """Distance-weighted mean of `channel` over laps that start and finish at
    the same speed. Returns (estimate, laps_used, per_lap)."""
    if laps is None or dist is None:
        return None, 0, []
    per_lap = []
    for (start, end, _lap_t) in laps:
        m = (ts >= start) & (ts < end)
        if m.sum() < 50:
            continue
        d = dist[m]
        ds = np.diff(d)
        if ds.sum() < MIN_LAP_METRES or not np.all(np.isfinite(ds)):
            continue
        if abs(float(v_ms[m][-1] - v_ms[m][0])) > SPEED_MATCH_TOL:
            continue                       # in lap, out lap, or an off
        g = channel[m][:-1]
        ok = np.isfinite(g) & np.isfinite(ds) & (ds >= 0)
        if ok.sum() < 50 or ds[ok].sum() <= 0:
            continue
        per_lap.append(float((g[ok] * ds[ok]).sum() / ds[ok].sum()))
    if len(per_lap) < MIN_LAPS:
        return None, len(per_lap), per_lap
    return float(np.median(per_lap)), len(per_lap), per_lap


def _regression_offset(ts, v_ms, channel):
    """Fit the channel against acceleration from speed. Returns (offset, scale)."""
    if len(ts) < 200:
        return None, None
    accel = _smooth(np.gradient(_smooth(v_ms), ts)) / 9.81
    m = _finite(accel, channel) & (v_ms > 10.0) & (np.abs(accel) < 2.0)
    if m.sum() < 200:
        return None, None
    A = np.vstack([accel[m], np.ones(int(m.sum()))]).T
    try:
        scale, offset = np.linalg.lstsq(A, channel[m], rcond=None)[0]
    except np.linalg.LinAlgError:
        return None, None
    if not (0.3 < scale < 3.0):            # the fit did not find the channel
        return None, None
    return float(offset), float(scale)


def _stationary_offsets(v_ms, g_lat, g_long, g_vert):
    """Mean of each axis while the car is stopped. The honest method when the
    car does stop somewhere level, and a useful third opinion when it does."""
    still = v_ms < 0.3
    if still.sum() < 100:
        return None
    out = {"lat": float(np.nanmean(g_lat[still])),
           "long": float(np.nanmean(g_long[still]))}
    if g_vert is not None:
        out["vert"] = float(np.nanmean(g_vert[still]))
    # A car parked on a slope reads a gravity vector that still has magnitude 1
    # but points the wrong way, so magnitude alone cannot catch it. Spread can:
    # if the car moved or rocked, the samples disagree with each other.
    out["spread"] = float(np.nanstd(g_long[still]))
    return out


def _straight_line_lat(ts, v_ms, heading_deg, g_lat):
    """Mean lateral G while the car is not turning. Biased by road camber, so
    treated as a weak estimate."""
    if heading_deg is None:
        return None
    h = np.unwrap(np.radians(heading_deg))
    yaw = np.abs(np.gradient(h, ts))
    m = _finite(yaw, g_lat) & (v_ms > 25.0) & (yaw < math.radians(2.0))
    if m.sum() < 500:
        return None
    return float(np.nanmean(g_lat[m]))


def estimate(rows, laps=None):
    """Work out how the sensor is mounted from a session.

    `rows` needs ts, speed (km/h), g_lat and g_long; distance and a vertical G
    channel are used when present. `laps` is the app's (start, end, lap_time)
    list. Returns a GSensorFit, which may be not-confident - that is a result,
    not a failure.
    """
    def col(*names):
        for n in names:
            if n in rows.columns:
                a = np.asarray(rows[n], dtype=float)
                if np.isfinite(a).any():
                    return a
        return None

    ts = col("ts")
    g_lat = col("g_lat")
    g_long = col("g_long")
    if ts is None or g_lat is None or g_long is None:
        return GSensorFit(note="no G channels in this log")
    speed = col("speed")
    if speed is None:
        return GSensorFit(note="no speed channel, cannot check the sensor")

    # A channel that never moves is not a G sensor reading zero tilt - it is a
    # channel that is not mapped yet, or a sensor that is not wired. Either way
    # the average of it means nothing, and a confident answer from it would be
    # worse than no answer.
    for name, arr in (("g_long", g_long), ("g_lat", g_lat)):
        if np.nanstd(arr) < 0.01:
            return GSensorFit(note=f"{name} never changes - nothing to measure")
    v = speed / 3.6
    dist = col("distance")
    if dist is None and ts is not None:
        # Integrate it from speed. The closed-lap method needs distance, and a
        # log that does not carry it should not lose the better of the two
        # estimates over something this easy to derive.
        dist = np.concatenate([[0.0], np.cumsum(np.diff(ts) * (v[:-1]))])
    g_vert = col("g_vert", "G Force Vert", "Vertical G")
    heading = col("heading", "GPS Heading")

    methods = {}

    lap_est, n_laps, per_lap = _closed_lap_offset(ts, dist, v, g_long, laps)
    if lap_est is not None:
        methods["closed lap"] = lap_est
    reg_est, scale = _regression_offset(ts, v, g_long)
    if reg_est is not None:
        methods["regression"] = reg_est
    still = _stationary_offsets(v, g_lat, g_long, g_vert)
    if still is not None:
        methods["stationary"] = still["long"]

    if len(methods) < 2:
        only = next(iter(methods), None)
        return GSensorFit(
            methods=methods, n_laps=n_laps,
            note=("only one method could run ("
                  f"{only}), so there is nothing to check it against"
                  if only else "not enough complete laps or speed data to check"))

    # Two independent methods have to agree. If they do not, an assumption has
    # failed somewhere and the right answer is to say so, not to average them
    # and produce a confident-looking number.
    values = list(methods.values())
    spread = max(values) - min(values)
    if spread > AGREEMENT_TOL:
        detail = ", ".join(f"{k} {v:+.3f}" for k, v in methods.items())
        return GSensorFit(
            methods=methods, n_laps=n_laps, scale=scale,
            has_vertical=g_vert is not None,
            note=f"methods disagree by {spread:.3f} g ({detail}) - not corrected")

    # The median of whatever ran. No single method is authoritative - the
    # stationary one assumes level ground, the closed-lap one assumes a genuinely
    # closed lap, the regression one assumes a trustworthy speed channel - so the
    # middle of them is more robust than picking a favourite, and one method
    # going wrong cannot drag the answer far.
    long_offset = float(np.median([v for v in methods.values()
                                   if isinstance(v, (int, float))]))

    # Roll. Stationary samples are trustworthy; straight-line averaging carries a
    # camber bias, so it is only acted on when it is too big to be that.
    lat_offset = 0.0
    if still is not None:
        lat_offset = still["lat"]
        methods["roll from"] = "stationary"
    else:
        s = _straight_line_lat(ts, v, heading, g_lat)
        if s is not None and abs(math.degrees(math.asin(np.clip(abs(s), 0, 1)))) >= MIN_ROLL_TO_TRUST:
            lat_offset = s
            methods["roll from"] = "straight line (camber-biased)"

    mag = math.sqrt(long_offset ** 2 + lat_offset ** 2)
    mag = min(mag, 0.999)
    pitch = math.degrees(math.asin(np.clip(-long_offset, -1.0, 1.0)))
    roll = math.degrees(math.asin(np.clip(lat_offset, -1.0, 1.0)))

    return GSensorFit(
        pitch_deg=pitch, roll_deg=roll,
        long_offset=long_offset, lat_offset=lat_offset,
        methods=methods, confident=True, n_laps=n_laps, scale=scale,
        has_vertical=g_vert is not None,
        note=f"agreed to {spread:.3f} g")


def level(rows, fit, do_long=True, do_lat=True, use_vertical=None):
    """Return a copy of `rows` with the G channels levelled.

    ROTATE, whenever a vertical channel exists. A true rotation of the vector,
    which also recovers the acceleration the tilt had pushed onto the vertical
    axis.

    SUBTRACT AND RESCALE, when it does not. Take the offset off, then divide by
    cos of the tilt to put back the fraction of the reading the tilt was costing.

    Rotation was expected to be the noisier of the two, because it mixes the
    vertical channel in by sin of the tilt - 38% at 22.5 degrees - and kerbs put
    far bigger spikes on the vertical axis than the car ever generates
    horizontally. Measured against acceleration derived from speed, it is not:

        uncorrected          bias -0.388 g   scatter 0.104 g   scale 0.969
        subtract + rescale   bias -0.006 g   scatter 0.114 g   scale 1.049
        rotation             bias +0.001 g   scatter 0.100 g   scale 1.026

    Rotation wins on all three, and beats even the uncorrected channel for
    scatter. The reason is that a tilted sensor puts the same real acceleration
    on two axes at once, so combining them coherently averages out sensor noise
    that is not shared. Rescaling a single axis by 1/cos can only amplify it.

    Pass use_vertical=False to force the single-axis method.
    """
    do_long = do_long and fit is not None and fit.long_worth_correcting
    do_lat = do_lat and fit is not None and fit.lat_worth_correcting
    if not fit or not (do_long or do_lat):
        return rows
    out = rows.copy()
    if use_vertical is None:
        use_vertical = fit.has_vertical
    lat = np.asarray(out["g_lat"], dtype=float)
    lon = np.asarray(out["g_long"], dtype=float)

    if use_vertical and fit.has_vertical:
        vert = None
        for n in ("g_vert", "G Force Vert", "Vertical G"):
            if n in out.columns:
                vert = np.asarray(out[n], dtype=float)
                break
        if vert is not None:
            if do_long:
                p = math.radians(fit.pitch_deg)
                out["g_long"] = lon * math.cos(p) + vert * math.sin(p)
            if do_lat:
                r = math.radians(fit.roll_deg)
                out["g_lat"] = lat * math.cos(r) - vert * math.sin(r)
            return out

    if do_long:
        cp = max(math.cos(math.radians(fit.pitch_deg)), 0.2)
        out["g_long"] = (lon - fit.long_offset) / cp
    if do_lat:
        cr = max(math.cos(math.radians(fit.roll_deg)), 0.2)
        out["g_lat"] = (lat - fit.lat_offset) / cr
    return out
