"""Part A1: the buyer's course page (atlas/modules/academy/course_page.py)."""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from html import escape

import pytest

from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import course_page as cp
from atlas.modules.academy import stripe_read

SID = "cs_test_a1B2c3D4e5F6g7H8i9J0"


def _raw() -> dict:
    return copy.deepcopy(json.loads(cat_mod.CATALOG_PATH.read_text()))


def _cat(raw: dict | None = None) -> cat_mod.Catalog:
    return cat_mod.parse(raw or _raw())


def _offer(cat, offer_id):
    if offer_id == cat.bundle.offer.id:
        return cat.bundle.offer
    return next(o for p in cat.products for o in p.offers if o.id == offer_id)


def _verdict(*offers) -> stripe_read.Verdict:
    return stripe_read.Verdict(status="paid", session_id=SID, offers=tuple(offers))


def _links(v, *, missing=()) -> dict[str, cp.FileLink]:
    out = {}
    for o in v.offers:
        for f in o.files:
            if f.key in missing:
                out[f.key] = cp.FileLink(present=False)
            else:
                url = f"/public/academy/file/TOKEN-{f.key}"
                out[f.key] = cp.FileLink(present=True, href=url, view_href=url + "?view=1",
                                         facts=" <span>(1.0 MB)</span>")
    return out


def _page(cat, v, *, sidecars=None, links=None, nonce="N0NCE"):
    return cp.render(cat, v, links if links is not None else _links(v),
                     sidecars or {}, contact_html="help@example.com", nonce=nonce)


def _lesson_numbers(html: str) -> list[str]:
    return re.findall(r'data-num="([^"]*)"', html)


def _module_titles(html: str) -> list[str]:
    return re.findall(r'<h3 class="module">(.*?)</h3>', html)


# ------------------------------------------------------------------ structure

def test_operator_modules_follow_the_catalog_order():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "safe-ai-operator")))
    titles = _module_titles(page)
    inside = [escape(m[0]) for m in next(p for p in cat.products
                                    if p.slug == "safe-ai-operator").inside]
    assert titles == [t for t in inside if t in titles] and len(titles) >= 10
    assert titles[0].startswith("Module 0")
    assert _lesson_numbers(page)[:3] == ["0.1", "0.2", "1.1"]
    assert page.count("<video") == 1


def test_adults_lessons_sit_under_their_modules_with_gaps_in_numbering():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "ai-for-every-age-adults")))
    titles = _module_titles(page)
    assert "Module 8" not in " ".join(titles)           # the catalog has no module 8
    assert titles[0] == "Module 0: Start here" and "Module 9: Capstone and certificate" in titles
    assert len(_lesson_numbers(page)) == 60
    assert page.count("<video") == 1


def test_sixty_plus_is_one_course_without_a_repeated_module_heading():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "ai-for-every-age-60plus")))
    assert len(_lesson_numbers(page)) == 17
    assert "Safe and Simple AI 60+" in page
    assert _module_titles(page) == []


def test_sell_course_and_its_ebook_in_one_purchase():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "sell-with-ai-both")))
    assert len(_lesson_numbers(page)) == 27
    assert "Module 1: Clipping, with permission" in _module_titles(page)
    assert "sell-with-ai.pdf" not in page                # no file path is ever printed
    assert "TOKEN-sell-ebook-pdf" in page                # the ebook is in the materials


def test_both_editions_make_two_course_sections_and_one_player():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "ai-for-every-age-both")))
    assert page.count('<section class="course"') == 2
    assert len(_lesson_numbers(page)) == 77
    assert page.count("<video") == 1
    assert "Everyday AI for Adults" in page and "Safe and Simple AI 60+" in page


def test_the_bundle_shows_each_course_once_and_all_materials():
    cat = _cat()
    page = _page(cat, _verdict(cat.bundle.offer))
    assert page.count('<section class="course"') == 3  # Operator, Adults, 60+
    assert page.count("<video") == 1
    assert "TOKEN-lessons-pdf" in page and "TOKEN-vault-kit-zip" in page


