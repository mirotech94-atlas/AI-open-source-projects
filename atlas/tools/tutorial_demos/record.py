"""Record the ATLAS Academy live demos on the Mac (atlas.modules.academy.tutorial_demos).

    .venv/bin/python tools/tutorial_demos/record.py [--session n8n-ep1 ...] [--shots DIR]

Plays each demo's declarative step list in headless Chromium against REAL
local software and writes assets/tutorial_demos/demo-<key>.mp4 + .json.
Never run on the server: it has one CPU and no browser budget.

What it needs on this machine (all local, nothing signed in anywhere):
  * Docker with the n8n image named in demo.env.example (DEMO_N8N_IMAGE); a
    throwaway container `atlas-demo-n8n` is made on 127.0.0.1:DEMO_N8N_PORT
    with a fresh data folder, its owner made of the fake test data in
    demo.env.example, and removed at the end;
  * uv (DEMO_UV, default `uv` on PATH) and Node >= 22.19 (DEMO_NODE, the
    `node` binary) with @modelcontextprotocol/inspector@DEMO_INSPECTOR_VERSION
    installed under DEMO_INSPECTOR_DIR (`npm install` there);
  * DEMO_HOME: an empty scratch folder used as HOME for the terminal, the
    Inspector and the editor, so no real config, path or key is ever read or
    shown. Its path is printed as ~.
  * the hello project for the MCP demos is made for real in DEMO_HOME by the
    terminal demo (uv init, uv add "mcp[cli]"), with server.py written from
    tutorial_series.MCP_SERVER_PY, verbatim.

Capture: the DevTools screencast (one JPEG per painted frame, with its
timestamp) at 1280x720 CSS pixels and device scale 1.5, so frames are
1920x1080 and the UI reads large; ffmpeg turns them into a constant 30 fps
DEMO_W x DEMO_H H.264 file with a keyframe every second. The cursor and the
blue ring are drawn into the page by an init script.

Colours (Miro, 2026-10-17: the navy video system) come from tutorial_design's
tokens, painted into the pages below at import (%TOKEN% placeholders): the
ring, the click and every action in blue, the terminal and the editor on
navy-raised with the faint grid; IBM Plex Sans, and Plex Mono for code only.
The committed recordings predate this and keep the old colours until they
are recorded again. A real
third-party UI (n8n, the Inspector) keeps its own colours; only the ring
and the cursor are drawn over it.

Look at the frames (--shots) before committing a new recording: every
re-record adds the binaries to git again.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from atlas.modules.academy import tutorial_demos as td
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy.tutorial_series import (
    MCP_DESKTOP_JSON,
    MCP_SERVER_PY,
)
from atlas.modules.academy.video_tokens import fonts_css

VIEW_W, VIEW_H, SCALE = 1280, 720, 1.5
FPS = 30
CONTAINER = "atlas-demo-n8n"
PAGES = "http://demo.local"


def env() -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in (HERE / "demo.env.example").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()
    vals.update({k: v for k, v in os.environ.items() if k.startswith("DEMO_")})
    return vals


E = env()
HOME = Path(E.get("DEMO_HOME", "")).expanduser() if E.get("DEMO_HOME") else None


def tilde(text: str) -> str:
    """The scratch HOME is shown as ~, and nothing that looks like a token is shown."""
    if HOME is not None:
        text = text.replace(str(HOME.resolve()), "~").replace(str(HOME), "~")
        text = text.replace("/private~", "~")
    text = re.sub(r"\?MCP_INSPECTOR_API_TOKEN=\S+", "", text)
    return "\n".join(x for x in text.split("\n")
                     if not re.search(r"token|secret|password", x, re.IGNORECASE))


TOKEN = re.compile(r"%([A-Z][A-Z0-9_]*)(\.\d+)?%")


def paint(page: str) -> str:
    """Fill a page's %TOKEN% (or %TOKEN.25% for a tint) from tutorial_design's
    palette, %GRID% with its faint grid and %FONTS% with the brand faces."""
    def one(m: re.Match) -> str:
        name, alpha = m.group(1), m.group(2)
        if name == "GRID":
            return design.GRID
        if name == "FONTS":
            return fonts_css()
        colour = getattr(design, name)
        assert colour in design.PALETTE, name
        return design.rgba(colour, float(alpha)) if alpha else colour
    out = TOKEN.sub(one, page)
    assert not TOKEN.search(out)
    return out


# ------------------------------------------------------------ the overlay
OVERLAY = r"""
(() => {
  if (window.__demo) return;
  const css = `
  #__cur{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
    transition:transform .55s cubic-bezier(.3,.7,.2,1);transform:translate(640px,420px)}
  #__cur svg{width:26px;height:26px;filter:drop-shadow(0 2px 3px rgba(0,0,0,.45))}
  #__cur.down svg{transform:scale(.85)}
  .__ring{position:fixed;z-index:2147483646;pointer-events:none;border:3px solid %BLUE%;border-radius:10px;
    box-shadow:0 0 0 4px %BLUE.25%,0 0 22px %BLUE.55%;animation:__p 1.1s ease-in-out infinite}
  @keyframes __p{0%,100%{box-shadow:0 0 0 3px %BLUE.2%,0 0 16px %BLUE.45%}
    50%{box-shadow:0 0 0 8px %BLUE.12%,0 0 30px %BLUE.7%}}
  .__click{position:fixed;z-index:2147483646;pointer-events:none;width:44px;height:44px;margin:-22px 0 0 -22px;
    border-radius:50%;border:3px solid %BLUE%;animation:__c .5s ease-out forwards}
  @keyframes __c{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}`;
  const boot = () => {
    if (document.getElementById('__cur')) return;
    const st = document.createElement('style'); st.textContent = css; document.documentElement.appendChild(st);
    const c = document.createElement('div'); c.id = '__cur';
    c.innerHTML = '<svg viewBox="0 0 24 24"><path d="M3 2l7 19 2.6-7.6L20 11z" fill="#fff" stroke="%NAVY%" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
  };
  window.__demo = {
    boot,
    move(x, y) { boot(); document.getElementById('__cur').style.transform = `translate(${x - 4}px,${y - 3}px)`; },
    tap(x, y) {
      const d = document.createElement('div'); d.className = '__click'; d.style.left = x + 'px'; d.style.top = y + 'px';
      document.documentElement.appendChild(d); setTimeout(() => d.remove(), 600);
    },
    ring(r, ms) {
      const d = document.createElement('div'); d.className = '__ring';
      const p = 6; Object.assign(d.style, {left: (r.x - p) + 'px', top: (r.y - p) + 'px', width: (r.width + 2 * p) + 'px', height: (r.height + 2 * p) + 'px'});
      document.documentElement.appendChild(d); setTimeout(() => d.remove(), ms);
    },
  };
  if (document.readyState !== 'loading') boot(); else document.addEventListener('DOMContentLoaded', boot);
})();
"""
OVERLAY = paint(OVERLAY)

TERMINAL = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;min-height:100%;background-color:%NAVY_RAISED%;%GRID%;color:%PAPER%;
font:22px/1.45 "IBM Plex Mono",monospace}
#t{padding:22px 28px;white-space:pre-wrap;word-break:break-all}
.p{color:%BLUE_TEXT%}.c{color:%PAPER%}.o{color:%PAPER_MUTED%}
.cur{display:inline-block;width:.6em;height:1.1em;background:%BLUE%;vertical-align:-3px;animation:b 1s steps(1) infinite}
@keyframes b{50%{opacity:0}}
</style></head><body><div id="t"></div><script>
const t = document.getElementById('t');
let line = null;
window.T = {
  prompt(cwd) { line = document.createElement('div'); line.innerHTML = '<span class="p">' + cwd + ' $</span> <span class="c"></span><span class="cur"></span>'; t.appendChild(line); scrollTo(0, 1e9); },
  async type(s, ms) { const c = line.querySelector('.c'); for (const ch of s) { c.textContent += ch; await new Promise(r => setTimeout(r, ms)); } },
  done() { const k = line.querySelector('.cur'); if (k) k.remove(); },
  async print(s, ms) { for (const ln of s.split('\\n')) { const d = document.createElement('div'); d.className = 'o'; d.textContent = ln || '\\u00a0'; t.appendChild(d); scrollTo(0, 1e9); await new Promise(r => setTimeout(r, ms)); } },
};
</script></body></html>"""
TERMINAL = paint(TERMINAL)

