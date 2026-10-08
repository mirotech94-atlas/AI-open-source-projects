# The fifty-point pass: Guardian reads as a security product

Miro's list of 2026-09-21, kept verbatim in intent and grouped for execution.
One line per item, checked when it is **shipped and verified on a real
render** (`hub/scripts/preview.py`), not when the code is written.

The through-line, and item 50: every one of these should leave the app
reading as a **cybersecurity product** — something that stops attackers —
rather than a dashboard that happens to list devices.

**Item 50 is shipped** as its own pass over the framing copy: every page's
eyebrow and lead now say what that page has to do with stopping someone,
not what data it lists. Assets is "every way in" rather than "everything on
your network"; Topology is "how an attacker would move"; Updates leads with
unpatched software being the most-used way in; History is the evidence
trail; Activity is Guardian at work, so "protected" is watchable rather
than taken on trust. The login hero changed with them. No figure changed —
this is what the numbers are FOR, stated.

---

## A. Deletions and duplication (fast, low risk, big legibility win)

- [x] **02 / 09** Delete the Owner / Customer / Technician view toggle from
      every tab. It has no use.
- [x] **03** Keep either "Scan now" (top bar) or "Run a check" (Overview
      header) — not both on one page doing the same thing.
- [~] **08** (deletion done; steps + approve/decline/chat toggles still to build) Drop "every open alert waiting for your yes": it repeats the
      "Waiting on you" block directly above it.
- [x] **11** Remove "Hide filters" from Activity.
- [x] **12** "Everything" tab deleted; the websites "DNS" column is now
      "IP address" — it always held A records, i.e. the answer, not the lookup.
- [x] **15** Table view deleted; each card now says HOW it was identified
      (named / hostname / maker only / unidentified).
- [x] **22** Hosts and Sites are the same thing — keep one per tab. "Roots
      host" vs "Roots site" in Security posture will confuse a client who
      has a server and an app with the same name.
- [x] **42** Cameras/recorders must not render as an empty section when the
      network has none (Integrations included).

## B. Numbers that must be true (the trust layer)

- [x] **05** Security score must cover every scan and every device — nothing
      silently outside the total. (`PostureCoverage`: still excluded from
      scoring, never excluded from the count.)
- [x] **07** Inbox: "Handled for you" read 0 and was 62 — it required
      `verified_at` (5 of 139 rows) AND string-matched `by == "auto"`.
- [x] **13** Every website/server/device "at a glance" figure accurate and
      live, so a number cannot change without the client seeing it.
- [x] **16** "Who makes your devices" → circular graphs, not racers. The
      grid now also has a lens (All / Online / Not here / Unidentified),
      each counted over the whole list rather than the current view.
- [x] **19** Route check needs real depth — not just evil twin, gateway and
      one router DHCP server.
- [x] **29** Updates: security-update totals, needs-reboot and pending must
      appear in Security too, for **all** devices, not only servers.
- [x] **30** Updates must list every server, including ones that look
      up to date.
- [x] **32** CVEs: every dangerous one per device and server Guardian
      tracks, with filters beyond name.
- [~] **35** Zeros now read as "no answer" while filtering is off (they were
      honest — the resolver is not running). Real numbers need the filter ON:
      see the wizard. DNS filter: the stream never stops.
- [x] **43 / 44 / 45** Config: users, targets, sensors counts correct;
      auto-fix shows **on** (Guardian does fix automatically); sites and
      targets pre-populated from the client's real estate; the
      target/action-policy host dropdown is not empty. Re-explain what
      target and action policy mean. Targets now OFFERS what Guardian has
      already seen and nobody is watching (`_suggested_sites`), with the
      evidence for each — and writes none of it until a person presses Add.
- [x] **49** Everything live-updating between refreshes, whether the client
      or Guardian made the change. Topology, Updates and Config were the three
      pages that never refreshed on their own; they now listen to the same
      live feed the rest of the app does.

## C. Evidence a client can hand to someone else

- [~] **01** Export core + Inbox/History/Activity/devices/fixes done; Security
      findings and per-scan detail still to wire. Every scan viewable and
      exportable as PDF or XLSX from Security or Reports.