def test_a_lesson_whose_number_is_in_no_module_goes_under_lessons():
    raw = _raw()
    prod = next(p for p in raw["products"] if p["slug"] == "safe-ai-operator")
    vid = next(f for f in prod["offers"][0]["files"] if f["path"].endswith(".mp4"))
    vid["label"] = "Lesson 42.7: Extra (video)"
    cat = _cat(raw)
    page = _page(cat, _verdict(_offer(cat, "safe-ai-operator")))
    assert _module_titles(page)[-1] == "Lessons"
    assert "42.7" in _lesson_numbers(page)


# ------------------------------------------------------------------ the player

def test_the_player_is_one_lazy_inline_video_with_prev_next_and_a_download():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "safe-ai-operator")))
    assert page.count("<video") == 1
    assert 'preload="none"' in page and "playsinline" in page and "controls" in page
    assert 'preload="metadata"' not in page
    assert "Previous" in page and "Next" in page and "Download this lesson" in page


def test_without_javascript_every_lesson_is_still_a_link():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "safe-ai-operator")))
    assert page.count('class="lesson"') == 28
    assert 'href="/public/academy/file/TOKEN-operator-video-1-1?view=1"' in page
    assert "<noscript>" in page


def test_both_pdf_versions_are_in_the_materials_when_the_catalog_has_them():
    raw = _raw()
    prod = next(p for p in raw["products"] if p["slug"] == "30-business-lessons")
    base = prod["offers"][0]["files"][0]
    prod["offers"][0]["files"].append({
        "key": base["key"] + "-print", "label": "30 Business Lessons (print version)",
        "path": base["path"].replace(".pdf", "-print.pdf")})
    cat = _cat(raw)
    page = _page(cat, _verdict(_offer(cat, "30-business-lessons")))
    assert "Read on screen" in page and "Print version" in page
    assert page.count('class="pdf"') == 2
    assert "TOKEN-lessons-pdf-print?view=1" in page and "TOKEN-lessons-pdf?view=1" in page


def test_a_pdf_without_a_print_twin_keeps_the_existing_reader():
    raw = _raw()
    for prod in raw["products"]:
        for off in prod["offers"]:
            off["files"] = [f for f in off["files"] if not f["key"].endswith("-print")]
    cat = _cat(raw)
    page = _page(cat, _verdict(_offer(cat, "30-business-lessons")))
    assert page.count('class="pdf"') == 1
    assert "Open full screen and print" in page and "Download PDF" in page
    assert "<h1>Your files</h1>" in page and "<video" not in page


def test_a_missing_file_says_so_and_has_no_link():
    cat = _cat()
    v = _verdict(_offer(cat, "safe-ai-operator"))
    page = _page(cat, v, links=_links(v, missing={"operator-video-1-1", "operator-starter"}))
    assert page.count("Not on the server yet") == 2
    assert "TOKEN-operator-video-1-1" not in page and "help@example.com" in page


def test_zips_are_plain_downloads():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "claude-obsidian-vault-kit")))
    assert "TOKEN-vault-kit-zip" in page and "download" in page and "<video" not in page


# ------------------------------------------------------------------ safety

def test_every_label_is_escaped():
    raw = _raw()
    prod = next(p for p in raw["products"] if p["slug"] == "safe-ai-operator")
    prod["offers"][0]["files"][0]["label"] = 'Lesson 0.1: <script>alert(1)</script>"x (video)'
    cat = _cat(raw)
    page = _page(cat, _verdict(_offer(cat, "safe-ai-operator")))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert page.count("<script") == 1                   # only ours, carrying the nonce
    assert '<script nonce="N0NCE">' in page


def test_the_script_keeps_no_session_id_and_no_path():
    cat = _cat()
    v = _verdict(_offer(cat, "ai-for-every-age-both"))
    page = _page(cat, v)
    script = re.search(r'<script nonce="N0NCE">(.*?)</script>', page, re.DOTALL).group(1)
    assert SID not in script and ".mp4" not in script and "/public/" not in script
    assert "localStorage" in script and "try" in script
    assert SID not in page.replace("TOKEN-", "")


