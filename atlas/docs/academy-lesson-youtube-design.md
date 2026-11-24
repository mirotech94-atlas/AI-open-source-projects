# Academy lesson videos on YouTube: design note (2026-11-14)

Decision (The owner): free previews PUBLIC on YouTube with a link to and from the store; paid lessons
UNLISTED, embedded (youtube-nocookie) only on the buyer's paid course page, never linked publicly.
Uploads go through ATLAS's approval and executor flow, a few per day. Branch `feat/academy-youtube`,
based on the Part A course page (`e6c0252c`).

## What already exists (reused, not rebuilt)
- `youtube/api.py`: resumable upload (always lands private), `set_privacy`, `set_thumbnail`,
  `set_captions` (needs force-ssl), playlists (`create_playlist(privacy)`, `add_playlist_item`,
  `take_out_of_playlist`), `synthetic` AI-voice flag, en-GB, Education category.
- `academy/tutorials._publish`: the pattern copied (idempotency claim, resumable row, undo token).
- Executor: one path, `ATLAS_WATCH_ONLY` refuses, Tier 2 cards, dry run and rollback required.
- Course page (Part A): one player, lessons in modules, signed own-file links, nonce CSP.
- Motion gate: `<lesson>.mp4.gate.json` `{pass, read_at}` beside each staged render.

## What was built (no secret, no live call)
| Piece | Where |
|---|---|
| Action `academy.upload_lesson(course, num)`, Tier 2, dry run, rollback | `academy/lesson_youtube.py`, registered in `academy/__init__.py` |
| Source gate: plain `lesson-<course>-<n.m>.mp4` directly in the staging folder, no symlink, gate passed, gate newer than the file, sha256 recorded. Files under the store's `files/` and old slide videos are refused | `staged_refusal` |
| Audience decided in code: `FREE_PREVIEWS` constant (empty until the owner names them) so every lesson is unlisted until then | `privacy_for` |
| Channel from `ACADEMY_LESSONS_YT_CHANNEL`; unset = refused, never falls back to clips. Generic `youtube.upload` refuses that channel | `lesson_channel`, `vet_upload` |
| ID column and migration: table `academy_lesson_youtube` keyed by catalog file key (video_id, privacy, playlist, thumb, captions, sha256, done, withdrawn) | `db/schema.sql` (idempotent) |
| Per-day cap counted from the table (default 3, max 4) | `per_day`, `uploaded_today` |
| Metadata: title (swappable function, default = catalog lesson title), description with store link and "synthetic voice", tags, en-GB, SRT from the render's cues, thumbnail if `<stem>.jpg/png` is staged | `build_*`, `srt_from_cues` |
| Playlists: paid = own UNLISTED playlist per course; free previews = separate PUBLIC playlist (a public playlist of unlisted lessons would expose them) | `playlist_for` |
| Course-page embed: lesson row carries `data-yt`, an iframe swaps with the `<video>`, id checked against `^[A-Za-z0-9_-]{11}$` in Python and in JS; CSP `frame-src 'self' https://www.youtube-nocookie.com`; no IFrame API script (keeps the nonce CSP) so a YouTube lesson is marked watched on open | `course_page.py`, `api/academy.py` |
| Referrer fix: YouTube embeds refuse to play with no referrer and the public Caddy sends `no-referrer`. The paid page now sends `strict-origin-when-cross-origin`; `Caddyfile.public` uses `?Referrer-Policy` so the app's value wins. **The Caddy edit takes effect only on deploy (The owner's OK).** | `api/academy.py`, `Caddyfile.public` |
| Fallback: no id, bad id or unreadable DB = the existing signed own-file player | `embed_ids` returns `{}` |

## Rollback: not delete
Deletion is permanently Tier 3, so rollback = set the video private, take it out of its playlist,
mark the row `withdrawn`. (The brief offered "delete"; that would break invariant 3.)

## The owner's answers (2026-11-14) and what they change
- Channel is @example_handle (seen in Chrome, 67 videos, 272 subscribers). ATLAS knows it as the
  `clips` key (`YOUTUBE_CLIPS_*`). Channel id UCexamplechannel00000001 is NOT confirmed: no
  credentials are set here, so `channels.list` was not run.
- Titles: "Learn X in less than N minutes!" (built: `build_title`, N = next whole minute above the
  gate-measured length; the channel's own 5:07 video is titled "6 Minutes", same rule).
- Every lesson and every ebook/PDF product gets a short PUBLIC preview, the full lesson UNLISTED
  (embedded on the paid page only), and a Short. Store pages link to the previews.
- Thumbnails: branded, in the channel's layout: dark navy/black ground (#0A0D14), big bold white
  caps title top-centre (2 lines), a glowing royal-blue (#5B8CFF) isometric icon/diagram centre,
  3-4 small labelled callouts around it. One template, text from the lesson; rendered locally,
  uploaded with `thumbnails.set` (50 units; needs a verified channel).
- Shorts: vertical 9:16, under 60 s, same title style, the lesson's simplified how-to. Reuses
  `lesson_short.py` picking. Design only; no render or upload code yet.

## Gate conflict (separate commit 520435dc, unmerged, for the owner)
`youtube.upload` and public visibility on the clips key stay refused (`vet_upload`, `vet_visibility`
untouched in effect). Change: `api.upload` on the clips key accepts a third kind of file, a gated
lesson render (`staged_refusal`: plain file in the staging folder, passed gate newer than the file),
and only the Tier 2 action `academy.upload_lesson` reaches it. No new channel key (a second key on
the same credentials would double every per-channel collector and dashboard tile). No runtime
override; WATCH_ONLY still applies.

## Quota and pacing
Costs per upload: full lesson 2,150 (insert 1,600, captions 400, thumbnail, playlist, status 50
each); preview 2,150; Short about 1,700 (no captions or thumbnail). A lesson trio is about 6,000.
Project quota is 10,000 a day and the tutorials lane keeps 1,700. The owner's cap is 2-3 uploads a day
in total, one shared budget, counted from the table (not yet extended to previews and Shorts).

Uploads to make: 132 course lessons + 4 ebook/PDF products (one overview each; the 30 Business
Lessons ebook per-lesson would add 29 more) = 136 full, 136 previews, 136 Shorts = 408.

| Uploads a day | Full + preview (272) | Full + preview + Short (408) |
|---|---|---|
| 3 | 91 days | 136 days |
| 2 | 136 days | 204 days |

If the daily tutorial upload counts inside the 2-3, the lessons get 1-2 a day and the totals
double (about 272 days for 408 at 1.5). Only 10 gated full renders exist, so rendering also limits
the start. Quota is not the limit at 3 a day (about 6,500 + 1,700 reserve of 10,000).

## Risks
- Unlisted is not private: anyone with the link can watch. It is the choice made; the link only ever
  appears on a verified paid page.
- Embeds need the referrer fix above live, or they show an error.
- YouTube may flag the channel's AI-voice videos; the synthetic flag is set on every upload.
