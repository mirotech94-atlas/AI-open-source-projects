"""Reels — the whole plan for the AI-stories account, as the Reels Producer
and Growth use it.

Seeded into the library at boot like every pack here, so the plan is in
ATLAS's own knowledge base, not only in the code. The code is the authority
(`atlas/modules/content/{calendar,series,reels,render,crosspost}.py`,
`atlas/kernel/reels.py`); this is how to explain it and what never to do.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="The reels plan",
    triggers=("reel", "reels", "series", "episode", "part ", "veo", "tiktok",
              "instagram", "stories account", "the end", "captions"),
    agents=("reels", "growth", "marketing", "concierge"),
    priority=88,
    body="""
What gets made, in this order (Miro, 2026-09-27/28):

- The series being made, one part at a time. A series is planned whole --
  3 to 8 parts with a real ending -- with a fixed character sheet. A part is
  drafted only after the one before it posted; episodes end on a "PART N"
  card, the finale on "THE END" with a "Final part" caption; then the next
  queued series starts, or one is planned in the top-priority serial format.
  Never leave a series on "Part 2". Emergency Room: Code Chaos was rendered
  by hand Sep 28 - Oct 2 and is recorded as made; The Parking Spot (original
  car designs) is next.
- The two packages Miro supplied: 30 Egyptian-Arabic stories and 30
  business lessons. They are a plan, not a timetable -- made now, as fast
  as Veo allows, two stories then one lesson; a RETURN sequel waits for its
  episode to post.
- The daily reels, once the packages are done, in his priority order:
  Egyptian-Arabic comedy, rival-car comedy (unbadged), the hospital series,
  the serials, then fruit and vegetable dramas.

How every reel looks:

- Real Veo video only. Never slides, never a stills fallback: a day without
  clips posts nothing.
- Characters talk to each other on camera, each with its own mouth. No
  narrator, no voice-over, ever. Every line is written 'Name: line'; one
  exchange per 8-second beat; a cliffhanger is a character's last line.
- Stylized 3D with human-like faces (a car: eyes in the windshield, a mouth
  in the bumper), lip-sync, quick cuts, moving cameras, effects, a score and
  real sound, 60 fps delivery at 1080x1920 and a 4K archive master.
- Arabic voice, English captions. Captions only on series episodes, one
  colour per speaker; every reel keeps the small "Made with AI" tag.
- Never a real brand, person, hospital, studio look or film character. The
  guard refuses them in code before Veo is asked.

Length and pace: a series episode is 8-10 clips (64-80 s), a regular reel
4-5 (32-40 s). Veo's free tier makes 10 clips a day, counted from midnight
Pacific, so it is about one finished reel a day, series part and regular reel
alternating. Say that plainly; never pad a reel to look busier.

Posting: a reel posts itself to Instagram, the Facebook Page and TikTok only
past the checks in code -- the switch, at most two a day, the AI label, the
brand guard, a 1080x1920 render with sound that is not silent. A reel that
fails one becomes a card for Miro with the reason first. A TikTok post cannot
be taken back once Postiz publishes it. Nothing here ever costs money beyond
the Veo cap he set, and nothing sells: the business lessons' $29 and $99
offers stay drafts.

What an agent does: read the state (module_status, the digest in
#instagram-reels), explain it with the time each figure was read, and say
what is waiting and why. An agent never posts, never edits a posted caption,
and never raises the Veo cap -- those are the pipeline's or Miro's.
"""))
