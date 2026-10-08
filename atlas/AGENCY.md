# AGENCY.md — local business web work as an ATLAS module

Find local businesses with no website or a bad one, build them a good one,
host it, keep it running. ATLAS runs the pipeline; Miro approves anything that
leaves the building.

This is also the proof of the architecture: a whole new business line, and the
kernel does not change by one line.

---

## 1. On apex.host — read this first

Apex (apex.host) is Dan Martell's autonomous agent platform. Its feature list
is email triage, calendar management, Slack/Telegram/WhatsApp monitoring, KPI
tracking and anomaly detection, voice call screening, persistent memory.

That is ATLAS. It is **a competitor, not a component.** Two agents both
claiming to run your businesses is not an integration, it is a conflict —
whose proposal wins when they disagree, and which one owns the executor.

It is also **waitlist-only with no public API documented**, so there is
nothing to build against right now.

What to do instead:

1. **Mine it for features.** Their call-screening and content-repurposing are
   worth stealing as ATLAS capabilities.
2. **Build the adapter, leave the socket empty.** `kernel/external.py` defines
   an `ExternalAgent` protocol — `ask(question) -> answer`. If Apex ever ships
   an API it plugs in there, as a **tool the reasoner may consult**, never as a
   second brain.

**Hard rule:** an external agent is a read-only advisor. It can answer
questions. It can never propose, approve, or execute. It has no path to
`executor.execute`, and its output is wrapped in `wrap_untrusted()` like any
other outside text — because it *is* outside text, written by someone else's
model.

---

## 2. What the module does

```
modules/agency/
├── prospect.py    Google Places -> local businesses
├── audit.py       is their website bad, and how
├── score.py       which leads are worth the time
├── mockup.py      GetLayers + Claude -> a real page to show them
├── outreach.py    drafts the email/script. Never sends by itself.
├── build.py       the actual site
└── deploy.py      ship it, then hand to Guardian
```

### 2.1 Prospect — Google Places API, not scraping

**Do not scrape Google search results.** It breaks their terms, the layout
changes constantly, and the IP gets blocked. Use the **Google Places API**,
which is the supported path and returns structured data:

- Text Search / Nearby Search by category and radius (NYC boroughs, by trade)
- Place Details for phone, website, rating, review count, hours

Billed per request, so put a hard monthly cap in config and have the module
report `Health.UNKNOWN` when it hits the cap rather than silently stopping.

### 2.2 Audit — the qualifying signal

This is what makes the whole thing work. A business with no website, or a
visibly bad one, is a lead with an obvious pitch. All of it is public data,
fetched politely, one request per site:

| Check | Why it's a lead |
| --- | --- |
| No website at all | The easiest conversation you'll have |
| No HTTPS | Chrome shows "Not secure" to their customers |
| Not mobile-friendly | Most of their traffic is phones |
| Lighthouse score < 50 | Slow enough to lose bookings |
| No schema.org markup | Invisible in local search results |
| Dead links / 404s | Nobody has touched it in years |
| Copyright year 3+ years stale | Same |
| Flash, table layouts, `<marquee>` | Built in 2009 |

Each finding becomes a line in the pitch: *"your site takes 11 seconds on a
phone and Google flags it as insecure."* That's concrete and checkable, which
is why it lands.

### 2.3 Score

Feed it to the judgment layer (`JUDGMENT.md`). Features: category, review
count, rating, audit severity, whether they run ads, distance. Label: did Miro
pursue it, and did it close. Within a few dozen leads it learns which ones are
worth your Saturday.

### 2.4 Mockup — GetLayers over MCP

GetLayers is a library of AI-native "layers" — templates, 3D scenes, sections,
video backgrounds, WebGL gradients — that output a **single self-contained HTML
file**. It ships an **MCP server**, so ATLAS connects to it directly and
searches the library rather than anyone copy-pasting prompts.

