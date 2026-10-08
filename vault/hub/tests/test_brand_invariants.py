"""The identifiers the Vault rebrand must never touch.

The rebrand changes strings a customer can read and nothing else. These are
the ones that look like branding and are not: a cookie name, a Home Assistant
entity identity, a User-Agent, a persisted key. Renaming any of them is
silent -- nothing raises, a test somewhere else does not fail, and the damage
shows up as customers logged out or dashboards pointing at entities that no
longer exist.

`integrations/homeassistant.py` is the one worth reading before editing
anything: `"name": f"Vault {name}"` and `"unique_id": f"guardian_{node}_{obj_id}"`
sit on the same line and have opposite answers.

See docs/superpowers/specs/2026-09-24-vault-rebrand-design.md.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: (literal, where it must still appear -- a path, or a list of paths that
#: must EACH still hold it individually -- what breaks if it moves)
FROZEN = [
    # Pinned to its definer, not the whole `hub/guardian_hub` tree: `COOKIE`
    # is assigned exactly once, in `deps.py`. Three OTHER files quote the
    # cookie name in their own docstrings (`apideps.py`, `models.py`,
    # `routers/live.py`) -- real prose explaining what authenticates what,
    # not the definition. A tree-wide search treats those the same as the
    # assignment, so deleting `COOKIE = "guardian_session"` and leaving the
    # docstrings alone used to pass this guard: the exact failure it exists
    # to catch. Pinning to `deps.py` means the guard fails the moment the
    # definition moves, regardless of what else still mentions the name.
    ("guardian_session", "hub/guardian_hub/deps.py",
     "the session cookie -- every signed-in user is logged out"),
    ('f"guardian_{node}_{obj_id}"', "hub/guardian_hub/integrations/homeassistant.py",
     "the Home Assistant entity unique_id -- a change does not rename the entity, "
     "it creates a second one and orphans every customer dashboard card and "
     "automation built on the first"),
    ('f"guardian_{node}"', "hub/guardian_hub/integrations/homeassistant.py",
     "the Home Assistant device identifier -- same failure as unique_id"),
    # Pinned to the set of files that actually carry it, not the whole
    # `sensor/guardian_sensor` tree -- same reasoning as `guardian_session`
    # above, generalised. `grep -rln` against the tree confirms exactly one
    # file uses this string today (`vulnscan.py`); it's still a list rather
    # than a bare path so a second file adopting the same User-Agent later
    # is covered the same way, and so this entry and the next one share one
    # shape. A tree-wide search would let every real occurrence in the list
    # be renamed away while a stray docstring anywhere else in the tree kept
    # the guard green -- the same hole `guardian_session` had before it was
    # pinned to `deps.py`.
    ("guardian-scan/1.0", ["sensor/guardian_sensor/vulnscan.py"],
     "the User-Agent the scanner puts on the wire -- a customer may have it in "
     "an IDS rule or an allowlist"),
    # Genuinely spans two files (`grep -rln` confirms exactly these two, no
    # more), so a single definer isn't available the way it is for
    # `guardian-scan/1.0`. Pinned to the list rather than the tree for the
    # same reason: losing it from either file must fail on its own, not stay
    # hidden behind whichever file still has it.
    ("guardian-agent/1.0", ["sensor/guardian_sensor/lan.py", "sensor/guardian_sensor/sites.py"],
     "a User-Agent on the wire -- same reason"),
    ("guardian-nvr:", "sensor/guardian_sensor/nvr.py",
     "the seed of a sha1 that derives every camera-channel id -- changing the "
     "string changes every id derived from it, and nothing that stored one "
     "can find its camera again"),
    # Not in the brief's original list: this key lives in the hub, not under
    # web/src. `apidocs.py` builds the API reference console's inline JS
    # (CONSOLE_JS) and stores the reader's token in sessionStorage there, so
    # the identifier belongs to this guard, not the web-side one.
    ("guardian.docs.token", "hub/guardian_hub/apidocs.py",
     "the API-reference console's sessionStorage key -- renaming it does not "
     "migrate the stored token, it abandons it, and the reader silently has to "
     "retype it with nothing to indicate why"),

    # ------------------------------------------------------------------
    # Added after the rebrand shipped. The seven entries above were the
    # brief's own list, and a reviewer's note said it froze seven of roughly
    # twenty identifier families -- i.e. the guard's silence about the other
    # thirteen was not evidence they were safe. The rule used to decide what
    # belongs here: freeze it when a rename SILENTLY ORPHANS SOMETHING THAT
    # ALREADY EXISTS -- a row, a file, an installed service, a stored value,
    # a dashboard somebody else wrote. Nothing raises, no test fails, and the
    # damage is only visible to the person who lost the thing.
    #
    # Deliberately NOT frozen, so the next reader does not add them back:
    # thread-name prefixes (`guardian-integration`, `guardian-alert-dispatch`),
    # temp-directory prefixes (`guardian-pdf-`, `guardian-install.XXXXXX`) and
    # log-line prefixes. They name nothing that outlives the process, so a
    # rename costs nothing, and carrying them here is the kind of noise that
    # eventually gets a real entry waved through in review.
    # ------------------------------------------------------------------

    ("guardian.db", "hub/guardian_hub/config.py",
     "the SQLite filename under GUARDIAN_DATA_DIR -- a rename does not move "
     "the database, it creates an empty second one beside it, and the hub "
     "boots cleanly with every tenant, user and scan gone"),
    ("/var/lib/guardian", "sensor/guardian_sensor/paths.py",
     "the sensor's Linux data directory -- it holds the enrolment; a renamed "
     "path re-enrols the sensor as a new node and the old one goes silent"),
    ("~/.guardian", "sensor/guardian_sensor/paths.py",
     "the sensor's macOS data directory -- same failure as /var/lib/guardian"),
    ("com.guardian.sensor", "sensor/guardian_sensor/install.py",
     "the macOS LaunchAgent label -- installed agents are keyed by it, so a "
     "rename leaves the old agent running and unmanageable while the new one "
     "is never loaded"),
    ("com.guardian.dns", "sensor/guardian_sensor/install.py",
     "the DNS filter's LaunchAgent label -- same failure"),
    ("guardian-sensor.service", "sensor/guardian_sensor/install.py",
     "the systemd unit name -- same failure as the LaunchAgent labels, and "
     "`remedy` tells a customer to run `systemctl restart` on this exact name"),
    ("guardian-dns.service", "sensor/guardian_sensor/install.py",
     "the DNS filter's systemd unit -- same failure"),
    ('f"guardian_{self.id}_{interval}"', "hub/guardian_hub/plans.py",
     "the Stripe lookup key. Prices already exist in the live Stripe account "
     "under these keys; renaming resolves nothing and checkout fails for every "
     "plan at once, with the failure visible only to a customer trying to pay"),
    ('"guardian_"', "hub/guardian_hub/billing.py",
     "the reading half of the same Stripe lookup key -- billing parses the "
     "plan id back out of it, so this and plans.py must be changed together "
     "or not at all"),
    ("x-guardian-scope", "hub/guardian_hub/apidocs.py",
     "an OpenAPI extension field -- it is in the published spec, so anything "
     "a customer generated from it reads this name"),
    ("x-guardian-role", "hub/guardian_hub/apidocs.py",
     "the same, for the role"),
    ('localStorage.getItem("guardian.theme")',
     "hub/guardian_hub/reports/builder.py",
     "a frozen report reads the dashboard's OWN theme key so it opens in the "
     "skin the reader chose. It must stay the same literal the app writes -- "
     "web/tests/brandInvariants.test.ts freezes that side; this is the pair"),
    ("CONF.guardian-bak-", "sensor/guardian_sensor/fix.py",
     "the filename a fix writes its rollback copy to. A repair in flight when "
     "the name changes leaves a backup the rollback path can no longer find, "
     "so a failed fix cannot be undone"),

    # User-Agents. A customer may have any of these in an IDS rule, a WAF
    # allowlist or a log filter, the same reason `guardian-scan/1.0` and
    # `guardian-agent/1.0` are frozen above. The two f-string entries carry
    # the `{VERSION}` interpolation because that is what the source segment
    # holds -- see `_string_literals`.
    ("guardian/1.0", ["hub/guardian_hub/integrations/discord.py",
                      "hub/guardian_hub/integrations/homeassistant.py",
                      "hub/guardian_hub/integrations/ntfy.py",
                      "hub/guardian_hub/integrations/pagerduty.py",
                      "hub/guardian_hub/integrations/psa.py",
                      "hub/guardian_hub/integrations/slack.py",
                      "hub/guardian_hub/integrations/teams.py",
                      "hub/guardian_hub/integrations/telegram.py",
                      "hub/guardian_hub/integrations/tickets.py",
                      "hub/guardian_hub/integrations/webhook.py"],
     "the User-Agent every outbound integration puts on the wire"),
    ("guardian-hub/1.0", "hub/guardian_hub/adminnotify.py",
     "the User-Agent on platform notices"),
    ("guardian-live/1.0", "hub/guardian_hub/routers/live.py",
     "the User-Agent on live-feed requests"),
    ('f"guardian-sensor/{VERSION}"', "sensor/guardian_sensor/sync.py",
     "the sensor's own User-Agent to the hub"),
    ('f"guardian-dns/{VERSION}"', "sensor/guardian_sensor/dnsfilter.py",
     "the DNS filter's User-Agent to its upstream resolvers"),
]

#: Prometheus metric names, exported by `routers/public.py` at `/metrics`.
#:
#: Frozen as their own list because they fail differently from everything in
#: FROZEN above: nothing in THIS repo breaks when one is renamed. What breaks
#: is a Grafana dashboard, an alerting rule or a recording rule in somebody
#: else's Prometheus -- a customer's, which we cannot see, cannot test and will
#: not hear about until a panel they rely on has been flat for a week.
#:
#: Each entry keeps the `{{` that opens its label set, so the match is the
#: metric name followed immediately by its labels. A bare name would also be
#: satisfied by a LONGER name containing it, which is exactly the rename most
#: likely to happen by accident (`guardian_site_up` -> `guardian_site_uptime`).
METRICS = [
    "guardian_site_up{{", "guardian_site_latency_seconds{{",
    "guardian_site_cert_days{{", "guardian_host_reachable{{",
    "guardian_host_disk_pct{{", "guardian_host_mem_pct{{",
    "guardian_host_load_per_core{{", "guardian_host_failed_ssh_24h{{",
    "guardian_host_security_updates{{", "guardian_alerts{{",
    "guardian_lan_devices_online{{", "guardian_lan_unverified{{",
    "guardian_speed_down_mbps{{", "guardian_speed_up_mbps{{",
    "guardian_dns_blocked_24h{{", "guardian_data_age_seconds{{",
    "guardian_sensor_up{{",
]


def _string_literals(rel: str) -> str:
    """Every string literal a Python file under `rel` actually holds, joined
    for substring search -- not the file's raw text.

    A `#` comment is not a string literal and `ast.parse` never sees one:
    Python's tokenizer discards comments before the parser runs, so nothing
    a comment says can appear in this output. That is the whole point --
    the first Step 3 run of this guard proved that a raw-text version is
    satisfied by a comment that merely *mentions* a frozen identifier, which
    means it can stay green while the code that actually used the identifier
    is renamed out from under it. `ast.Constant` string values (and, for an
    f-string, the `ast.JoinedStr` node's own source segment, which preserves
    the original quotes and `{...}` interpolations exactly as written) are
    real values the running program holds, so they are what this guard reads.

    Docstrings are `ast.Constant` strings too -- a docstring is just an
    `Expr` statement whose value is a Constant -- and are deliberately NOT
    filtered out here. Excluding them would need its own detector for
    "is this node the first statement of a Module/ClassDef/FunctionDef",
    and it would buy nothing for this guard: the one docstring collision
    this repo actually has (`guardian_session` quoted in three unrelated
    docstrings, explaining what authenticates what) is handled by pinning
    that FROZEN entry to its definer, `deps.py`, above -- not by teaching
    every entry to distrust docstrings everywhere.
    """
    p = ROOT / rel
    paths = [p] if p.is_file() else sorted(p.rglob("*.py"))
    segments: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            is_str_constant = isinstance(node, ast.Constant) and isinstance(node.value, str)
            if is_str_constant or isinstance(node, ast.JoinedStr):
                segment = ast.get_source_segment(source, node)
                if segment is not None:
                    segments.append(segment)
    return "\n".join(segments)


def test_the_frozen_identifiers_are_all_still_there():
    # A `where` that names several files (`guardian-scan/1.0`,
    # `guardian-agent/1.0`) is checked file BY file, not concatenated first --
    # concatenating would let one file lose the literal for free as long as
    # another file on the list still has it, which is the tree-wide hole this
    # guard exists to close. Checking each file on its own also means the
    # failure message below can name the specific file that lost it, which is
    # the first thing the next engineer needs to know.
    missing = []
    for lit, where, why in FROZEN:
        paths = where if isinstance(where, list) else [where]
        for path in paths:
            if lit not in _string_literals(path):
                missing.append((lit, path, why))
    assert not missing, (
        "The Vault rebrand changes strings a customer can read, never an "
        "identifier. These are gone:\n  "
        + "\n  ".join(f"{lit!r} -- gone from {path} -- {why}" for lit, path, why in missing)
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


def test_the_exported_metric_names_are_all_still_there():
    """Every `/metrics` series keeps its name.

    Asserted against `routers/public.py`'s string literals rather than by
    calling the endpoint: the endpoint only emits a series when the tenant
    has data of that kind, so a live check would quietly skip most of this
    list on any fixture that happens not to have, say, a speedtest.
    """
    text = _string_literals("hub/guardian_hub/routers/public.py")
    missing = [m for m in METRICS if m not in text]
    assert not missing, (
        "These Prometheus series were renamed:\n  " + "\n  ".join(missing)
        + "\n\nThey are a published interface. A customer's Grafana panel or "
          "alerting rule keys off the exact name and goes silently flat -- we "
          "do not see it, and they find out late. Put them back.")


def test_the_frozen_list_is_actually_looking_at_the_files():
    """`_string_literals` still reads real literals, and still ignores comments.

    Without this, the guard above has the failure mode its web-side twin
    actually shipped: a helper that returns "" for every path makes every
    `in` test below pass, and the suite reports a rebrand as safe because it
    can no longer see anything. (See hub/tests/test_no_stale_brand.py's
    docstring for how that played out.) Both directions are checked -- a
    literal that IS there, and a comment that must not count as one.
    """
    text = _string_literals("hub/guardian_hub/deps.py")
    assert text, "the helper returned nothing for a file that certainly has strings"
    assert "guardian_session" in text
    # `_string_literals` is fed a path with no Python in it at all -> empty,
    # which is what makes a typo'd path in FROZEN fail loudly rather than pass.
    assert _string_literals("hub/tests/fixtures") == ""
