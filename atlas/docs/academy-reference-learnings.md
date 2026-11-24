# ATLAS Academy: what the reference tutorials teach us

The owner, 2026-10-11, pointing at five YouTube searches ("how to use n8n",
"claude tutorials", "hermes agent", "agentic ai", "n8n labs"): "these all
contain specific tutorials for n8n, Claude, Hermes. Take a look at these
videos, don't mock them, but add improvements to your own and learn from
them."

We took patterns from these videos and copied nothing: no title, wording,
thumbnail, brand or creator name of anyone else's goes into our videos.
The rules that came out of this are in `docs/academy-youtube-plan.md`
(rules 9-13), and the code follows them.

## Method

- **Source.** YouTube Data API v3, read on 2026-10-11 through the server's
  `clips` token. For each of the five queries we ran `search.list` twice
  (`order=relevance` and `order=viewCount`), 15 results each,
  `publishedAfter=2025-10-11`, `relevanceLanguage=en`. That is 10
  searches, about 1,000 quota units. Then `videos.list` fetched the
  snippet, statistics and contentDetails. The result is 128 unique videos,
  saved raw at `.claude/reference-videos-2026-10-11.json`.
- **What counts.** "Views" is the view count at read time. When we say
  "long-form", we mean videos over 70 s, which leaves Shorts out.
- **Chapters.** We parsed chapters from description timestamps: 78 of the
  128 videos have them.
- **The "first hands-on" figure is a heuristic.** It is the first chapter
  after 0:00 whose title matches build, create, install, set up, add,
  connect, first, demo or lab. Read it as a direction, not a measurement.
- **"n8n labs" was mostly noise.** It returned unrelated "Labs" brands,
  homelab tours, n8n's own "Business Lab" talks and viral Shorts. The lab
  structure below therefore comes from the videos that actually teach in
  labs, not from that query.
- **Thumbnails.** We downloaded ten thumbnails (`i.ytimg.com`, hqdefault)
  and looked at them, then left them in the scratchpad. None is in the
  repository.

## The patterns, with evidence

### 1. Title formulas

Counts are over 110 English long-form videos. The median view count
across all of them is 333k.

| Pattern | Videos | Median views | Strongest examples |
|---|---|---|---|
| A time in the title ("in 13 Minutes", "4 hours") | 17 | 896k | P5sKKnWCvzk 2.79M, wZeOwqmSw84 2.49M, 5_N84t1rUU0 666k, 9GpWELm3_XI 360k |
| "Full course / ultimate / complete / zero to" | 26 | 366k | ntDIxaeo3Wg 1.80M, 9oJySubZRSA 1.38M |
| "Explained / what is" | 7 | 283k | bjdBVZa66oU 1.79M (2 min), 9GpWELm3_XI 360k (4 min) |
| "for beginners" | 30 | 160k | iLyDaoqO784 1.81M, rRrBbyv3ChM 1.39M |
| "Step-by-step" | 6 | 197k | r2vYObllqJU 1.31M (8 min) |
| "Build / first" | 18 | 87k | GuaKeDS6UKU 552k |
| Percent claims ("97% of", "better than 99%") | 8 | 280k | wZeOwqmSw84, 28TGfTZweqw |
| Contrarian ("Stop learning X", "Don't…") | 6 | 444k | ZaPbP9DwBOE 1.33M |

How to read the table:

- **The time median is confounded.** Several titles with a time in them
  are multi-hour full courses (QoQBzR1NIqI, 4 hours, 2.65M views). The
  short versions still do well, though. 9GpWELm3_XI ("…Explained In 5
  Minutes", 4 min) has 360k views. P5sKKnWCvzk (13 min) has 2.79M. Our
  rule 1, a true time in every title, stands.
- **"Build / first" scores low as a title word** but leads in teaching
  structure (section 3). We keep "Build Your First" only where the video
  really builds something (plan rule 2).

### 2. Length by topic

Lengths are in minutes, long-form only.

| Query | Median | Middle half | Median of the top 10 by views | Top 10 at or under 10 min |
|---|---|---|---|---|
| how to use n8n | 19.7 | 13.2-43.5 | 67.1 | 0 |
| claude tutorials | 19.4 | 14.9-42.7 | 19.2 | 3 |
| hermes agent | 22.3 | 15.3-31.5 | 29.5 | 1 |
| agentic ai | 23.0 | 11.9-96.5 | 18.4 | 3 |

Median views by length band: under 5 min 191k (n=10), 5-10 min 200k
(n=11), 10-20 min 258k (n=32), 20-40 min 284k (n=30), over 40 min 629k
(n=27).

The niche runs long. Short videos still work when they deliver one clear
thing:

- r2vYObllqJU: 8 min, 1.31M views.
- w7_yWjYyxjE: 6 min, 359k.
- 0vZ_UVLhSQQ: 5 min, 2.39M.
- 9GpWELm3_XI: 4 min, 360k.

**Our 4-10 minute gate stays.** We win on "one thing, done properly, fast",
not on length.

### 3. Chapter layout: how soon the hands-on starts

- **First hands-on chapter.** The median is 82 s for videos of 20 minutes
  or less (n=21) and 214 s for all of them (n=56). The fastest short
  tutorials start inside half a minute:
  - r2vYObllqJU opens Claude at 0:33.
  - w7_yWjYyxjE creates a project at 0:26.
  - s96JeuuwLzc starts on Requirements at 0:24.
  - ntDIxaeo3Wg reaches setup at 1:10.
- **A median of 12 chapters per video.** The best short tutorials name each
  chapter for the action you take, as an imperative: open, write, upload,
  create, organise, connect. They don't use topic nouns.
- **Which chapters appear** (count of videos with chapters, out of 78):
  - Basics or "what is": 64.
  - Setup or install: 35.
  - Recap, wrap-up or next steps: 25.
  - A preview or demo of the result: 14.
  - A lab, exercise, challenge or quiz: 13.
  - Security or risk: 13.
  - Troubleshooting or common mistakes: 12.
  - Requirements or prerequisites: 3.
- **Two signatures stand out:**
  - A closing **"Next steps / what you can build from here"** chapter
    (r2vYObllqJU "Wrap up and next steps", DYdvJCxWd6M "What You Can Build
    From Here").
  - An explicit **"common mistakes / owner's toolkit: logs, fixes"** beat
    (WSPChlfxJyA "Avoid this mistake" at 1:09, DYdvJCxWd6M at 30:24).

### 4. What descriptions carry

Counts are out of 118 long-form videos.

| Item | Videos |
|---|---|
| Chapters (0:00 …) | 78 |
| Community / Skool / newsletter / Discord upsell | 58 |
| Affiliate / sponsor / discount code | 53 |
| Hashtags | 51 |
| "Free resource / guide / cheat sheet" | 28 |
| Template, workflow JSON or a download | 19 |
| GitHub link | 14 |
| Official docs link | 5 |
| A prerequisites line ("what you'll need") | 4 |

Three of these matter for us:

- **Prerequisites are the cheapest gap to close.** Only 4 of 118 videos
  say up front what you need.
- **Downloads come first and come from first-party hosts.** n8n's own
  quickstart (GuaKeDS6UKU, 552k) opens its description with two workflow
  template links. s96JeuuwLzc (678k) links a GitHub guide.
- **Most "free resource" links are email gates** on a creator's own site.
  Ours will be a plain file on our own store, with no email asked.

### 5. How lab-style videos are built

- **ZaPbP9DwBOE (1.33M, 56 min) is the clearest lab structure.** Every
  concept chapter is followed by a "Practice Labs" chapter: Your First AI
  API, LangChain, prompt engineering, semantic search, RAG, LangGraph and
  MCP. Concept, then do it, then the next concept.
- **5_N84t1rUU0 (666k) ends on a "Quiz" chapter** and links its video
  resources at the top of the description.
- **GuaKeDS6UKU (552k), the official n8n quickstart, builds two flows,**
  then ends with "Testing in Chat Hub" and "Viewing past executions". The
  viewer checks that the result works and sees where the runs live.
- **What they share:**
  - Each exercise has one expected result.
  - The file is downloadable, so a viewer who got lost can import it and
    carry on.
  - The video closes on a check, not just a summary.
- **What we found no example of in this sample is a lab that ends with a
  challenge**: one more step to try alone, with no answer shown. That is
  where we can be better.

### 6. Hermes Agent and agentic AI: what draws views

Hermes Agent (long-form, English, by views):

- **Setup and install, end to end, gets the most views.**
  - DYdvJCxWd6M, "Full Tutorial & Setup Guide": 508k.
  - gb5TlGw6Uks, "Zero to Personal AI Assistant": 396k.
  - Its tags repeat the setup angle: "hermes agent setup" (9 videos),
    "setup hermes agent" (8), "hermes agent install" (6), "how to setup
    hermes agent" (5).
- **"Fundamentals / explained in N minutes" is next.**
  - 5_N84t1rUU0: 666k.
  - 9GpWELm3_XI, 4 min: 360k.
- **Use cases follow.**
  - AQHlyGA2cZM, use cases: 233k.
  - iDxBhPWF2RY, a customer-service build: 56k.
- **The memory and skills chapters are the ones that recur** in the
  top videos: QQEgIo4Juxg ("How Hermes memory actually works", "Agents that
  write their own skills") and 5_N84t1rUU0 ("Memory Management System").
- **The single biggest Hermes video leans on a "goodbye <other product>"
  hook.** QQEgIo4Juxg has 1.78M views. We don't do that (see below).

Agentic AI:

- **The explainers that work are the ones that differ things clearly.**
  - _R_d3ibfttY, "Generative AI vs AI Agents vs Agentic AI": 527k.
  - ZELPNFXJ4_o, "AI Model vs Agentic Harness": 169k.
  - mnbERkIp6Wo, under 5 min: 23k.
- **Architecture "demo to production" walk-throughs do well.**
  ZIAzZtKWmbI has 491k. Its chapters include "Demo – The Security Issue",
  "Prompt that breaks the system" and "The bill that tripled". The risk is
  taught on screen.
- **The top of the viewCount list is news, interviews and course
  announcements,** not tutorials. We don't chase it.

### 7. Thumbnails

We looked at ten: GuaKeDS6UKU, r2vYObllqJU, 9GpWELm3_XI, P5sKKnWCvzk,
wZeOwqmSw84, 5_N84t1rUU0, ZaPbP9DwBOE, s96JeuuwLzc, w7_yWjYyxjE and
DYdvJCxWd6M.

- **Seven of ten show the product's real UI or its logo.** Examples are an
  n8n canvas with an AI Agent node, the Claude composer, and the Projects
  screen. It is the thing you will learn, not a stock picture.
- **Eight of ten put a face on the right.** We have no on-camera presenter,
  so we don't add one.
- **The text is 2-4 big words, with one coloured slab behind a single
  word** ("quickstart", "TUTORIAL", "PROJECTS").
- **One has a time badge across the top** ("IN 5 MINUTES", 9GpWELm3_XI).
  That is our rule 4's "5 MIN" badge.
- **Dark backgrounds with one warm accent** dominate.

## What we already do

- **A true time in the title,** with one formula per level (plan rules
  1-2), and the time badge on the thumbnail (rule 4).
- **The finished result shown first,** in the hook (rule 3).
- **Live demos in real n8n, the MCP Inspector, a terminal and an editor,**
  not slides of screenshots (`tutorial_demos.py`).
- **Chapters from 0:00,** every chapter at least 10 s, and a recap
  checklist.
- **Two quizzes, a pause-and-try beat and a subscribe beat** in every
  lesson. This is the "check, don't just watch" that the lab videos have.
- **A troubleshooting chapter in Claude #2** ("Not working? Check these").
  n8n #1 reads a red node's error.
- **Official docs in the links,** with the read date in the code.
- **No affiliate, no sponsor, no community upsell, no money words** (tests
  enforce this).

## What we adopt (each one is a rule in the plan and a test)

1. **The first hands-on step comes within 90 s of 0:00** in every episode
   whose title promises a build (plan rule 9). Claude #1 was at about 156 s
   and Claude #2 at about 99 s. Both were reordered so that the code shows
   first and the explanation follows.
2. **A "What you need" line, said and written.** Every lesson's
   description carries a prerequisites line under the summary
   (`Episode.needs`), and the hook of each build episode says it out loud
   (rule 10).
3. **A Lab at the end of every n8n lesson** (rule 11), in the place where
   the reference videos put "next steps". It is one chapter with four
   parts:
   - the exercise;
   - the result you should see;
   - "Stuck?", the common error and its fix;
   - a challenge with no answer shown.

   The lab file is a workflow JSON, built so that anyone can import it with
   no credentials. It is a free download at
   `https://agency.example.com/free/<file>`, generated with the store, never
   on a third-party host, and linked in the description (rule 12).
4. **Common errors are named where they happen** (rule 13). Every lab has
   its "Stuck?" line, and every build episode names at least one failure
   and its fix.
5. **Hooks promise the lab.** An n8n hook ends by saying a downloadable lab
   waits at the end, so the viewer knows the payoff.

## What we deliberately won't do

- **Clickbait and contrarian hooks** ("Stop learning X…", "you need to use
  X RIGHT NOW", "goodbye <product>", "HOLY…"), percent claims we can't
  measure ("97% of"), and a "secret".
- **Money claims of any kind** ("Build & Sell", "prints money", trading
  bots). The scripts and tags are tested for this.
- **Other creators' or companies' names** in our titles, tags or speech,
  and comparison tags against a product we don't cover (for example
  "openclaw").
- **Affiliate links, discount codes, sponsor reads, community or "free
  course" funnels, and email-gated downloads.** Our lab files are plain
  public files.
- **A face thumbnail, or a fake one.**
- **The 20-60 minute format.** We stay at 4-10 minutes, and a longer topic
  becomes another episode.