Full Stack tier is what you want: MCP access, downloadable source, and the
commercial-use licence. **Check the licence covers client work before you sell
anything built on it** — that is a real constraint, not a formality.
Checked 2026-09-29: the Terms of Use grant paid plans a commercial licence for
"websites, designs, or other deliverables for yourself or your clients", with
no client limit; MCP access needs Full Stack Lifetime; the fair-use clause
forbids bulk extraction by automation, so mockups stay rate-limited
(`max_per_hour`). There is no API key: the server connects with OAuth, set up
once from the Mac with `python3 bootstrap/getlayers-connect.py` (CONFIGURE.md §8).

Flow: audit findings + their trade + their actual photos → pick layers over
MCP → Claude assembles → one HTML file → screenshot.

**Showing up with their site already rebuilt is the pitch.** Not a deck, not a
quote — the thing itself.

### 2.5 Outreach — Tier 2, every time

ATLAS drafts. Miro sends. No exceptions at the start.

- Email, call script, or a walk-in one-pager
- Leads with the audit finding, not with "I noticed your website"
- Links to the mockup

**Cold outreach is regulated.** CAN-SPAM requires a real physical address, a
working opt-out, and non-deceptive headers. Volume sending from a young domain
gets it blacklisted, and you need that domain for everything else. So:

- Hard cap, low: start at 10 sends a day
- Separate sending domain, never `example.com` or anything you rely on
- Suppression list is permanent and checked before every send
- One follow-up, then stop. Two is fine. Five is spam.

Auto-send only ever becomes a standing rule, per campaign, after the judgment
layer shows you approve that exact shape ≥95% of the time — and even then it
keeps the daily cap.

### 2.6 Build and deploy

GetLayers layers as the base, Claude Code for the rest. Static where possible
— a local business site is a brochure, not an application.

Deploying to a client's production domain is **Tier 2**. Buying a domain on
their behalf is **money out, which is Tier 3 and permanently denied** — you buy
it, or they do.

Once live, the site goes into **Guardian**, which already watches endpoints and
certs. New client, new monitored endpoint, and you now have a recurring
maintenance reason to invoice.

---

## 3. Tiers

| Action | Tier | Why |
| --- | --- | --- |
| Search Places, audit a site | 1 | Read-only, public data |
| Generate a mockup | 1 | Nothing leaves the box |
| Score and rank leads | 1 | Advisory |
| Send outreach | **2** | Your name, their inbox |
| Deploy to client production | **2** | Someone else's business |
| Register a domain, pay anything | **3** | Money out. Permanently denied. |

---

## 4. Generalising — why this took no kernel changes

`agency/` is nine files under `modules/`. It declares its checks, its actions
and their tiers, and its `requires_env`. The scheduler picks it up, the
detector scores its findings, the executor gates its actions, the audit chain
records them, the judgment layer learns from your decisions about them.

**Nothing in `kernel/` was touched.** That was the whole point of building it
as a kernel with modules rather than an app — the next business line is another
folder, and the one after that is another folder.

`kernel/external.py` and the `ExternalAgent` protocol are the same idea applied
outward: third-party agents become one more read-only tool, not a rewrite.

---

## 5. Build steps

`BUILD_ORDER.md` **Phase 12**, after the learning layer — lead scoring wants
the judgment layer already running.

1. `ExternalAgent` protocol in `kernel/external.py`. No implementations. Test
   proving no path to `executor.execute`.
2. `prospect.py` on Google Places API with a hard spend cap, `UNKNOWN` on cap.
3. `audit.py` — all eight checks, polite fetching, results cached 30 days.
4. GetLayers MCP wired; confirm the commercial licence covers client work.
5. `mockup.py` — audit + trade + their photos → one HTML file + screenshot.
6. `score.py` into the judgment layer.
7. `outreach.py` — drafts only, Tier 2, suppression list, 10/day cap,
   separate sending domain, CAN-SPAM fields present and tested.
8. `build.py` / `deploy.py`; new sites auto-register with Guardian.

**Gate 12:** ATLAS finds ten local businesses with genuinely bad sites,
produces a mockup for the best one that you would actually show someone, and
drafts outreach you would actually send. Then confirm it cannot send it
without you.
