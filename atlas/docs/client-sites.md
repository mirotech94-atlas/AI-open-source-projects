# Client sites and demos (Mapfront lane)

ATLAS's agency module owns every client site: a registry (`agency_sites`), a
publish action per kind, a dashboard tab (Client Sites), a line in the daily
brief, and the owner-only phone path when a demo goes live or fails to.

| kind | what it is | how it goes live |
|---|---|---|
| `built` | an ATLAS template build (`agency.build_site`) | `agency.deploy_site`, Tier AUTO, launch check, `https://agency.storefront.test/preview/<slug>/` |
| `demo` | a static build made elsewhere and dropped into the intake dir | `agency.deploy_demo`, **Tier ASK** (a card Miro approves), one Caddy block per hostname |
| `app` | a site with a backend, operator-deployed | registry only: ATLAS watches it, never deploys it |

## Hosting decision (2026-10-27)

No new VM. A demo is a static build with no backend: the Royal HairStyles
demo's `scripts/build_static_demo.py` answers `/api/*` in the browser
(`mock-api.js`) and keeps bookings in the visitor's own localStorage. ATLAS's
own Caddy serves it from a read-only volume:

- the api publishes into `AGENCY_DEMO_PUBLISH_DIR` (`/app/data/demos`, host
  `./data/app/demos`) as release directories plus a relative `<slug>` link,
  the same layout `hosting.py` uses for client previews;
- Caddy mounts that directory read-only at `/srv/demos` and serves
  `/srv/demos/<slug>` through the `(demo_site)` snippet: files only, no
  listing, no dotfiles, `X-Robots-Tag: noindex, nofollow` on every response
  including the 404, the same security headers as client sites;
- each hostname is its own site block with an ordinary certificate (no
  on-demand TLS).

Rejected: a new VM (Miro), a tunnel to the Mac (home machine, not always on),
a backend container on the ATLAS host (code execution and a second deploy
path outside GitLab CD), on-demand TLS for example.com.

Nothing a visitor types is stored anywhere ATLAS can read. No Stripe keys:
checkout in the demo is an in-browser mock until a production VM exists.

## Miro's steps (not the builder's)

1. DNS (done 2026-10-27): A record `examplesalon.storefront.test -> 162.220.52.191`.
2. Once, on the Mac (ATLAS runs from `~/atlas-agency` since 2026-10-28):
   `install -d -o 10001 -g 10001 ~/atlas-agency/data/app/demos ~/atlas-agency/data/app/demos-intake`
   and, if the defaults are not wanted, set `AGENCY_DEMO_PUBLISH_DIR` /
   `AGENCY_DEMO_INTAKE_DIR` in `~/atlas-agency/.env` with `bootstrap/set-secret.sh`
   (they are not secrets; the defaults `data/demos` and `data/demos-intake`
   need nothing set).
3. Ship the branch through GitLab CD. CD validates the Caddyfile before it
   copies it, and recreates caddy only.
4. Gate: `curl -sI https://examplesalon.storefront.test/` answers
   `404` with a valid Let's Encrypt certificate and
   `x-robots-tag: noindex, nofollow` (nothing published yet).

## Publishing a demo (the intake drop)

1. Build it outside ATLAS's repo. Royal, from its worktree at `176c272`:
   `python scripts/build_static_demo.py --out /private/tmp/rhs-dist --site-url https://examplesalon.storefront.test`
   then delete `_headers` from the output (Caddy sets the headers).
2. Register it (operator, on the host):
   `docker compose exec api python -m atlas.cli sites-add examplesalon examplesalon.storefront.test demo "royal-hairstyles-site-demo@176c272"`
   This writes the registry row only. The `site_<slug>` up/cert check is
   built from the registry when the api starts, so it begins after the next
   api restart (the next CD deploy restarts it).
3. Drop the files (a data drop, not a code deploy):
   `rsync -a --delete /private/tmp/rhs-dist/ ~/atlas-agency/data/app/demos-intake/examplesalon/`
