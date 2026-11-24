"""Live demos for the ATLAS Academy tutorials: real screen recordings.

Miro, 2026-10-11: "if you are doing a live demo or lab -- when talking about
how to use n8n and Claude hands-on -- switch to a web browser as the
background to do the live demo and explain step by step how to go through
it."

A demo is a declarative list of steps, each a caption and the browser
actions that perform it (goto / click / fill / type / press / wait / ring
...). The steps live here, in code, so a recording can be made again and
comes out the same. tools/tutorial_demos/record.py plays them on the Mac in
headless Chromium (1280x720 CSS pixels at 1.5x, captured as 1920x1080 frames
through the DevTools screencast, never wall-clock video), with a drawn
cursor and a royal-blue highlight ring, against REAL local software:

  n8n            the official image in a throwaway container on 127.0.0.1,
                 an owner made of clearly fake test data
                 (tools/tutorial_demos/demo.env.example), a public no-key
                 API (Open-Meteo's geocoding) and no Slack credentials: an
                 Edit Fields node shapes the message and the narration says
                 the Slack node takes its place in your own workflow;
  MCP Inspector  the official @modelcontextprotocol/inspector, local, listing
                 and calling the `greet` tool of the tutorial's own server
                 (tutorial_series.MCP_SERVER_PY, run verbatim with uv);
  terminal       a terminal page that types each command and prints the
                 command's REAL output (run in a neutral scratch HOME, its
                 path shown as ~, any token line dropped);
  editor         a code-editor page where claude_desktop_config.json is
                 typed and saved (a scratch HOME, never the real file).

No real account, key, token or address is ever on screen. The recordings
are encoded at DEMO_W x DEMO_H with a keyframe every second and committed
under assets/tutorial_demos/ with a sidecar JSON per demo: when each step
starts and ends in the file, the page address at that step, the tool
versions and the file's sha256. The one-CPU server only cuts a window per
step, speeds it up (at most MAX_SPEED) or holds its last frame to fit the
narration, and overlays the step caption: no browser runs on the server.

2026-10-13 (rule 18): each step names its `box`, the region where its action
happens in 1536x864 recording pixels, at most 1024x576, and the server crops
straight to it (crop_filter), so a phone never shows a whole, mostly-white page.
"""
from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .reel_design import ASSETS

DEMO_DIR = ASSETS / "tutorial_demos"
DEMO_W, DEMO_H = 1536, 864           # the recording's own pixels: every step box is in these
# Where the recording sits on the 1920x1080 frame (2026-10-18, the safe-area
# gate): scaled to 1344x756 (16:9, 7/8 of the recording) at (288, 146), so
# the window bar above it (y 96-146) and the step caption under it (from
# y 924, its text ending by 972) sit inside safe-16-9 (96..984). The
# recorded pixels are not text we draw; the bar and the caption are.
HOLE_X, HOLE_Y, HOLE_W, HOLE_H = 288, 146, 1344, 756
HOLE = (HOLE_X, HOLE_Y, HOLE_W, HOLE_H)
# The hole of a render made before 2026-10-18 (the recording 1:1 at 258, 92):
# a teaching Short cut from such a render crops its demo there.
LEGACY_HOLE = (258, 92, DEMO_W, DEMO_H)
MAX_SPEED = 2.0                      # a step plays at most this much faster than recorded


@dataclass(frozen=True)
class Act:
    """One browser action. `op` is one of OPS; `target` a Playwright selector
    or a URL; `text` what is typed; `ms` a pause or a duration."""
    op: str
    target: str = ""
    text: str = ""
    ms: int = 0


OPS = ("goto", "click", "dblclick", "fill", "fill_nth", "lines", "type", "press", "wait",
       "wait_text", "ring", "post", "publish", "term", "edit", "save")


@dataclass(frozen=True)
class Step:
    caption: str                     # shown under the recording while this step is said
    acts: tuple[Act, ...]
    # 2026-10-13 (rule 18): the region this step shows, x, y, w, h in the
    # 1536x864 recording pixels, 16:9 and at most 1024x576, so it reads on a
    # phone. Chosen from a frame of the recording, never guessed.
    box: tuple[int, int, int, int] = (256, 144, 1024, 576)


