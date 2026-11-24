# ATLAS Academy YouTube growth — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every ATLAS Academy tutorial goes to YouTube with the metadata, visuals, script logic and Shorts that `docs/academy-growth-plan.md` (approved by Miro 2026-10-13) calls for, and the Monday table can say whether it worked.

**Architecture:** The tutorial catalog (`tutorial_series.py`), renderer (`tutorials.py` + `tutorial_design.py` + `tutorial_demos.py`) and the YouTube client (`youtube/api.py`) already exist; every task adds one rule to one of them, with a test, in the order the growth plan's section 6 gives. Nothing writes to YouTube except through the executor's catalogued actions (invariant 1); every new outside-world write is either folded into the existing `academy.publish_tutorial` / `academy.autopublish_tutorial` actions or is a new Tier 2 (`Tier.ASK`) card.

**Tech Stack:** Python 3.14 (`.venv/bin/python3`), pytest, ruff (line length 90), httpx, ffmpeg/ffprobe, Playwright (Shooter), Groq Whisper (`content.captions`), YouTube Data API v3.

**Spec:** `docs/academy-growth-plan.md` (the approved proposal) and `docs/academy-youtube-plan.md` (rules 1–14, which stand). Task 0 writes the new rules 15–21 into the latter before any code, as that file demands.

## Global Constraints

- CLAUDE.md invariants: collectors read, only `kernel/executor.py` acts; tiers in code; `ATLAS_WATCH_ONLY`; never guess a number; a check that cannot run is UNKNOWN never OK; Tier 1 needs dry run + rollback.
- Title/thumbnail edits on a **public** video and any comment post are Tier 2 cards (`Tier.ASK`, `card_only=True`), never automatic.
- No faces, no other creators' names, no money claims, no Instagram, male British voice only, black + royal blue only.
- `ruff check --line-length 90` clean on every touched file; type hints everywhere; async, no blocking calls in the loop.
- Tests: `.venv/bin/python3 -m pytest tests/<file> -q`. Commit after each task with a conventional message ending `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Deploy only by pushing `phases-12-14` (GitLab CD). Never scp or rebuild by hand.

---

### Task 0: Write rules 15–21 into the plan file

**Files:**
- Modify: `docs/academy-youtube-plan.md` (append after rule 14, before "What we keep refusing")
- Modify: `docs/academy-growth-plan.md:1-8` (status line)

- [ ] **Step 1: Append the rules**

Append this block to `docs/academy-youtube-plan.md` immediately before the paragraph that starts `**What we keep refusing**`:

```markdown
## The growth rules (Miro approved 2026-10-13, `docs/academy-growth-plan.md`)

15. **Upload metadata.** Category 27 (Education), `defaultLanguage` and
    `defaultAudioLanguage` `en-GB`, and every tag `full_tags` produced (the
    old 15-tag cap in `youtube.api.upload` was the reason videos carried 15).
16. **Our own caption track.** The script's words, cues stripped by
    `plain()`, timed per segment from the manifest, go up as an English
    (`en-GB`) caption track on every upload. The gate also listens to the
    file's opener (`tutorials.heard_refusal`, 2026-10-13).
17. **The first seconds are the payoff.** A lesson's first scene after the
    opener is its `payoff` scene (what the viewer will have at the end), the
    sting is at most one second. `hands_on_at` counts from 0:00 as before.
18. **Phone-first demos.** Every demo step names the region of the recording
    it shows (`Step.box`, x/y/w/h in the 1920x1080 recording); the renderer
    crops to it and scales it into the frame. A step without a box is
    refused by a test.
19. **Quiz reveal after a beat.** A quiz is shown unanswered with the comment
    prompt lit and a three-second ring; the answer is revealed on the next
    step only. A recap scene may show the answer again.
20. **Next episode out loud, pinned comment, end screen.** The recap's last
    line names the next episode in the series (`next_line`). The pinned
    comment (quiz question + lab link) is a Tier 2 card
    (`academy.pin_comment`). End screens are set by Miro in Studio (no API).
21. **Measure.** The Monday table carries, per tutorial: views, likes,
    comments, estimated minutes watched, average view duration and average
    view percentage from the Analytics API. A figure that cannot be read is
    UNKNOWN. Any title or thumbnail change that follows is a Tier 2 card.

