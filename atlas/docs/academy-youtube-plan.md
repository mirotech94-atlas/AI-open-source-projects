# ATLAS Academy on YouTube — the title, tag and timing plan

Miro, 2026-10-11: "search for new titles for each video to keep it attractive,
add under 4 / 5 / 10 minutes depending on the video so people can learn a lot
in a set time; look at other videos that do the same thing, improve from
them, their tags, how they gain viewers; make a plan and stick to it."

This is the plan. The catalog (`atlas/modules/academy/tutorial_series.py`) and
the renderer follow it; a change here is a change there, with a test.

## What the top videos do (YouTube Data API, read 2026-10-11)

Most-viewed videos since 2025-10 for our eight topics
(`.claude/competitors-2026-10-11.json`):

- A time promise in the title is the single most repeated pattern among the
  winners: "Learn 97% of Claude in Under 16 Minutes" (2.5M), "Learn AI Agents
  in 13 Minutes" (2.8M), "How to learn AI in 18 minutes" (3.0M), "800+ hours
  of Learning Claude Code in 8 minutes" (1.1M), "Build your First AI Agent in
  1 Minute" (1.1M), "What is RAG? Explained in 15 Minutes" (1.1M), "MCP In 26
  Minutes" (262k).
- Short, plain "what is" videos win on the basics: "What is Claude Code?" (2
  min, 13M), "Getting started with Claude.ai" (5 min, 2.4M), "How MCP actually
  works" (7 min, 372k).
- The other hooks that repeat: "You're not behind (yet)", "for normal people",
  "Everything you need to know", "X vs Y: do you really need X?", "Stop
  learning X… learn THIS instead", "the hard way / use this instead".
- Durations cluster at 8–20 min for builds and 2–7 min for explainers. Our
  4–10 minute lane sits exactly where "learn it fast" titles are credible.
- Tags that recur across the winners (count of videos carrying them, top 40):
  ai, n8n, chatgpt, ai agents, claude, claude code, automation, ai tools,
  llm, ai automation, mcp, n8n tutorial, n8n ai agent, claude code tutorial,
  anthropic, claude ai, agentic ai, model context protocol, n8n automation,
  ai automation agency, no code, workflow, process automation, walkthrough,
  guide, chatbot, nvidia, machine learning.
- Padding we do not copy: other creators' names, sponsors, money-claim tags,
  unrelated model names stuffed for reach.

## The rules

1. **Every title states its time, and the time is true.** The number is the
   whole video's length rounded UP to the next minute, measured from the
   finished render, never from an estimate: 4:01 → "5 minutes"; 2:58 →
   "3 minutes". The title is finalised at render time from the measured
   length; a title whose minutes are under the real length is refused.
2. **One formula per level**, filled from the episode's hook:
   - Level 0 (basics): `What is <X>? Explained in <N> Minutes (for total beginners)`
   - Level 1 (beginner): `Build Your First <X> in <N> Minutes (step by step)`
   - Level 2 (intermediate): `Learn <X> in <N> Minutes`
   - Level 3 (advanced): `<X> Explained in <N> Minutes: <benefit>`
   - Welcome / channel: no number; the hook carries it.
   An episode that explains rather than builds uses the Learn formula
   whatever its level ("Build Your First" is a promise the video must keep):
   today project #1, agents #2 and local #1 (`EXPLAINERS` in
   `tutorial_series.py`), and n8n #3 (2026-10-16, managing workflows). An
   episode written in `tutorial_scripts/` carries it itself as
   `title_formula=2`; that field, on the titled episode, is the source of
   truth for which formula applies.
   A series may add one proven hook in front ("You're not behind (yet):"),
   never two, never a money claim, never "secret", never a name we don't own.
