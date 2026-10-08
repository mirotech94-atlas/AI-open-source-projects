"""Compatibility shim. All .env writes go through envlock.py now.

Kept so that callers piping NAME=value lines into `python3 setenv.py`
keep working -- google_login.py among them -- while gaining the lock they
did not have. See envlock.py for why the lock exists.
"""
from __future__ import annotations

import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
for candidate in (os.path.join(here, "envlock.py"), "/opt/atlas/envlock.py"):
    if os.path.exists(candidate):
        sys.argv = [candidate] + sys.argv[1:]
        runpy.run_path(candidate, run_name="__main__")
        break
else:
    sys.exit("envlock.py not found beside setenv.py or in /opt/atlas")
