# ATLAS Academy on YouTube — the free growth plan

**Status: approved by Miro 2026-10-13 ("approve all, fix the opener bug
first").** Rules 15-21 are in `docs/academy-youtube-plan.md`; the work is
`docs/superpowers/plans/2026-10-13-academy-growth.md`. Defect 1's root
cause: the five lessons were rendered before fd7a501 shipped and uploaded
before the fixed image was built; the gate now also listens to the file
(9b0aa91).

Asked 2026-10-13: "understand the channel, then plan the technical, visual,
logic and feature changes for every video, and how to get more viewers
without paying for ads".

## 1. What the channel is today (read 2026-10-13)

@atlasacademyphp is the same channel as `UC-smnVfEbe18jA4fFmSDIAw`. At read
time it had 5 subscribers and 17 videos. The About text reads "practical AI
and automation through real demonstrations… agency.storefront.test/academy/".

| Video | Length | Views | Age |
|---|---|---|---|
| What is Small Business Automation? Explained in 5 Minutes (for total beginners) — `pdMWOphvBp0` | 4:51 | 1 | 10 h |
| What is a Claude + n8n AI Assistant? Explained in 5 Minutes (for total beginners) — `c0gqF7WjY10` | 4:46 | 11 | 10 h |
| Build Your First MCP Server for Claude in 6 Minutes (step by step) — `iRyhxkLk-Yo` | 5:40 | 34 | 10 h |
| What Is n8n? Automation for Beginners… (n8n Basics) — `gOvJXCFpvWA` | 4:16 | 16 | 1 d |
| What Is Claude and How to Use It… (2026) — `M1a9Rd9mN6E` | 4:01 | 38 | 2 d |
| Welcome to ATLAS Academy… — `uPzByskDRXw` | 1:58 | 16 | 2 d |

The 11 Shorts are mostly product promos ("Local Business Automation Pack",
"An AI that asks before it moves money (3 courses)") with 0–7 views each.
The one outlier is "Let Claude run your week with Obsidian" at 81 views, and
it is the only Short that teaches something.

**Not measured:** CTR, impressions, retention and average view percentage.
vidIQ is out of credits, and those figures live in Studio or the Analytics
API. This plan doesn't guess them (invariant 6). Rule 21 below adds the
measurement.

## 2. Defects found, with evidence

These are fixes, not ideas. They come first because they cost views on
every upload.

1. **Wrong opener on all five lessons.** Each auto-caption track starts
   "Welcome guys to my new channel. My name is Myron and welcome to Atlas
   Academy."
   - Only the welcome video may say that (rule 3). The MCP episode also
     promises "under 5 minutes", while the title says 6 and the video runs
     5:40.
   - `ready()` passed them. `opener_refusal` checks the minutes recorded
     in the render metadata (`m.spoken_minutes`), not what the audio
     actually says. Either these were rendered before the 10-12 gate, or
     the metadata and the audio disagree. **Needs a check before the next
     upload.**
   - Retitling or re-uploading live videos is a Tier 2 card for Miro.
2. **Category is "People & Blogs".** The uploads set no `categoryId`, so
   YouTube uses its default. It should be **27 (Education)**, which puts
   the videos next to the tutorials they compete with. This is a free,
   one-field change.
3. **Auto-captions mishear the product.** The captions say "clawed" for
   Claude, "MEXP.tool" for `mcp.tool`, and "UV UV init" for `uv init`.
   - The only caption tracks are YouTube's ASR ones.
   - We already have the exact narration script, so we should upload it
     as an English caption track (`captions.insert`). That fixes
     accessibility and the text YouTube indexes for search.
4. **The first 10 seconds are a logo sting plus a "Welcome to ATLAS
   Academy" card.** Rule 3 says those seconds show the finished result.
   That window is when a viewer decides to stay.
5. **The quiz shows its own answer.** In the MCP video at about 5:00, the
   question "What breaks a local stdio server?" appears already ticked,
   with the explanation underneath. That cancels the "comment your answer"
   prompt.
6. **The live demo can't be read on a phone.** The MCP Inspector frame is
   mostly an empty white page, shown full size, with the action in a
   small corner.
7. **Brand drift.**
   - Thumbnails and lesson frames still use gold/orange, and the Level 1
     chip is plum. The 10-12 brand system is black plus royal blue.
   - There are two badge systems ("Ep 0" vs "Level 0 · 5 MIN") across six
     thumbnails.
8. **Thumbnail text that doesn't match the title.**
   - `c0gqF7WjY10`'s thumbnail says "Before we build", but its title is
     "What is a Claude + n8n AI Assistant?".
   - `iRyhxkLk-Yo`'s thumbnail says "What is MCP?", but its title is
     "Build Your First MCP Server".
   - A viewer who clicks for one and gets the other leaves.
9. **Two titles are off-formula.** `gOvJXCFpvWA` and `M1a9Rd9mN6E` predate
   rule 2 and carry no time promise. The fix is a Tier 2 retitle card.
10. **Fewer tags than the plan says.** Rule 5 asks for 20–25 tags. The two
    videos read here carry 15 each.

## 3. Per-video plan: what every episode gets

### Technical (upload pipeline)

- **15. Category 27 (Education)** on every upload.
- **16. Our own caption track.** Upload the script as English captions on
  every upload, with cues stripped by `plain()`. Product names are spelled
  right.
- **17. Default language and audio language** set to `en-GB`. Fill in
  `recordingDetails` and the "Made with AI" disclosure the same way every
  time.
- **18. Publish, don't drop.**
  - Upload private, then schedule public for one fixed slot each day.
  - Never publish two long videos within the same hour.
  - Add every video to its series playlist and to one "Start here" playlist,
    ordered L0→L3.

### Visual (renderer)

- **19. The first 3 seconds are the payoff.**
  - The finished result plays first: the n8n run turning green, Claude
    answering through the MCP tool. The topic line runs over it.
  - The opener line follows.
  - The logo sting moves to the end card, or shrinks to under 1 s in a
    corner.
- **20. Phone-first frames.**
  - Live demos are cropped and zoomed to the region that changes: never a
    full white page.
  - Keep a minimum on-screen font size so text reads at phone width.
  - Highlight the cursor or click target.
  - Use a pattern interrupt (zoom, cut, new diagram) at least every 20–30 s
    so the picture never sits still.
- **Thumbnails: one system.**
  - Use black plus royal blue only, with a single "N MIN" badge and one
    level chip.
  - The big words come from the **title's** hook, never a different phrase.
  - Use 2–4 words and one 3D focal object.
  - No faces (as rule 4 already says).
  - Test what changes each week: one variable at a time.
- **Chapters in the progress bar.** These are already in the description.
  Keep 0:00 and at least 3 chapters of 10 s or more each, so YouTube draws
  them.

### Logic (script)

- **Quiz reveal after a beat.**
  - The question shows with no tick.
  - A 3-second "pause and comment your guess" follows.
  - The answer is revealed at the recap.
  - Pin the same question as the first comment.
- **An open loop in the hook.** The hook says one concrete thing the viewer
  will be able to do by the end ("by the end, Claude will call a tool you
  wrote"). The recap closes it.
- **One next video, named out loud.**
  - The recap ends by naming the next episode in the series.
  - The end screen points to that same video, not to "subscribe" alone.
  - Subscribe is the second element.
- **Keep rules 9–14**: hands-on within 90 s, "what you need", labs, the
  common error, a human voice.

### Features (per video)

- **End screen and cards.** Two end-screen elements (next episode + the
  "Start here" playlist), plus one card at the moment a term from another
  episode comes up.
- **Pinned comment** with the quiz question and the free lab-file link.
  Posting it is a Tier 2 card or a catalogued action, never a connector
  write from the Mac.
- **Comment replies.** In the first 24 h, every real question gets a draft
  reply on a Slack card for Miro. Nothing posts by itself.
- **Free lab file** on every build episode (rule 12 already says so). This
  is the reason to come back and the reason to subscribe.

## 4. Getting viewers for free

There are no ads. The levers are search, suggested videos, Shorts, and
sharing done by hand.

1. **Search first, then browse.**
   - At 5 subscribers, browse traffic is near zero, so for now views come
     from search.
   - Each long video targets one exact phrase people already type ("what is
     mcp", "n8n tutorial for beginners", "claude mcp tutorial"). That phrase
     goes in the title, the first description line, the first spoken
     sentence after the opener, the file name and the captions.
   - The phrase list is read from YouTube autocomplete and the Data API
     (`search.list`), not guessed. When vidIQ credits are back, check
     volume against competition.
2. **Series that feed each other.**
   - Playlists in order, end screens chained to the next episode, and
     "Start here" pinned on the channel home.
   - Suggested traffic grows when viewers watch a second video. This is how
     a small channel gets it.
3. **Shorts as trailers for lessons, not ads for products.**
   - Cut 2–3 vertical teaching moments from each long video: the payoff,
     the common error and fix, a quiz.
   - Each one is 20–45 s, ends on the answer, and links the full video
     through YouTube's "Related video" field.
   - The single teaching Short already beat every promo Short.
   - **Miro's call:** move the product promo reels off this channel, or
     down to at most one in five Shorts.
   - The same cuts go to TikTok. The voice stays the British male voice,
     and nothing goes to Instagram.
4. **Consistency over volume.**
   - Plan: one long video a day in a fixed slot, plus 1–2 Shorts.
   - If render cost or approval time can't keep that up, three long videos
     a week at fixed times beats bursts. The algorithm and returning
     viewers both learn a schedule.
5. **Community posts** become available as the channel grows. Use them for
   a weekly "which should we build next?" poll and post the winner, with
   the comment question carried into the video.
6. **Places Miro already is, posted by hand, value first.** On LinkedIn
   (through ATLAS, Tier 2), a post teaches the episode's one idea in text
   and links the video. In Reddit and Discord-style communities, answer a
   real question and link the lesson only where the rules allow. **No
   spam, no cold DMs**, and Discord stays dropped.
7. **Collaborate without paying.**
   - Answer questions in comments on bigger tutorials, with no links,
     where the answer is genuinely useful.
   - Offer a free lab file to small creators in the same niche for a
     mention.
   - This is outreach, so every message is drafted to Miro for approval.
8. **Refresh what works.** When a video outperforms the channel median
   after 7 days, make the follow-up ("part 2", or the next level) within
   the week, and send a Tier 2 card for a thumbnail or title A/B test on
   the weak ones.

## 5. Measurement (rule 21)

- Grant the channel token `yt-analytics.readonly`.
  `atlas/modules/youtube/api.py` already reports when the scope is missing.
  Check it with `token_scopes` before relying on it.
- The Monday table (rule 7) adds the following per video: impressions,
  CTR, average view duration, average view percentage, and the share of
  viewers still watching at 0:30.
- One change a week, chosen from the worst metric:
  - Low CTR means a thumbnail or title change.
  - Low 0:30 retention means a hook or first-frame change.
  - Low average percentage means a pacing or demo-zoom change.
- A metric that can't be read shows UNKNOWN, never 0 or OK (invariant 7).

## 6. Order of work, once approved

1. Fix the opener gate so it checks the audio, not just the metadata. Then
   add category 27, the script caption track and `en-GB`. These go in code
   with tests.
2. Send Tier 2 cards for the five live lessons: retitle the two old ones,
   fix the mismatched thumbnails, and pin the comments.
3. Renderer changes: payoff first, demo zoom, delayed quiz reveal, one
   thumbnail system in blue.
4. Shorts: teaching cuts with a related-video link, and Miro's decision on
   promo reels.
5. The analytics scope and the extended Monday table.
6. After 4 weeks of data, revisit everything above that is not a defect fix.