def crop_filter(step: Step, width: int, height: int) -> str:
    """The ffmpeg chain that cuts `step.box` out of a recording frame
    (DEMO_W x DEMO_H) and scales it to `width` x `height`. A box reaching past
    the frame is pulled back inside it, never cropped outside."""
    x, y, w, h = step.box
    w, h = min(w, DEMO_W), min(h, DEMO_H)
    x, y = max(0, min(x, DEMO_W - w)), max(0, min(y, DEMO_H - h))
    return f"crop={w}:{h}:{x}:{y},scale={width}:{height}"


@dataclass(frozen=True)
class Demo:
    key: str
    session: str                     # demos of one session are recorded in one browser run
    app: str                         # "n8n" | "inspector" | "terminal" | "editor"
    title: str                       # the window's title bar when no address is shown
    steps: tuple[Step, ...]


# Short constructors, so a step list reads like the clicks it makes.
def goto(url: str) -> Act:
    return Act("goto", url)


def click(sel: str, ms: int = 0) -> Act:
    return Act("click", sel, ms=ms)


def fill(sel: str, text: str) -> Act:
    return Act("fill", sel, text)


def press(key: str) -> Act:
    return Act("press", key)


def kind(text: str) -> Act:
    return Act("type", text=text)


def wait(ms: int) -> Act:
    return Act("wait", ms=ms)


def ring(sel: str, ms: int = 1600) -> Act:
    return Act("ring", sel, ms=ms)


def post(path: str, body: str) -> Act:
    """POST `body` (JSON) to the demo n8n at `path`, the way the curl does."""
    return Act("post", path, body)


def term(cmd: str, ms: int = 0) -> Act:
    return Act("term", text=cmd, ms=ms)


N8N = "{n8n}"                        # the recorder puts the local n8n's origin here
INSPECTOR = "{inspector}"

# --------------------------------------------------------------- n8n
NDV_CLOSE = "[data-test-id=ndv-close-button]"
PLUS = "[data-test-id=canvas-handle-plus]"
EXEC_WF = "[data-test-id=execute-workflow-button]"
OUTPUT = "[data-test-id=output-panel]"
INPUT = "[data-test-id=input-panel]"

N8N_TOUR = Demo("n8n-tour", "n8n-ep0", "n8n", "n8n", steps=(
    # the canvas round its "Add first step" square
    Step("The canvas: where you build", box=(280, 156, 1024, 576), acts=(
        wait(900), click("text=Build a workflow"), wait(1400),
        ring("[data-test-id=canvas]", 2200), wait(600))),
    # the + square and the trigger list that opens on the right
    Step("Add first step: pick a trigger", box=(512, 72, 1024, 576), acts=(
        click("[data-test-id=canvas-add-button]"), wait(1400),
        ring("[data-test-id=node-creator]", 2000), wait(400),
        kind("Manual"), wait(900), press("Enter"), wait(1500))),
    # the node panel's INPUT and its settings
    Step("Click a node: its settings, INPUT and OUTPUT", box=(32, 38, 1024, 576), acts=(
        click(PLUS), wait(900), kind("Edit Fields"), wait(900), press("Enter"), wait(1400),
        click("button:has-text('Execute previous nodes')"), wait(1600),
        ring(INPUT, 1500), ring(OUTPUT, 1300), wait(300))),
    # both nodes turning green, the Execute workflow button
    Step("Execute Workflow: run it and watch", box=(358, 240, 1024, 576), acts=(
        click(NDV_CLOSE), wait(900), click(EXEC_WF), wait(2200),
        ring("[data-test-id=canvas]", 1600), wait(800))),
))

