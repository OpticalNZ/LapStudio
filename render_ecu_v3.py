import pandas as pd, numpy as np, sys, subprocess, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Wedge
from matplotlib.collections import LineCollection
from io import BytesIO
from PIL import Image

# ── Usage: python render_ecu_v3.py input.csv output.mp4 ──────────────────────
CSV_PATH   = sys.argv[1] if len(sys.argv) > 1 else "export1.csv"
OUTPUT_MP4 = sys.argv[2] if len(sys.argv) > 2 else "ecu_full.mp4"

CHROMA = "#ff00ff"
AMBER  = "#ffaa00"
RED    = "#ff2020"
WHITE  = "#ffffff"
GREY   = "#888899"
BORDER = "#555566"
CYAN   = "#40d0ff"
ORANGE = "#ff7700"
GREEN  = "#39d353"

W, H = 1280, 500
DPI  = 100
FPS  = 30

# ── Load & interpolate CSV to 30fps ──────────────────────────────────────────
print(f"Loading {CSV_PATH} ...", flush=True)
raw = pd.read_csv(CSV_PATH)
raw = raw.dropna(subset=['TimeStamp']).sort_values('TimeStamp').reset_index(drop=True)
raw['rpm']      = raw['Engine Speed [RPM]'].clip(0, 10000).fillna(0)
raw['throttle'] = raw['Throttle Position [%]'].clip(0, 100).fillna(0)
raw['speed']    = raw['Vehicle Speed (kph)'].clip(0, 280).fillna(0)
raw['gear']     = raw['Gear'].fillna(0).astype(int)
raw['g_lat']    = raw['G-lat'].clip(-2, 2).fillna(0)
raw['g_long']   = raw['G-long'].clip(-2, 2).fillna(0)

t0 = raw['TimeStamp'].min()
t1 = raw['TimeStamp'].max()
tt = np.arange(t0, t1, 1.0 / FPS)
rows = pd.DataFrame({'ts': tt})
for col in ['rpm', 'throttle', 'speed', 'g_lat', 'g_long']:
    rows[col] = np.interp(tt, raw['TimeStamp'].values, raw[col].values)
rows['gear'] = np.round(np.interp(tt, raw['TimeStamp'].values, raw['gear'].values)).astype(int)
rows = rows.reset_index(drop=True)

spd_arr = rows.speed.values
ts_arr  = rows.ts.values
print(f"Loaded {len(rows)} frames  ({t1-t0:.1f}s @ {FPS}fps)", flush=True)

# ── Lap detection ─────────────────────────────────────────────────────────────
def find_lap_events(spd, ts, min_spd=200, min_gap=60.0, cluster_gap=8.0):
    peaks = []
    for i in range(2, len(spd)-2):
        if spd[i] > min_spd and spd[i] == max(spd[max(0,i-15):i+15]):
            peaks.append((ts[i], spd[i]))
    clusters = []
    if peaks:
        cur = [peaks[0]]
        for p in peaks[1:]:
            if p[0] - cur[-1][0] < cluster_gap:
                cur.append(p)
            else:
                clusters.append(max(cur, key=lambda x: x[1]))
                cur = [p]
        clusters.append(max(cur, key=lambda x: x[1]))
    laps = []
    i = 0
    while i < len(clusters):
        st = clusters[i][0]
        for j in range(i+1, len(clusters)):
            if clusters[j][0] - st >= min_gap:
                laps.append((st, clusters[j][0], clusters[j][0]-st))
                i = j
                break
        else:
            break
    return laps

laps = find_lap_events(spd_arr, ts_arr)
print(f"Detected {len(laps)} laps", flush=True)
for i,(st,et,lt) in enumerate(laps):
    print(f"  Lap {i+1}: {st:.1f}s -> {et:.1f}s  ({lt:.2f}s)", flush=True)

def get_timer_display(t_now, laps, show_duration=5.0):
    for (st, et, lt) in laps:
        if et <= t_now <= et + show_duration:
            return f"{int(lt)//60}:{lt%60:05.2f}", "#ffd60a", "LAP TIME"
    for (st, et, lt) in laps:
        if st <= t_now < et:
            elapsed = t_now - st
            return f"{int(elapsed)//60}:{elapsed%60:05.2f}", GREEN, "TIMER"
    return "--:--.--", GREY, "TIMER"

N_FRAMES = len(rows)

