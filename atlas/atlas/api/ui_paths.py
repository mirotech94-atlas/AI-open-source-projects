"""Where the built dashboard lives, resolved once for everything that needs it.

`main.py` serves the bundle from here, including `/wake`, and `voice.py`
decides what the wake detectors can use by looking here too. Both read the
same `STATIC`, so a capability can only say a model is present when the
server is actually able to hand that model to the browser. Checking the
source tree (`ui/public/wake/`) instead would report a file that was fetched
but never built as ready, while the browser received the dashboard page in
its place.
"""
from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"


def wake_dir() -> Path:
    """What `/wake` serves: the model files Vite copied from `ui/public/wake/`.

    Read at call time rather than import time, so the answer follows the
    current build (and a test can point it elsewhere).
    """
    return STATIC / "wake"
