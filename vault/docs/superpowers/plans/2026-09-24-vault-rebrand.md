# Vault Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every string a customer can read says Vault; every identifier still says guardian.

**Architecture:** A guard test lands first and freezes the identifiers that must never move — the session cookie, the Home Assistant `unique_id`, the localStorage keys, the packages, paths and service labels. Only then does the copy change, one customer-facing surface per task. A second guard lands last and fails on the old name in user-visible strings, so the rebrand cannot rot back.

**Tech Stack:** Python 3.10 (hub, sensor), React 18 + TypeScript + Vite (web), static HTML in nine locales (site), pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-09-24-vault-rebrand-design.md`

## Global Constraints

- **The rule: a string changes only if a customer can read it.** Identifiers, storage keys, wire formats and anything persisted keep the word `guardian`, however brand-looking they appear.
- **The name is `Vault`, singular. Never `Vaults`.** The product speaks in the first person; "Vaults blocks" is not a sentence.
- **The domain does not move.** `guarder.io`, `my.guarder.io`, `api.guarder.io`, `admin.guarder.io` and every email address on them are untouched. `guardian@guarder.io` keeps its address; only the display name beside it becomes Vault.
- **Never change any of these**, and Task 1's guard will fail if you do: `guardian_session` · `guardian_{node}` / `guardian_{node}_{obj_id}` · `guardian.theme` · `guardian.sidebar` · `guardian.sidebar.expanded` · `guardian.chat.open` · `guardian.chat.speak` · `guardian.topbar.advanced` · `guardian.network.` · `guardian.onboarding.dismissed.` · `guardian.docs.token` · `guardian-scan/1.0` · `guardian-agent/1.0` · `guardian-nvr:` · `guardian_hub` · `guardian_sensor` · `GUARDIAN_*` · `/opt/guardian` · `~/.guardian` · `com.guardian.sensor` · `com.guardian.dns` · `guardian-hub.service` · the `guardian` CLI and the `guardian-hub` / `guardian-sensor` binaries.
- **Out of scope: comments, docstrings, `CLAUDE.md` and `docs/`.** They are invisible to customers. Do not touch them — a diff full of comment churn hides the real change from its reviewer.
- **Never use `sed` or any bulk find-and-replace across a file.** `integrations/homeassistant.py` has `"name": f"Guardian {name}"` and `"unique_id": f"guardian_{node}_{obj_id}"` on the same line with opposite answers. Edit deliberately, read each hit.
- **Never assert wall-clock.**
- Hub tests from `hub/`: `.venv/bin/pytest`. Sensor from `sensor/`. Web from `web/`: `npm test`, `npm run lint`, `npm run build`.
- Commit subjects: a sentence describing the user-visible behaviour change, present tense, sentence case, no `feat:`/`fix:` prefix.
- End every commit message with: `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **Stage by explicit path.** Never `git add -A` or `git add .` — this checkout is shared with other sessions and carries an untracked `.codex/` and `tmp/`.

---

### Task 1: Freeze the identifiers before anything moves

This lands first on purpose. Every later task edits files that also contain frozen identifiers, and this is what catches a slip.