3. **The video opens on the finished result** (Miro, 2026-10-17;
   `docs/academy-video-system/episode-structure.md` "Opening (0–20 s)",
   `narration.md`). 0–6 s: the result on screen from frame 0 (ResultTitle),
   its badge (`Episode.result`, three words or fewer; `success` only when
   `Episode.verified`) rising at frame 12, one trace into the result at 3 s,
   under "Here is the result: <result>." 6–7 s: the 1 s BrandSting on flat
   navy. From 7 s: the scope line, "In this lesson: <topic>. You'll need …
   Start with …" (`opening_lines`, from the episode's own fields). No
   welcome, no "guys", no name, no presenter clip, and no spoken minutes: the
   title carries the time, measured (rules 1–2). The publish gate listens to
   the file: a lesson must be heard saying "here is the result" and no
   welcome word (`tutorials.heard_refusal`); a file that cannot be heard is
   held. Only the channel welcome still says "Welcome, guys, to my new
   channel! ...". Superseded: the 2026-10-12 "Welcome back, guys! ... in
   under <N> minutes" opener and Miro's on-camera intro (2026-10-13).
4. **Thumbnail carries the time badge** ("5 MIN") as a royal-blue badge, big
   text of 2–4 words, one 3D focal object, the level chip.
5. **(Superseded 2026-10-18 by rules 27–28.) Tags: 20–25, under 500 characters**, in this order: the series head
   terms (from the list above), the episode's exact search phrases, the
   level word (beginner / intermediate / advanced), the year, "ATLAS
   Academy". Hashtags: three, the first is the product (#n8n, #ClaudeAI,
   #AIAgents, #DGXSpark, #Automation).
6. **Description** opens with the level and the time ("Level 0 · Basics ·
   5 minutes"), then one line of what you'll be able to do, the comment
   question, chapters from 0:00, the links, agency.storefront.test/academy/, the hashtags,
   and the AI label last.
7. **Measure, then adjust, weekly.** Every Monday ATLAS reads each video's
   views, likes and comments from the Data API (CTR is not exposed by the
   API) and posts a short table to #atlas-academy-videos: the best and
   worst title formula of the week and one change to try. A title or
   thumbnail change on a public video is a Tier 2 card, never automatic.
8. **Stick to it.** The formulas live in code with this file's date; a
   title that does not match its level's formula fails a test.

## What the reference tutorials added (2026-10-11)

Miro, 2026-10-11, pointing at the searches "how to use n8n", "claude
tutorials", "hermes agent", "agentic ai" and "n8n labs": "don't mock them
but add improvements to your own and learn from them". The evidence is in
`docs/academy-reference-learnings.md` (128 videos, YouTube Data API,
`.claude/reference-videos-2026-10-11.json`). Rules 1-8 stand; these follow.

9. **The first hands-on step within 90 seconds.** In an episode whose title
   promises a build (the Level 1 formula), the first scene where the viewer
   does something (a live demo, steps, code or a lab) starts within 90 s of
   0:00, counting the result, the sting and the scope line, at the
   cautious voice pace (`tutorials.hands_on_at`, `HANDS_ON_SECONDS`). The
   reference tutorials of 20 minutes or less reach theirs at a median of
   82 s. Show first, explain right after: Claude #1 now shows the server
   before the host / client / server diagram, Claude #2 the command before
   the scope quiz.
10. **Say what you need.** The opening's scope line says it out loud
    (2026-10-17: "You'll need …", or "Nothing to install …" for an
    explainer; the hook no longer plays), and the description
    carries a "What you need:" line under the summary (`Episode.needs`).
    Only 4 of 118 reference descriptions did this. It applies to every
    episode written or revised from 2026-10-11; project and business get
    it at their next revision.
11. **Every n8n lesson ends with a Lab** before the recap: one chapter, four
    beats, one narrated line each (`lab_scene`, kind "lab"):
    - **Do**: import the lab file and run the exercise;
    - **Expect**: the result you should see;
    - **Stuck?**: the common error and its fix;
    - **Challenge**: one more step, with no answer shown.

    The opening's scope line promises the lab (2026-10-17). A lab uses
    only credential-free nodes (a trigger, the free no-key Open-Meteo
    search, Edit Fields), so an import runs as it is.
12. **Lab files are free downloads from the store itself:**
    `https://agency.storefront.test/free/<file>`.
    - **Where they come from.** `tutorial_series.LAB_FILES` holds them.
      `site.render` writes them, so the store republishes them after a
      deploy. They are never on a third-party host, behind an email gate, or
      in a "community".
    - **What the description says.** It lists them under "Free lab files (no
      sign-up)".
    - **The publish gate.** A video whose description links a lab file
      waits until the live store serves that file byte for byte
      (`tutorials.downloads_refusal`). A render of an older script is
      refused, so a description never names a lab the video doesn't show.