EDITOR = """<!doctype html><html><head><meta charset="utf-8"><style>%FONTS%
html,body{margin:0;min-height:100%;background-color:%NAVY_RAISED%;%GRID%;color:%PAPER_MUTED%;
font:27px/1.55 "IBM Plex Mono",monospace}
.bar{height:44px;background:%NAVY%;display:flex;align-items:flex-end;padding-left:12px}
.tab{background:%NAVY_RAISED%;color:%PAPER%;padding:10px 20px;font:500 18px/1 "IBM Plex Sans",system-ui,sans-serif;border-top:2px solid %BLUE%}
.tab i{display:inline-block;width:9px;height:9px;border-radius:50%;background:%PAPER%;margin-left:10px}
.tab.saved i{display:none}
.crumb{font:17px/1 "IBM Plex Sans",system-ui,sans-serif;color:%PAPER_MUTED%;padding:10px 22px;background:%NAVY_RAISED%}
.ed{display:flex}.ln{color:%PAPER_MUTED%;text-align:right;padding:0 18px 0 22px;user-select:none}
pre{margin:0;font:inherit;white-space:pre}
.k{color:%BLUE_TEXT%}.s{color:%PAPER%}.p{color:%PAPER_MUTED%}
.cur{display:inline-block;width:2px;height:1.2em;background:%BLUE%;vertical-align:-4px}
.toast{position:fixed;right:24px;bottom:24px;background:%BLUE%;color:%ON_BLUE%;font:700 16px/1 "IBM Plex Sans",system-ui,sans-serif;
padding:12px 18px;border-radius:8px;opacity:0;transition:opacity .3s}
.toast.on{opacity:1}
</style></head><body>
<div class="bar"><div class="tab" id="tab">claude_desktop_config.json<i></i></div></div>
<div class="crumb">~ › Library › Application Support › Claude › claude_desktop_config.json</div>
<div class="ed"><pre class="ln" id="ln"></pre><pre id="code"></pre></div>
<div class="toast" id="toast">Saved</div>
<script>
let text = '';
function hl(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/("(?:[^"\\\\]|\\\\.)*")(\\s*:)?/g, (m, q, colon) => colon ? '<span class="k">' + q + '</span>' + colon : '<span class="s">' + q + '</span>');
}
function draw() {
  document.getElementById('code').innerHTML = hl(text) + '<span class="cur"></span>';
  const n = text.split('\\n').length;
  document.getElementById('ln').textContent = Array.from({length: n}, (_, i) => i + 1).join('\\n');
}
window.E = {
  set(s) { text = s; draw(); },
  async type(s, ms) { document.getElementById('tab').classList.remove('saved');
    for (const ch of s) { text += ch; draw(); await new Promise(r => setTimeout(r, ch === ' ' ? ms / 3 : ms)); } },
  save() { document.getElementById('tab').classList.add('saved'); const t = document.getElementById('toast'); t.classList.add('on'); },
  value() { return text; },
};
draw();
</script></body></html>"""
EDITOR = paint(EDITOR)