N8N_BUILD = Demo("n8n-build", "n8n-ep1", "n8n", "n8n", steps=(
    # the node search, then the Webhook's parameters
    Step("Create a workflow · add a Webhook trigger", box=(512, 48, 1024, 576), acts=(
        wait(600), click("text=Build a workflow"), wait(1200),
        click("[data-test-id=canvas-add-button]"), wait(1000),
        kind("Webhook"), wait(800), press("Enter"), wait(1600))),
    # the test URL, HTTP Method and Path fields
    Step("HTTP Method: POST · Path: city-info", box=(320, 74, 896, 504), acts=(
        click("[data-test-id=parameter-input-httpMethod]"), wait(700),
        click("[role=option]:has-text('POST'), li:has-text('POST')"), wait(600),
        fill("[data-test-id=parameter-input-path] input", "city-info"), wait(500),
        ring("[data-test-id=node-parameters] >> text=webhook-test", 1800))),
    # Listen for test event and the OUTPUT that arrives
    Step("Listen for test event · send it a city", box=(640, 50, 896, 504), acts=(
        click("[data-test-id=trigger-execute-button]"), wait(1500),
        post("/webhook-test/city-info", '{"city": "Lisbon"}'), wait(2200),
        ring(OUTPUT, 2200), wait(500))),
))

N8N_API = Demo("n8n-api", "n8n-ep1", "n8n", "n8n", steps=(
    # the node search, then the HTTP Request's parameters
    Step("Add an HTTP Request node", box=(512, 48, 1024, 576), acts=(
        click(NDV_CLOSE), wait(800), click(PLUS), wait(900),
        kind("HTTP Request"), wait(900), press("Enter"), wait(1500))),
    # the Method and URL fields
    Step("URL: Open-Meteo's free city search, no key", box=(320, 74, 896, 504), acts=(
        fill("[data-test-id=parameter-input-url] [contenteditable], "
             "[data-test-id=parameter-input-url] input",
             "https://geocoding-api.open-meteo.com/v1/search"), wait(700),
        ring("[data-test-id=parameter-input-url]", 1500))),
    # the query parameter and the OUTPUT it brings back
    Step("Query parameter: name = {{ $json.body.city }}",
         box=(512, 240, 1024, 576), acts=(
        click("[data-test-id=parameter-input-sendQuery] .el-switch, "
              "[data-test-id=parameter-input-sendQuery]"), wait(900),
        Act("fill_nth", "[data-test-id=parameter-input-name] input", "name", 0), wait(500),
        Act("fill_nth", "[data-test-id=parameter-input-value] [contenteditable], "
                        "[data-test-id=parameter-input-value] input",
            "{{ $json.body.city }}", 0), wait(900),
        click("[data-test-id=node-execute-button]"), wait(2800),
        ring(OUTPUT, 2000))),
))

N8N_MESSAGE = Demo("n8n-message", "n8n-ep1", "n8n", "n8n", steps=(
    # the node picker on the right, then the text field, its expression and result
    Step("Edit Fields shapes the message (your Slack node goes here)",
         box=(512, 48, 1024, 576), acts=(
        click(NDV_CLOSE), wait(800), click(PLUS), wait(900),
        kind("Edit Fields"), wait(900), press("Enter"), wait(1500),
        click("text=Add Field"), wait(800),
        Act("fill_nth", "[data-test-id=assignment-name] input, "
                        "[data-test-id=assignment] input", "text", 0), wait(500),
        Act("fill_nth", "[data-test-id=assignment-value] [contenteditable], "
                        "[data-test-id=assignment-value] input",
            "{{ $json.results[0].name }} is in {{ $json.results[0].country }}", 0),
        wait(800))),
    # Execute step, the expression and "Lisbon is in Portugal" in OUTPUT
    Step("Execute step: the message is ready", box=(512, 48, 1024, 576), acts=(
        click("[data-test-id=node-execute-button]"), wait(2600),
        ring(OUTPUT, 2000))),
    # the three nodes and Execute workflow
    Step("Execute workflow: every node turns green", box=(512, 240, 1024, 576), acts=(
        click(NDV_CLOSE), wait(900), click(EXEC_WF), wait(1500),
        post("/webhook-test/city-info", '{"city": "Lisbon"}'), wait(2600),
        ring("[data-test-id=canvas]", 1800))),
))