# ── Frame builder ─────────────────────────────────────────────────────────────
def build_frame(row, trace):
    rpm      = float(row.rpm)
    throttle = float(row.throttle)
    speed    = float(row.speed)
    gear     = int(row.gear)
    g_lat    = float(row.g_lat)
    g_long   = float(row.g_long)
    ts       = float(row.ts)

    brake_pct      = float(np.clip((-g_long / 1.2) * 100, 0, 100))
    g_long_display = -g_long

    timer_str, timer_col, timer_label = get_timer_display(ts, laps)

    fig = plt.figure(figsize=(W/DPI, H/DPI), dpi=DPI)
    fig.patch.set_facecolor(CHROMA)

    def ax_at(l, b, w, h, bg=CHROMA):
        a = fig.add_axes([l/W, b/H, w/W, h/H])
        a.set_facecolor(bg)
        return a

    BAR_H = 360
    BAR_Y = (H - BAR_H) // 2

    # ── RPM GAUGE ────────────────────────────────────────────────────────────
    GS = 460; GX = 10; GY = (H - GS) // 2
    ax_g = ax_at(GX, GY, GS, GS)
    ax_g.set_xlim(-1.15,1.15); ax_g.set_ylim(-1.15,1.15)
    ax_g.set_aspect('equal'); ax_g.axis('off')

    START_DEG = 225; SWEEP = 270; RPM_MAX = 9000
    frac_rpm = np.clip(rpm/RPM_MAX, 0, 1)

    ax_g.add_patch(plt.Circle((0,0), 1.10, color="#111111", zorder=0))
    ax_g.add_patch(plt.Circle((0,0), 1.08, color="#303030", fill=False, lw=3, zorder=1))
    ax_g.add_patch(plt.Circle((0,0), 1.04, color="#0d0d0d", zorder=0))

    for i in range(60):
        fs = i/60; a0 = START_DEG - fs*SWEEP; sw = (SWEEP/60) - 1.5
        col = (RED if frac_rpm >= fs else "#3a0000") if fs > 0.85 \
              else (AMBER if frac_rpm >= fs else "#2a2000")
        ax_g.add_patch(Wedge((0,0), 1.0, a0-sw, a0, width=0.14,
                              facecolor=col, edgecolor='none', zorder=2))

    for i in range(9):
        a = np.radians(START_DEG - i/8*SWEEP)
        ax_g.plot([0.82*np.cos(a),0.78*np.cos(a)],
                  [0.82*np.sin(a),0.78*np.sin(a)], color=WHITE, lw=2.0, zorder=5)
        ax_g.text(0.68*np.cos(a),0.68*np.sin(a), str(i+1),
                  ha='center', va='center', fontsize=13,
                  color=WHITE, fontweight='bold', zorder=5)
    for i in range(45):
        a = np.radians(START_DEG - i/44*SWEEP)
        ax_g.plot([0.82*np.cos(a),0.79*np.cos(a)],
                  [0.82*np.sin(a),0.79*np.sin(a)], color=GREY, lw=0.8, zorder=4)

    ax_g.add_patch(plt.Circle((0,0), 0.60, color="#0d0d0d", zorder=3))
    ax_g.add_patch(plt.Circle((0,0), 0.61, color="#222222", fill=False, lw=1.5, zorder=4))
    ax_g.text(0,-0.22, f"{int(rpm):,}", ha='center', va='center',
              fontsize=20, color=WHITE, fontweight='bold', zorder=10, fontfamily='monospace')
    ax_g.text(0,-0.40, "rpm ×1000", ha='center', va='center',
              fontsize=7.5, color=GREY, zorder=10)

    na = np.radians(START_DEG - frac_rpm*SWEEP)
    ax_g.plot([-0.15*np.cos(na),0.78*np.cos(na)],
              [-0.15*np.sin(na),0.78*np.sin(na)],
              color=RED, lw=2.5, zorder=11, solid_capstyle='round')
    ax_g.add_patch(plt.Circle((0,0), 0.055, color="#cccccc", zorder=12))
    ax_g.add_patch(plt.Circle((0,0), 0.035, color="#333333", zorder=13))

    # ── TIMER / SPEED / GEAR ─────────────────────────────────────────────────
    SG_X  = GX + GS + 10
    BOX_W = 170; GAP = 8
    BOX_H = (BAR_H - GAP*2) // 3

    def draw_label(ax, big_text, big_col, small_text, big_size=46):
        ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
        ax.text(0.5, 0.62, big_text, ha='center', va='center',
                fontsize=big_size, color=big_col, fontweight='bold', zorder=5)
        ax.text(0.5, 0.18, small_text, ha='center', va='center',
                fontsize=11, color=WHITE, fontweight='bold', zorder=5)

    ax_tim = ax_at(SG_X, BAR_Y + BOX_H*2 + GAP*2, BOX_W, BOX_H)
    draw_label(ax_tim, timer_str, timer_col, timer_label, big_size=28)
    ax_spd = ax_at(SG_X, BAR_Y + BOX_H + GAP, BOX_W, BOX_H)
    draw_label(ax_spd, f"{int(speed)}", WHITE, "km/h", big_size=50)
    ax_gr = ax_at(SG_X, BAR_Y, BOX_W, BOX_H)
    draw_label(ax_gr, str(gear) if gear > 0 else "N", WHITE, "GEAR", big_size=56)

    # ── THROTTLE + BRAKE BARS ────────────────────────────────────────────────
    BAR_X  = SG_X + BOX_W + 20
    BAR_W  = 55; BAR_GAP = 18

    for ix, (pct, col) in enumerate([(throttle, GREEN), (brake_pct, RED)]):
        bx = BAR_X + ix*(BAR_W + BAR_GAP)
        ax_b = ax_at(bx, BAR_Y, BAR_W, BAR_H)
        ax_b.set_xlim(0,1); ax_b.set_ylim(0,100); ax_b.axis('off')
        ax_b.add_patch(FancyBboxPatch((0.1,0), 0.80, 99, boxstyle="round,pad=1",
            facecolor="#111111", edgecolor=WHITE, lw=1.0))
        if pct > 1:
            ax_b.add_patch(FancyBboxPatch((0.1,0), 0.80, pct, boxstyle="round,pad=1",
                facecolor=col, edgecolor='none'))

    # ── COMBINED GG TRACE ────────────────────────────────────────────────────
    TR_X = BAR_X + BAR_W*2 + BAR_GAP + 25
    TR_W = W - TR_X - 15

    ax_tr = ax_at(TR_X, BAR_Y, TR_W, BAR_H)
    ax_tr.set_xlim(-2.2, 2.2)
    ax_tr.set_ylim(-2.2, 2.2)
    ax_tr.set_aspect('equal')
    ax_tr.set_facecolor("#111111")

    for v in [-2, -1, 1, 2]:
        ax_tr.axhline(v, color=BORDER, lw=0.8, zorder=1)
        ax_tr.axvline(v, color=BORDER, lw=0.8, zorder=1)
    ax_tr.axhline(0, color=WHITE, lw=1.2, zorder=2)
    ax_tr.axvline(0, color=WHITE, lw=1.2, zorder=2)
    ax_tr.set_xticks([]); ax_tr.set_yticks([])
    for sp in ax_tr.spines.values(): sp.set_edgecolor(BORDER)

    g_lat_tr  = trace.g_lat.values
    g_long_tr = -trace.g_long.values

    n = len(g_lat_tr)
    if n > 1:
        alphas = np.linspace(0.05, 1.0, max(n-1, 1))
        pts  = np.array([g_lat_tr, g_long_tr]).T.reshape(-1,1,2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        ax_tr.add_collection(LineCollection(segs,
            colors=[(0.25, 0.82, 1.0, a) for a in alphas],
            linewidth=2.2, zorder=4))

    ax_tr.scatter([g_lat], [g_long_display],
                  s=90, c=CYAN, zorder=6, edgecolors=WHITE, lw=1.5)

    plt.subplots_adjust(0,0,1,1,0,0)
    return fig

# ── Pipe to ffmpeg ────────────────────────────────────────────────────────────
out_dir = os.path.dirname(os.path.abspath(OUTPUT_MP4))
os.makedirs(out_dir, exist_ok=True)

cmd = ["ffmpeg","-y",
       "-f","rawvideo","-vcodec","rawvideo",
       "-s",f"{W}x{H}","-pix_fmt","rgb24",
       "-r",str(FPS),"-i","pipe:0",
       "-c:v","libx264","-preset","fast",
       "-crf","18","-pix_fmt","yuv420p",
       OUTPUT_MP4]
ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

print(f"Rendering {N_FRAMES} frames to {OUTPUT_MP4} ...", flush=True)
for i, (_, row) in enumerate(rows.iterrows()):
    if i % 150 == 0:
        pct = 100*i/N_FRAMES
        print(f"  {pct:.1f}%  frame {i}/{N_FRAMES}  t={row.ts:.1f}s", flush=True)
    t_now = float(row.ts)
    trace = rows[(rows.ts >= t_now-5) & (rows.ts <= t_now)]
    fig   = build_frame(row, trace)
    buf   = BytesIO()
    fig.savefig(buf, format='png', dpi=DPI,
                bbox_inches='tight', pad_inches=0, facecolor=CHROMA)
    plt.close(fig)
    buf.seek(0)
    img = Image.open(buf).convert('RGB').resize((W,H), Image.LANCZOS)
    ff.stdin.write(np.array(img, dtype=np.uint8).tobytes())

ff.stdin.close()
ff.wait()
print(f"\nDone! Output saved to: {OUTPUT_MP4}", flush=True)