# ------------------------------------------------------------ the capture
class Capture:
    """The DevTools screencast of one page: (timestamp, jpeg) for every
    painted frame while running."""

    def __init__(self, page) -> None:
        self.page = page
        self.frames: list[tuple[float, bytes]] = []
        self.on = False
        self.cdp = None

    async def start(self) -> None:
        self.cdp = await self.page.context.new_cdp_session(self.page)

        async def frame(ev) -> None:
            with contextlib.suppress(Exception):
                await self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
            if self.on:
                self.frames.append((ev["metadata"].get("timestamp") or time.time(),
                                    base64.b64decode(ev["data"])))

        self.cdp.on("Page.screencastFrame", lambda ev: asyncio.ensure_future(frame(ev)))
        await self.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 92, "maxWidth": int(VIEW_W * SCALE),
            "maxHeight": int(VIEW_H * SCALE), "everyNthFrame": 1})

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self.cdp.send("Page.stopScreencast")


def encode(frames: list[tuple[float, bytes]], t0: float, t1: float, out: Path) -> None:
    """Variable-rate frames -> constant 30 fps DEMO_W x DEMO_H, keyframe every second."""
    frames = sorted(frames)
    # the frame on screen at t0 opens the file
    first = max((i for i, (ts, _) in enumerate(frames) if ts <= t0), default=0)
    keep = [(max(ts, t0), b) for ts, b in frames[first:] if ts < t1]
    with tempfile.TemporaryDirectory(prefix="demo-enc-") as work:
        lines = []
        for i, (ts, buf) in enumerate(keep):
            p = Path(work) / f"f{i:05d}.jpg"
            p.write_bytes(buf)
            nxt = keep[i + 1][0] if i + 1 < len(keep) else t1
            lines.append(f"file '{p}'\nduration {max(0.001, nxt - ts):.4f}\n")
        lines.append(f"file '{Path(work) / f'f{len(keep) - 1:05d}.jpg'}'\n")
        listing = Path(work) / "list.txt"
        listing.write_text("".join(lines))
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-vf",
             f"fps={FPS},scale={td.DEMO_W}:{td.DEMO_H}:flags=lanczos,format=yuv420p",
             "-c:v", "libx264", "-preset", "slow", "-crf", "23", "-g", str(FPS),
             "-movflags", "+faststart", "-an", "-t", f"{t1 - t0:.3f}", str(out)], check=True)