N8N_MANAGE = Demo("n8n-manage", "n8n-ep1", "n8n", "n8n", steps=(
    # the HTTP Request's parameters and its OUTPUT
    Step("Open a node: what went in, what came out", box=(512, 48, 1024, 576), acts=(
        Act("dblclick", "[data-test-id=canvas-node]:has-text('HTTP Request')"), wait(1500),
        ring(INPUT, 1400), ring(OUTPUT, 1400), click(NDV_CLOSE), wait(600))),
    # the three nodes on the canvas
    Step("A red node? Open it, read the error, fix, test again",
         box=(512, 240, 1024, 576), acts=(
        ring("[data-test-id=canvas]", 2400), wait(800))),
    # the Publish workflow dialog, centred
    Step("Publish: the Production URL starts listening", box=(384, 144, 1024, 576), acts=(
        click("[data-test-id=workflow-open-publish-modal-button]"), wait(1200),
        Act("publish"), wait(1800),
        ring("[role=dialog]:has-text('Workflow published')", 2200),
        click("button:has-text('Got it')"), wait(700))),
    # the Executions list and the run's nodes
    Step("Every real run is in the Executions tab", box=(0, 48, 1024, 576), acts=(
        post("/webhook/city-info", '{"city": "Porto"}'), wait(1200),
        click("[data-test-id=radio-button-executions]"), wait(2200),
        ring("[data-test-id=execution-list], [data-test-id=executions-list]", 2000),
        wait(600))),
))

# --------------------------------------------------------------- MCP
MCP_SETUP = Demo("mcp-setup", "mcp-terminal", "terminal", "Terminal — ~", steps=(
    # the first lines of the terminal, top left
    Step("uv init hello · cd hello", box=(0, 0, 768, 432), acts=(
        term("uv init hello"), wait(700), term("cd hello"), wait(500))),
    # the packages uv installs, bottom left
    Step("uv add \"mcp[cli]\": the official Python SDK", box=(0, 432, 768, 432), acts=(
        term('uv add "mcp[cli]"', ms=40), wait(900))),
    # cat server.py and the whole file
    Step("server.py: the tiny server from the card", box=(0, 288, 1024, 576), acts=(
        term("cat server.py"), wait(1600))),
    # the npx line and the Inspector's address
    Step("Test it: npx @modelcontextprotocol/inspector", box=(0, 288, 1024, 576), acts=(
        term("npx @modelcontextprotocol/inspector"), wait(1800))),
))

MCP_INSPECTOR = Demo("mcp-inspector", "mcp-browser", "inspector", "MCP Inspector", steps=(
    # the Add server form: ID, command, arguments
    Step("Add the server: uv --directory hello run server.py",
         box=(256, 48, 1024, 576), acts=(
        wait(900), click("text=Add Servers"), wait(700),
        click("[role=menuitem]:has-text('Add manually')"), wait(1000),
        fill("[role=dialog] input[placeholder=my-server]", "hello"), wait(300),
        fill("[role=dialog] input[placeholder=npx]", "uv"), wait(300),
        Act("lines", "[role=dialog] textarea >> nth=0", "--directory\nhello\nrun\nserver.py"),
        wait(500), click("[role=dialog] button:has-text('Add')"), wait(1200))),
    # the hello server card and Connected
    Step("Connect: the Inspector starts it over stdio", box=(0, 48, 1024, 576), acts=(
        click("[aria-label='Connect or disconnect \"hello\"'] >> xpath=.."),
        Act("wait_text", "text=Connected", ms=20000), wait(900),
        ring("text=Connected >> nth=0", 1600))),
    # the Tools list and greet with its docstring
    Step("Tools: there's greet, with its docstring", box=(0, 48, 1024, 576), acts=(
        click("label:has-text('Tools')"), wait(1200),
        click("button:has-text('greet')"), wait(1200),
        ring("text=Say hello to someone by name.", 1800))),
    # the Name field, Execute Tool and the result
    Step("Call it: name = Ana → \"Hello, Ana!\"", box=(0, 48, 1024, 576), acts=(
        click("xpath=//label[contains(.,'Name')]/following::input[1]"), kind("Ana"), wait(500),
        click("button:has-text('Execute Tool')"), wait(1800),
        ring("text=Hello, Ana! >> nth=0", 2200))),
))

