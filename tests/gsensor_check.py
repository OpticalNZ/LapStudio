"""
gsensor_check.py - does the tilt estimator find a tilt we planted, and does it
refuse when it should?

The interesting failures are not "it returned a number" but "it returned a
confident number it had no business being confident about". So most of this file
builds sessions where the estimator is supposed to decline, and checks that it
does.

    python tests/gsensor_check.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gsensor as GS                             # noqa: E402

FAILURES = []


def chk(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   {detail}"))
    if not ok:
        FAILURES.append(label)


def synth_session(pitch_deg=0.0, roll_deg=0.0, n_laps=6, lap_secs=80.0, hz=25.0,
                  gradient=True, noise=0.01, seed=1):
    """A car going round a closed, hilly loop with a sensor tilted on purpose.

    The point of the gradient is that it must NOT leak into the estimate: the
    closed-lap method claims to cancel it, and this is where that claim is
    tested.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(0, n_laps * lap_secs, 1.0 / hz)
    phase = 2 * np.pi * (t % lap_secs) / lap_secs

    # Speed varies round the lap and returns to where it began each lap.
    v = 40.0 + 20.0 * np.sin(phase) + 6.0 * np.sin(3 * phase)      # m/s
    a_long = np.gradient(v, t) / 9.81                              # true, in g
    a_lat = 1.2 * np.sin(2 * phase)                                # true, in g

    # Track height, returning to the same value every lap, so the slope the car
    # sits on swings through the lap and cancels over it.
    slope = np.radians(4.0 * np.sin(2 * phase)) if gradient else np.zeros_like(t)

    # What a tilted sensor reads: the real acceleration rotated into its frame,
    # plus gravity leaking in through both the mount angle and the track slope.
    p, r = math.radians(pitch_deg), math.radians(roll_deg)
    g_long = a_long * math.cos(p) - math.sin(p) * np.cos(slope) + np.sin(slope)
    g_lat = a_lat * math.cos(r) + math.sin(r)
    g_vert = a_long * math.sin(p) + math.cos(p) * np.cos(slope)
    g_long += rng.normal(0, noise, len(t))
    g_lat += rng.normal(0, noise, len(t))
    g_vert += rng.normal(0, noise, len(t))

    dist = np.concatenate([[0.0], np.cumsum(np.diff(t) * v[:-1])])
    df = pd.DataFrame({"ts": t, "speed": v * 3.6, "g_lat": g_lat,
                       "g_long": g_long, "g_vert": g_vert, "distance": dist})
    laps = [(i * lap_secs, (i + 1) * lap_secs, lap_secs) for i in range(n_laps)]
    return df, laps


def main():
    print("--- finds a tilt that was planted ---")
    for want in (0.0, 5.0, 22.5, -12.0):
        df, laps = synth_session(pitch_deg=want)
        fit = GS.estimate(df, laps)
        got = fit.pitch_deg if fit.confident else float("nan")
        chk(f"pitch {want:+6.1f} deg -> {got:+6.2f}",
            fit.confident and abs(got - want) < 1.5, fit.note)

    print("\n--- track gradient must not leak into the estimate ---")
    flat, laps_f = synth_session(pitch_deg=15.0, gradient=False)
    hilly, laps_h = synth_session(pitch_deg=15.0, gradient=True)
    a, b = GS.estimate(flat, laps_f), GS.estimate(hilly, laps_h)
    chk("hilly and flat agree within 0.5 deg",
        abs(a.pitch_deg - b.pitch_deg) < 0.5,
        f"flat {a.pitch_deg:.2f} vs hilly {b.pitch_deg:.2f}")

    print("\n--- correction actually removes the tilt ---")
    df, laps = synth_session(pitch_deg=22.5)
    fit = GS.estimate(df, laps)
    for label, kw in (("rotation", {}), ("single axis", dict(use_vertical=False))):
        out = GS.level(df, fit, **kw)
        t = df.ts.to_numpy()
        truth = np.gradient(df.speed.to_numpy() / 3.6, t) / 9.81
        err = out.g_long.to_numpy() - truth
        chk(f"{label}: bias under 0.02 g", abs(err.mean()) < 0.02, f"{err.mean():+.4f}")
    raw_err = df.g_long.to_numpy() - np.gradient(df.speed.to_numpy() / 3.6, df.ts.to_numpy()) / 9.81
    chk("uncorrected really was biased", abs(raw_err.mean()) > 0.3, f"{raw_err.mean():+.3f}")

    print("\n--- declines when it should ---")
    df, laps = synth_session(pitch_deg=20.0)
    fit = GS.estimate(df, None)
    chk("no laps: still fits, via regression alone or declines cleanly",
        (not fit.confident) or abs(fit.pitch_deg - 20.0) < 2.0, fit.note)

    no_speed = df.drop(columns=["speed"])
    chk("no speed channel: declines", not GS.estimate(no_speed, laps).confident)

    no_g = df.drop(columns=["g_long"])
    chk("no G channel: declines", not GS.estimate(no_g, laps).confident)

    short = df.iloc[:50].copy()
    chk("almost no data: declines", not GS.estimate(short, laps[:1]).confident)

    # A sensor whose reading has nothing to do with the car's motion must not
    # produce a confident tilt just because the average is non-zero.
    rng = np.random.default_rng(7)
    junk = df.copy()
    junk["g_long"] = rng.normal(-0.4, 0.6, len(junk))
    jf = GS.estimate(junk, laps)
    chk("G channel that is pure noise: declines or reports no scale",
        (not jf.confident) or jf.scale is None, f"confident={jf.confident} scale={jf.scale}")

    print("\n--- a lap that does not close is rejected ---")
    df, laps = synth_session(pitch_deg=20.0, n_laps=6)
    # Turn the last lap into an in-lap by braking to a stop through it.
    last = df.ts >= laps[-1][0]
    df.loc[last, "speed"] = np.linspace(float(df.speed[last].iloc[0]), 5.0, int(last.sum()))
    fit = GS.estimate(df, laps)
    chk("in-lap excluded from the lap count", fit.n_laps == 5, f"used {fit.n_laps}")
    chk("estimate still right despite the bad lap",
        fit.confident and abs(fit.pitch_deg - 20.0) < 1.5, f"{fit.pitch_deg:.2f}")

    print("\n--- a channel that is not really there ---")
    # The bug this catches: the app can call the estimator before the channels
    # are mapped, when every column is zero. A confident "level sensor" answer
    # from that would stick and never be revisited.
    flat = df.copy()
    flat["g_long"] = 0.0
    ff = GS.estimate(flat, laps)
    chk("all-zero G channel: declines", not ff.confident, ff.note)
    flat2 = df.copy(); flat2["g_lat"] = 0.0
    chk("all-zero lateral: declines", not GS.estimate(flat2, laps).confident)

    print("\n--- a level sensor is left alone ---")
    df, laps = synth_session(pitch_deg=0.3)
    fit = GS.estimate(df, laps)
    chk("tiny tilt reported but not corrected", not fit.worth_correcting, fit.describe())
    out = GS.level(df, fit)
    chk("level() is a no-op when nothing is worth correcting",
        np.allclose(out.g_long.to_numpy(), df.g_long.to_numpy()))

    print()
    if FAILURES:
        print(f"FAILURES: {len(FAILURES)}")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("FAILURES: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