# ------------------------------------------------------------ the player
class Player:
    def __init__(self, page, origins: dict[str, str]) -> None:
        self.page = page
        self.origins = origins
        self.cwd = "~"
        self.inspector: subprocess.Popen | None = None

    def url(self, target: str) -> str:
        for k, v in self.origins.items():
            target = target.replace("{" + k + "}", v)
        return target

    async def box(self, sel: str):
        loc = self.page.locator(sel).first
        await loc.wait_for(state="visible", timeout=15000)
        await loc.scroll_into_view_if_needed()
        return loc, await loc.bounding_box()

    async def point(self, sel: str):
        loc, b = await self.box(sel)
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        await self.page.evaluate(f"window.__demo && window.__demo.move({x}, {y})")
        await self.page.wait_for_timeout(600)
        return loc, x, y

    async def act(self, a: td.Act) -> None:
        pg = self.page
        if a.op == "goto":
            await pg.goto(self.url(a.target), wait_until="load")
        elif a.op in ("click", "dblclick"):
            loc, x, y = await self.point(a.target)
            await pg.evaluate(f"window.__demo.tap({x}, {y})")
            if a.op == "dblclick":
                await loc.dblclick()
            else:
                await loc.click()
        elif a.op in ("fill", "fill_nth"):
            sel = a.target
            if a.op == "fill_nth":
                sel = f"{a.target} >> nth={a.ms}"
            loc, x, y = await self.point(sel)
            await pg.evaluate(f"window.__demo.tap({x}, {y})")
            await loc.click()
            await pg.keyboard.press("ControlOrMeta+a")
            if "{{" in a.text:
                await loc.fill(a.text)
                # n8n closes an open "{{" itself; type the rest as a person would
            else:
                await pg.keyboard.type(a.text, delay=55)
        elif a.op == "lines":
            loc, x, y = await self.point(a.target)
            await loc.click()
            for i, ln in enumerate(a.text.split("\n")):
                if i:
                    await pg.keyboard.press("Enter")
                await pg.keyboard.type(ln, delay=55)
        elif a.op == "type":
            await pg.keyboard.type(a.text, delay=70)
        elif a.op == "press":
            await pg.keyboard.press(a.target)
        elif a.op == "wait":
            await pg.wait_for_timeout(a.ms)
        elif a.op == "wait_text":
            await pg.locator(a.target).first.wait_for(timeout=a.ms or 15000)
        elif a.op == "ring":
            for sel in [s.strip() for s in a.target.split(", ")] if ">>" not in a.target else [a.target]:
                loc = pg.locator(sel).first
                if await loc.count():
                    b = await loc.bounding_box()
                    if b:
                        await pg.evaluate(f"window.__demo.ring({json.dumps(b)}, {a.ms})")
                        await pg.evaluate(f"window.__demo.move({b['x'] + b['width'] * .8}, "
                                          f"{b['y'] + b['height'] * .8})")
                        break
            await pg.wait_for_timeout(a.ms)
        elif a.op == "post":
            await asyncio.to_thread(self._post, self.url(N8N_ORIGIN[0] + a.target), a.text)
        elif a.op == "publish":
            # the Publish modal: confirm with its own button
            btn = pg.locator("[role=dialog] button:has-text('Publish'), "
                             ".el-dialog button:has-text('Publish')").last
            await btn.wait_for(timeout=10000)
            b = await btn.bounding_box()
            await pg.evaluate(f"window.__demo.move({b['x'] + b['width'] / 2}, {b['y'] + b['height'] / 2})")
            await pg.wait_for_timeout(600)
            await btn.click()
        elif a.op == "term":
            await self.term(a.text, a.ms)
        elif a.op == "edit":
            text = MCP_DESKTOP_JSON if a.text == "MCP_DESKTOP_JSON" else a.text
            await pg.evaluate("window.E.set('')")
            await pg.evaluate(f"window.E.type({json.dumps(text)}, {a.ms or 30})")
        elif a.op == "save":
            text = await pg.evaluate("window.E.value()")
            json.loads(text)                        # it must be valid JSON before it is saved
            cfg = HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(text + "\n")
            await pg.evaluate("window.E.save()")
        else:
            raise SystemExit(f"unknown op {a.op}")

    @staticmethod
    def _post(url: str, body: str) -> None:
        req = urllib.request.Request(url, data=body.encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        for _ in range(10):
            try:
                urllib.request.urlopen(req, timeout=10).read()
                return
            except OSError:
                time.sleep(0.6)
        raise SystemExit(f"POST {url} failed")

    async def term(self, cmd: str, ms: int) -> None:
        pg = self.page
        await pg.evaluate(f"window.T.prompt({json.dumps(self.cwd)})")
        await pg.evaluate(f"window.T.type({json.dumps(cmd)}, 65)")
        await pg.wait_for_timeout(350)
        await pg.evaluate("window.T.done()")
        out = await asyncio.to_thread(self.run, cmd)
        if out:
            await pg.evaluate(f"window.T.print({json.dumps(out)}, {ms or 70})")

    def shell_env(self) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TERM", "TMPDIR")}
        uv_dir = str(Path(E.get("DEMO_UV", shutil.which("uv") or "uv")).parent)
        node_dir = str(Path(E.get("DEMO_NODE", shutil.which("node") or "node")).parent)
        e.update(HOME=str(HOME), PATH=f"{uv_dir}:{node_dir}:{e.get('PATH', '')}", NO_COLOR="1",
                 UV_NO_PROGRESS="1")
        for k in ("HTTPS_PROXY", "UV_CACHE_DIR"):
            if os.environ.get(k):
                e[k] = os.environ[k]
        return e

    def run(self, cmd: str) -> str:
        """Run a terminal-demo command for real in the scratch HOME."""
        here = HOME if self.cwd == "~" else HOME / self.cwd.removeprefix("~/")
        if cmd.startswith("cd "):
            self.cwd = "~/" + cmd[3:].strip()
            return ""
        if cmd.startswith("npx @modelcontextprotocol/inspector"):
            return self.start_inspector()
        if cmd == "cat server.py":
            (here / "server.py").write_text(MCP_SERVER_PY + "\n")
        p = subprocess.run(cmd, shell=True, check=False, cwd=here, env=self.shell_env(), capture_output=True,
                           text=True, timeout=600)
        if p.returncode != 0:
            raise SystemExit(f"{cmd}: {p.stdout}{p.stderr}")
        return tilde((p.stdout + p.stderr).rstrip())

    def start_inspector(self) -> str:
        """The official Inspector, pinned, local, on an empty server list."""
        cat = HOME / ".mcp-inspector" / "mcp.json"
        cat.parent.mkdir(parents=True, exist_ok=True)
        cat.write_text('{"mcpServers": {}}\n')
        e = self.shell_env()
        e.update(MCP_AUTO_OPEN_ENABLED="false", MCP_INSPECTOR_SECRET_STORE="memory")
        launcher = (Path(E["DEMO_INSPECTOR_DIR"]) / "node_modules" / "@modelcontextprotocol"
                    / "inspector" / "clients" / "launcher" / "build" / "index.js")
        log = Path(tempfile.mkstemp(prefix="inspector-", suffix=".log")[1])
        self.inspector = subprocess.Popen(
            [E.get("DEMO_NODE", "node"), str(launcher)], cwd=HOME, env=e,
            stdout=log.open("w"), stderr=subprocess.STDOUT)
        for _ in range(60):
            time.sleep(0.5)
            text = log.read_text()
            if "up and running" in text:
                break
        else:
            raise SystemExit("the Inspector did not start: " + tilde(log.read_text())[-400:])
        time.sleep(0.5)
        lines = [x for x in tilde(log.read_text()).split("\n")
                 if x.strip() and "secret" not in x.lower() and "Secrets" not in x]
        return "\n".join(lines)


N8N_ORIGIN: list[str] = []


# ------------------------------------------------------------ n8n
def n8n_up() -> str:
    port = E["DEMO_N8N_PORT"]
    origin = f"http://localhost:{port}"
    data = Path(tempfile.mkdtemp(prefix="atlas-demo-n8n-"))
    data.chmod(0o777)
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER, "-p", f"127.0.0.1:{port}:5678",
         "-e", "GENERIC_TIMEZONE=Europe/Lisbon", "-e", "TZ=Europe/Lisbon",
         "-e", f"WEBHOOK_URL={origin}/", "-e", "N8N_DIAGNOSTICS_ENABLED=false",
         "-e", "N8N_VERSION_NOTIFICATIONS_ENABLED=false", "-e", "N8N_PERSONALIZATION_ENABLED=false",
         "-e", "N8N_TEMPLATES_ENABLED=false", "-e", "N8N_HIRING_BANNER_ENABLED=false",
         "-e", "N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true",
         "-v", f"{data}:/home/node/.n8n", E["DEMO_N8N_IMAGE"]], check=True, capture_output=True)
    # /healthz answers 200 while the migrations still run: wait for the API
    for _ in range(240):
        time.sleep(1)
        with contextlib.suppress(Exception):
            json.loads(urllib.request.urlopen(origin + "/rest/settings", timeout=3).read())
            break
    else:
        raise SystemExit("n8n did not come up")
    body = json.dumps({"email": E["DEMO_N8N_OWNER_EMAIL"], "firstName": E["DEMO_N8N_OWNER_FIRST"],
                       "lastName": E["DEMO_N8N_OWNER_LAST"],
                       "password": E["DEMO_N8N_OWNER_PASSWORD"]}).encode()
    for _ in range(30):
        try:
            got = urllib.request.urlopen(urllib.request.Request(
                origin + "/rest/owner/setup", data=body, method="POST",
                headers={"Content-Type": "application/json"}), timeout=10).read()
            if E["DEMO_N8N_OWNER_EMAIL"].encode() in got:
                break
        except OSError:
            pass                 # not up yet: try again
        time.sleep(1)
    else:
        raise SystemExit("the demo owner could not be set up")
    N8N_ORIGIN[:] = [origin]
    return origin