MCP_CONFIG = Demo("mcp-config", "mcp-editor", "editor",
                  "~/Library/Application Support/Claude/claude_desktop_config.json", steps=(
    # the file as typed, its path line whole
    Step("claude_desktop_config.json: add your server under mcpServers",
         box=(116, 0, 1024, 576), acts=(
        wait(800), Act("edit", text="MCP_DESKTOP_JSON", ms=28), wait(900))),
    # the same view: the tab's unsaved dot goes (the Saved notice, far bottom
    # right, cannot share a 1280 box with the file's path line)
    Step("Save, restart Claude for Desktop, ask it to greet someone",
         box=(116, 0, 1024, 576), acts=(
        Act("save"), wait(2200))),
))

DEMOS: dict[str, Demo] = {d.key: d for d in (
    N8N_TOUR, N8N_BUILD, N8N_API, N8N_MESSAGE, N8N_MANAGE,
    MCP_SETUP, MCP_INSPECTOR, MCP_CONFIG)}


def video_path(key: str) -> Path:
    return DEMO_DIR / f"demo-{key}.mp4"


def meta_path(key: str) -> Path:
    return DEMO_DIR / f"demo-{key}.json"


@functools.lru_cache(maxsize=32)
def _meta(key: str, mtime: float) -> dict | None:
    try:
        return json.loads(meta_path(key).read_text())
    except (OSError, ValueError):
        return None


def meta(key: str) -> dict | None:
    """The sidecar of a recording: {"seconds", "marks": [[start, end], ...],
    "urls": [...], "sha256", "bytes", "versions", "recorded_at"}."""
    try:
        mtime = meta_path(key).stat().st_mtime
    except OSError:
        return None
    return _meta(key, mtime)


def missing(keys: list[str] | tuple[str, ...]) -> str | None:
    """None when every demo named is recorded and its sidecar matches its
    steps; otherwise why not. A render never falls back to a slide."""
    for key in keys:
        demo = DEMOS.get(key)
        if demo is None:
            return f"no demo called {key!r}"
        m = meta(key)
        if not video_path(key).is_file() or m is None:
            return (f"the {key} recording is missing "
                    f"(tools/tutorial_demos/record.py --session {demo.session})")
        if len(m.get("marks", ())) != len(demo.steps):
            return f"the {key} recording has {len(m.get('marks', ()))} steps, the demo {len(demo.steps)}"
    return None


def window(key: str, step: int) -> tuple[float, float]:
    """(start, length) in seconds of step `step` in the recording."""
    a, b = meta(key)["marks"][step]
    return float(a), max(0.1, float(b) - float(a))


def address(key: str, step: int) -> str:
    """What the browser bar says during step `step` (the address recorded at
    that moment, query stripped), or the window title for a terminal or an
    editor."""
    demo = DEMOS[key]
    urls = (meta(key) or {}).get("urls") or []
    if demo.app in ("n8n", "inspector") and step < len(urls) and urls[step]:
        return urls[step]
    return demo.title


def _boxes(key: str) -> str:
    demo = DEMOS.get(key)
    return ";".join(",".join(map(str, st.box)) for st in demo.steps) if demo else ""


def stamp(keys: list[str] | tuple[str, ...]) -> str:
    """The recordings a render used and the boxes cut from them: a new
    recording, or a step's box moved, renders again."""
    raw = "|".join(f"{k}:{(meta(k) or {}).get('sha256', '')}:{_boxes(k)}"
                   for k in sorted(set(keys)))
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def fit(window_seconds: float, seconds: float) -> tuple[float, float]:
    """(speed, source seconds used) to show a recorded step in `seconds`: as
    recorded when it fits (its last frame then holds), faster up to
    MAX_SPEED when it is longer, its tail trimmed past that."""
    if window_seconds <= seconds:
        return 1.0, window_seconds
    speed = min(MAX_SPEED, window_seconds / seconds)
    return round(speed, 4), min(window_seconds, seconds * speed)