def test_the_csp_is_the_agreed_one_with_the_nonce():
    csp = cp.csp("abc123")
    assert "default-src 'none'" in csp and "script-src 'nonce-abc123'" in csp
    assert "media-src 'self'" in csp and "frame-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    assert "form-action 'self'" in csp and "unsafe-eval" not in csp
    assert "script-src 'unsafe-inline'" not in csp


# ------------------------------------------------------------------ durations

def _sidecar(seconds: dict[str, float], at="2026-11-14T15:00:00+00:00") -> cp.Sidecar:
    return cp.Sidecar(read_at=datetime.fromisoformat(at), seconds=seconds)


def test_durations_come_only_from_the_sidecar_and_carry_its_date():
    cat = _cat()
    off = _offer(cat, "safe-ai-operator")
    vids = [f for f in off.files if f.path.endswith(".mp4")]
    side = _sidecar({f.path.rsplit("/", 1)[1]: 300.0 for f in vids})
    page = _page(cat, _verdict(off), sidecars={"safe-ai-operator/videos": side})
    assert "28 lessons" in page and "2 h 20 min" in page and "measured 14 Nov 2026" in page
    assert "5:00" in page


def test_no_sidecar_means_no_durations_and_no_guess():
    cat = _cat()
    page = _page(cat, _verdict(_offer(cat, "safe-ai-operator")))
    assert "28 lessons" in page
    assert "measured" not in page and " min" not in page.split("<h3")[0]
    assert not re.search(r"\b\d+:\d\d\b", page)


def test_a_partial_sidecar_shows_those_lessons_but_no_total():
    cat = _cat()
    side = _sidecar({"lesson-0.1.mp4": 61.0})
    page = _page(cat, _verdict(_offer(cat, "safe-ai-operator")),
                 sidecars={"safe-ai-operator/videos": side})
    assert "1:01" in page and "measured" not in page and " h " not in page


# ------------------------------------------------------------------ sidecar file

def test_a_valid_sidecar_file_loads_and_is_cached_by_mtime(tmp_path):
    d = tmp_path / "course" / "videos"
    d.mkdir(parents=True)
    (d / "index.json").write_text(json.dumps(
        {"read_at": "2026-11-14T15:00:00Z", "lessons": {"lesson-1.1.mp4": {"seconds": 62.5}}}))
    s = cp.load_sidecar(tmp_path, "course/videos")
    assert s is not None and s.seconds == {"lesson-1.1.mp4": 62.5}
    assert s.read_at.astimezone(UTC).year == 2026
    assert cp.load_sidecar(tmp_path, "course/videos") is s       # same mtime, same object


@pytest.mark.parametrize("body", [
    "not json", "[]", "{}", '{"read_at": "yesterday", "lessons": {}}',
    '{"read_at": "2026-11-14T15:00:00Z", "lessons": []}',
    '{"read_at": "2026-11-14T15:00:00Z", "lessons": {"a.mp4": {"seconds": -3}}}',
    '{"read_at": "2026-11-14T15:00:00Z", "lessons": {"a.mp4": {"seconds": "x"}}}',
])
def test_a_missing_or_malformed_sidecar_is_none(tmp_path, body):
    d = tmp_path / "v"
    d.mkdir()
    assert cp.load_sidecar(tmp_path, "v") is None
    (d / "index.json").write_text(body)
    assert cp.load_sidecar(tmp_path, "v") is None


def test_a_symlinked_sidecar_is_refused(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    real = tmp_path / "real.json"
    real.write_text('{"read_at": "2026-11-14T15:00:00Z", "lessons": {}}')
    (d / "index.json").symlink_to(real)
    assert cp.load_sidecar(tmp_path, "v") is None


def test_video_dirs_name_each_course_folder_once():
    cat = _cat()
    v = _verdict(cat.bundle.offer)
    assert cp.video_dirs(v) == ["safe-ai-operator/videos", "ai-for-every-age/videos-adults",
                                "ai-for-every-age/videos-60plus"]
