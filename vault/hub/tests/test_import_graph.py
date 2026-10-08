"""Every module in `guardian_hub` imports on its own, from a cold interpreter.

**Why this is not covered by the rest of the suite, and cannot be.** A circular
import only raises when the cycle is entered at the wrong node. `app.py` imports
the routers first, which happens to walk the graph in an order where every name
is already bound by the time anything asks for it, so the running hub never sees
the cycle -- and neither does pytest, whose `conftest` imports the app before
the first test runs. From then on every module in the process is already in
`sys.modules` and every later import is a cache hit. The suite could be entirely
green with half the package unimportable, and for a long time it was:

    >>> python -c "import guardian_hub.chat.providers"
    ImportError: cannot import name 'FIX_LABELS' from partially initialized
    module 'guardian_hub.remedy'

Eleven of the hundred and forty-nine modules failed that way. What they all had
in common was one edge -- `remedy` importing `chat.registry`/`chat.tools` at
module scope, closing `remedy -> chat -> routers.write -> payloads.jobs ->
remedy` -- and `payloads/jobs.py` reading `remedy.FIX_LABELS`, which is defined
*below* that import block. Removing the edge (the names are now imported inside
the one function that uses them; see `remedy.py`'s import-block comment) took
all eleven to zero at once.

**Why a subprocess.** The check has to purge `guardian_hub*` from `sys.modules`
between modules, and doing that inside the pytest process would hand every later
test a second copy of `models` with its own SQLAlchemy registry. One clean
interpreter, one report, nothing shared.

This is the kind of failure that surfaces first to whoever writes a small CLI
script or a one-off import and is told the package is broken. It should surface
here instead.
"""
import pathlib
import subprocess
import sys

PROBE = r'''
import importlib, json, pkgutil, sys
import guardian_hub

bad = {}
names = sorted(m.name for m in pkgutil.walk_packages(guardian_hub.__path__, "guardian_hub."))
for name in names:
    for cached in [k for k in sys.modules if k.startswith("guardian_hub")]:
        del sys.modules[cached]
    try:
        importlib.import_module(name)
    except ImportError as e:
        # Only cycles. An optional third-party dependency that is not
        # installed in this venv is a different problem with a different fix,
        # and failing on it here would make this guard flap on developer
        # machines rather than report the thing it is for.
        text = str(e)
        if "partially initialized" in text or "circular" in text:
            bad[name] = text
    except Exception:
        # Likewise: a module that raises at import for its own reasons (a
        # missing env var, say) is out of scope. This guard answers one
        # question -- is the import graph acyclic enough to import cold.
        pass
print(json.dumps({"count": len(names), "bad": bad}))
'''


def test_every_module_imports_from_a_cold_interpreter():
    import json

    root = pathlib.Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                         text=True, cwd=root)
    assert out.returncode == 0, f"probe failed:\n{out.stderr}"
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert result["count"] > 100, (
        f"only walked {result['count']} modules -- the probe is not finding the "
        "package, so a green result here means nothing")
    assert not result["bad"], (
        "These modules cannot be imported on their own -- there is a cycle in "
        "the import graph:\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in sorted(result["bad"].items()))
        + "\n\nBreak the edge at the LOWER-level module (the one the other "
          "depends on), by moving the import into the function that uses it. "
          "See the import-block comment in guardian_hub/remedy.py for the "
          "worked example.")
