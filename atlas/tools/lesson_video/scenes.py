"""One 1920x1080 page per beat, on the ATLAS Academy brand (black + royal blue).

Palette (Miro, 2026-11-14): ground #0A0D14, royal blue #5B8CFF, surfaces
#0F1420 / #131A28 / #1B2334, light blue #8FB0FF, text #EEF2FA / #C9D2E3 /
#8E99AE, a faint grid, the ATLAS tile. Mono type is for data only (numbers,
code). It replaces the YouTube-navy look.

Every page is driven by time: `window.setT(t)` (t in seconds since the beat
began) draws that instant, so the renderer can shoot the enter animation
densely and the rest of the beat sparsely. Things that move: the grid and
glow drift, the lesson progress bar fills, words reveal one by one, rows and
tables stagger in, the active row/cell sweeps blue, flows draw their links
and carry a travelling dot, numbers count up, rings draw, a scene change
wipes. `window.overflow()` reports what does not fit at the final state.
The caption is not in these pages: libass burns it into the band the pages
keep empty (y >= CAP_TOP).
"""
from __future__ import annotations

import base64
import html
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from atlas.modules.academy import video_tokens as vt


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"lv_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


try:
    from . import beats as B  # type: ignore[no-redef]
except ImportError:
    B = sys.modules.get("lv_beats") or _sibling("beats")

W, H = 1920, 1080
SAFE = vt.SAFE_16_9
CAP_TOP = 824                      # nothing but the caption below this line
MAIN_TOP = 204
MAIN_BOTTOM = 810
INSET = SAFE + 24                  # room for the push-in (ZOOM) to stay in the safe area
MAIN_L, MAIN_R = INSET, W - INSET
DENSE_S = 0.8                      # seconds shot at 30 fps at the start of a beat
AMBIENT_FPS = 6                    # after that
WIPE_S = 0.55