def n8n_version() -> str:
    p = subprocess.run(["docker", "exec", CONTAINER, "n8n", "--version"], capture_output=True,
                       text=True, check=False)
    return p.stdout.strip()


async def n8n_login(page, origin: str) -> None:
    await page.goto(origin + "/signin")
    await page.locator("input[type=email]").fill(E["DEMO_N8N_OWNER_EMAIL"])
    await page.locator("input[type=password]").fill(E["DEMO_N8N_OWNER_PASSWORD"])
    await page.keyboard.press("Enter")
    await page.wait_for_url(re.compile(r".*/home/.*"), timeout=30000)


# ------------------------------------------------------------ a session
async def session(browser, name: str, shots: Path | None, keep: dict) -> None:
    demos = [d for d in td.DEMOS.values() if d.session == name]
    app = demos[0].app
    ctx = await browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H},
                                    device_scale_factor=SCALE)
    await ctx.add_init_script(OVERLAY)

    async def pages(route) -> None:
        path = route.request.url.removeprefix(PAGES)
        body = {"/terminal.html": TERMINAL, "/editor.html": EDITOR}.get(path)
        await route.fulfill(status=200 if body else 404, body=body or "",
                            content_type="text/html")
    await ctx.route(PAGES + "/**", pages)
    page = await ctx.new_page()
    origins = {"n8n": N8N_ORIGIN[0] if N8N_ORIGIN else "",
               "inspector": "http://localhost:6274"}
    player = keep.setdefault("player", Player(page, origins))
    player.page, player.origins = page, origins
    versions: dict[str, str] = {"chromium": browser.version}
    if app == "n8n":
        await n8n_login(page, origins["n8n"])
        versions["n8n"] = n8n_version()
        await page.goto(origins["n8n"] + "/home/workflows")
        await page.locator("text=Build a workflow").first.wait_for(timeout=30000)
    elif app == "terminal":
        if HOME.exists():
            shutil.rmtree(HOME / "hello", ignore_errors=True)
        HOME.mkdir(parents=True, exist_ok=True)
        await page.goto(PAGES + "/terminal.html")
        uv = await asyncio.to_thread(subprocess.run, [E.get("DEMO_UV", "uv"), "--version"],
                                     capture_output=True, text=True, check=False)
        versions["uv"] = uv.stdout.strip()
        versions["inspector"] = E.get("DEMO_INSPECTOR_VERSION", "")
    elif app == "inspector":
        versions["inspector"] = E.get("DEMO_INSPECTOR_VERSION", "")
        if player.inspector is None:
            await asyncio.to_thread(player.start_inspector)
        await page.goto(origins["inspector"] + "/", wait_until="load")
        await page.locator("text=Add Servers").first.wait_for(timeout=30000)
    elif app == "editor":
        await page.goto(PAGES + "/editor.html")
    await page.evaluate("window.__demo && window.__demo.boot()")
    await page.wait_for_timeout(800)
    cap = Capture(page)
    await cap.start()
    td.DEMO_DIR.mkdir(parents=True, exist_ok=True)
    for demo in demos:
        print(f"{demo.key}: recording {len(demo.steps)} steps")
        cap.frames.clear()
        cap.on = True
        await page.wait_for_timeout(400)
        marks: list[list[float]] = []
        urls: list[str] = []
        for k, step in enumerate(demo.steps):
            start = time.time()
            for a in step.acts:
                await player.act(a)
            await page.wait_for_timeout(500)
            end = time.time()
            marks.append([start, end])
            urls.append(re.sub(r"[?#].*$", "", page.url).replace("http://", "")
                        if demo.app in ("n8n", "inspector") else "")
            if shots:
                await page.screenshot(path=str(shots / f"{demo.key}-{k}.png"))
            print(f"  step {k + 1}: {end - start:.1f} s  {step.caption}")
        cap.on = False
        t0, t1 = marks[0][0], marks[-1][1]
        out = td.video_path(demo.key)
        await asyncio.to_thread(encode, list(cap.frames), t0, t1, out)
        data = out.read_bytes()
        meta = {"key": demo.key, "seconds": round(t1 - t0, 3),
                "marks": [[round(a - t0, 3), round(b - t0, 3)] for a, b in marks],
                "urls": urls, "captions": [s.caption for s in demo.steps],
                "width": td.DEMO_W, "height": td.DEMO_H, "fps": FPS,
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "versions": versions,
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds")}
        td.meta_path(demo.key).write_text(json.dumps(meta, indent=1) + "\n")
        print(f"  -> {out.name} {len(data) / 1e6:.2f} MB, {t1 - t0:.1f} s")
    await cap.stop()
    await ctx.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", action="append", default=[])
    ap.add_argument("--shots")
    ap.add_argument("--keep-n8n", action="store_true", help="leave the demo container running")
    args = ap.parse_args()
    if HOME is None:
        raise SystemExit("set DEMO_HOME to an empty scratch folder")
    HOME.mkdir(parents=True, exist_ok=True)
    order = list(dict.fromkeys(d.session for d in td.DEMOS.values()))
    names = args.session or order
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    keep: dict = {}
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            for name in [n for n in order if n in names]:
                if name.startswith("n8n"):
                    await asyncio.to_thread(n8n_up)
                await session(browser, name, shots, keep)
            await browser.close()
    finally:
        player = keep.get("player")
        if player is not None and player.inspector is not None:
            player.inspector.terminate()
        if not args.keep_n8n:
            await asyncio.to_thread(subprocess.run, ["docker", "rm", "-f", CONTAINER],
                                    capture_output=True, check=False)


if __name__ == "__main__":
    asyncio.run(main())
