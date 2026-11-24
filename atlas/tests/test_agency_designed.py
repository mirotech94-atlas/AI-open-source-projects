"""Hand-designed sites: `agency.mockup` registers `<MOCKUP_DIR>/<lead>/index.html`
without the GetLayers licence flag, `agency.build_site` copies it (never
re-renders), pagegate still gates both, and a rollback keeps the design."""
from __future__ import annotations

import json

import pytest

from atlas.modules.agency import _store, build, mockup

LEAD = {"id": 7, "name": "Joe's Barbers", "category": "barber_shop",
        "phone": "(212) 555-0101", "address": "1 Main St, New York, NY 10001",
        "website": "https://joes.example/", "status": "audited"}

PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Joe\'s Barbers'
    '</title><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<meta name="robots" content="noindex"><style data-atlas="theme">body{margin:0}'
    '</style></head><body><header data-atlas="header"><a data-atlas="cta" '
    'href="tel:2125550101">Call (212) 555-0101</a></header><main><h1>HANDMADE-MARK '
    "Joe's Barbers</h1><p>Haircuts and shaves at 1 Main St in New York, with walk-ins "
    "welcome and a chair waiting for you.</p></main><footer data-atlas=\"footer\">"
    '<a href="index.html">Home</a></footer><nav data-atlas="sticky-cta">'
    '<a href="tel:2125550101">Call</a></nav></body></html>')


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENCY_GETLAYERS_LICENCE_OK", raising=False)
    monkeypatch.setattr(mockup, "MOCKUP_DIR", tmp_path / "mockups")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(build, "UPLOADS_DIR", tmp_path / "uploads")
    state: dict = {"mockups": [], "sites": {}, "status": []}

    async def get_lead(_id):
        return dict(LEAD)

    async def insert_mockup(lead_id, path, shot, layers):
        state["mockups"].append((lead_id, path, layers))
        return len(state["mockups"])

    async def delete_mockup(mid):
        state["deleted"] = mid

    async def latest(_id):
        return state["mockups"][-1][1] if state["mockups"] else None

    async def get_site(slug):
        return state["sites"].get(slug)

    async def insert_site(lead_id, slug, domain, path, variants=None):
        state["sites"][slug] = {"slug": slug, "path": path}
        return 1

    async def delete_site(slug):
        state["sites"].pop(slug, None)

    async def set_status(lead_id, status):
        state["status"].append(status)

    async def no_shot(_p):
        return None, "no screenshot: test"

    for name, fn in [("get_lead", get_lead), ("insert_mockup", insert_mockup),
                     ("delete_mockup", delete_mockup), ("latest_mockup_path", latest),
                     ("get_site", get_site), ("insert_site", insert_site),
                     ("delete_site", delete_site), ("set_lead_status", set_status)]:
        monkeypatch.setattr(_store, name, fn)
    monkeypatch.setattr(mockup, "_screenshot", no_shot)
    state["dir"] = tmp_path
    return state


def _design(env, html=PAGE):
    path = mockup.designed_path(7)
    path.parent.mkdir(parents=True)
    path.write_text(html, encoding="utf-8")
    (path.parent / "style.css").write_text("body{}", encoding="utf-8")
    (path.parent / "index.png").write_bytes(b"png")
    return path


async def test_designed_mockup_needs_no_licence_flag(env):
    path = _design(env)
    result = await mockup.make_mockup(7)
    assert result.ok, result.detail
    assert env["mockups"] == [(7, str(path), ["design:hand"])]
    assert env["status"] == ["mocked"]
    dry = await mockup.make_mockup_dry(7)
    assert dry.ok and "hand-designed" in dry.detail


async def test_template_mockup_still_needs_the_licence_flag(env):
    result = await mockup.make_mockup(7)
    assert not result.ok and "AGENCY_GETLAYERS_LICENCE_OK" in result.detail


@pytest.mark.parametrize("html", [
    "<p>just a fragment</p>",
    PAGE.replace("Haircuts and shaves", "The data contains a JSON object, haircuts and shaves"),
])
async def test_designed_page_is_refused_by_pagegate(env, html):
    _design(env, html)
    result = await mockup.make_mockup(7)
    assert not result.ok and "refused the designed page" in result.detail
    assert env["mockups"] == []
    assert not (await mockup.make_mockup_dry(7)).ok


async def test_build_site_copies_the_designed_page_not_a_template(env):
    _design(env)
    assert (await mockup.make_mockup(7)).ok
    result = await build.build_site(7)
    assert result.ok, result.detail
    site = env["dir"] / "sites" / build.slugify(LEAD["name"], 7)
    html = (site / "index.html").read_text()
    assert "HANDMADE-MARK" in html and 'data-family' not in html
    assert (site / "style.css").exists() and not (site / "index.png").exists()
    assert (site / "contact.html").exists()      # the launch pages are added around it
    assert (await build.build_site_dry(7)).ok


async def test_build_site_refuses_a_designed_page_that_went_bad(env):
    path = _design(env)
    assert (await mockup.make_mockup(7)).ok
    path.write_text("<p>fragment</p>", encoding="utf-8")
    result = await build.build_site(7)
    assert not result.ok and "nothing was written" in result.detail
    assert not (env["dir"] / "sites").exists()


async def test_rollbacks_remove_rows_and_files_but_keep_the_design(env):
    path = _design(env)
    made = await mockup.make_mockup(7)
    built = await build.build_site(7)
    assert built.ok
    site = env["dir"] / "sites" / build.slugify(LEAD["name"], 7)
    assert (await build.build_site_rollback(built.rollback_token)).ok
    assert not site.exists() and env["sites"] == {}
    assert (await mockup.make_mockup_rollback(made.rollback_token)).ok
    assert env["deleted"] == 1
    assert path.exists(), "the design is the source; a rollback must not delete it"
    assert json.loads(made.rollback_token)["html_path"] is None