PAL = {
    "BG": "#0A0D14", "S1": "#0F1420", "S2": "#131A28", "S3": "#1B2334",
    "BLUE": "#5B8CFF", "BLUE_L": "#8FB0FF", "T1": "#EEF2FA", "T2": "#C9D2E3", "T3": "#8E99AE",
}
LOGO_URI = "data:image/svg+xml;base64," + base64.b64encode(
    (ROOT / "atlas/modules/academy/assets/icon.svg").read_bytes()).decode()

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:@W@px;height:@H@px;overflow:hidden;background:@BG@;color:@T1@;
font-family:@SANS@;-webkit-font-smoothing:antialiased}
.root{position:absolute;inset:0;overflow:hidden}
.grid{position:absolute;inset:-100px;
background-image:linear-gradient(rgba(91,140,255,.07) 2px,transparent 2px),
linear-gradient(90deg,rgba(91,140,255,.07) 2px,transparent 2px);background-size:96px 96px}
.glow{position:absolute;left:1100px;top:-200px;width:1100px;height:900px;
background:radial-gradient(closest-side,rgba(91,140,255,.16),transparent)}
.glow.b{left:-400px;top:500px;width:900px;height:700px;
background:radial-gradient(closest-side,rgba(143,176,255,.08),transparent)}
.top{position:absolute;left:@MAIN_L@px;right:@INSET@px;top:@INSET@px;height:48px;
display:flex;justify-content:space-between;align-items:center;font-size:@FLOOR@px}
.brand{display:flex;align-items:center;gap:14px;font-weight:600;letter-spacing:.06em;color:@T1@}
.brand img{width:44px;height:44px;display:block}
.brand b{color:@BLUE_L@;font-weight:500;letter-spacing:.02em}
.chip{color:@BLUE_L@;background:@S2@;border:2px solid @S3@;border-radius:999px;
padding:4px 20px;max-width:900px;white-space:nowrap;overflow:hidden;text-overflow:clip}
.prog{position:absolute;left:@MAIN_L@px;width:@MAINW@px;top:182px;height:4px;
background:@S3@;border-radius:2px;overflow:hidden}
.prog i{display:block;height:100%;width:0;background:@BLUE@;border-radius:2px}
.main{position:absolute;left:@MAIN_L@px;width:@MAINW@px;top:@MAIN_TOP@px;
height:@MAINH@px;display:flex;flex-direction:column;justify-content:center;gap:26px}
.sub{color:@T3@;font-weight:400}
.mono{font-family:@MONO@;font-weight:500;font-variant-numeric:tabular-nums}
.big{font-weight:600;letter-spacing:-.02em;line-height:1.2}
.w{display:inline-block;white-space:pre}
.rule{height:8px;border-radius:4px;background:@BLUE@;transform-origin:left}
.rule.alt{background:@BLUE_L@}
.card{position:relative;background:@S1@;border:2px solid @S3@;border-radius:22px;
padding:44px 56px 44px 64px;flex:1 1 0;min-width:0}
.card .bar{position:absolute;left:0;top:28px;bottom:28px;width:8px;border-radius:4px;
background:@BLUE@;transform-origin:top}
.card.alt .bar{background:@BLUE_L@}
.phrase{font-weight:600;letter-spacing:-.01em;line-height:1.25}
.quote .phrase{font-weight:500}
.keyrow{display:flex;align-items:center;gap:64px}
.keyrow.flip{flex-direction:row-reverse}
.gauge{flex:none;width:300px;height:300px;position:relative}
.gauge svg{position:absolute;inset:0}
.gauge .mid{position:absolute;inset:0;display:flex;flex-direction:column;
align-items:center;justify-content:center;gap:4px}
.gauge .mid .n{font-size:96px;line-height:1;color:@T1@}
.gauge .mid .of{font-size:28px;color:@T3@}
.gauge img{width:150px;height:150px;position:relative}
.ring{position:absolute;left:75px;top:75px;width:150px;height:150px;border-radius:50%;
border:3px solid @BLUE@}
.rows{position:relative;display:flex;flex-direction:column;gap:22px}
.spine{position:absolute;width:4px;border-radius:2px;background:@S3@;z-index:0}
.spine i{display:block;width:100%;height:0;background:@BLUE@;border-radius:2px}
.row{position:relative;z-index:1;display:flex;align-items:center;gap:24px;padding:12px 28px;
border-radius:18px;border:2px solid @S3@;background:@S1@;color:@T2@;font-weight:500;
line-height:1.25;max-width:1560px;overflow:hidden}
.row>*{position:relative;z-index:1}
.row::before{content:"";position:absolute;inset:0;background:rgba(91,140,255,.0);
transform-origin:left;z-index:0}
.row.on{border-color:@BLUE@;color:@T1@}
.row.on.alt{border-color:@BLUE_L@}
.row.on::before{background:rgba(91,140,255,.2);transform:scaleX(var(--s,1))}
.row .sh{position:absolute;top:0;bottom:0;width:26%;left:-30%;z-index:0;
background:linear-gradient(90deg,transparent,rgba(143,176,255,.16),transparent);display:none}
.row.on .sh{display:block}
.bd{flex:none;width:52px;height:52px;border-radius:50%;background:@S2@;border:2px solid @S3@;
display:flex;align-items:center;justify-content:center;font-size:26px;color:@T2@}
.row.on .bd{background:@BLUE@;border-color:@BLUE@;color:@BG@}
.bd.dotb::after{content:"";width:14px;height:14px;border-radius:50%;background:@T3@}
.row.on .bd.dotb::after{background:@BG@}
.flowwrap{position:relative;display:flex;flex-direction:column;gap:30px}
.flow{display:flex;align-items:stretch;width:100%}
.node{flex:1 1 0;min-width:0;position:relative;display:flex;align-items:center;
justify-content:center;text-align:center;padding:44px 16px 22px;border-radius:20px;
border:2px solid @S3@;background:@S1@;color:@T2@;font-weight:500;line-height:1.2;min-height:190px}
.node .no{position:absolute;left:14px;top:10px;font-size:24px;color:@T3@}
.node .ok{position:absolute;right:14px;top:6px;font-size:30px;color:@BLUE_L@;line-height:1.2}
.node.done{border-color:rgba(91,140,255,.55);color:@T1@}
.node.on{border-color:@BLUE@;background:@S2@;color:@T1@}
.node.on .no{color:@BLUE_L@}
.lk{flex:0 0 76px;align-self:center;height:8px;position:relative;background:@S3@;
border-radius:4px}
.lk .fill{position:absolute;inset:0;background:@BLUE@;border-radius:4px;transform-origin:left;
transform:scaleX(0)}
.lk .dot{position:absolute;top:-9px;width:26px;height:26px;margin-left:-13px;border-radius:50%;
background:@BLUE_L@;box-shadow:0 0 0 7px rgba(91,140,255,.25);left:0;display:none}
.lk.act .dot{display:block}
.counter{display:flex;align-items:baseline;gap:14px;color:@T3@}
.counter .cnt,.counter .tot{color:@BLUE_L@}
table{border-collapse:separate;border-spacing:0 12px;width:100%;table-layout:fixed}
th{text-align:left;color:@BLUE_L@;font-weight:600;padding:6px 24px}
td{padding:16px 24px;background:@S1@;color:@T2@;font-weight:500;line-height:1.25;
border-top:2px solid @S3@;border-bottom:2px solid @S3@;vertical-align:middle}
td:first-child{border-left:2px solid @S3@;border-radius:14px 0 0 14px}
td:last-child{border-right:2px solid @S3@;border-radius:0 14px 14px 0}
td.on{color:@T1@;border-color:@BLUE@;
background-image:linear-gradient(90deg,rgba(91,140,255,.28) calc(var(--s,1)*100%),transparent 0)}
td.empty{background:transparent;border:2px dashed @S3@;border-radius:14px;opacity:.55}
.term{background:@S1@;border:2px solid @S3@;border-radius:22px;overflow:hidden;max-width:1560px;
width:100%}
.term .bar{display:flex;gap:10px;padding:16px 24px;background:@S2@;border-bottom:2px solid @S3@}
.term .bar i{width:16px;height:16px;border-radius:50%;background:@S3@}
.term .bar i:first-child{background:@BLUE@}
.term .bar i:nth-child(2){background:@BLUE_L@}
.term .body{padding:22px 0;font-family:@MONO@;line-height:1.5;color:@T2@}
.term .ln{display:flex;position:relative;padding:0 36px 0 0;white-space:pre-wrap}
.term .ln .g{flex:none;width:84px;text-align:right;padding-right:24px;color:@T3@}
.term .ln.on{color:@T1@;background-image:linear-gradient(90deg,rgba(91,140,255,.22)
calc(var(--s,1)*100%),transparent 0);box-shadow:inset 8px 0 0 @BLUE@}
.term .cur{display:inline-block;width:.6em;height:1.1em;background:@BLUE_L@;margin-left:4px;
vertical-align:text-bottom;opacity:0}
.ckrow{display:flex;align-items:center;gap:72px}
.ckwrap{flex:none;width:300px;height:300px;position:relative}
.ckwrap svg{position:absolute;inset:0}
.endrow{display:flex;align-items:center;gap:80px}
.endl{flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:26px}
.nextcard{background:@S1@;border:2px solid @S3@;border-radius:20px;padding:24px 36px;
display:flex;flex-direction:column;gap:8px}
.arrow{display:inline-block;color:@BLUE_L@}
.titlerow{display:flex;align-items:center;gap:72px}
.mark{flex:none;width:280px;height:280px;position:relative}
.mark img{position:absolute;left:40px;top:40px;width:200px;height:200px}
.mark svg{position:absolute;inset:0}
.titlec{flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:22px}
.wipe{position:absolute;top:0;bottom:0;width:420px;left:-500px;pointer-events:none;
background:linear-gradient(90deg,transparent,rgba(91,140,255,.55),rgba(143,176,255,.25),transparent)}
.sting{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
justify-content:center;gap:34px}
.sting .mk{position:relative;width:300px;height:300px}
.sting .mk img{position:absolute;left:50px;top:50px;width:200px;height:200px}
.sting .mk svg{position:absolute;inset:0}
.sting .wd{font-size:64px;font-weight:600;letter-spacing:.16em;color:@T1@}
.sting .wd b{color:@BLUE_L@;font-weight:500}
"""

JS = r"""
const E=x=>x<0?0:x>1?1:1-Math.pow(1-x,3);
const C=(t,a,d)=>E((t-a)/d);
function fmt(el,v){
  const dec=+el.dataset.dec||0;
  let s=dec?v.toFixed(dec):String(Math.round(v));
  if(el.dataset.grp) s=s.replace(/\B(?=(\d{3})+(?!\d))/g,',');
  el.textContent=(el.dataset.pre||'')+s+(el.dataset.suf||'');
}
window.setT=function(t){
  const D=window.D||3, T0=window.T0||0, still=!!window.STILL, R=document.querySelector('.root');
  const A=T0+t;
  R.style.opacity=R.dataset.fade==='1'?0.2+0.8*C(t,0,0.3):1;
  const g=document.querySelector('.grid');
  if(g) g.style.transform='translate('+((A*14)%96)+'px,'+((A*8)%96)+'px)';
  document.querySelectorAll('.glow').forEach((e,i)=>{
    e.style.transform='translate('+(Math.sin(A*0.5+i*2)*70)+'px,'+(Math.cos(A*0.4+i)*50)+'px)';});
  const pr=document.querySelector('.prog i');
  if(pr) pr.style.width=(100*((window.P0||0)+((window.P1||0)-(window.P0||0))*Math.min(1,t/D)))+'%';
  const wp=document.querySelector('.wipe');
  if(wp){const k=Math.min(1,t/%WIPE%); wp.style.left=(-500+(%W%+700)*k)+'px';
    wp.style.opacity=k>=1?0:1-k*0.5;}
  document.querySelectorAll('.en').forEach(el=>{
    const d=+el.dataset.d||0, l=still?1:C(t,d*0.09,0.45), dx=+el.dataset.dx||0;
    el.style.opacity=l;
    el.style.transform='translate('+((1-l)*dx)+'px,'+((1-l)*(dx?0:24))+'px)';});
  document.querySelectorAll('.w').forEach(el=>{
    const l=C(t,0.12+(+el.dataset.w||0)*0.07,0.35);
    el.style.opacity=l; el.style.transform='translateY('+((1-l)*18)+'px)';});
  document.querySelectorAll('.draw').forEach(el=>{
    const s=C(t,+el.dataset.d||0,+el.dataset.dur||0.5);
    el.style.transform=el.dataset.axis==='y'?'scaleY('+s+')':'scaleX('+s+')';});
  document.querySelectorAll('.cnt').forEach(el=>{fmt(el,(+el.dataset.to)*C(t,0.1,0.7));});
  document.querySelectorAll('.arc').forEach(el=>{
    const c=+el.dataset.c; el.style.strokeDashoffset=c*(1-(+el.dataset.frac)*C(t,0.1,0.7));});
  document.querySelectorAll('.drain').forEach(el=>{
    const c=+el.dataset.c; el.style.strokeDashoffset=c*Math.min(1,t/D);});
  document.querySelectorAll('.chk').forEach(el=>{
    const c=+el.dataset.len; el.style.strokeDashoffset=c*(1-C(t,0.25,0.5));});
  document.querySelectorAll('.spin').forEach(el=>{
    el.style.transform='rotate('+(A*36)+'deg)';});
  document.querySelectorAll('.pulse').forEach(el=>{
    el.style.boxShadow='0 0 '+(16+14*Math.sin(A*4))+'px rgba(91,140,255,.5)';});
  document.querySelectorAll('.ringx').forEach(el=>{
    const k=(A*0.8+(+el.dataset.o||0))%1;
    el.style.transform='scale('+(1+k*1.1)+')'; el.style.opacity=0.55*(1-k);});
  document.querySelectorAll('.hl').forEach(el=>{el.style.setProperty('--s',C(t,0.05,0.5));});
  document.querySelectorAll('.sh').forEach(el=>{
    el.style.left=(((A*0.45)%1.6)*130-30)+'%';});
  document.querySelectorAll('.fillk').forEach(el=>{
    const d=+el.dataset.d||0, anim=el.dataset.anim==='1';
    el.style.transform='scaleX('+(anim||!still?C(t,0.1+d*0.12,0.5):1)+')';});
  document.querySelectorAll('.lk .dot').forEach(el=>{
    el.style.left=(((A*0.9)%1)*100)+'%';});
  document.querySelectorAll('.spine').forEach(sp=>{
    const par=sp.parentElement, rows=[...par.querySelectorAll('.row')], a=par.querySelector('.row.on');
    if(!rows.length||!a) return;
    const f=rows[0], l=rows[rows.length-1], top=f.offsetTop+f.offsetHeight/2;
    sp.style.top=top+'px'; sp.style.left=(28+26-2)+'px';
    sp.style.height=(l.offsetTop+l.offsetHeight/2-top)+'px';
    sp.firstElementChild.style.height=((a.offsetTop+a.offsetHeight/2-top)*C(t,0.1,0.6))+'px';});
  document.querySelectorAll('.cur').forEach(el=>{el.style.opacity=(Math.floor(A*2)%2)?0:1;});
  document.querySelectorAll('.type').forEach(el=>{
    const l=C(t,(+el.dataset.d||0)*0.06,0.3);
    el.style.clipPath='inset(0 '+((1-l)*100)+'% 0 0)';});
};
window.overflow=function(){
  const out=[];
  document.querySelectorAll('[data-fit]').forEach(e=>{
    const mh=parseFloat(e.dataset.maxh||'0');
    if(e.scrollWidth>e.clientWidth+1||(mh&&e.offsetHeight>mh+1))
      out.push((e.dataset.fit||'box')+': '+e.textContent.trim().slice(0,60));
  });
  document.querySelectorAll('.en,.row,.node,td,.term,.card,.phrase,.big,.gauge,.nextcard').forEach(e=>{
    const r=e.getBoundingClientRect();
    if(r.width===0) return;
    if(r.left<%SAFE%-1||r.right>%W%-%SAFE%+1||r.bottom>%CAPTOP%-8||r.top<%SAFE%-8)
      out.push('outside safe area: '+e.textContent.trim().slice(0,50));
  });
  return out;
};
"""


def _fill(s: str, extra: dict | None = None) -> str:
    vals = {**PAL, "W": W, "H": H, "SAFE": SAFE, "CAPTOP": CAP_TOP, "MAIN_L": MAIN_L,
            "INSET": INSET, "MAIN_TOP": MAIN_TOP, "MAINW": MAIN_R - MAIN_L,
            "MAINH": MAIN_BOTTOM - MAIN_TOP, "FLOOR": vt.FLOOR_16_9,
            "SANS": vt.SANS_FAMILY, "MONO": vt.MONO_FAMILY, "WIPE": WIPE_S, **(extra or {})}
    for k, v in vals.items():
        s = s.replace(f"@{k}@", str(v)).replace(f"%{k}%", str(v))
    return s


def mh(px: float, lines: int, pad: int = 0) -> int:
    """Tallest a text block may be: `lines` lines at 1.25 line height, plus padding."""
    return int(px * 1.25 * lines + pad + 2)


def e(text: str) -> str:
    return html.escape(text, quote=False)


_NUM = re.compile(r"^([^\d]*)(\d{1,6}(?:\.\d+)?)([^\d]*)$")


def _words(text: str, count_first: bool = False) -> str:
    """Words as spans that reveal one by one; the first number counts up."""
    out, counted = [], False
    for i, w in enumerate(text.split()):
        m = _NUM.match(w)
        if m and "," not in w:
            pre, num, suf = m.groups()
            dec = len(num.split(".")[1]) if "." in num else 0
            if count_first and not counted:
                inner = (f'{e(pre)}<span class="cnt mono" data-to="{num}" data-dec="{dec}">'
                         f'{num}</span>{e(suf)}')
                counted = True
            else:
                inner = f'{e(pre)}<span class="mono">{num}</span>{e(suf)}'
        else:
            inner = e(w)
        out.append(f'<span class="w" data-w="{i}">{inner}</span>')
    return " ".join(out)


def _top(chip_text: str) -> str:
    chip = (f'<div class="chip en" data-d="0" data-fit="chip">{e(chip_text)}</div>'
            if chip_text else "")
    return (f'<div class="top"><div class="brand"><img src="{LOGO_URI}" alt="">'
            f'<span>ATLAS <b>Academy</b></span></div>{chip}</div>'
            '<div class="prog"><i></i></div>')


def _doc(inner: str, *, fade: bool, b=None, still: bool = False, total: float | None = None,
         wipe: bool = False) -> str:
    d = (b.t1 - b.t0) if b is not None else 3.0
    p0 = (b.t0 / total) if (b is not None and total) else 0
    p1 = (b.t1 / total) if (b is not None and total) else 0
    t0 = b.t0 if b is not None else 0
    wipe_div = '<div class="wipe"></div>' if wipe else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{vt.fonts_css()}'
            f'{_fill(CSS)}</style></head><body><div class="root" data-fade="{1 if fade else 0}">'
            f'<div class="grid"></div><div class="glow"></div><div class="glow b"></div>'
            f'{inner}{wipe_div}</div><script>window.D={d:.3f};window.T0={t0:.3f};'
            f'window.P0={p0:.5f};window.P1={p1:.5f};window.STILL={"true" if still else "false"};'
            f'{_fill(JS)}</script></body></html>')


def _ring(r: int, size: int, frac: float, c_track: str = "S3", cls: str = "arc",
          width: int = 14) -> str:
    c = 2 * 3.14159265 * r
    mid = size / 2
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{mid}" cy="{mid}" r="{r}" fill="none" stroke="{PAL[c_track]}" '
            f'stroke-width="{width}"/>'
            f'<circle class="{cls}" data-c="{c:.2f}" data-frac="{frac:.4f}" cx="{mid}" cy="{mid}" '
            f'r="{r}" fill="none" stroke="{PAL["BLUE"]}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-dasharray="{c:.2f}" stroke-dashoffset="{c:.2f}" '
            f'transform="rotate(-90 {mid} {mid})"/></svg>')


def _check_svg(size: int = 300, stroke: int = 16) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{size / 2 - 12}" fill="{PAL["S1"]}" '
            f'stroke="{PAL["S3"]}" stroke-width="6"/>'
            f'<path class="chk" data-len="260" d="M{size * .30} {size * .52} L{size * .45} '
            f'{size * .66} L{size * .72} {size * .36}" fill="none" stroke="{PAL["BLUE_L"]}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" '
            f'stroke-dasharray="260" stroke-dashoffset="260"/></svg>')


def _cell(text: str) -> str:
    """Numbers in tables are data: mono, and they count up."""
    m = re.match(r"^(\$?)(\d[\d,]*)(\.\d+)?(%?)$", text.strip())
    if not m:
        return e(text)
    pre, whole, frac, suf = m.group(1), m.group(2), m.group(3) or "", m.group(4)
    grp = ' data-grp="1"' if "," in whole else ""
    to = whole.replace(",", "") + frac
    return (f'<span class="cnt mono" data-to="{to}" data-dec="{len(frac) - 1 if frac else 0}" '
            f'data-pre="{pre}" data-suf="{suf}"{grp}>{e(text)}</span>')


def scene(b, *, senior: bool, fade: bool, prev=None, total: float | None = None) -> str:
    """The page for beat `b`. `fade`: the kind changed, so a wipe crosses the frame.
    `prev`: the beat before; the same kind of list/flow/table does not re-enter."""
    st = B.STYLE[senior]
    d = b.data
    k = b.kind
    alt = " alt" if d.get("alt") else ""
    still = bool(prev is not None and prev.kind == k
                 and k in ("list", "map", "flow", "table", "code"))
    chip = d.get("head", "")
    if k == "title":
        inner = (f'{_top("")}<div class="main"><div class="titlerow">'
                 f'<div class="mark en" data-d="0"><svg width="280" height="280" '
                 f'viewBox="0 0 280 280"><g class="spin" style="transform-origin:140px 140px">'
                 f'<circle cx="140" cy="140" r="130" fill="none" stroke="{PAL["BLUE"]}" '
                 f'stroke-width="4" stroke-dasharray="26 18"/></g></svg>'
                 f'<img class="pulse" style="border-radius:44px" src="{LOGO_URI}" alt=""></div>'
                 f'<div class="titlec"><div class="sub en" data-d="1" style="font-size:'
                 f'{st["chip"]}px">{e(d.get("lesson", ""))}</div>'
                 f'<div class="big" data-fit="title" data-maxh="{mh(104, 2)}" style="font-size:'
                 f'{96 if not senior else 104}px">{_words(d["title"])}</div>'
                 f'<div class="rule draw{alt}" data-d="0.5" data-dur="0.6" style="width:420px">'
                 f'</div><div class="sub en" data-d="5" style="font-size:{st["chip"]}px">'
                 f'{e(d.get("sub", ""))}</div></div></div></div>')
    elif k in ("key", "quote"):
        step, steps = d.get("step", 0) or 0, d.get("steps", 0) or 0
        if step and steps:
            gauge = (f'<div class="gauge en" data-d="0" data-dx="-60">'
                     f'{_ring(128, 300, min(1.0, step / steps))}'
                     f'<div class="mid"><span class="n cnt mono" data-to="{step}">{step}</span>'
                     f'<span class="of"><span class="mono">/{steps}</span></span></div></div>')
        else:
            gauge = (f'<div class="gauge en" data-d="0" data-dx="-60">'
                     f'<div class="ringx ring" data-o="0"></div>'
                     f'<div class="ringx ring" data-o="0.5"></div>'
                     f'<div class="mid"><img class="pulse" style="border-radius:34px" '
                     f'src="{LOGO_URI}" alt=""></div></div>')
        flip = " flip" if d.get("alt") else ""
        qmark = (f'<div class="mono" style="position:absolute;right:40px;top:6px;font-size:'
                 f'140px;line-height:1;color:{PAL["S3"]}">&rdquo;</div>' if k == "quote" else "")
        inner = (f'{_top(chip)}<div class="main"><div class="keyrow{flip}">{gauge}'
                 f'<div class="card {k}{alt} en" data-d="1" data-dx="60">{qmark}'
                 f'<i class="bar draw" data-axis="y" data-d="0.2" data-dur="0.5"></i>'
                 f'<div class="phrase" data-fit="phrase" data-maxh="'
                 f'{mh(st["key"], 3 if k == "quote" else 2)}" style="font-size:{st["key"]}px">'
                 f'{_words(d["phrase"], count_first=True)}</div>'
                 f'<div class="rule draw{alt}" data-d="0.7" data-dur="0.6" '
                 f'style="width:240px;margin-top:26px"></div></div></div></div>')
    elif k in ("list", "map"):
        if k == "map":
            steps = d["steps"]
            n = st["items"] + (2 if not senior else 0)
            at = d["at"]
            lo = max(0, min(at - n + 1, len(steps) - n))
            idx = list(range(lo, min(len(steps), lo + n)))
            labels = [B.label(steps[j], chars=st["item_chars"]) for j in idx]
            nums = [str(j + 1) for j in idx]
            now = at - lo
            top, fit, lines_ = "In this lesson", "step", 1
        else:
            labels, now = d["items"], d["now"]
            first = d.get("first", 0)
            nums = [str(first + j + 1) if d.get("ordered") else "" for j in range(len(labels))]
            top, fit, lines_ = chip, "item", 2
        rows = []
        for j, x in enumerate(labels):
            on = j == now
            bd = (f'<span class="bd mono">{nums[j]}</span>' if nums[j]
                  else '<span class="bd dotb"></span>')
            rows.append(f'<div class="row{" on hl" if on else ""}{alt if on else ""} en" '
                        f'data-d="{j}" data-dx="-50" style="font-size:'
                        f'{st["item"]}px">{"<i class=sh></i>" if on else ""}{bd}'
                        f'<span style="min-width:0;flex:1" data-fit="{fit}" '
                        f'data-maxh="{mh(st["item"], lines_)}">{e(x)}</span></div>')
        sub = (f'<div class="sub en" data-d="{len(rows)}" data-fit="sub" data-maxh="'
               f'{mh(st["chip"], 2)}" style="font-size:{st["chip"]}px">{e(d["sub"])}</div>'
               ) if d.get("sub") else ""
        inner = (f'{_top(top)}<div class="main"><div class="rows"><i class="spine"><i></i></i>'
                 f'{"".join(rows)}</div>{sub}</div>')
    elif k == "flow":
        nodes, now = d["nodes"], d["now"]
        lo, hi = 0, len(nodes)
        if senior and len(nodes) > 3:
            lo = max(0, min(now - 1, len(nodes) - 3))
            hi = lo + 3
        cells = []
        for j in range(lo, hi):
            cls = " on pulse" if j == now else (" done" if j < now else "")
            ok = '<span class="ok">&#10003;</span>' if j < now else ""
            cells.append(f'<div class="node{cls} en" data-d="{j - lo}" style="font-size:'
                         f'{st["item"] - 4}px"><span class="no mono">{j + 1}</span>{ok}'
                         f'<span style="min-width:0" data-fit="node" '
                         f'data-maxh="{mh(st["item"] - 4, 3)}">{e(nodes[j])}</span></div>')
            if j + 1 < hi:
                act = j == now - 1
                cells.append(f'<div class="lk{" act" if act else ""}"><i class="fill fillk" '
                             f'data-d="{j - lo}" data-anim="{1 if act else 0}"></i>'
                             f'{"<i class=dot></i>" if act else ""}</div>')
        sub = (f'<div class="sub en" data-d="4" data-fit="sub" data-maxh="{mh(st["chip"], 2)}" '
               f'style="font-size:{st["chip"]}px">{e(d["sub"])}</div>') if d.get("sub") else ""
        counter = (f'<div class="counter en" data-d="5" style="font-size:{st["chip"]}px">'
                   f'<span class="cnt mono" data-to="{now + 1}" style="font-size:'
                   f'{st["item"]}px">{now + 1}</span><span class="tot mono">/{len(nodes)}'
                   f'</span></div>')
        inner = (f'{_top(chip)}<div class="main"><div class="flowwrap"><div class="flow">'
                 f'{"".join(cells)}</div>{counter}{sub}</div></div>')
    elif k == "table":
        cols = d["cols"]
        head = "".join(f'<th data-fit="th" style="font-size:{st["chip"]}px">{e(c)}</th>'
                       for c in cols)
        nr, nc = d["now"]
        body = []
        for r, row in enumerate(d["rows"]):
            cells = []
            for c, x in enumerate(row):
                on = (r, c) == (nr, nc)
                cls = "on hl" if on else ("empty" if not x else "")
                cells.append(f'<td class="{cls}" data-fit="cell" data-maxh="'
                             f'{mh(st["cell"], 3, 36)}" style="font-size:{st["cell"]}px">'
                             f'{_cell(x)}</td>')
            body.append(f'<tr class="en" data-d="{r}" data-dx="-40">{"".join(cells)}</tr>')
        inner = (f'{_top(chip)}<div class="main"><table><tr>{head}</tr>'
                 f'{"".join(body)}</table></div>')
    elif k == "code":
        focus = d.get("focus", -1)
        lines = []
        for j, x in enumerate(d["lines"]):
            on = j == focus
            cur = '<i class="cur"></i>' if on else ""
            lines.append(f'<div class="ln{" on hl" if on else ""}"><span class="g">{j + 1}</span>'
                         f'<span class="type" data-d="{0 if still else j}">{e(x) or "&nbsp;"}'
                         f'{cur}</span></div>')
        more = f'<div class="sub" style="font-size:{st["chip"]}px">&hellip;</div>' if d.get(
            "more") else ""
        inner = (f'{_top(chip)}<div class="main"><div class="term en" data-d="0" data-fit="code" '
                 f'data-maxh="640"><div class="bar"><i></i><i></i><i></i></div>'
                 f'<div class="body" style="font-size:{st["code"]}px">{"".join(lines)}</div>'
                 f'</div>{more}</div>')
    elif k == "checkpoint":
        inner = (f'{_top(chip)}<div class="main"><div class="ckrow"><div class="ckwrap en" '
                 f'data-d="0"><div class="ringx ring" data-o="0" style="left:75px;top:75px;'
                 f'width:150px;height:150px"></div>{_ring(138, 300, 1.0, cls="drain", width=10)}'
                 f'{_check_svg(300)}</div><div><div class="big en" data-d="2" data-dx="60" '
                 f'style="font-size:{st["key"]}px">{e(d.get("label", "Checkpoint"))}</div>'
                 f'<div class="rule draw" data-d="0.5" data-dur="0.5" style="width:240px;'
                 f'margin-top:22px"></div></div></div></div>')
    elif k == "end":
        nxt = (f'<div class="nextcard en" data-d="3" data-dx="60"><div class="sub" '
               f'style="font-size:{st["chip"]}px">Next</div><div style="font-size:{st["item"]}px;'
               f'font-weight:600;line-height:1.25" data-fit="next" data-maxh="{mh(st["item"], 2)}">'
               f'<span class="arrow">&rarr;</span> {e(d["next"])}</div></div>'
               ) if d.get("next") else ""
        inner = (f'{_top("")}<div class="main"><div class="endrow"><div class="ckwrap en" '
                 f'data-d="0">{_ring(138, 300, 1.0, width=10)}{_check_svg(300)}</div>'
                 f'<div class="endl"><div class="big en" data-d="1" data-dx="60" data-fit="done" '
                 f'data-maxh="{mh(st["key"], 2)}" style="font-size:{st["key"]}px">'
                 f'{e(d.get("lesson", "Lesson"))}</div><div class="rule draw" data-d="0.4" '
                 f'data-dur="0.6" style="width:240px"></div>{nxt}</div></div></div>')
    else:
        raise ValueError(f"no scene for beat kind {k!r}")
    return _doc(inner, fade=fade, b=b, still=still, total=total, wipe=fade)


def sting_page() -> str:
    """The brand sting as one page: draw it with setT(t), t 0..1 s."""
    inner = (f'<div class="sting"><div class="mk"><svg width="300" height="300" '
             f'viewBox="0 0 300 300"><circle class="arc" data-c="{2 * 3.14159265 * 140:.2f}" '
             f'data-frac="1" cx="150" cy="150" r="140" fill="none" stroke="{PAL["BLUE"]}" '
             f'stroke-width="6" stroke-linecap="round" stroke-dasharray="'
             f'{2 * 3.14159265 * 140:.2f}" stroke-dashoffset="{2 * 3.14159265 * 140:.2f}" '
             f'transform="rotate(-90 150 150)"/></svg><img class="pulse" '
             f'style="border-radius:44px" src="{LOGO_URI}" alt=""></div>'
             f'<div class="wd en" data-d="3">ATLAS <b>Academy</b></div></div>')
    return _doc(inner, fade=False)


def sting(frame: int) -> str:                      # kept for callers of the old API
    return sting_page()
