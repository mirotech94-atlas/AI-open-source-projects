"""Every client site ships the launch basics: robots.txt, a sitemap when the
address is known, a privacy page and a custom 404 (2026-09-28)."""
from __future__ import annotations

from atlas.modules.agency.launch import launch_files, write_launch_files


def test_with_a_known_address_the_sitemap_is_absolute_and_robots_points_at_it():
    files = launch_files("Harbor Dental", "harbordental.example", "207-555-0100", None)
    assert "https://harbordental.example/sitemap.xml" in files["robots.txt"]
    assert "<loc>https://harbordental.example/</loc>" in files["sitemap.xml"]


def test_without_an_address_there_is_no_sitemap_rather_than_a_wrong_one():
    files = launch_files("Harbor Dental", None, None, "hi@harbordental.example")
    assert "sitemap.xml" not in files
    assert "Sitemap:" not in files["robots.txt"]


def test_privacy_and_404_name_the_business_and_escape_it():
    files = launch_files("Tom & <Jerry>", None, "555", None)
    assert "Tom &amp; &lt;Jerry&gt;" in files["privacy.html"]
    assert "<Jerry>" not in files["404.html"]
    assert 'href="/"' in files["404.html"]


def test_privacy_page_promises_nothing_the_site_does_not_do():
    page = launch_files("X", None, None, None)["privacy.html"]
    assert "does not use tracking" in page
    assert "the details on our home page" in page


def test_existing_files_are_never_overwritten(tmp_path):
    (tmp_path / "robots.txt").write_text("mine")
    written = write_launch_files(tmp_path, launch_files("X", None, None, None))
    assert (tmp_path / "robots.txt").read_text() == "mine"
    assert "robots.txt" not in written and "404.html" in written
