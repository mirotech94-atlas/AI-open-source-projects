"""Whether a real headless Chromium can be launched here.

`reel_design.available()` only checks that the playwright package imports.
The CI image has the package but no browser download, so tests that launch
Chromium must also check that the executable exists, or they fail instead
of skipping (pipeline 273, 2026-10-19).
"""
from __future__ import annotations

import os


def chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            return os.path.exists(p.chromium.executable_path)
    except Exception:  # noqa: BLE001
        return False