13. **Name the common error where it happens.** Every lab has its
    "Stuck?" beat. Every build episode written or revised from 2026-10-11
    names at least one failure and its fix: a red node in n8n #1, "No tool?
    Check the path" in Claude #1, the "Not working?" checklist in Claude #2,
    and the lab's Stuck? beat in n8n #2.
14. **A person talking, with feelings** (Miro, 2026-10-12: "don't make
    him sound like a robot", "make it like a human talking, not an AI").
    Contractions, short sentences, "you". Now and then a rhetorical question
    or a light, generic aside: never at the viewer's expense, never a made-up
    story about a client or a number. The tutorials use Fish `s2.1-pro`
    (`ACADEMY_TUTORIALS_FISH_MODEL`, the same British male voice), which
    reads inline cues. The cues come from a fixed list
    (`tutorial_series.CUE_WORDS`): a `[happy]`/`[excited]` opener, `[excited]`
    on a win ("And there it is."), `[chuckling]` after a joke, `[sighing]` on
    a pain point, `[emphasis]` on the key word, `[break]` before a reveal.
    Use at most one cue per line and one per two lines across an episode, at
    least four per episode, and no laugh in a safety or money scene.
    `plain()` strips the cues from everything shown, counted or timed. An S1
    voice and the `say -v Daniel` preview get the words only. These are
    banned from every line: "delve", "in today's fast-paced world",
    "seamless", "leverage", "unlock", "game-changer", "it's important to
    note", "let's dive in" and "in conclusion".

## The growth rules (Miro approved 2026-10-13, `docs/academy-growth-plan.md`)

15. **Upload metadata.** Category 27 (Education), `defaultLanguage` and
    `defaultAudioLanguage` `en-GB`, and every tag `full_tags` produced (the
    old 15-tag cap in `youtube.api.upload` was the reason videos carried 15).
16. **Our own caption track.** The script's words, cues stripped by
    `plain()`, timed per segment from the manifest, go up as an English
    (`en-GB`) caption track on every upload. The gate also listens to the
    file's opener (`tutorials.heard_refusal`, 2026-10-13).
17. **The first seconds are the payoff.** Since 2026-10-17 the payoff's
    result is the opening itself (rule 3): on screen from frame 0, its
    chapter at 0:00, the 1 s sting at 6–7 s, never before the result. The
    payoff and hook scenes no longer play after it. `hands_on_at` counts from
    0:00 as before.
18. **Phone-first demos.** Every demo step names the region of the recording
    it shows (`Step.box`, x/y/w/h in the 1920x1080 recording); the renderer
    crops to it and scales it into the frame. A step without a box is
    refused by a test.
19. **Quiz reveal after a beat.** A quiz is shown unanswered with the comment
    prompt lit and a three-second ring; the answer is revealed on the next
    step only. A recap scene may show the answer again.
20. **Next episode out loud, pinned comment, end screen.** The recap's last
    line names the next episode in the series (`next_line`); since
    2026-10-17 that sentence is said over the EndCard. The EndCard (Miro,
    2026-10-17; `docs/academy-video-system/components/EndCard`) is the last
    15 s or more, to the last frame, on flat navy: "You now have <result>.",
    the next lesson (the next scripted episode's title up to its measured
    minutes, else the series playlist; never an invented title) and why it
    follows, and "Practice files: agency.storefront.test/academy/ (link in the
    description)" only when the lesson has lab files. The right 640 px stay
    empty: no Subscribe button, no painted thumbnail, nothing that loops.
    Miro's outro clip and "Thanks for watching" are gone. The pinned comment
    (quiz question + lab link) is a Tier 2 card (`academy.pin_comment`). End
    screens are set by Miro in Studio (no API).
21. **Measure.** The Monday table carries, per tutorial: views, likes,
    comments, estimated minutes watched, average view duration and average
    view percentage from the Analytics API. A figure that cannot be read is
    UNKNOWN. Any title or thumbnail change that follows is a Tier 2 card.