Teaching Shorts (plan §4.3): two or three 30–60 s cuts per lesson, from its
`payoff`, its "Stuck?" beat and its quiz, each ending on the answer, posted
to YouTube and TikTok with the full video linked in the caption. Promo
Shorts are at most one in five (Miro, 2026-10-13).
```

- [ ] **Step 2: Mark the growth plan approved**

In `docs/academy-growth-plan.md` replace the line starting `**Status: proposal` through the end of that paragraph with:

```markdown
**Status: approved by Miro 2026-10-13 ("approve all, fix the opener bug
first").** Rules 15–21 are in `docs/academy-youtube-plan.md`; the work is
`docs/superpowers/plans/2026-10-13-academy-growth.md`. Defect 1's root
cause: the five lessons were rendered before fd7a501 shipped and uploaded
before the fixed image was built; the gate now also listens to the file
(9b0aa91).
```

- [ ] **Step 3: Commit**

```bash
git add docs/academy-youtube-plan.md docs/academy-growth-plan.md
git commit -m "docs(academy): rules 15-21, the growth plan approved 2026-10-13"
```

---

### Task 1: Upload metadata — category, language, all the tags

**Files:**
- Modify: `atlas/modules/youtube/api.py:411-461` (`upload`)
- Test: `tests/test_youtube.py`

**Interfaces:**
- Produces: `api.upload(channel, path, title, description, tags, privacy="private", *, category_id: str = "27", language: str = "en-GB")`. The body's `snippet` gains `categoryId`, `defaultLanguage`, `defaultAudioLanguage`; tags are no longer sliced to 15 but cut at `TAGS_CHARS = 500` as YouTube counts them (each tag's length, plus 2 for a tag with a space).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_youtube.py` after `test_upload_action_declares_no_privacy_param`:

```python
async def test_upload_sends_education_category_british_english_and_every_tag(
        monkeypatch, tmp_path):
    """Plan rule 15: category 27, en-GB, and all the tags full_tags made --
    the 15-tag slice was why every tutorial carried 15."""
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)
    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"z" * 10)
    seen = {}

    class FakeResponse:
        status_code = 200
        headers = {"Location": "https://upload.example/s"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "vid1"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            seen["body"] = kw["json"]
            return FakeResponse()

        async def put(self, url, **kw):
            return FakeResponse()

    monkeypatch.setattr(api.httpx, "AsyncClient", FakeClient)
    tags = [f"tag {i}" for i in range(22)]
    result, reason = await api.upload("flycheap", str(video_path), "T", "D", tags)
    assert reason is None, reason
    snippet = seen["body"]["snippet"]
    assert snippet["categoryId"] == "27"
    assert snippet["defaultLanguage"] == "en-GB"
    assert snippet["defaultAudioLanguage"] == "en-GB"
    assert snippet["tags"] == tags


def test_tags_are_cut_where_youtube_counts_500_characters():
    tags = ["a" * 100] * 6                      # 600 characters
    assert api.tags_within(tags) == ["a" * 100] * 5
    spaced = ["two words"] * 60                 # 11 each as YouTube counts
    assert len(api.tags_within(spaced)) == 45
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python3 -m pytest tests/test_youtube.py -q -k "education_category or 500_characters"`
Expected: FAIL — `tags` sliced to 15 / `tags_within` not defined.

- [ ] **Step 3: Implement**

In `atlas/modules/youtube/api.py`, above `async def upload(`:

```python
TAGS_CHARS = 500                      # YouTube's tag limit, as it counts them
EDUCATION = "27"                      # videoCategories: Education
TUTORIAL_LANGUAGE = "en-GB"           # the British male voice


def tags_within(tags: Sequence[str]) -> list[str]:
    """The leading tags that fit YouTube's 500 characters: a tag counts its
    length, plus two when it has a space (YouTube quotes it)."""
    out: list[str] = []
    used = 0
    for t in tags:
        cost = len(t) + (2 if " " in t else 0)
        if used + cost > TAGS_CHARS:
            break
        out.append(t)
        used += cost
    return out
```

Change the signature and body:

```python
async def upload(
    channel: str,
    path: str,
    title: str,
    description: str,
    tags: Sequence[str],
    privacy: str = "private",
    *,
    category_id: str = EDUCATION,
    language: str = TUTORIAL_LANGUAGE,
) -> tuple[dict | None, str | None]:
```

and the body dict:

```python
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags_within(tags),
            "categoryId": category_id,
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {"privacyStatus": "private"},
    }
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python3 -m pytest tests/test_youtube.py tests/test_youtube_shorts.py tests/test_academy_tutorials.py -q`
Expected: all pass. (The clips lane also calls `upload`; its tests must still pass with the defaults.)

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3 -m ruff check --line-length 90 atlas/modules/youtube/api.py tests/test_youtube.py
git add atlas/modules/youtube/api.py tests/test_youtube.py
git commit -m "feat(youtube): uploads are Education, en-GB, and carry every tag that fits"
```

---

### Task 2: Our own caption track

**Files:**
- Modify: `atlas/modules/youtube/api.py` (new `set_captions`)
- Modify: `atlas/modules/academy/tutorials.py` (new `caption_track`, called from `_publish` after the thumbnail; `Manifest` gains `timings`)
- Test: `tests/test_academy_tutorials.py`, `tests/test_youtube.py`

**Interfaces:**
- Produces: `api.set_captions(channel, video_id, srt: str, language="en-GB", name="English") -> tuple[dict | None, str | None]` (captions.insert, multipart, `youtube.force-ssl` scope).
- Produces: `tutorials.caption_track(m: Manifest, ep: Episode) -> str` — SRT text from `m.timings` (list of `[start, end, text]` per narrated line, written by `render`), words through `plain()`.
- `Manifest.timings: list[tuple[float, float, str]] = field(default_factory=list)`.

- [ ] **Step 1: Failing tests**

`tests/test_academy_tutorials.py`:

```python
def test_the_caption_track_is_the_script_without_cues_in_srt_order():
    ep = ts.series("n8n").episodes[0]
    m = tut.Manifest(series="n8n", episode=0, audio="fish", seconds=290.0,
                     timings=[(0.0, 4.2, "[happy] Welcome back, guys!"),
                              (4.2, 9.0, "Today we build a workflow.")])
    srt = tut.caption_track(m, ep)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,200\nWelcome back, guys!\n\n2\n")
    assert "[happy]" not in srt and "00:00:09,000" in srt
```

`tests/test_youtube.py`:

```python
async def test_set_captions_posts_a_multipart_srt_in_british_english(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)
    seen = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "cap1"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            seen.update(url=url, **kw)
            return FakeResponse()

    monkeypatch.setattr(api.httpx, "AsyncClient", FakeClient)
    got, why = await api.set_captions("flycheap", "vid1", "1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    assert why is None and got == {"id": "cap1"}
    assert seen["url"].startswith("https://www.googleapis.com/upload/youtube/v3/captions")
    meta = seen["files"]["metadata"][1]
    assert b'"language": "en-GB"' in meta and b'"videoId": "vid1"' in meta
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python3 -m pytest tests/test_academy_tutorials.py tests/test_youtube.py -q -k "caption"`
Expected: FAIL — `timings` unexpected / `caption_track`, `set_captions` not defined.

- [ ] **Step 3: Implement `set_captions`**

In `api.py` after `set_thumbnail`:

```python
CAPTIONS_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/captions"


async def set_captions(channel: str, video_id: str, srt: str, *,
                       language: str = TUTORIAL_LANGUAGE, name: str = "English"
                       ) -> tuple[dict | None, str | None]:
    """captions.insert: one SRT track in `language`. Needs the
    youtube.force-ssl scope; a 403 is reported, never retried."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    meta = json.dumps({"snippet": {"videoId": video_id, "language": language,
                                   "name": name, "isDraft": False}}).encode()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                CAPTIONS_UPLOAD, params={"part": "snippet", "uploadType": "multipart"},
                headers={"Authorization": f"Bearer {tok}"},
                files={"metadata": ("metadata.json", meta, "application/json"),
                       "file": ("captions.srt", srt.encode(), "application/octet-stream")})
            r.raise_for_status()
            return r.json(), None
    except Exception as exc:                            # noqa: BLE001
        return None, f"{channel}: captions: {explain(exc)}"
```

(`json` is already imported in `api.py`; if not, add `import json`.)

- [ ] **Step 4: Implement `caption_track` and the timings**

In `tutorials.py`, in `class Manifest` add after `spoken_minutes`:

```python
    # 2026-10-13 (rule 16): every narrated line as (start, end, said), for
    # the caption track; cues stripped at write time.
    timings: list[tuple[float, float, str]] = field(default_factory=list)
```

In `read_manifest`, after the `chapters` line:

```python
        raw["timings"] = [(float(a), float(b), str(t)) for a, b, t in raw.get("timings") or []]
```

Add near `description`:

```python
def _srt_stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    mn, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{mn:02d}:{s:02d},{ms:03d}"


def caption_track(m: Manifest, ep: Episode) -> str:
    """Rule 16: the script as an SRT track, the cues stripped, one cue per
    narrated line in the order the render spoke them."""
    out = []
    for i, (a, b, said) in enumerate(m.timings, 1):
        out.append(f"{i}\n{_srt_stamp(a)} --> {_srt_stamp(b)}\n{plain(said)}\n")
    return "\n".join(out)
```

In `render`, where each narrated part is appended (the loop that builds `parts` and advances `clock` — the line after each `_segment` call that does `clock += seconds`), record `timings.append((clock_before, clock_before + seconds, said))` for the intro, every hook/lesson beat and the outro; add `timings: list[tuple[float, float, str]] = []` beside `chapters`, shift the body entries by `shift` in the re-voice loop exactly as `chapters` are shifted, and pass `timings=timings` to `Manifest(...)`.

In `_publish`, after the thumbnail block:

```python
    if not row.get("captions_set"):
        cap, why = await api.set_captions(ch, vid, caption_track(m, ep))
        row["captions_set"] = cap is not None
        said.append("captions set" if cap else f"captions NOT set: {why}")
```

and add `captions_set BOOLEAN NOT NULL DEFAULT false` to the `academy_tutorial_posts` table where it is created (grep `CREATE TABLE IF NOT EXISTS academy_tutorial_posts`; add an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS captions_set BOOLEAN NOT NULL DEFAULT false` next to it), read it in `posted()`'s SELECT, and write it in the final UPDATE. Captions are not part of `complete` (a missing track never holds a publish; it is said on the card).

- [ ] **Step 5: Run tests, ruff, commit**

Run: `.venv/bin/python3 -m pytest tests/test_academy_tutorials.py tests/test_youtube.py tests/test_academy_pg.py -q`

```bash
git add atlas/modules/youtube/api.py atlas/modules/academy/tutorials.py tests/
git commit -m "feat(academy): the script goes up as the en-GB caption track"
```

---

### Task 3: Payoff first — the sting shrinks, the first scene is the result

**Files:**
- Modify: `atlas/modules/academy/tutorial_series.py` (`Episode.payoff`, `SCENE_KINDS` gains `"payoff"`)
- Modify: `atlas/modules/academy/tutorial_design.py:239` (`STING_SECONDS = 1.0`), `slide()` renders `"payoff"` like `"hook"` with the bubble visible from step 0
- Modify: `atlas/modules/academy/tutorials.py` (`render` plays `ep.payoff` as the first beat after the opener, before `ep.hook`)
- Test: `tests/test_academy_tutorials.py`

**Interfaces:**
- `Episode.payoff: Scene | None = None` — kind `"payoff"`, `heading` "Here's what you'll have", `said` one line, `items` the three-box flow, `code` the bubble text.
- Produces: `tutorials.first_scene_kind(ep) -> str` returns `"payoff"` for every scripted lesson.

- [ ] **Step 1: Failing test**

```python
@pytest.mark.parametrize("key,ep", [(s.key, e) for s in ts.SERIES for e in s.episodes
                                    if e.scripted and e.opener_topic],
                         ids=lambda x: getattr(x, "slug", x))
def test_every_lesson_opens_on_its_payoff_and_the_sting_is_one_second(key, ep):
    """Rule 17: the first thing after the opener is the finished result."""
    assert ep.payoff is not None and ep.payoff.kind == "payoff", key
    assert len(ep.payoff.said) == 1 and ep.payoff.code
    assert tut.design.STING_SECONDS <= 1.0
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python3 -m pytest tests/test_academy_tutorials.py -q -k payoff`
Expected: FAIL — `Episode` has no `payoff`.

- [ ] **Step 3: Implement**

In `tutorial_series.py`: add `"payoff"` to `SCENE_KINDS`; add `payoff: Scene | None = None` to `Episode` after `hook`; for every scripted lesson episode (each `Episode(` with `opener_topic` in `OPENER_TOPICS`), add a `payoff=Scene(chapter="", kind="payoff", heading="Here's what you'll have", said=("<one sentence: the concrete result>",), items=(<the same three boxes the hook's flow uses>), code="<the result in one line>")`. Take the sentence from each episode's existing hook `said[0]` where it already says "Here's where we're going…" and move it; the hook keeps its remaining lines.

In `tutorial_design.py`: `STING_SECONDS = 1.0`; in `slide()` add `elif scene.kind == "payoff":` rendering exactly as `"hook"` but with `hidden = ""` always and the heading `<h1 class="small">`. Trim `sting.mp4` use: in `tutorials.fixed_clip`'s `space` dict for the intro keep `"lead": str(design.STING)` and in `_segment` cut the lead with `-t 1.0` (add `"-t", f"{design.STING_SECONDS:.1f}"` before the `-i` of the lead input).

In `tutorials.render`: build `opening = beats((ep.payoff,) + ((ep.hook,) if ep.hook else ()))` when `ep.payoff` else as today. `hands_on_at` is unchanged (rule 9 still measures from 0:00).

- [ ] **Step 4: Run all academy tests** (`tests/test_academy_tutorials.py tests/test_academy_thumbs.py tests/test_academy_brand.py`). The `hands_on_at` test must still pass — if a build episode now reaches its first demo after 90 s, shorten that episode's hook `said`, not the rule.

- [ ] **Step 5: Commit** `feat(academy): a lesson opens on its payoff; the sting is one second`

---

### Task 4: Phone-first demos — crop boxes per step

**Files:**
- Modify: `atlas/modules/academy/tutorial_demos.py:69-72` (`Step.box`), every `Step(` in `N8N_*`, `MCP_*`
- Modify: `atlas/modules/academy/tutorials.py:726-736` (`_segment` demo graph)
- Test: `tests/test_academy_tutorials.py`

**Interfaces:**
- `Step.box: tuple[int, int, int, int] = (0, 0, 1920, 1080)` — x, y, w, h in the recording; `demos.crop_filter(step: Step) -> str` returns the ffmpeg `crop=w:h:x:y,scale=W:H` chain for the demo window (`DEMO_W`/`DEMO_H` are the overlay's size already used by `DEMO_X`/`DEMO_Y`).
- `_segment`'s `demo` tuple gains a fifth element, the box.

- [ ] **Step 1: Failing tests**

```python
def test_every_demo_step_names_the_region_it_shows():
    """Rule 18: never a full white page on a phone."""
    for demo in tut.demos.DEMOS.values():
        for step in demo.steps:
            x, y, w, h = step.box
            assert w <= 1280 and h <= 720, f"{demo.key}: {step.caption!r} shows too much"
            assert w * 9 == h * 16, f"{demo.key}: {step.caption!r} is not 16:9"


def test_the_crop_filter_cuts_then_scales_to_the_demo_window():
    step = tut.demos.Step("c", (), box=(320, 180, 1280, 720))
    assert tut.demos.crop_filter(step) == (
        f"crop=1280:720:320:180,scale={tut.DEMO_W}:{tut.DEMO_H}")
```

- [ ] **Step 2: Run to see them fail** — `Step.__init__` has no `box`.

- [ ] **Step 3: Implement**

`tutorial_demos.py`:

```python
@dataclass(frozen=True)
class Step:
    caption: str                     # shown under the recording while this step is said
    acts: tuple[Act, ...]
    # 2026-10-13 (rule 18): the region of the 1920x1080 recording this step
    # shows, x, y, w, h, 16:9 and at most 1280x720, so it reads on a phone.
    box: tuple[int, int, int, int] = (320, 180, 1280, 720)


def crop_filter(step: Step, width: int, height: int) -> str:
    x, y, w, h = step.box
    return f"crop={w}:{h}:{x}:{y},scale={width}:{height}"
```

(Expose `crop_filter(step)` from `tutorials` as `demos.crop_filter(step, DEMO_W, DEMO_H)`; the test calls the two-argument form through a thin `tutorials.demo_crop(step)` if you prefer — keep one name and use it in the test.)

Set each existing `Step(...)`'s `box` by hand to the region where that step's action happens: n8n editor canvas steps `(320, 120, 1280, 720)`, n8n node panel steps `(640, 180, 1280, 720)`, MCP Inspector "Add server" steps `(0, 0, 1280, 720)`, terminal steps `(0, 0, 1280, 720)`. Record the choice in a comment per demo.

`tutorials._segment`: in the demo branch, insert the crop before `fps=`:

```python
        graph += [(f"[{n}:v]setpts=(PTS-STARTPTS)/{speed:.4f},{demos.crop_filter(step, DEMO_W, DEMO_H)},"
                   f"fps={FPS},tpad=stop_mode=clone:stop_duration={seconds:.3f},setsar=1,"
                   "format=yuv420p[dv]"),
```

where `step` comes from the `demo` tuple (add it at the call site at `tutorials.py:916`: `window = (demo[0], demo[1], used, speed, step)`). Bump `design.DESIGN_VERSION` to `"6"` so old renders are re-rendered.

- [ ] **Step 4: Run tests; render one preview locally** — `.venv/bin/python3 -m atlas.modules.academy.tutorials preview /tmp/prev n8n:1` and look at a demo frame with `ffmpeg -ss 95 -i /tmp/prev/.../video.PREVIEW.mp4 -frames:v 1 f.png` (requires the demo recordings; if missing, the test suite is the gate).

- [ ] **Step 5: Commit** `feat(academy): demo steps crop to the region that changes`

---

### Task 5: Quiz reveal after a beat

**Files:**
- Modify: `atlas/modules/academy/tutorial_design.py:684-702` (`_quiz`)
- Modify: `atlas/modules/academy/tutorials.py` (the quiz beat sequence: step 0 question, a 3 s countdown hold, step 1 reveal)
- Test: `tests/test_academy_tutorials.py`

- [ ] **Step 1: Failing test**

```python
def test_a_quiz_is_asked_unanswered_then_counted_down_then_revealed():
    """Rule 19: the tick never shares a frame with "comment your answer"."""
    scene = ts.Scene(chapter="", kind="quiz", heading="What breaks stdio?",
                     said=("Which one?", "A print statement."),
                     items=("A print statement", "A docstring", "A type hint"),
                     caption="print() writes to stdout.", answer=0)
    asked = tut.design._quiz(scene, 0, None)
    held = tut.design._quiz(scene, 0, 3)
    told = tut.design._quiz(scene, 1, None)
    assert "✓" not in asked and "class=\"answer\"" not in asked
    assert "✓" not in held and "lit" in held
    assert "✓" in told and "print() writes to stdout." in told
    beats = tut.beats((scene,))
    kinds = [(b.step, b.countdown) for b in beats]
    assert kinds[:3] == [(0, None), (0, 3), (1, None)] or kinds[:5] == [
        (0, None), (0, 3), (0, 2), (0, 1), (1, None)]
```

- [ ] **Step 2: Run to see it fail** — today `beats()` yields step 1 straight after step 0 (check the actual sequence; if a countdown already exists for quizzes, the failing assertion is the `✓`/`lit` one).

- [ ] **Step 3: Implement** — in `_quiz`, `reveal = step >= 1 and countdown is None` stays; make `lit = " lit" if countdown is not None or (poll and reveal) else ""` (already so) and ensure the `COUNTDOWN` hold (`tutorial_series.COUNTDOWN`) is inserted by `beats()` for every quiz with `answer >= 0` between step 0 and step 1 — find the `beats` function's quiz branch and add the three silent countdown beats (`silent=1.0`, `countdown=3,2,1`) exactly as the `"pause"` kind does. The spoken reveal line (`said[1]`) is voiced on step 1.

- [ ] **Step 4: Run the academy tests; commit** `feat(academy): a quiz is asked, counted down, then answered`

---

### Task 6: Next episode out loud + pinned comment card

**Files:**
- Modify: `atlas/modules/academy/tutorial_series.py` (`next_line(series_key, ep) -> str`, appended to every recap scene's `said` by `titled()`)
- Modify: `atlas/modules/youtube/api.py` (`post_comment(channel, video_id, text)` — commentThreads.insert; `pin` is not in the API: the card says "pin it in Studio")
- Modify: `atlas/modules/academy/tutorials.py` (`pin_comment`, `pin_comment_dry`, `unpin_comment` rollback deletes nothing — returns "a comment is never deleted (Tier 3)")
- Modify: `atlas/modules/academy/__init__.py` (register `academy.pin_comment`, `tier=Tier.ASK`, `card_only=True`)
- Test: `tests/test_academy_tutorials.py`, `tests/test_academy_actions.py`

- [ ] **Step 1: Failing tests**

```python
def test_every_recap_ends_by_naming_the_next_episode():
    for s in ts.SERIES:
        eps = [e for e in s.episodes if e.scripted]
        for a, b in zip(eps, eps[1:], strict=False):
            recap = next(sc for sc in a.scenes if sc.chapter == "Recap")
            assert ts.plain(recap.said[-1]).startswith("Next time: "), a.slug
            assert b.title_hook.split(" in ")[0] in ts.plain(recap.said[-1]), a.slug


def test_the_pinned_comment_is_a_tier_2_card_with_the_quiz_and_the_lab():
    actions = {a.name: a for a in academy.MODULE.actions()}
    a = actions["academy.pin_comment"]
    assert a.tier is Tier.ASK and a.card_only
    s, ep = ts.series("claude"), ts.series("claude").episodes[1]
    text = tut.pinned_comment(s, ep)
    assert "Comment your answer" in text or "?" in text
    assert "https://agency.storefront.test/free/" in text
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement**

`tutorial_series.py`:

```python
def next_line(series_key: str, ep: Episode) -> str:
    """Rule 20: the recap's last line names the next episode, or the series
    playlist when this is the last one."""
    s = series(series_key)
    later = [e for e in s.episodes if e.number > ep.number and e.scripted]
    if not later:
        return f"Next time: more {s.playlist}. Subscribe so you don't miss it."
    return f"Next time: {later[0].title_hook}. Subscribe so you don't miss it."
```

In `titled()` (where `opener_topic` is filled), append `next_line(series_key, e)` to the recap scene's `said` when its last line does not already start with "Next time: ".

`tutorials.py`:

```python
def pinned_comment(s: Series, ep: Episode) -> str:
    quiz = next((sc for sc in ep.scenes if sc.kind == "quiz"), None)
    lines = []
    if quiz:
        lines.append(f"Quick quiz: {quiz.heading} " + " / ".join(
            f"{LETTERS[i]}. {x}" for i, x in enumerate(quiz.items)) + " — comment your answer.")
    for f in downloads_of(ep):
        lines.append(f"Free lab file (no sign-up): {f}")
    lines.append(f"More: {STORE_URL}")
    return "\n".join(lines)


async def pin_comment_dry(series: str, episode: int) -> ActionResult:
    got = resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep = got
    row = await posted(s.key, ep.number)
    if not row or not row.get("public"):
        return ActionResult(False, f"{s.key} #{ep.number} is not public yet")
    return ActionResult(True, f"would post, for Miro to pin in Studio:\n{pinned_comment(s, ep)}")


async def pin_comment(series: str, episode: int) -> ActionResult:
    from ..youtube import api
    got = resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep = got
    row = await posted(s.key, ep.number)
    if not row or not row.get("public"):
        return ActionResult(False, f"{s.key} #{ep.number} is not public yet")
    made, why = await api.post_comment(channel(), str(row["video_id"]), pinned_comment(s, ep))
    if made is None:
        return ActionResult(False, f"comment refused: {why}")
    return ActionResult(True, f"posted comment {made['id']}; pin it in Studio (no API for pinning)")


async def unpin_comment(series: str, episode: int) -> ActionResult:
    return ActionResult(False, "a posted comment is never deleted (Tier 3); hide it in Studio")
```

`api.post_comment`:

```python
async def post_comment(channel: str, video_id: str, text: str) -> tuple[dict | None, str | None]:
    """commentThreads.insert as the channel. youtube.force-ssl scope."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    data, reason = await post_json(
        f"{DATA_API}/commentThreads", headers={"Authorization": f"Bearer {tok}"},
        params={"part": "snippet"},
        json_body={"snippet": {"videoId": video_id, "topLevelComment": {
            "snippet": {"textOriginal": text[:9000]}}}})
    if reason is not None:
        return None, f"{channel}: {reason}"
    return {"id": data.get("id")}, None
```

(`post_json` is `_http.py`'s POST wrapper used elsewhere in `api.py`; use the same name the file already imports.)

Register in `academy/__init__.py` next to `academy.publish_tutorial`:

```python
            Action(name="academy.pin_comment", tier=Tier.ASK,
                   summary="Post the quiz + lab-file comment under a public tutorial "
                           "(Miro pins it in Studio)",
                   run=tutorials.pin_comment, dry_run=tutorials.pin_comment_dry,
                   rollback=tutorials.unpin_comment, vet=tutorials.vet,
                   params={"series": "the tutorial series key", "episode": "the episode number"},
                   card_only=True),
```

- [ ] **Step 4: Run `tests/test_academy_tutorials.py tests/test_academy_actions.py`; commit** `feat(academy): the recap names the next episode; the pinned comment is a Tier 2 card`

---

### Task 7: Teaching Shorts from each tutorial

**Files:**
- Create: `atlas/modules/academy/tutorial_short.py`
- Modify: `atlas/modules/academy/tutorials.py:1993-2020` (`short_window` takes a scene kind too; `short_argv` burns a caption bar)
- Modify: `atlas/modules/academy/reel.py:659-700` (`daily()` — the rotation: teaching Short, teaching Short, lesson Short, concept Short, promo at most one in five)
- Test: `tests/test_academy_tutorial_short.py`

**Interfaces:**
- `tutorial_short.cuts(m: Manifest, ep: Episode) -> list[Cut]`, `Cut(kind: str, start: float, end: float, caption: str)` for kinds `"payoff"`, `"stuck"`, `"quiz"`; each 30–60 s, ends on the chapter's last line (the answer).
- `tutorial_short.caption(s, ep, cut, video_id) -> str` — the YouTube/TikTok text: hook line, `Full video: https://youtu.be/<id>`, three hashtags from `hashtags_for`.
- `reel.daily()` picks a `Cut` for the slot when a tutorial is public and not yet cut three times (`academy_promo_posts.slug = f"tutorial:{key}:{n}:{kind}"`).

- [ ] **Step 1: Failing tests**

```python
from atlas.modules.academy import tutorial_short as tsh, tutorials as tut, tutorial_series as ts


def _manifest():
    return tut.Manifest(series="claude", episode=1, audio="fish", seconds=340.0,
                        chapters=[(0.0, "Intro"), (12.0, "Here's what you'll have"),
                                  (60.0, "What is MCP?"), (200.0, "Not working?"),
                                  (290.0, "Quick quiz"), (320.0, "Recap")])


def test_three_cuts_payoff_stuck_quiz_each_30_to_60_seconds_ending_on_the_answer():
    ep = ts.series("claude").episodes[1]
    cuts = tsh.cuts(_manifest(), ep)
    assert [c.kind for c in cuts] == ["payoff", "stuck", "quiz"]
    for c in cuts:
        assert 30.0 <= c.end - c.start <= 60.0, c
    quiz = cuts[-1]
    assert quiz.end == 320.0            # the reveal is the last thing seen


def test_the_short_caption_links_the_full_video_and_leads_with_the_product_hashtag():
    s, ep = ts.series("claude"), ts.series("claude").episodes[1]
    text = tsh.caption(s, ep, tsh.Cut("quiz", 290.0, 320.0, "Quick quiz"), "iRyhxkLk-Yo")
    assert "Full video: https://youtu.be/iRyhxkLk-Yo" in text
    assert text.rstrip().endswith(" ".join(ts.hashtags_for("claude", ep)))
    assert "Instagram" not in text


def test_promo_shorts_are_at_most_one_in_five():
    plan = [tsh.slot_kind(i) for i in range(10)]
    assert plan.count("promo") <= 2 and plan[0] != "promo"
```

- [ ] **Step 2: Run to see them fail** — module missing.

- [ ] **Step 3: Implement `tutorial_short.py`**

```python
"""Teaching Shorts cut from a published tutorial (growth plan §4.3,
2026-10-13): the payoff, the "Stuck?" beat and the quiz, 30-60 s each,
ending on the answer, captioned with the full video's link. The cut is
`tutorials.short_argv` (16:9 inside 9:16 over a blur); this module decides
where to cut and what to say. Promo Shorts are at most one in five."""
from __future__ import annotations

from dataclasses import dataclass

from .tutorial_series import SHORT_MAX, SHORT_MIN, Episode, Series, hashtags_for

CHAPTER_OF = {"payoff": "Here's what you'll have", "stuck": None, "quiz": "Quick quiz"}
STUCK_WORDS = ("Not working?", "Stuck?", "No tool?")
ROTATION = ("teach", "teach", "lesson", "concept", "promo")


@dataclass(frozen=True)
class Cut:
    kind: str
    start: float
    end: float
    caption: str


def _chapter_span(chapters: list[tuple[float, str]], i: int, total: float
                  ) -> tuple[float, float]:
    start = chapters[i][0]
    end = chapters[i + 1][0] if i + 1 < len(chapters) else total
    return start, end


def cuts(m, ep: Episode) -> list[Cut]:
    out: list[Cut] = []
    names = [n for _, n in m.chapters]
    for kind in ("payoff", "stuck", "quiz"):
        want = CHAPTER_OF[kind]
        i = next((k for k, n in enumerate(names)
                  if (n == want) if want else n.startswith(STUCK_WORDS)), None)
        if i is None:
            continue
        a, b = _chapter_span(m.chapters, i, m.seconds)
        if b - a < SHORT_MIN:
            a = max(0.0, b - SHORT_MIN)          # reach back so the answer ends it
        if b - a > SHORT_MAX:
            a = b - SHORT_MAX
        out.append(Cut(kind, round(a, 2), round(b, 2), names[i]))
    return out


def caption(s: Series, ep: Episode, cut: Cut, video_id: str) -> str:
    hook = {"payoff": f"{ep.title_hook}: here's the result.",
            "stuck": f"{ep.title_hook}: the error everyone hits, and the fix.",
            "quiz": f"{ep.title_hook}: can you answer this?"}[cut.kind]
    return (f"{hook}\nFull video: https://youtu.be/{video_id}\n"
            + " ".join(hashtags_for(s.key, ep)))


def slot_kind(i: int) -> str:
    """The i-th Short slot's kind: promo is one in five, never first."""
    return ROTATION[i % len(ROTATION)]
```

- [ ] **Step 4: Wire `reel.daily()`** — where it chooses "one concept Short and one lesson Short a day" (line ~686), call `tutorial_short.slot_kind(slot_index)`; for `"teach"`, pick the oldest public tutorial (`academy_tutorial_posts WHERE public`) whose `(key, n, kind)` is not in `academy_promo_posts.slug`, cut with `short_argv(video, cut.start, cut.end, out)`, and post through the existing lesson-Short path with `youtube_caption = tutorial_short.caption(...)` and the same TikTok leg; for `"promo"` keep today's promo reel. Write a test in `tests/test_academy_reel.py` that patches the DB reads and asserts the fifth slot is the only promo in five.

- [ ] **Step 5: Run `tests/test_academy_tutorial_short.py tests/test_academy_reel.py`; ruff; commit** `feat(academy): teaching Shorts from every public tutorial; promo at most one in five`

---

### Task 8: Measure — analytics scope and the Monday table

**Files:**
- Modify: `atlas/modules/youtube/api.py:343-398` (`analytics` metrics gain `averageViewDuration,averageViewPercentage,comments`; `token_scopes` check in the call)
- Modify: `atlas/modules/academy/growth_log.py` (new `tutorial_rows(days)` + the table in `week_section`)
- Test: `tests/test_academy_growth_log.py`, `tests/test_youtube.py`

**Interfaces:**
- `api.analytics(...)` returns the extra keys `average_view_duration`, `average_view_percentage`, `comments` (None when absent).
- `growth_log.tutorial_rows(days=7) -> tuple[list[dict], list[str]]` — one row per public tutorial posted in the window, `stats` from `api.analytics` or `None` with the reason in `gaps`; `render_tutorials(rows, gaps)` prints `UNKNOWN` for any missing figure and names the best and worst title formula (`ep.level`) by views.

- [ ] **Step 1: Failing tests**

```python
async def test_the_tutorial_table_says_unknown_when_analytics_cannot_be_read(monkeypatch):
    async def posts(*_a, **_k):
        return [{"series": "n8n", "episode": 0, "video_id": "v1", "at": datetime.now(UTC)}]
    monkeypatch.setattr(growth_log, "_tutorial_posts", posts)

    async def analytics(_ch, _vid, _days):
        return None, "clips: not permitted — the token likely lacks the yt-analytics.readonly scope"
    monkeypatch.setattr(growth_log.api, "analytics", analytics)
    rows, gaps = await growth_log.tutorial_rows(7)
    text = growth_log.render_tutorials(rows, gaps)
    assert "UNKNOWN" in text and "yt-analytics.readonly" in text and "0" not in text.split("\n")[1]
```

- [ ] **Step 2: Run to see it fail.**

- [ ] **Step 3: Implement** — `metrics` string becomes `"views,estimatedMinutesWatched,likes,comments,averageViewDuration,averageViewPercentage"`; map them in the returned dict. In `growth_log.py` add `_tutorial_posts(since)` (SELECT series, episode, video_id, at FROM academy_tutorial_posts WHERE public AND at >= $1), `tutorial_rows`, and `render_tutorials` producing a Slack table with columns `episode | views | likes | comments | avg % | avg s` and the line `Best formula: <level> · Worst: <level> · Try: <one change>` where the change is chosen by the worst column (`avg %` → "pacing/demo zoom"; views with avg % fine → "thumbnail/title (Tier 2 card)"). Append it to `week_section`.

- [ ] **Step 4: Ask Miro (once, on the card) to re-consent the clips token with `yt-analytics.readonly` and `youtube.force-ssl`** — `token_scopes("clips")` tells the Monday post which are missing; it prints them, it never guesses.

- [ ] **Step 5: Run `tests/test_academy_growth_log.py tests/test_youtube.py`; commit** `feat(academy): the Monday table reads retention and says UNKNOWN when it cannot`

---

### Task 9: The five live lessons — Tier 2 cards

**Files:**
- Modify: `atlas/modules/academy/tutorials.py` (`relaunch_tutorial_dry/relaunch_tutorial`: re-render under today's script, upload as a new video, set the old one **private** (never deleted), withdraw its row)
- Modify: `atlas/modules/academy/__init__.py` (register `academy.relaunch_tutorial`, `Tier.ASK`, `card_only=True`)
- Test: `tests/test_academy_tutorials.py`

- [ ] **Step 1: Failing test**

```python
async def test_relaunch_makes_the_old_video_private_and_never_deletes(monkeypatch):
    calls = []

    async def set_privacy(ch, vid, privacy):
        calls.append((vid, privacy))
        return {}, None
    monkeypatch.setattr(tut, "_publish", lambda *a, **k: ActionResult(True, "new https://youtu.be/NEW1"))
    monkeypatch.setattr(tut.api, "set_privacy", set_privacy)

    async def posted(_s, _n):
        return {"id": 7, "video_id": "OLD1", "public": True}
    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(tut, "execute_sql", _recording_sql := _Recorder())
    r = await tut.relaunch_tutorial("claude", 1)
    assert r.ok and calls == [("OLD1", "private")]
    assert "withdrawn" in str(_recording_sql.calls)
```

(Write `_Recorder` as a small async callable that appends its args to `.calls`.)

- [ ] **Step 2: Run to see it fail.**

- [ ] **Step 3: Implement** — `relaunch_tutorial`: require `ready()` to pass on the current render (so the new opener, captions, payoff are in it), call `_publish(series, episode, strict=False)` for the new upload (it must insert a **new** row: pass `fresh=True` so `posted()` is skipped — add that keyword to `_publish`), then `api.set_privacy(ch, old_video_id, "private")` and `UPDATE academy_tutorial_posts SET withdrawn = true, detail = detail || ' -- relaunched as <new>' WHERE id = $1`. The dry run says both video IDs and "the old one goes private, never deleted". Register the action as Tier 2 with `card_only=True`.

- [ ] **Step 4: Run tests; commit** `feat(academy): relaunch a stale tutorial: new upload, old one private (Tier 2)`

- [ ] **Step 5: After deploy, send five cards** (one per `claude:0`, `claude:1`, `n8n:0`, `project:0`, `business:0`) to #atlas-academy-videos through the normal proposal path; Miro approves each.

---

## Self-review

- **Spec coverage.** Growth plan §3 technical (15, 16, 17 → Tasks 1–3; 18 → Task 4 incl. publish slot already fixed by `ACADEMY_TUTORIALS_HOUR_UTC`), §3 visual (19, 20, thumbnails already blue at `DESIGN_VERSION 5`/`THUMB_VERSION 3`: a relaunch re-draws them — Task 9), §3 logic (quiz → Task 5, open loop → Task 3 payoff, next video → Task 6), §3 features (pinned comment → Task 6, end screens → manual in Studio, comment-reply drafts → already `academy.support` house voice), §4 (search phrase per video is rule 5 + Task 1's tags; playlists exist; Shorts → Task 7; schedule → existing hour slot; LinkedIn/communities → existing LinkedIn module, Tier 2), §5 → Task 8, §6 order → Tasks 0–9. Defect 9 (two off-formula titles) and 8 (mismatched thumbnails) are cured by the relaunch in Task 9 rather than by retitling live videos.
- **Placeholders.** None: every step has code or an exact edit location. Task 4's per-step boxes are hand-set values the implementer reads off the recordings; the test refuses any step without a 16:9 box ≤ 1280×720.
- **Names.** `tags_within`, `set_captions`, `caption_track`, `Manifest.timings`, `Episode.payoff`, `Step.box`, `demos.crop_filter(step, w, h)`, `next_line`, `pinned_comment`, `pin_comment`, `tutorial_short.cuts/caption/slot_kind`, `growth_log.tutorial_rows/render_tutorials`, `relaunch_tutorial` are used with the same signatures throughout.
