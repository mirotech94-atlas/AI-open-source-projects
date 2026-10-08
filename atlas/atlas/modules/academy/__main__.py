"""python -m atlas.modules.academy build <out_dir>: write the storefront."""
from __future__ import annotations

import sys

from .site import main

raise SystemExit(main(sys.argv))