**Files:**
- Create: `hub/tests/test_brand_invariants.py`
- Create: `web/tests/brandInvariants.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing importable. Two guards that later tasks must keep green.

- [ ] **Step 1: Write the hub-side guard**

Create `hub/tests/test_brand_invariants.py`:

```python
"""The identifiers the Vault rebrand must never touch.

The rebrand changes strings a customer can read and nothing else. These are
the ones that look like branding and are not: a cookie name, a Home Assistant
entity identity, a User-Agent, a persisted key. Renaming any of them is
silent -- nothing raises, a test somewhere else does not fail, and the damage
shows up as customers logged out or dashboards pointing at entities that no
longer exist.

`integrations/homeassistant.py` is the one worth reading before editing
anything: `"name": f"Guardian {name}"` and `"unique_id": f"guardian_{node}_{obj_id}"`
sit on the same line and have opposite answers.

See docs/superpowers/specs/2026-09-24-vault-rebrand-design.md.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: (literal, where it must still appear, what breaks if it moves)
FROZEN = [
    ("guardian_session", "hub/guardian_hub",
     "the session cookie -- every signed-in user is logged out"),
    ('f"guardian_{node}_{obj_id}"', "hub/guardian_hub/integrations/homeassistant.py",
     "the Home Assistant entity unique_id -- a change does not rename the entity, "
     "it creates a second one and orphans every customer dashboard card and "
     "automation built on the first"),
    ('f"guardian_{node}"', "hub/guardian_hub/integrations/homeassistant.py",
     "the Home Assistant device identifier -- same failure as unique_id"),
    ("guardian-scan/1.0", "sensor/guardian_sensor",
     "the User-Agent the scanner puts on the wire -- a customer may have it in "
     "an IDS rule or an allowlist"),
    ("guardian-agent/1.0", "sensor/guardian_sensor",
     "a User-Agent on the wire -- same reason"),
    ("guardian-nvr:", "sensor/guardian_sensor/nvr.py",
     "the seed of a sha1 that derives every camera-channel id -- changing the "
     "string changes every id derived from it, and nothing that stored one "
     "can find its camera again"),
]


def _tree_text(rel: str) -> str:
    """Every .py under `rel`, or the one file if `rel` names a file."""
    p = ROOT / rel
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return "\n".join(f.read_text(encoding="utf-8")
                     for f in sorted(p.rglob("*.py")))


def test_the_frozen_identifiers_are_all_still_there():
    missing = [(lit, why) for lit, where, why in FROZEN
               if lit not in _tree_text(where)]
    assert not missing, (
        "The Vault rebrand changes strings a customer can read, never an "
        "identifier. These are gone:\n  "
        + "\n  ".join(f"{lit!r} -- {why}" for lit, why in missing)
        + "\n\nPut them back. See the spec: "
          "docs/superpowers/specs/2026-09-24-vault-rebrand-design.md")


def test_the_python_packages_and_paths_keep_their_names():
    """The rebrand is not a rename. Renaming these means reinstalling every
    enrolled sensor and rewriting production's .env, for no customer-visible
    benefit -- which is why the spec rules it out."""
    pyproject = (ROOT / "hub" / "pyproject.toml").read_text(encoding="utf-8")
    assert "guardian_hub" in pyproject
    assert 'guardian = "guardian_hub.cli:main"' in pyproject

    unit = (ROOT / "deploy" / "guardian-hub.service").read_text(encoding="utf-8")
    assert "/opt/guardian" in unit
```

- [ ] **Step 2: Write the web-side guard**

Create `web/tests/brandInvariants.test.ts`:

```ts
/* The client-side identifiers the Vault rebrand must never touch.
 *
 * Every one of these is a key somebody's browser already has a value under.
 * Renaming one does not migrate it -- it abandons it, and the user's theme,
 * sidebar state or speaker preference silently reverts to the default with
 * nothing to indicate why. `guardian_session` is worse: it is the session
 * cookie, and renaming it signs everyone out.
 *
 * See docs/superpowers/specs/2026-09-24-vault-rebrand-design.md. */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");

/** [literal, what breaks if it moves] */
const FROZEN: [string, string][] = [
  ["guardian_session", "the session cookie — every signed-in user is logged out"],
  ["guardian.theme", "localStorage — resets every user's light/dark choice"],
  ["guardian.sidebar", "localStorage — resets the sidebar"],
  ["guardian.sidebar.expanded", "localStorage — resets the sidebar"],
  ["guardian.chat.open", "localStorage — resets whether the chat panel was open"],
  ["guardian.chat.speak", "localStorage — resets the read-answers-aloud preference"],
  ["guardian.topbar.advanced", "localStorage — resets advanced mode"],
  ["guardian.network.", "localStorage — forgets the selected network"],
  ["guardian.onboarding.dismissed.", "localStorage — re-shows onboarding to everyone"],
  ["guardian.docs.token", "localStorage — forgets the saved API-docs token"],
];

function allSource(dir: string): string {
  let out = "";
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out += allSource(p);
    else if (/\.(ts|tsx)$/.test(name)) out += readFileSync(p, "utf8");
  }
  return out;
}

describe("identifiers the rebrand must not touch", () => {
  const src = allSource(SRC);

  it.each(FROZEN)("keeps %s", (literal, why) => {
    expect(src, `${literal} is gone — ${why}`).toContain(literal);
  });
});
```

- [ ] **Step 3: Run both — they pass immediately, so prove they can fail**

```bash
cd hub && .venv/bin/pytest tests/test_brand_invariants.py -v
cd web && npm test -- brandInvariants
```

Both pass on the first run, which this project treats as suspect until shown otherwise. Prove each guard bites:

- Temporarily change `guardian_session` to `vault_session` in the hub source that defines it, re-run the hub guard, confirm it FAILS naming the cookie, then revert.
- Temporarily change `guardian.theme` to `vault.theme` in `web/src`, re-run the web guard, confirm it FAILS, then revert.

Capture both failing outputs in your report. `git status` must be clean afterwards.

- [ ] **Step 4: Commit**

```bash
git add hub/tests/test_brand_invariants.py web/tests/brandInvariants.test.ts
git commit -m "$(cat <<'MSG'
Renaming a cookie or a Home Assistant entity id now fails a test

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 2: The assistant, the alerts and the advice

The largest block of customer-read prose, and the one where the name is most personal — the assistant refers to itself by name, and `chat/rules.py` notes that when no provider is configured "this IS Guardian's voice".

**Files:**
- Modify: `hub/guardian_hub/payloads/security.py` (~29 hits), `hub/guardian_hub/chat/tools.py` (~27), `hub/guardian_hub/controls.py` (~15), `hub/guardian_hub/chat/surfaces.py` (~9), `hub/guardian_hub/payloads/incidents.py` (~6), `hub/guardian_hub/chat/rules.py`, `hub/guardian_hub/chat/prompt.py`, `hub/guardian_hub/remedy.py`, `hub/guardian_hub/payloads/jobs.py`
- Test: whichever existing tests assert on the copy you change

**Interfaces:**
- Consumes: Task 1's guards must stay green.
- Produces: nothing importable.

- [ ] **Step 1: Find every hit, then read each one**

```bash
cd hub && grep -rn '\bGuardian\b' guardian_hub/payloads/security.py guardian_hub/chat/ guardian_hub/controls.py guardian_hub/payloads/incidents.py guardian_hub/remedy.py guardian_hub/payloads/jobs.py
```

For each: is it inside a string a customer reads, or in a comment/docstring? **Only the strings change.** Comments are out of scope for this plan and their churn would bury the real diff.

- [ ] **Step 2: Change the strings**

Replace `Guardian` with `Vault` inside customer-read string literals only. Keep the grammar first-person and singular — "Vault blocks what it knows", never "Vaults block".

Watch for possessives: `Guardian's` becomes `Vault's`.

- [ ] **Step 3: Run the tests that assert on this copy**

```bash
cd hub && .venv/bin/pytest tests/test_chat_tools.py tests/test_chat_api.py tests/test_security.py tests/test_remedy.py tests/test_brand_invariants.py -q
```

Some will fail because they assert the old name. Those assertions are copy, and they move with it — update them. A test asserting the old product name is as wrong as the string was.

- [ ] **Step 4: Confirm the guards still hold**

```bash
cd hub && .venv/bin/pytest tests/test_brand_invariants.py -q
```

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub hub/tests
git commit -m "$(cat <<'MSG'
Vault introduces itself by name in chat, alerts and advice

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 3: Email and the PDF reports

These leave the product. A report gets filed and forwarded; an email sits in an inbox next to the sender's name.

**Files:**
- Modify: `hub/guardian_hub/mailer.py` (~21 hits), `hub/guardian_hub/reports/builder.py` (~24), `hub/guardian_hub/scheduler.py` (~5)
- Test: `hub/tests/` — whichever assert on email or report copy

- [ ] **Step 1: Find the hits**

```bash
cd hub && grep -rn '\bGuardian\b' guardian_hub/mailer.py guardian_hub/reports/builder.py guardian_hub/scheduler.py
```

- [ ] **Step 2: Change the copy, and mind the From line**

Subjects, bodies, report headers, footers and cover pages all become Vault.

**The `From` display name changes; the address does not.** `GUARDIAN_SMTP_FROM` is an environment variable and stays; its *value* in production reads `Guardian <guardian@guarder.io>` and the display half becomes `Vault`. The mailbox `guardian@guarder.io` is untouched — changing it breaks delivery and is explicitly out of scope.

Note in your report that production's `.env` holds that value, so the deployed display name does not change until somebody edits it there. Do not edit production.

- [ ] **Step 3: Run the tests**

```bash
cd hub && .venv/bin/pytest tests/test_mailer.py tests/test_reports.py tests/test_brand_invariants.py -q
```

Update assertions that pin the old copy.

- [ ] **Step 4: Commit**

```bash
git add hub/guardian_hub hub/tests
git commit -m "$(cat <<'MSG'
Emails and reports arrive from Vault

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 4: Notifications, integrations and the public API description

**This is the task the whole plan is shaped around.** `integrations/homeassistant.py` carries a display name and an entity identity on the same line, with opposite answers.

**Files:**
- Modify: `hub/guardian_hub/integrations/homeassistant.py`, `slack.py`, `discord.py`, `teams.py`, `telegram.py`, `ntfy.py`, `pagerduty.py`, `webhook.py`, `email.py`, `tickets.py`, `psa.py`
- Modify: `hub/guardian_hub/apidocs.py` (~9), `hub/guardian_hub/schemas_dash.py` (~8), `hub/guardian_hub/schemas_api.py` (~6), `hub/guardian_hub/routers/api_v1.py` (~17), `hub/guardian_hub/recorders.py` (~8)

- [ ] **Step 1: Read the Home Assistant file first, before editing anything**

```bash
cd hub && grep -n 'Guardian\|guardian' guardian_hub/integrations/homeassistant.py
```

Around line 220 and 243 you will find:

```python
device_info = {"identifiers": [f"guardian_{node}"], "name": f"Guardian ({ctx.tenant.name})",
cfg_payload = {"name": f"Guardian {name}", "unique_id": f"guardian_{node}_{obj_id}",
```

`name` → `Vault`. `identifiers` and `unique_id` → **unchanged**. Changing an entity's `unique_id` does not rename it in Home Assistant; it creates a second entity beside the first, and every dashboard card and automation the customer built points at the old one for ever.

- [ ] **Step 2: Change the display strings across the integrations**

Notification titles, card descriptions and any text a customer reads in Slack, Discord, Teams, Telegram, ntfy or a PagerDuty incident. **Dedup keys are a separate field and do not change** — `Message.dedup_key` carries the alert key and PagerDuty closes incidents on it.

- [ ] **Step 3: The API description**

In `apidocs.py`, the bearer-token description reads "An API token created in the Guardian app". The prose becomes Vault. `apikeys.TOKEN_PREFIX` is `gapi_` and is not a branded string — leave it alone; every customer's existing token starts with it.

- [ ] **Step 4: Run the tests**

```bash
cd hub && .venv/bin/pytest tests/test_integrations_core.py tests/test_integrations_new.py tests/test_ha_notify_inbound.py tests/test_api_docs.py tests/test_brand_invariants.py -q
```

- [ ] **Step 5: Prove the Home Assistant identity survived**

```bash
cd hub && grep -n 'unique_id\|identifiers' guardian_hub/integrations/homeassistant.py
```

Both must still read `guardian_`. Paste the output into your report.

- [ ] **Step 6: Commit**

```bash
git add hub/guardian_hub hub/tests
git commit -m "$(cat <<'MSG'
Notifications say Vault, and Home Assistant keeps the entities it had

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 5: The dashboard

**Files:**
- Modify: `web/src/pages/Support.tsx` (~23), `web/src/help/articles.ts` (~17), `web/src/pages/Security.tsx` (~16), `web/src/shell/ChatPanel.tsx` (~15), `web/src/pages/Integrations.tsx` (~15), `web/src/pages/Onboarding.tsx` (~14), `web/src/components/DnsWizard.tsx` (~13), `web/src/pages/Dns.tsx` (~12), `web/src/lib/api.ts` (~12), and the rest of the ~104 files under `web/src` carrying the word
- Test: `web/tests/` — whichever assert on the copy

- [ ] **Step 1: List the files and triage them**

```bash
cd web && grep -rln '\bGuardian\b' src
```

`src/types.ts` has ~33 hits and they are almost entirely JSDoc comments describing fields — **out of scope, leave them**. `src/lib/api.ts` contains `guardian_session` in a comment about the cookie — leave that too. The rule stays: only strings a customer reads.

- [ ] **Step 2: Change the copy**

Rendered text, button labels, placeholders, `aria-label`s, empty states, help articles, error messages.

- [ ] **Step 3: Run the suite and the guards**

```bash
cd web && npm test && npm run lint && npm run build
```

Update test assertions that pin the old copy. `brandInvariants` must stay green.

- [ ] **Step 4: Commit**

```bash
git add web/src web/tests
git commit -m "$(cat <<'MSG'
The dashboard calls itself Vault

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 6: The shell, the installable app, and the marketing site in nine languages

**Files:**
- Modify: `web/index.html`, `web/public/manifest.webmanifest`, `web/public/sw.js`, `web/public/auth-lattice.js`
- Modify: `site/index.html`, `site/ar/index.html`, `site/de/index.html`, `site/es/index.html`, `site/fr/index.html`, `site/hi/index.html`, `site/it/index.html`, `site/pt/index.html`, `site/zh/index.html`, `site/privacy.html`, `site/terms.html`, `site/signup.html`, `site/status.html`
- Test: `web/tests/pwa.test.ts`, `web/tests/indexHtmlCsp.test.ts`, `web/tests/site-signup.test.ts`, `web/tests/securityTxt.test.ts`

- [ ] **Step 1: The app shell**

`<title>`, the meta description, and in `manifest.webmanifest` the `name` and `short_name` — these are what an installed PWA shows on a home screen.

- [ ] **Step 2: All nine locales, and the legal pages**

```bash
cd site && grep -rln 'Guardian' .
```

Every locale, not the English one only — a locale left behind means a customer reads the old name in their own language. The legal pages name the product as the contracting party; change the name, leave the entity and contact details exactly as they are.

- [ ] **Step 3: Check the RTL locale renders**

`site/ar/index.html` is right-to-left. Confirm the replacement did not disturb its markup — this project has had an RTL layout bug before, where a stray rule stretched every Arabic page to 11279px.

- [ ] **Step 4: Run the tests**

```bash
cd web && npm test -- pwa indexHtmlCsp site-signup securityTxt && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/public site
git commit -m "$(cat <<'MSG'
The site and the installed app say Vault, in every language

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 7: The sensor and the installer a customer watches run

**Files:**
- Modify: `sensor/guardian_sensor/alerts.py` (~14), `sensor/guardian_sensor/runner.py` (~11), `sensor/guardian_sensor/install.py`, `sensor/guardian_sensor/sync.py`, `sensor/guardian_sensor/router_fios.py`
- Modify: `hub/guardian_hub/installer.py` (~33 hits — the script served to customers)

- [ ] **Step 1: The alert text**

`alerts.py` is where the sensor writes the sentences that become a customer's alerts. Change the prose.

- [ ] **Step 2: The installer script, carefully**

`installer.py` renders a shell script a customer pipes into `sh`. Its *output* becomes Vault — the banner, "Guardian sensor installed", the summary block.

Its **mechanics do not**: `guardian-install:` is the error prefix, the venv lives at `~/.guardian`, the binary is `guardian-sensor`, the launchd labels are `com.guardian.sensor` and `com.guardian.dns`. Every one of those exists on machines already, and renaming them orphans the install.

- [ ] **Step 3: Run the sensor suite and the guards**

```bash
cd sensor && .venv/bin/pytest -q
cd hub && .venv/bin/pytest tests/test_installer.py tests/test_brand_invariants.py -q
```

- [ ] **Step 4: Prove the installer's mechanics are intact**

```bash
cd hub && grep -nE "com\.guardian|\.guardian/|guardian-sensor|guardian-install" guardian_hub/installer.py
```

Paste the output into your report — every one of those must still be there.

- [ ] **Step 5: Commit**

```bash
git add sensor/guardian_sensor hub/guardian_hub/installer.py hub/tests sensor/tests
git commit -m "$(cat <<'MSG'
The sensor installs itself as Vault, onto the same paths as before

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

### Task 8: The sweep, and the guard that stops it rotting back

**Files:**
- Create: `hub/tests/test_no_stale_brand.py`
- Modify: `site/.well-known/security.txt`, `web/src/lib/api.ts:662` (the `guardian-alerts.csv` download filename), and whatever else the sweep finds

- [ ] **Step 1: Write the guard first**

Create `hub/tests/test_no_stale_brand.py`:

```python
"""No customer-visible string says the old name.

