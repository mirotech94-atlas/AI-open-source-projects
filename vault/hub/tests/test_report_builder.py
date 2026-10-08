import re

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import demoscrub, models as m
from guardian_hub.payloads import assets
from guardian_hub.reports import builder, presets, sections, compliance


def test_full_report_has_every_section_and_no_nexus_branding(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    html = builder.build_report(db, t, days=30, preset="full")
    for _, title in sections.SECTION_TITLES.items():
        assert title in html, title
    assert "VAULT" in html and "NEXUS" not in html.upper().replace("VAULT", "")
    assert re.search(r"<section class=\"sec\"><h2><span class=\"num\">01</span>", html)
    assert "<svg" in html and "@page" in html


def test_presets_are_subsets_in_canonical_order(client):
    for key, (label, keys) in presets.PRESETS.items():
        assert label and set(keys) <= set(sections.SECTION_KEYS), key
        assert keys == [k for k in sections.SECTION_KEYS if k in keys], f"{key} out of order"
    assert presets.PRESETS["full"][1] == sections.SECTION_KEYS


def test_history_lines_rebuild_the_nexus_shape(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    rows = builder.history_lines(db, t.id, since="2000-01-01T00:00:00+00:00")
    assert rows and set(rows[0]) >= {"ts", "sites", "hosts", "lan", "score", "live_macs"}
    assert isinstance(rows[0]["sites"], dict) and "ok" in next(iter(rows[0]["sites"].values()))
    assert rows == sorted(rows, key=lambda r: r["ts"])


def test_compliance_answers_twelve_controls(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    rows = compliance.rows(builder.last_of(db, t))
    assert len(rows) == 12 and all(set(r) == {"area", "control", "met"} for r in rows)


def test_presets_select_the_right_sections(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    for key, (_label, keys) in presets.PRESETS.items():
        html = builder.build_report(db, t, days=30, preset=key)
        for k in keys:
            assert sections.SECTION_TITLES[k] in html, f"{key} missing {k}"


def _section_body(html: str, heading: str) -> str:
    """The full `<section class="sec">...</section>` block whose heading is
    `heading` -- everything from that section's own opening tag up to the
    next section's opening tag (or the end of the doc)."""
    # From the first section on: the cover's table of contents names every
    # heading before its section exists, so a bare `index(heading)` lands in
    # the contents, before any `<section>` there is to slice.
    first = html.index('<section class="sec">')
    idx = html.index(heading, first)
    start = html.rindex('<section class="sec">', 0, idx)
    nxt = html.find('<section class="sec">', idx + 1)
    return html[start:] if nxt == -1 else html[start:nxt]


def test_device_group_sections_split_devices(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    html = builder.build_report(db, t, days=30, preset="full")
    assert html.count("<!doctype html>") == 1
    assert "<html>" in html and "</html>" in html

    # tests/fixtures/state.json's known_macs seeds a Reolink-branded device
    # (mac ec:71:db:05:5a:aa, label "Reolink camera") that `kind_of` classifies
    # as a camera from its vendor/label text alone (no explicit `kind`), so it
    # must land under "Devices — cameras" and nowhere else.
    cam_section = _section_body(html, "Devices — cameras")
    people_section = _section_body(html, "Devices — phones, tablets & computers")
    assert "Reolink camera" in cam_section
    assert "Reolink camera" not in people_section

    # The `cameras` preset renders only the camera device section (plus its
    # other listed sections) -- no other device-group section leaks in.
    cam_html = builder.build_report(db, t, days=30, preset="cameras")
    assert "Reolink camera" in cam_html
    assert "Devices — cameras" in cam_html
    assert "Devices — phones, tablets & computers" not in cam_html
    assert "Devices — TVs & media" not in cam_html


def test_hardening_scorecard_reuses_assets_grade(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    last = builder.last_of(db, t)
    host = next(h for h in last["ssh"] if h.get("name") == demoscrub.map_host_name("safehome"))
    expected = assets.hardening(host)

    html = builder.build_report(db, t, days=30, preset="full")
    posture_html = _section_body(html, "Hardening scorecard")
    assert f"<b>{expected.score}</b>" in posture_html
    assert f"<b>{expected.grade}</b>" in posture_html


def test_history_lines_convert_latency_ms_to_seconds(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    rows = builder.history_lines(db, t.id, since="2000-01-01T00:00:00+00:00")
    # the fixture's first site, as the demo scrub renames it; baseline
    # latency_s = 0.5, jittered +/-25%
    import json, pathlib
    first = json.loads((pathlib.Path(__file__).parent / "fixtures" / "last.json").read_text())["sites"][0]["name"]
    site = demoscrub.map_site_name(first)
    lats = [r["sites"][site]["lat"] for r in rows if site in r["sites"] and r["sites"][site]["lat"] is not None]
    assert lats
    avg = sum(lats) / len(lats)
    assert 0.3 < avg < 0.7, f"expected ~0.5s (converted from ms), got {avg}"

    html = builder.build_report(db, t, days=30, preset="full")
    m_ = re.search(r"(\d+\.\d+)s / (\d+\.\d+)s", html)
    assert m_, "latency avg/max cell not found in the availability section"
    avg_disp, max_disp = float(m_.group(1)), float(m_.group(2))
    # before the fix these were the raw millisecond values (e.g. ~500s);
    # a real home-network latency is well under a second.
    assert avg_disp < 5 and max_disp < 5


def test_brand_domain_falls_back_to_first_site_host(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()

    # branch 1: no brand.domain set -> falls back to the first monitored
    # site's bare host (not the full "https://...` URL).
    t.brand = {**(t.brand or {}), "domain": None}
    db.add(t)
    db.commit()
    html = builder.build_report(db, t, days=30, preset="full")
    last = builder.last_of(db, t)
    first_site_host = last["sites"][0]["url"].split("//", 1)[-1].split("/", 1)[0]
    assert first_site_host in html
    assert f"https://{first_site_host}" not in html.split('class="brand"')[1][:200]

    # branch 2: brand.domain set -> wins over the site fallback.
    t.brand = {**(t.brand or {}), "domain": "example-guardian.test"}
    db.add(t)
    db.commit()
    html2 = builder.build_report(db, t, days=30, preset="full")
    assert "example-guardian.test" in html2


def test_render_to_file_writes_and_inserts_report_row(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    report, path = builder.render_to_file(db, t, days=7, preset="executive", kind="manual")
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")
    row = db.query(m.Report).filter_by(id=report.id).one()
    assert row.preset == "executive"
    assert row.meta.get("scans", 0) >= 1


def test_device_and_presence_sections_split_per_network(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    html = builder.build_report(db, t, days=30, preset="full")
    assert html.count("Device inventory") >= 2
    assert "Office Wi-Fi — Device inventory" in html and "Lab — Device inventory" in html
    assert "Office Wi-Fi — Screen time" in html and "Lab — Screen time" in html
    # anchored to the "Networks watched" KPI tile itself, not just a bare
    # ">3<" that could match unrelated markup anywhere else in the report.
    # 3, not 2: the demo's own recorder (Task 83's camera census fixture)
    # owns a third network -- its cameras' -- alongside Office Wi-Fi and Lab.
    assert '<div class="k">Networks configured</div><div class="v">3</div>' in html
    # a Lab-only device appears under the Lab block, never under the Wi-Fi one
    lab_block = html.split("Lab — Device inventory", 1)[1].split("</section>", 1)[0]
    assert "10.10.0." in lab_block and "192.168.1." not in lab_block

    # Fix round 1, finding 3: the per-network screen-time table must keep the
    # "Last 60 scans" heartbeat column (presence_sample.network_id, Task 51)
    # rather than silently dropping it the way the initial split did.
    office_block = html.split("Office Wi-Fi — Screen time", 1)[1].split("</section>", 1)[0]
    lab_screen_block = html.split("Lab — Screen time", 1)[1].split("</section>", 1)[0]
    assert "Last 60 scans" in office_block and '<div class="hbr">' in office_block
    assert "Last 60 scans" in lab_screen_block and '<div class="hbr">' in lab_screen_block


def test_network_reachability_notes_only_for_unreachable_status(client):
    """Fix round 1, finding 2: the executive-summary note about a network is
    tied to `status == "unreachable"` specifically -- "pending" (the
    just-created default) and "no-sensor" (in the single-network case) must
    stay silent, and "no-sensor" in the multi-network case gets its own
    sensor-needed wording rather than "could not reach"."""
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()

    # Single-network tenant: collapse to just the primary network.
    db.query(m.Network).filter(m.Network.tenant_id == t.id, m.Network.is_primary.is_(False)).delete()
    db.commit()
    primary = db.query(m.Network).filter_by(tenant_id=t.id, is_primary=True).one()

    primary.status = "pending"
    db.add(primary); db.commit()
    html_pending = builder.build_report(db, t, days=30, preset="executive")
    assert "could not reach" not in html_pending.lower()
    assert "needs a sensor" not in html_pending.lower()

    primary.status = "no-sensor"
    db.add(primary); db.commit()
    html_no_sensor = builder.build_report(db, t, days=30, preset="executive")
    # single-network case: never a note unless actually unreachable.
    assert "could not reach" not in html_no_sensor.lower()
    assert "needs a sensor" not in html_no_sensor.lower()

    primary.status = "unreachable"
    db.add(primary); db.commit()
    html_unreachable = builder.build_report(db, t, days=30, preset="executive")
    assert f"could not reach the {primary.name} network" in html_unreachable


def test_a_network_with_no_devices_still_gets_a_block(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    db.add(m.Network(tenant_id=t.id, name="Guest Wi-Fi", slug="guest-wi-fi", kind="guest",
                     subnet_prefix="192.168.5", expected_gateway="192.168.5.1", status="no-sensor"))
    db.commit()
    html = builder.build_report(db, t, days=7, preset="network")
    assert "Guest Wi-Fi — Device inventory" in html
    assert "no devices seen on this network" in html
    # a "no-sensor" network (never "unreachable") gets the sensor-needed
    # note, not the "could not reach" wording that is reserved for a network
    # that actually went unreachable during the period.
    assert "needs a sensor on this network" in html
    assert "could not reach" not in html.lower()


def test_section_keys_and_presets_are_unchanged_by_the_split():
    assert len(sections.SECTION_KEYS) == 20 and presets.PRESETS["full"][1] == sections.SECTION_KEYS


def test_devices_section_with_zero_devices_still_renders_by_manufacturer(client):
    """Fix round 1, finding 1: `sections.devices()`'s per-network early
    return must not also fire for the legacy `network=None` path -- a
    single-network tenant with zero known devices should still get the "By
    manufacturer" heading + `svg_bars` chart the pre-Task-54 code emitted
    unconditionally (empty chart included)."""
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    db.query(m.Device).filter_by(tenant_id=t.id).delete()
    db.query(m.Network).filter(m.Network.tenant_id == t.id, m.Network.is_primary.is_(False)).delete()
    db.commit()

    html = builder.build_report(db, t, days=30, preset="full")
    dev_section = _section_body(html, "Device inventory")
    assert "By manufacturer" in dev_section
    # svg_bars([]) renders its own "no data" placeholder rather than an
    # empty <svg> -- the fix is that the heading + placeholder appear at
    # all (before the fix, the whole tail after the empty table vanished).
    assert '<div class="muted">no data</div>' in dev_section


def test_network_name_is_escaped_in_report_html(client):
    """A network name is tenant-supplied text rendered straight into the
    report's HTML (net_heading / the reachability note) -- it must come out
    escaped, never as live markup."""
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    payload = "<script>alert(1)</script>"
    db.add(m.Network(tenant_id=t.id, name=payload, slug="xss-net", kind="guest",
                      subnet_prefix="192.168.9", expected_gateway="192.168.9.1", status="unreachable"))
    db.commit()

    html = builder.build_report(db, t, days=30, preset="full")
    assert payload not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --------------------------------------------------------------------------
# Executive report (design/4-executive-report.html)
# --------------------------------------------------------------------------

def test_exec_report_is_not_a_section_preset(client):
    """`exec_report` is deliberately outside `presets.PRESETS` (see that
    module's `SPECIAL_PRESETS` comment) -- it must never silently start
    behaving like an ordinary section-subset preset again."""
    assert "exec_report" not in presets.PRESETS
    assert "exec_report" in presets.SPECIAL_PRESETS
    label, keys = presets.label_and_sections("exec_report")
    assert label == "Executive decision report" and keys == []


def test_exec_report_renders_the_one_page_document(client):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    html = builder.build_report(db, t, days=7, preset="exec_report")

    assert html.startswith("<!doctype html>")
    assert "<!doctype html>" not in html[20:]  # exactly one document
    # The document's own structure, not the generic 19-section engine's --
    # none of `sections.SECTION_TITLES` print here.
    assert '<div class="verdict">' in html
    assert '<div class="row">' in html and 'class="card score-card"' in html and 'class="card ded-card"' in html
    assert "Handled without anyone being asked" in html
    assert "Needs a decision" in html
    assert '<div class="strip">' in html
    for title in sections.SECTION_TITLES.values():
        assert title not in html, title
    # Self-hosted fonts only -- no Google Fonts CDN link, satisfying the
    # "must render with no internet" constraint.
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert '"/fonts/source-serif-4-latin-var.woff2"' in html
    assert 'localStorage.getItem("guardian.theme")' in html


def test_exec_report_score_card_matches_overview_and_triage(client):
    """The report must never disagree with what Overview/Inbox say right
    now -- it is built from the same two payloads, not a second reading of
    the underlying tables."""
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    from guardian_hub.payloads import overview as overview_payload, triage as triage_payload_mod

    ov = overview_payload.build(db, t)
    triage = triage_payload_mod.build(db, t, role="admin")
    html = builder.build_report(db, t, days=7, preset="exec_report")

    assert f'<span class="score-num">{ov.score}</span>' in html
    assert f'<span class="score-grade">{ov.grade}</span>' in html
    assert f'{triage.outcomes.handled} item(s), {triage.outcomes.handled_period}' in html
    assert f'{triage.outcomes.needs_yes} need a yes' in html
    assert f'{triage.outcomes.needs_you} need you directly' in html
    for d in ov.deductions:
        assert f"&minus;{d.points}" in html
        assert d.why in html  # the sensor's own sentence, never rewritten


def test_exec_report_never_prints_a_deduction_it_does_not_have(client):
    """No score, no deductions -> the report says so instead of printing a
    misleading zero or an empty table."""
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    db.query(m.Scan).filter_by(tenant_id=t.id).delete()
    db.commit()

    html = builder.build_report(db, t, days=7, preset="exec_report")
    assert "has not finished a first scan" in html
    assert "Nothing was deducted this period" in html


def test_exec_report_print_css_overrides_every_dark_variable_with_important(client):
    """Regression: the print block used to reset only the 5 custom
    properties that visibly differ, and without `!important` its plain
    `:root` selector loses the cascade to the higher-specificity
    `:root[data-theme="dark"]` rule anyway -- either gap means a reader who
    picked the dark skin gets a dark printout. Every property the dark skin
    redefines must be restated here, `!important`."""
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    html = builder.build_report(db, t, days=7, preset="exec_report")

    print_block = re.search(r"@media print\{(.*?)\n\}\n@media \(max-width", html, re.S)
    assert print_block, "no @media print block found"
    root_rule = re.search(r":root\{([^}]*)\}", print_block.group(1))
    assert root_rule, "no :root override inside @media print"
    for token in ("--paper", "--ink", "--ink-soft", "--line", "--signal", "--signal-bg",
                  "--handled", "--handled-bg", "--card-bg", "--shadow"):
        assert token in root_rule.group(1), token
    assert root_rule.group(1).count("!important") == 10


def test_exec_report_share_link_and_expiry_are_unaffected(client):
    """The share mechanism (`/r/<token>`, expiry) is format-agnostic -- an
    `exec_report` must go through it exactly like any other report."""
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    report, path = builder.render_to_file(db, t, days=7, preset="exec_report", kind="manual")
    assert path.exists()
    assert report.title == "Executive security report"
    token = builder.mint_share_token(report, hours=48)
    db.commit()

    r = client.get(f"/r/{token}")
    assert r.status_code == 200 and "Executive security report" in r.text

    report.share_expires = report.share_expires.replace(year=2000)
    db.commit()
    r2 = client.get(f"/r/{token}")
    assert r2.status_code == 410