- [x] **10** Export the alert log as PDF **or** XLSX (client's choice) — plus CSV.
- [x] **40 / 41** Preview fills the row + a real full-screen mode (Escape
      exits); ticking sections already updated it live. A report prints to
      PDF as a document; the LIBRARY exports as a table.
- [x] **18** Topology: red → live fix (already built, `LinkIssueDrawer`);
      amber → the recommendation; green → its own status, which was the gap.
- [x] **25** A red finding opens a popup that proves it was actually fixed,
      not merely found — and Guardian fixes **in parallel with** the scan,
      so most findings are closed by the time it ends. Results to Slack (or
      the client's integration) and email, from `alerts@guarder.io`.
      `CheckDetail.repairs` carries what Guardian RAN and, separately,
      whether a later scan looked again and found the world changed — the
      two are never merged, because a command that exits 0 has reported
      success, not achieved it. A PASSING check opens now too: it used to be
      inert, so the evidence became unreachable at exactly the moment the
      finding was fixed. Email is a notification channel at last (there was
      none), sending from its own address so findings and login codes do not
      land in one folder. The parallel half was already true in outcome:
      `fix.remediate` runs inside the scan cycle, before the push, so a
      finding reaches the hub already carrying its repair — now pinned by a
      test, because moving the push one line earlier would break it in
      silence.
- [x] **28** Fix log organised, with a search filter over action / host /
      when / by / result.
- [x] **34** Alerts (found + resolved) and reports already fanned out;
      UPDATES never did, and now do — once a morning, only when there is
      something waiting.

## D. Interaction fixes

- [x] **04** "Nothing needs you" still leaves a large gap, as do Devices
      matched and Blocked.
- [x] **06** Recent activity smaller; add a vulnerability-scan summary and a
      support-ticket section.
- [x] **10b** Snooze opens a small dialog that schedules the reminder to the
      client's email or the app. The reminder did not exist at all: expiry
      was lazy, at the top of an inbox read, so "remind me in 8 hours"
      produced no reminder — the alert just reappeared if anyone looked.
      `scheduler.snooze_wake_job` is the missing half.
- [x] **14** Clicking a device in Network presence opens the popup **without**
      switching to the Devices tab.
- [x] **23 / 24** Vulnerability scans: group the target dropdown (a device, a
      group of devices, the router) and add more depth options.
- [x] **31** Patch calendar colour-coded per server, patched or not — it is
      a blank calendar today.
- [x] **33** Fill the space below "Pending packages by host".
- [x] **36** Clicking a domain in the live query stream explains why it was
      blocked or allowed.
- [x] **37** Allow-rules and block-rules for the DNS filter (already built:
      `RuleManager`, verified present and wired to `writeDnsRule`).
- [x] **38** Setup wizard: thorough, stepwise advice.
- [x] **39** Group the scan log and event log in History.
- [x] **46** Support looks like a real ticketing system built for Guardian:
      a Guardian-shaped category on every ticket (`missed` first — it is a
      bug report about the product), the thing it was raised ABOUT carried
      and linked, ageing on the queue, and the ledger exportable.
- [~] **47** BLOCKED ON SMTP CREDENTIALS. The hub has no `smtp_host`, so
      `mailer.send` delivers nothing; the page now SAYS so instead of
      promising email. Set SMTP in /opt/guardian/.env to finish it.
- [x] **48** Help becomes Help **and API**, with clear steps per guide.

## E. Deep capability (design before code)

- [x] **17** Topology: fixed layout, 3D-looking device marks, the network
      itself above the internet mark; or square groups by what a device is
      (Wi-Fi by IP / websites / servers / cameras). Leaves WRAP now: a
      segment used to be one row however long, so 19 devices measured 5.2:1
      and every tile was a smudge once fitted. Plus: the map gets the full
      column (the route check reads fine underneath), the view takes its
      aspect from its own box instead of letterboxing, the whole graph opens
      visible when it fits, the toolbar wraps instead of overlapping itself,
      the marks have a lit face and a shadow, and the network chooser -- which
      had no stylesheet at all -- stopped running its three lines together.
- [x] **20** Traffic: today Guardian says it cannot see traffic (no mirror
      port, router reports no per-device counts). Use Wireshark/tshark where
      it is present so packet and per-device traffic data is real.
      **Only on networks the client owns and has asked Guardian to watch.**
      tshark joins tcpdump (either writes the same rotating pcaps, so
      `flows.py` never learns which ran); the opt-in is a control on the
      network's own row in Config, not a global switch; and the panel now
      says WHY there is nothing — "nobody has turned it on" and "Guardian is
      watching and it was quiet" used to render as the same sentence.
- [~] **21** Threats and protection: accurate throughout; this Mac's own
      firewall stated (it already was: `ProtectionPanel` reads the endpoint
      posture and says on / off / can't tell per defence); the 2 unverified
      checks visible to the client (they render as full rows, and the strip
      counts them); a firewall on the gateway with rules Guardian proposes
      from what its scans actually found — BUILT, per Miro's ruling: each
      rule names the finding it came from, "No" is a decision that sticks,
      and a finding that goes away expires rather than vanishing.
      **Apply is honestly dead.** Guardian has no write path to a gateway,
      and building one means reverse-engineering a form against Miro's own
      live router — his call, not something to acquire quietly on the way to
      shipping a feature. The button says so instead of pretending, and
      pressing it 409s rather than marking a rule applied that is not.
- [x] **26** Explain config drift in words a client understands.
- [~] **27** Heartbeats: none are configured on this hub, so the check is
      empty rather than green — the panel now explains what one IS and why it
      matters. Email spoofing already covers every domain from the client's
      sites minus explicit exclusions (example.com, flycheap.live; example.org excluded
      at Miro's request). Extending to domains merely SEEN in scans would
      re-create exactly the nagging he asked me to stop — needs his call.

---

## Order

1. **A** — deletions. Cheap, visible, removes the confusion the client is
   complaining about, and shrinks the surface every later item touches.
2. **B** — numbers. Nothing else is worth doing while a figure on screen is
   wrong; item 07 is a known live bug.
3. **D** — interaction fixes, mostly local to one component each.
4. **C** — evidence/export, which needs a shared PDF+XLSX path built once.
5. **E** — the capability work, each with its own design pass first.

## Also removed, on Miro's follow-up

The whole **Display & saved views** strip (spacing, response-time-strip
checkbox, saved views) went with the view picker: it sat at the top-left of
all fourteen tabs and was the first thing a client read on a security
product. `PersonalView` still mounts — it applies the stored density and
theme to the document — but renders nothing.

## Shipped so far

`02 / 09` view toggle gone (it drove nothing: `data-audience` was read by no
stylesheet, and the landing route is derived from role instead).
`03` Overview's "Run a check" gone; the top bar's "Scan now" is the one
control, and it reports live state. A test now guards the duplication
staying away rather than asserting the button exists.
`11` "Hide filters" gone from Activity.

## The export contract (built 2026-09-21)

`hub/guardian_hub/tabular.py` renders one table as CSV, XLSX or a printable
HTML; `EXPORT_DATASETS` in `routers/read.py` registers what can be exported;
`web/src/components/ExportMenu.tsx` is the only control that offers it.
Adding an export means adding a row builder, not a new endpoint shape.

- No xlsx library: a workbook is a zip of XML and every dependency is
  supply-chain surface the customer inherits. Tests open the result with
  openpyxl, so "valid zip" is never mistaken for "valid workbook".
- PDF is the browser's print engine over a print-stylesheet HTML page,
  served `inline` so it opens in a tab ready to print.
- CSV gets the formula-injection guard; XLSX must **not** (an `inlineStr`
  cell is text by construction, so the apostrophe would only corrupt the
  value the analyst reads).
- `/export/{dataset}` is registered LAST: declared before `/export/alerts`
  it silently shadows it, and "alerts" is deliberately not in the registry.

## Rules carried from the redesign

- Verify on a **real render** (`hub/scripts/preview.py`); jsdom does not lay
  out and a bundle grep proves only that a string shipped.
- 14 portal tabs and 14 console sections stay: `navInvariants` and
  `ownerNavInvariants` are the guard. Deleting a *view toggle* is not
  deleting a tab.
- No invented figures. If Guardian cannot measure it, the page says so.
- One classifier per fact (`payloads/account.is_person` is the precedent).