Python only, and deliberately so: `ast` can tell a string literal from a
comment, and a regex cannot. The web side is covered by its own suite and by
review -- claiming otherwise here would be a guard that looks stronger than
it is.

The allowlist is the spec's never-change list. Anything on it is an
identifier that happens to contain the word; everything else is copy.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
TREES = [ROOT / "hub" / "guardian_hub", ROOT / "sensor" / "guardian_sensor"]

#: Substrings that make a literal an identifier rather than copy.
ALLOWED = (
    "guardian_session", "guardian_{node}", "guardian-scan/", "guardian-agent/",
    "guardian-nvr:", "guardian_hub", "guardian_sensor", "GUARDIAN_",
    "/opt/guardian", ".guardian", "com.guardian.", "guardian-hub",
    "guardian-sensor", "guardian-install", "guardian@guarder.io",
    "guardian.docs.token", "guardian-dns",
)


def _docstrings(tree: ast.AST) -> set[int]:
    """Line numbers of every docstring in `tree`.

    A docstring is an `ast.Constant` like any other string, and this codebase
    is unusually full of them -- the house style is a paragraph explaining
    why. None of it is customer-visible, and the spec puts comments and
    docstrings out of scope, so they are skipped rather than edited.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(body[0].value.lineno)
    return out


def _offenders(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node.lineno in skip:
            continue
        text = node.value
        if "uardian" not in text:
            continue
        if any(a in text for a in ALLOWED):
            continue
        out.append((node.lineno, text[:90]))
    return out


def test_no_customer_visible_string_says_the_old_name():
    bad = []
    for tree in TREES:
        for f in sorted(tree.rglob("*.py")):
            for lineno, text in _offenders(f):
                bad.append(f"{f.relative_to(ROOT)}:{lineno}: {text!r}")
    assert not bad, (
        "These strings still say the old product name:\n  " + "\n  ".join(bad)
        + "\n\nThe product is Vault. If one of these is an identifier rather "
          "than copy, add it to ALLOWED here and to the never-change list in "
          "docs/superpowers/specs/2026-09-24-vault-rebrand-design.md -- do not "
          "rename it.")
```

- [ ] **Step 2: Run it and fix what it finds**

```bash
cd hub && .venv/bin/pytest tests/test_no_stale_brand.py -q
```

It will fail with a list. Work through it: each hit is either copy (change it) or an identifier the spec missed (add to `ALLOWED` **and** to the spec's table, and say so in your report — a new entry there is a finding, not a formality).

`_docstrings` already excludes module, class and function docstrings, so what remains should be real copy. If a docstring still slips through, it is because it is not the first statement of its body — read it before deciding; do not widen `ALLOWED` to silence it.

- [ ] **Step 3: The stragglers**

```bash
cd "$(git rev-parse --show-toplevel)"
grep -rn 'guardian-alerts.csv' web/src hub/guardian_hub
grep -rn -i 'guardian' site/.well-known/security.txt
```

`web/src/lib/api.ts:662` sets `link.download = named?.[1] ?? "guardian-alerts.csv"` — the fallback filename a customer sees on a saved export. Change it and update any test that asserts it. `site/.well-known/security.txt` names the product to a security researcher reading it; change the name, leave the contact address.

- [ ] **Step 4: Full suites**

```bash
cd hub && .venv/bin/pytest -q
cd sensor && .venv/bin/pytest -q
cd web && npm test && npm run lint && npm run build
```

The hub suite takes ~90 minutes. Run it once, here, not per task.

- [ ] **Step 5: Check the demo tenant**

```bash
cd hub && .venv/bin/guardian seed-demo
```

The demo is public and re-seeded constantly. Confirm its copy says Vault and that seeding is still idempotent — a second run must add zero rows.

- [ ] **Step 6: Commit**

```bash
git add hub/tests/test_no_stale_brand.py hub/guardian_hub sensor/guardian_sensor web
git commit -m "$(cat <<'MSG'
The old product name cannot come back without a test saying so

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
)"
```

---

## Before it ships

- **The two guards are the deliverable as much as the copy is.** Task 1 stops someone finishing the rename into the identifiers; Task 8 stops the copy drifting back. If either is weakened during review, that is the finding.
- **Production's `.env` still reads `GUARDIAN_SMTP_FROM="Guardian <guardian@guarder.io>"`.** The deployed From line does not change until somebody edits it on the VPS. That is a deliberate, separate step — the address stays, only the display name moves.
- **Deploy from `main`, and verify.** `DEPLOYED_SHA` on the VPS must match the merge commit, and the live bundle must actually contain the new copy. A deploy script exiting zero is not evidence.
