"""Dash 8 standalone renderer — builds background + composites live data."""
from PIL import Image, ImageDraw, ImageFont
import math

W,H = 1920,850
GCX,GCY,GR = 500,504,480
R_OUTER=GR; R_GREY1_IN=int(GR*.930); R_BLACK_IN=int(GR*.820)
R_GREY2_IN=int(GR*.795); R_BLUE_IN=int(GR*.770); R_FACE=R_BLUE_IN
WHITE=(255,255,255); CYAN=(80,180,230); BLACK=(0,0,0); NEEDLE=(220,20,20)
RED=(220,20,20)
GREY=(150,152,155); BLUE=(0,120,210); DARKGREY=(60,62,65)
START_ANG=206.7; SWEEP=233.3; N_MARKS=9

def _find_bsb():
    import os
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
    for p in [os.path.join(here, "BigShoulders-Bold.ttf"),
              "/home/claude/BigShoulders-Bold.ttf",
              os.path.join(here, "fonts", "BigShoulders-Bold.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            return p
    return None
BSB = _find_bsb()

def clamp(v): return max(0,min(255,int(v)))
def lerp(a,b,t): return tuple(clamp(a[i]*(1-t)+b[i]*t) for i in range(3))

def wide_text(img,pos,txt,size,fill,ow=4,outline=(0,0,0),stretch=1.5,anchor="mm"):
    fnt=ImageFont.truetype(BSB,size)
    tmp=Image.new("RGBA",(10,10)); td=ImageDraw.Draw(tmp)
    bb=td.textbbox((0,0),txt,font=fnt); tw=bb[2]-bb[0]; th=bb[3]-bb[1]; pad=ow+4
    layer=Image.new("RGBA",(tw+2*pad,th+2*pad),(0,0,0,0)); ld=ImageDraw.Draw(layer)
    for dx in range(-ow,ow+1):
        for dy in range(-ow,ow+1):
            if dx*dx+dy*dy<=ow*ow: ld.text((pad-bb[0]+dx,pad-bb[1]+dy),txt,font=fnt,fill=outline)
    ld.text((pad-bb[0],pad-bb[1]),txt,font=fnt,fill=fill)
    layer=layer.resize((int(layer.width*stretch),layer.height),Image.LANCZOS)
    pad_s=int(pad*stretch)   # padding width after horizontal stretch
    x,y=pos
    if anchor=="mm": ox,oy=x-layer.width/2,y-layer.height/2
    elif anchor=="lm": ox,oy=x-pad_s,y-layer.height/2      # ink left edge -> x
    elif anchor=="rm": ox,oy=x-layer.width+pad_s,y-layer.height/2  # ink right edge -> x
    else: ox,oy=x-layer.width/2,y-layer.height/2
    img.paste(layer,(int(ox),int(oy)),layer)
    return layer.width

def wide_text_width(txt,size,ow=4,stretch=1.5):
    """Measure the rendered width wide_text would produce."""
    fnt=ImageFont.truetype(BSB,size)
    tmp=Image.new("RGBA",(10,10)); td=ImageDraw.Draw(tmp)
    bb=td.textbbox((0,0),txt,font=fnt); tw=bb[2]-bb[0]; pad=ow+4
    return int((tw+2*pad)*stretch)

def norm_text(img_d,pos,txt,size,fill,anchor="mm"):
    f=ImageFont.truetype(BSB,size)
    img_d.text(pos,txt,font=f,fill=fill,anchor=anchor)


def speed_colour_fn(spd, min_spd=50.0, max_spd=200.0):
    """Map speed to colour (blue->cyan->green->yellow->red), matching other LapStudio styles."""
    t=max(0.0,min(1.0,(spd-min_spd)/(max_spd-min_spd)))
    if t<0.5:
        tt=t/0.5; return (0,int(180*tt),int(255-55*tt))
    else:
        tt=(t-0.5)/0.5
        if tt<0.5: ttt=tt/0.5; return (int(255*ttt),255,int(255*(1-ttt)))
        else: ttt=(tt-0.5)/0.5; return (255,int(255*(1-ttt)),0)

def build_background(rpm_max=9000):
    img=Image.new('RGB',(W,H),(0,0,0)); d=ImageDraw.Draw(img)
    for y in range(H):
        t=0.5+0.5*math.sin(y/H*math.pi*4-math.pi/2)
        d.line([0,y,W,y],fill=lerp((0,62,108),(8,98,165),t))
    for y in range(max(0,GCY-GR-2),min(H,GCY+GR+2)):
        for x in range(max(0,GCX-GR-2),min(W,GCX+GR+2)):
            dx=x-GCX; dy=y-GCY; r=math.sqrt(dx*dx+dy*dy)
            if r>R_OUTER: continue
            ang=math.atan2(-dy,dx)
            if r>R_GREY1_IN:
                sh=clamp(70+55*math.sin(ang+0.6)); d.point((x,y),fill=(sh,clamp(sh-1),clamp(sh-5)))
            elif r>R_BLACK_IN: d.point((x,y),fill=(10,11,13))
            elif r>R_GREY2_IN:
                sh=clamp(105+25*math.sin(ang+0.6)); d.point((x,y),fill=(sh,clamp(sh),clamp(sh-2)))
            elif r>R_BLUE_IN:
                below=max(0,-math.sin(ang)); d.point((x,y),fill=lerp((4,55,100),BLUE,0.4+0.6*below))
            else:
                fr=r/R_FACE; fy=dy/R_FACE
                bi=max(0,min(1,0.5+0.5*fy)); inten=max(0,min(1,bi*0.85+fr*0.12))
                d.point((x,y),fill=(clamp(2*inten),clamp(10+110*inten),clamp(18+190*inten)))
    # Major marks at each 1000 rpm; minor ticks subdivide into 5.
    _n_major = max(1, int(round(rpm_max/1000.0)))
    _n_minor = _n_major*5
    TICK_OUT=int(GR*.985); TICK_MAJ=int(GR*.935); TICK_MIN=int(GR*.955)
    for i in range(_n_minor+1):
        frac=i/_n_minor; a=math.radians(START_ANG-frac*SWEEP); ca,sa=math.cos(a),math.sin(a)
        is_maj=(i%5==0); t_in=TICK_MAJ if is_maj else TICK_MIN
        lw=max(3,int(GR*.010)) if is_maj else max(1,int(GR*.005))
        d.line([GCX+TICK_OUT*ca,GCY-TICK_OUT*sa,GCX+t_in*ca,GCY-t_in*sa],fill=WHITE,width=lw)
    NUM_R=int(GR*.870); num_fsz=max(26,int(GR*.085))
    for i in range(_n_major+1):
        frac=i/_n_major; a=math.radians(START_ANG-frac*SWEEP)
        lx=GCX+NUM_R*math.cos(a); ly=GCY-NUM_R*math.sin(a)
        if 0<lx<W and 0<ly<H: wide_text(img,(lx,ly),str(i),num_fsz,CYAN,ow=2,stretch=1.5)
    try: img.save('/mnt/user-data/outputs/dash8_synth_bg.png')
    except Exception: pass
    return img


def build_background_chroma(chroma=(255,0,255), rpm_max=9000):
    """Same gauge as build_background but on a solid chroma-key field instead of blue gradient."""
    img=Image.new('RGB',(W,H),chroma); d=ImageDraw.Draw(img)
    # NO blue gradient bands — solid chroma fills everything outside the gauge
    for y in range(max(0,GCY-GR-2),min(H,GCY+GR+2)):
        for x in range(max(0,GCX-GR-2),min(W,GCX+GR+2)):
            dx=x-GCX; dy=y-GCY; r=math.sqrt(dx*dx+dy*dy)
            if r>R_OUTER: continue
            ang=math.atan2(-dy,dx)
            if r>R_GREY1_IN:
                sh=clamp(70+55*math.sin(ang+0.6)); d.point((x,y),fill=(sh,clamp(sh-1),clamp(sh-5)))
            elif r>R_BLACK_IN: d.point((x,y),fill=(10,11,13))
            elif r>R_GREY2_IN:
                sh=clamp(105+25*math.sin(ang+0.6)); d.point((x,y),fill=(sh,clamp(sh),clamp(sh-2)))
            elif r>R_BLUE_IN:
                below=max(0,-math.sin(ang)); d.point((x,y),fill=lerp((4,55,100),BLUE,0.4+0.6*below))
            else:
                fr=r/R_FACE; fy=dy/R_FACE
                bi=max(0,min(1,0.5+0.5*fy)); inten=max(0,min(1,bi*0.85+fr*0.12))
                d.point((x,y),fill=(clamp(2*inten),clamp(10+110*inten),clamp(18+190*inten)))
    _n_major = max(1, int(round(rpm_max/1000.0)))
    _n_minor = _n_major*5
    TICK_OUT=int(GR*.985); TICK_MAJ=int(GR*.935); TICK_MIN=int(GR*.955)
    for i in range(_n_minor+1):
        frac=i/_n_minor; a=math.radians(START_ANG-frac*SWEEP); ca,sa=math.cos(a),math.sin(a)
        is_maj=(i%5==0); t_in=TICK_MAJ if is_maj else TICK_MIN
        lw=max(3,int(GR*.010)) if is_maj else max(1,int(GR*.005))
        d.line([GCX+TICK_OUT*ca,GCY-TICK_OUT*sa,GCX+t_in*ca,GCY-t_in*sa],fill=WHITE,width=lw)
    NUM_R=int(GR*.870); num_fsz=max(26,int(GR*.085))
    for i in range(_n_major+1):
        frac=i/_n_major; a=math.radians(START_ANG-frac*SWEEP)
        lx=GCX+NUM_R*math.cos(a); ly=GCY-NUM_R*math.sin(a)
        if 0<lx<W and 0<ly<H: wide_text(img,(lx,ly),str(i),num_fsz,CYAN,ow=2,stretch=1.5)
    try: img.save('/mnt/user-data/outputs/dash8_chroma_bg.png')
    except Exception: pass
    return img


if __name__=="__main__":
    build_background()
    print("Background built")

def render_frame(rpm=5500,speed=195,gear=4,lap=12,timer_str="2:03.24",
                 rpm_max=9000,peak_rpm=8100,throttle=75,brake=10,
                 g_lat=-0.4,g_long=0.3,trace_glat=None,trace_glong=None,
                 trace_speed=None,speed_colour=True,bg=None,
                 chanA=None,chanA_label="",chanB=None,chanB_label="",
                 chanA_unit="",chanB_unit="",
                 bars_inside=False):
    if bg is None: bg=Image.open('/mnt/user-data/outputs/dash8_synth_bg.png').convert('RGB')
    img=bg.copy(); d=ImageDraw.Draw(img)

    # ── Shield (convex top, sloped sides) ──────────────────────────────────────
    sh_top_y=GCY-R_BLACK_IN+int(GR*.005); sh_bot_y=GCY-int(GR*.14)
    sh_hw_top=int(GR*.27); sh_hw_bot=int(GR*.13)
    SG={0.0:(8,80,140),0.35:(15,110,185),0.6:(28,140,225),0.8:(15,95,160),1.0:(6,45,82)}
    sk=sorted(SG.keys())
    for y in range(sh_top_y,sh_bot_y+1):
        vt=(y-sh_top_y)/(sh_bot_y-sh_top_y); cur_hw=int(sh_hw_top-(sh_hw_top-sh_hw_bot)*vt)
        row_px=[]
        for x in range(GCX-cur_hw,GCX+cur_hw+1):
            dx2=x-GCX
            if abs(dx2)<R_BLACK_IN:
                y_arc=GCY-int(math.sqrt(max(0,R_BLACK_IN**2-dx2**2)))
                if y>=y_arc: row_px.append(x)
        if row_px:
            col=SG[sk[0]]
            for i in range(len(sk)-1):
                if sk[i]<=vt<=sk[i+1]:
                    tt=(vt-sk[i])/(sk[i+1]-sk[i]); col=lerp(SG[sk[i]],SG[sk[i+1]],tt); break
            d.line([min(row_px),y,max(row_px),y],fill=col)

    # Gear
    g_str=str(gear) if gear>0 else "N"
    g_cy=(sh_top_y+sh_bot_y)//2+int(GR*.01)
    g_fsz=max(40,int((sh_bot_y-sh_top_y)*0.62))
    wide_text(img,(GCX,g_cy),g_str,g_fsz,WHITE,ow=5,stretch=1.5)

    # ── RPM stack — computed positions so nothing overlaps ──────────────────────
    def text_h(txt,size):
        f=ImageFont.truetype(BSB,size); bb=d.textbbox((0,0),txt,font=f); return bb[3]-bb[1]

    # RPM number (slightly smaller to leave room for bigger peak)
    rpm_fsz=max(54,int(GR*.23))
    while rpm_fsz>20:
        f=ImageFont.truetype(BSB,rpm_fsz)
        bb=d.textbbox((0,0),str(int(rpm)),font=f)
        if (bb[2]-bb[0])*1.5 < R_FACE*1.75: break
        rpm_fsz-=2
    # Peak rpm — DOUBLED (was .40, now .80 of rpm size)
    peak_fsz=max(34,int(rpm_fsz*.80))
    peak_str=f"({int(peak_rpm)})"

    rpm_h=text_h(str(int(rpm)),rpm_fsz)
    peak_h=text_h(peak_str,peak_fsz)
    gap=int(GR*.018)

    # Stack centred around GCY-0.05*GR: rpm on top, peak below, then divider
    stack_top=GCY-int(GR*.20)
    rpm_cy=stack_top+rpm_h//2
    peak_cy=rpm_cy+rpm_h//2+gap+peak_h//2
    div_y=peak_cy+peak_h//2+int(GR*.04)
    dl=int(GR*.30)

    wide_text(img,(GCX,rpm_cy),str(int(rpm)),rpm_fsz,WHITE,ow=6,stretch=1.5)
    wide_text(img,(GCX,peak_cy),peak_str,peak_fsz,CYAN,ow=3,stretch=1.4)
    d.line([GCX-dl,div_y,GCX+dl,div_y],fill=(215,220,225),width=max(6,int(GR*.016)))

    # ── Speed — moved DOWN, more room ───────────────────────────────────────────
    spd_fsz=max(54,int(GR*.24))
    while spd_fsz>20:
        f=ImageFont.truetype(BSB,spd_fsz)
        bb=d.textbbox((0,0),str(int(speed)),font=f)
        if (bb[2]-bb[0])*1.5 < R_FACE*1.5: break
        spd_fsz-=2
    spd_h=text_h(str(int(speed)),spd_fsz)
    spd_cy=div_y+int(GR*.05)+spd_h//2
    wide_text(img,(GCX,spd_cy),str(int(speed)),spd_fsz,WHITE,ow=6,stretch=1.5)
    wide_text(img,(GCX,spd_cy+spd_h//2+int(GR*.04)),"km/h",max(20,int(spd_fsz*.30)),(210,230,248),ow=2,stretch=1.4)

    # ── Needle in black ring ─────────────────────────────────────────────────────
    # Needle scale must match the gauge's rounded major-mark span (n_major*1000)
    _gauge_full = max(1000, int(round(rpm_max/1000.0))*1000)
    rpm_frac=min(1.0,rpm/_gauge_full)
    n_ang=math.radians(START_ANG-rpm_frac*SWEEP)
    NR_TIP=int(GR*.910); NR_BASE=int(GR*.830); NW=int(GR*.052)
    perp=n_ang+math.pi/2
    nt=(GCX+NR_TIP*math.cos(n_ang),GCY-NR_TIP*math.sin(n_ang))
    nb1=(GCX+NR_BASE*math.cos(n_ang)+NW*math.cos(perp),GCY-NR_BASE*math.sin(n_ang)-NW*math.sin(perp))
    nb2=(GCX+NR_BASE*math.cos(n_ang)-NW*math.cos(perp),GCY-NR_BASE*math.sin(n_ang)+NW*math.sin(perp))
    if nt[1]<H:
        d.polygon([nt,nb1,nb2],fill=NEEDLE); d.polygon([nt,nb1,nb2],outline=(150,8,8),width=2)

    # ── Data column — Lap & Lap Time (+ Channel A/B in the inside-bars variant) ──
    info_x=GCX+GR+int(GR*.03)   # COMMON LEFT MARGIN for entire data column
    import math as _mc
    def _chan_ok2(v): return v is not None and not (isinstance(v,float) and _mc.isnan(v))
    if bars_inside:
        # 2×2 grid of data cells, each on a slanted parallelogram backing panel,
        # bottom-justified in the data area to the right of the gauge.
        #   Top row:    Channel A  | Channel B
        #   Bottom row: LAP        | LAP TIME
        _row2 = [(str(lap), "LAP"), (timer_str, "LAP TIME")]   # bottom row
        _cells = []                                            # top row (channels)
        if _chan_ok2(chanA):
            _cells.append((f"{int(round(chanA))}{chanA_unit or ''}", (chanA_label or "A").upper()))
        if _chan_ok2(chanB):
            _cells.append((f"{int(round(chanB))}{chanB_unit or ''}", (chanB_label or "B").upper()))

        # Grid geometry — fill the data area (info_x .. W) with a small margin.
        _grid_x0 = info_x
        _grid_x1 = W - int(W*0.015)
        _grid_w  = _grid_x1 - _grid_x0
        _col_gap = int(W*0.012)
        _row_gap = int(H*0.030)
        _slope   = int(H*0.055)             # parallelogram lean
        _cell_w  = (_grid_w - _col_gap - _slope) // 2
        _cell_h  = int(H*0.150)
        # Bottom-justify the 2×2 block
        _grid_bot = H - int(H*0.06)
        _row1_y0  = _grid_bot - 2*_cell_h - _row_gap
        _row2_y0  = _grid_bot - _cell_h

        _val_fs = int(H*.078)
        _lbl_fs = int(H*.038)
        _PANEL_FILL = (18, 40, 78)          # deep blue, matches Dash 8 theme
        _PANEL_EDGE = (70, 130, 210)

        def _para_panel(x0, y0, w, h, slope, value, label):
            # slanted parallelogram (top edge shifted right by `slope`)
            poly = [(x0+slope, y0), (x0+slope+w, y0), (x0+w, y0+h), (x0, y0+h)]
            d.polygon(poly, fill=_PANEL_FILL)
            d.polygon(poly, outline=_PANEL_EDGE, width=max(2, int(GR*.006)))
            # Label small near the TOP, value large below it — clearly separated.
            _tx = x0 + slope//2 + int(w*0.10)
            wide_text(img,(_tx, y0+int(h*0.26)),label,_lbl_fs,CYAN,ow=2,stretch=1.5,anchor="lm")
            wide_text(img,(_tx, y0+int(h*0.66)),value,_val_fs,WHITE,ow=4,stretch=1.4,anchor="lm")

        # Column x positions. The bottom row is shifted left so the slanted
        # left edges of the left-column panels form one continuous diagonal
        # (each lower row steps left by the parallelogram lean + row gap).
        _row_shift = _slope + int(_slope * (_row_gap / _cell_h))
        _cxL = _grid_x0
        _cxR = _grid_x0 + _cell_w + _col_gap
        # Top row: Channel A | Channel B (only those selected)
        if len(_cells) >= 1:
            _para_panel(_cxL, _row1_y0, _cell_w, _cell_h, _slope, _cells[0][0], _cells[0][1])
        if len(_cells) >= 2:
            _para_panel(_cxR, _row1_y0, _cell_w, _cell_h, _slope, _cells[1][0], _cells[1][1])
        # Bottom row: LAP | LAP TIME — shifted left for diagonal alignment
        _cxL2 = _cxL - _row_shift
        _cxR2 = _cxR - _row_shift
        if len(_row2) >= 1:
            _para_panel(_cxL2, _row2_y0, _cell_w, _cell_h, _slope, _row2[0][0], _row2[0][1])
        if len(_row2) >= 2:
            _para_panel(_cxR2, _row2_y0, _cell_w, _cell_h, _slope, _row2[1][0], _row2[1][1])
    else:
        # Standard layout: LAP and LAP TIME stacked. Channels (if any) are drawn
        # BELOW the throttle/brake bars further down (see after the bars block),
        # so they don't collide with the bars. G-trace removed (standalone now).
        lap_y=int(H*0.13)
        wide_text(img,(info_x,lap_y),str(lap),int(H*.14),WHITE,ow=4,stretch=1.5,anchor="lm")
        wide_text(img,(info_x,lap_y+int(H*.11)),"LAP",int(H*.045),CYAN,ow=2,stretch=1.5,anchor="lm")
        lt_y=int(H*0.36)
        wide_text(img,(info_x,lt_y),timer_str,int(H*.085),WHITE,ow=4,stretch=1.35,anchor="lm")
        wide_text(img,(info_x,lt_y+int(H*.085)),"LAP TIME",int(H*.045),CYAN,ow=2,stretch=1.5,anchor="lm")

    # ── Throttle & Brake bars ──────────────────────────────────────────────────
    if bars_inside:
        # Vertical bars INSIDE the gauge, flanking the central data.
        # Throttle on the left, brake on the right; fill grows upward.
        import math as _mb
        _bx_off = int(R_FACE*0.66)
        _bw_v   = int(GR*.060)
        _half_chord = int(_mb.sqrt(max(0, R_FACE**2 - (_bx_off+_bw_v//2)**2)))
        _bar_h_v = int(_half_chord*1.55)
        _bar_top = GCY - _bar_h_v//2
        _bar_bot = GCY + _bar_h_v//2
        def vbar(cx_off, pct, fillcol, label):
            bx = GCX + cx_off - _bw_v//2
            # track
            d.rectangle([bx,_bar_top,bx+_bw_v,_bar_bot],fill=(20,22,26),outline=GREY,width=2)
            fh = int(_bar_h_v*max(0,min(100,pct))/100)
            d.rectangle([bx,_bar_bot-fh,bx+_bw_v,_bar_bot],fill=fillcol)
            # label above the bar, percentage below
            wide_text(img,(GCX+cx_off,_bar_top-int(H*.030)),label,int(H*.032),WHITE,ow=3,stretch=1.4,anchor="mm")
            wide_text(img,(GCX+cx_off,_bar_bot+int(H*.030)),f"{int(pct)}%",int(H*.036),WHITE,ow=3,stretch=1.4,anchor="mm")
        vbar(-_bx_off, throttle, (230,230,235), "THR")   # left = throttle (white)
        vbar(+_bx_off, brake,    RED,           "BRK")   # right = brake (red)
    else:
        # ── Throttle & Brake horizontal bars (original layout) ────────────────
        pct_w=int(GR*.14)
        bar_x=info_x; bar_w=int(GR*.46); bar_h=int(H*.058)
        bar_y0=int(H*0.56)
        def hbar(y,label,pct,fillcol):
            d.rectangle([bar_x,y,bar_x+bar_w,y+bar_h],fill=(20,22,26),outline=GREY,width=2)
            fw=int(bar_w*max(0,min(100,pct))/100)
            d.rectangle([bar_x,y,bar_x+fw,y+bar_h],fill=fillcol)
            wide_text(img,(bar_x,y-int(H*.030)),label,int(H*.042),WHITE,ow=3,stretch=1.5,anchor="lm")
            wide_text(img,(bar_x+bar_w+int(GR*.03),y+bar_h//2),f"{int(pct)}%",int(H*.046),WHITE,ow=3,stretch=1.5,anchor="lm")
        hbar(bar_y0,"THROTTLE",throttle,(230,230,235))
        hbar(bar_y0+int(H*.14),"BRAKE",brake,RED)

        # Channel A / B below the bars (standard layout) — value + small label.
        _ch_items=[]
        if _chan_ok2(chanA):
            _ch_items.append((f"{int(round(chanA))}{chanA_unit or ''}", (chanA_label or "A").upper()))
        if _chan_ok2(chanB):
            _ch_items.append((f"{int(round(chanB))}{chanB_unit or ''}", (chanB_label or "B").upper()))
        if _ch_items:
            _ch_y = bar_y0 + int(H*.14) + bar_h + int(H*.075)
            _cv_fs = int(H*.075); _cl_fs = int(H*.038)
            # two channels side by side, each in its own half of the data column
            _col_pitch = int(GR*.52)
            for _ci,(_vstr,_lstr) in enumerate(_ch_items):
                _cx = info_x + _ci*_col_pitch
                wide_text(img,(_cx,_ch_y),_vstr,_cv_fs,WHITE,ow=4,stretch=1.35,anchor="lm")
                wide_text(img,(_cx,_ch_y+int(_cv_fs*0.78)),_lstr,_cl_fs,CYAN,ow=2,stretch=1.5,anchor="lm")

    # G-trace plot, Lat/Lon readouts, and below-plot channels REMOVED from this
    # dash — the G-force trace is available as a separate overlay video, and the
    # channels now live in the data column above.

    return img