22. **Background music.** Music plays only under the 1 s brand sting and
    the end card; explanations are voice only (Miro, 2026-10-17;
    docs/academy-video-system/narration.md, "Delivery": "ducked to nothing
    beneath explanations"). The bed is the same quiet synthesized one
    (`tutorial_music`), at −16 dB inside its windows (Miro, 2026-10-13:
    "make sure it isn't too loud"), ducked by the voice where narration
    overlaps a window, faded over the 300 ms `title-rise` token just inside
    each window edge, and multiplied by zero everywhere else. The sting's
    window is 6–7 s and the end card's is the EndCard's own span to the end
    (2026-10-17; before then the sting opened the file and the card was the
    3D one after Miro's clip, or the narrated ending); the windows are
    recorded in the manifest. A teaching Short cut from such a render
    carries no music: its cut stops before the end card's window. Shorts
    cut from renders made before 2026-10-17 (no windows in the manifest)
    still carry the old whole-lesson bed. A render without the bed is refused.
    Superseded (2026-10-13): one bed under the whole lesson, −24 dB under
    speech and −16 dB in the open, 1.5 s in and 3 s out.
23. **Posting by itself.** Since 2026-10-15 tutorials go public
    automatically once every gate passes (Miro); a failed gate is a card in
    #atlas-academy-videos. `ACADEMY_TUTORIALS_AUTOPUBLISH=off` brings back
    the review card; the automatic cap is seven a day, one per series.
24. **The AI voice is disclosed** (Miro, 2026-10-17;
    `docs/academy-video-system/narration.md`). Every tutorial description
    carries "Narration: synthetic voice (AI). Script written and checked by
    ATLAS Academy." above "Made with AI", and every teaching-Short caption
    "Synthetic voice (AI)."; `description_refusal` refuses one without it.
25. **`status.containsSyntheticMedia = true`** on every tutorial upload and
    visibility change (Miro, 2026-10-17; `docs/academy-video-system/narration.md`;
    the field per developers.google.com/youtube/v3/docs/videos, read
    2026-10-17). The Academy's Shorts post through Postiz, which cannot set
    it, so `academy.flag_synthetic_short` (Tier 1, Miro 2026-10-17: "yes add
    the AI flag for Shorts too") sets it after each teaching Short and in a
    daily 14-day catch-up, on the recorded id only, every other status field
    resent as read.
26. **End screens to add, every Monday** (Miro, 2026-10-17;
    `docs/academy-video-system/episode-structure.md`). Miro adds the native
    element in Studio; the Monday digest lists each tutorial public in the
    last 7 days with the next public episode, else its series playlist, else
    "Start here"; a failed read says UNKNOWN.

## The video system pack (Miro 2026-10-18, video system pack)

`docs/academy-video-system/tags-and-hashtags.md` and
`series-ai-explained-and-security.md`. These supersede rule 5 (tags and
hashtags) and the head of rule 6 (the description's first lines); every
other rule stands.

27. **Tags: channel, series, lesson, under 500** (Miro 2026-10-18, video
    system pack). Every upload carries the five channel tags (`atlas
    academy`, `atlasacademyphp`, `miro atlas academy`, `practical ai
    tutorial`, `ai automation for beginners`), then its series' set in the
    spec's exact phrases (`tutorial_series.SERIES_TAGS`), then 3–5 tags
    specific to the lesson: the exact tool, node or feature name
    (`lesson_tags`, from the episode's own tags, or `LESSON_TAGS` where the
    script's tags are all head terms). No year, nothing the channel or
    series set already says. The total stays under 500 characters as
    YouTube counts them: commas count, and a tag with a space counts its
    two quotation marks (YouTube Data API, videos resource,
    `snippet.tags[]`, read 2026-10-18; YouTube Help 146402 says tags play a
    minimal role in discovery and states no counting rule). Fewer than three
    lesson tags that fit hold the episode with the reason
    (`tags_refusal`); nothing is padded or cut. The Claude + n8n project
    takes the Claude / MCP / Skills set (the spec names none; Miro to
    confirm).
28. **Hashtags: 3–5, at the end, the strongest three first** (Miro
    2026-10-18, video system pack). One line of the series' hashtags
    (`SERIES_HASHTAGS`, lower case as the spec writes them) just above the
    synthetic-voice line and "Made with AI". YouTube shows the first three
    above the title, ignores all of them past 60, and treats unrelated or
    stuffed hashtags as misleading (YouTube Help 6390658). A teaching Short
    puts `#shorts` fourth, never first. `hashtags_refusal` holds a
    description with a hashtag in its body, more than five, or `#shorts`
    first.
29. **The description's first two lines are the result and the
    prerequisite** (Miro 2026-10-18, video system pack): "What you'll have:
    <result>." then "What you need: …" (rule 10's `Episode.needs`; an
    episode without needs shows its level and time there). Then the level
    and time, the summary, the lab files, chapters, links, hashtags, the
    voice line and the label. Where AI Is Going adds "Opinion: …" under the
    summary, and its scope line says "an opinion piece on …".
30. **Title at most 60 characters, the tool name front-loaded** (Miro
    2026-10-18, video system pack). Length is a check, never a truncation:
    past 60 the level 0/1 formula's closing aside ("(for total beginners)",
    "(step by step)") is dropped first, since it carries no search term;
    a title still over 60 is held by `title_refusal` with the reason.
    "Tool name first" is advice, not a hold: as a hold it stopped the
    project and business series outright. `tool_first()` lists the hooks
    to reword (project #1-#4, business #2-#4, agents #2); changing a public
    title is a Tier 2 card (rule 7).
31. **Chapter names are search terms** (Miro 2026-10-18, video system pack):
    "Add the Wait for approval node", never "Step 3". `chapters_refusal`
    refuses a bare "Step N" / "Part N" / "Section N".
32. **Three more series** (Miro 2026-10-18, video system pack): AI Explained
    (8 concept lessons, Level 0 then 1), AI Security (8, every one ends with
    a fix verified on camera), Where AI Is Going (6, one a month, opinion
    labelled as opinion). Planned in `tutorial_series_ai.py`: each outline
    opens with the 0–3 s result, the demo anchor (or the attack, on our own
    test setup) and the checkpoint (or the verified fix). Episodes that need
    a screen recording or Miro on camera are held (`HOLDS`,
    `tutorial_series.held`): the writer skips them, render refuses them, the
    lineup passes over them and the runway does not count them. Writable
    today: AI Explained #1, #6, #7 and Where AI Is Going #6. Security rules:
    our own accounts and test data only, never a working attack on a third
    party, the defense named before the attack is fully shown, no fear
    music, no red flashing.
33. **Study the human tutorials first** (Miro 2026-10-18, video system pack).
    Before an episode is written, `tools/academy_refs/study.py` finds the
    three most-viewed human-made videos on its topic (4–25 min, not ours),
    measures their pace, first action and chapters, and the writer commits a
    note in our own words, `docs/academy-references/<series>_<NN>.md`: how
    they open, the order they explain, their examples, the mistakes they
    address, their pacing, what they do badly, and what we take (structure
    only). Transcripts are never committed; `study.py check` refuses a note
    sharing any run of 8+ words with a transcript or quoting more than 15
    words. Our video-system rules win any conflict with the note.

Teaching Shorts (plan §4.3): two or three 30-60 s cuts per lesson, from its
`payoff`, its "Stuck?" beat and its quiz, each ending on the answer, posted
to YouTube and TikTok with the full video linked in the caption. Promo
Shorts are at most one in five (Miro, 2026-10-13).

The teaching Short's slot (Miro, 2026-10-13: two Shorts a day):
it posts at or after 23 UTC (`ACADEMY_TEACHING_HOUR_UTC`) from its own
scheduler tick (`academy_teaching`, `tutorials.teaching_tick`), not from the
17 UTC tutorial tick, and only on alternate New York days (even day of the
year in America/New_York). It shares the `CLIPS_PROMO_PER_DAY` cap of two
with the engine's 16 and 23 UTC Shorts. The teaching tick is spawned before
the reel's 23 UTC slot; the cut takes seconds, the reel render minutes, so
the teaching Short normally takes the slot and the reel holds silently (no
card); if the reel wins, the teaching Short waits two days. On the other days
the reel keeps its slot.

**What we keep refusing** from the reference videos: contrarian or
"goodbye <product>" hooks, percent claims, money words, other creators'
or companies' names, affiliate and sponsor blocks, community upsells,
email-gated "free resources", face thumbnails, and the 20-60 minute
format.