4. ATLAS raises the `agency.deploy_demo` card. Its preview names the hostname,
   the file count, the audit outcome and the release that is live now.
   Approve only after the screenshot re-judge and `/pre-launch-check`.
5. On approval the intake is audited again on the release copy, swapped in
   atomically, the event is recorded (`agency_site_events`), and Miro's phone
   rings once (owner only, quiet hours and the 3-a-day budget apply; Slack
   always gets it). A failed deploy rings the same way.

The audit refuses: a symlink or a dotfile in the build; a file over 10 MB or
a total over 50 MB; a `.html` page without `<meta name="robots"
content="noindex`; an `index.html` without the text `Demo concept by
Mapfront`; any `sk_live_` / `pk_live_` / `rk_live_`; a `<form>` that posts
to `http(s)://`; a `<script src="http(s)://...">` (assets are self-hosted).

## Adding a client hostname

Client previews are path-based: `https://agency.storefront.test/preview/<slug>/`
(no wildcard DNS, no on-demand TLS). A demo on its own hostname needs its own
block in the client-demos section of the Caddyfile:

```
<hostname> {
	import demo_site <slug>
}
```

A custom hostname needs Miro's DNS record first, so Let's Encrypt succeeds on
the first try. Ship the block
through CD; then `sites-add` the row with that hostname as its `domain`.

## Removing a demo (manual, Miro)

There is no deletion action (Tier 3). To take a demo down:

1. On the Mac: `rm ~/atlas-agency/data/app/demos/<slug>` (the symlink only;
   the releases under `.releases/<slug>/` can stay or be removed by hand).
   The hostname then answers 404 at once.
2. Remove its Caddy block in a commit and ship it through CD.
3. Remove the DNS record if the hostname is not coming back.
4. The registry row stays as history unless Miro deletes it by hand.

## Rolling back

- One deploy: the card's rollback restores the previous release, or
  unpublishes when it was the first; it refuses when a newer release has gone
  live since.
- By hand: point `<slug>` at an older `.releases/<slug>/<stamp>` (a relative
  link, `ln -sfn .releases/<slug>/<stamp> <slug>` inside the publish dir).
- Code: revert the commits through CD. The schema changes are additive.

## Skills and templates

The `sites` agent reads, and writes only its own document drafts (below).
New know-how arrives as a text-only skill
through `agency.add_skill` (Tier ASK, card only, at most 1500 characters, no
markup, no templating, https links only). A new template is never written by
ATLAS: `agency.request_template` (Tier ASK, card only) queues a handoff to the
coding agent on Miro's Mac, which authors it with frontend-design and
GetLayers and ships it through the repo and CD.

## Documents are Claude Docs artifacts

Miro's rule (2026-10-29): every document the agency makes for a client site
(brief, handoff, report, proposal, client guide) is a Claude Docs artifact,
never a file in this repo or a message body.

- The `sites` agent drafts it with `sites_draft_doc` (`agency.draft_doc`,
  Tier AUTO, rollback withdraws the draft). That writes one row in
  `agency_site_docs` and raises an `agency.publish_doc` card. The client's
  own words go in `client_text`; they reach the brief through
  `wrap_untrusted` and are quoted, never followed.
- `agency.publish_doc` (Tier ASK, card only) queues a handoff. ATLAS never
  creates the artifact (invariant 1). The handoff runner on Miro's Mac makes
  it with the docs skill or connector, notes the link in the site repo's
  README when the brief allows, and reports the link with `--artifact-url`.
- The report records the link (claude.ai artifact links only) on the row and
  in the Slack receipt, as the coding agent's word. The Client Sites tab
  links it; until then the document shows UNKNOWN (grey, hatched, worded).

Setup and the operator command: CONFIGURE.md, "Agency: client-site documents
are Claude Docs artifacts".
