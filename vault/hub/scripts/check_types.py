#!/usr/bin/env python
"""Fail the build when `schemas_dash.py` and `web/src/types.ts` drift apart.

Three comparisons, in this order:
1. every pydantic model in `guardian_hub.schemas_dash` has an
   `export type <Name>` in `web/src/types.ts`;
2. every `export type <Name>` in types.ts has a model behind it;
3. every OpenAPI component schema that IS one of those models has a TS
   mirror (catches a router that starts returning a payload whose TS type
   was deleted).

OpenAPI components that are NOT dashboard models -- the auth/session schemas
in `guardian_hub.schemas`, plus FastAPI's own HTTPValidationError /
ValidationError -- are deliberately ignored: they are mirrored in
`web/src/api.ts`, not in types.ts, and must not be added there.

Usage: `python scripts/check_types.py` (exit 0 = in sync).
"""

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve()
HUB = HERE.parents[1]
TYPES_TS = HUB.parent / "web" / "src" / "types.ts"

sys.path.insert(0, str(HUB))

# TS-only helper unions derived from a const tuple (e.g. `LiveEventType =
# (typeof LIVE_EVENT_TYPES)[number]`) -- there is no pydantic model behind
# these, so they are never expected to appear in schemas_dash.py.
TS_ONLY_ALIASES = {"LiveEventType"}

# Models with deliberately no `export type` yet. EMPTY as of the phase-4
# closeout merge: it only ever carried `ChatBullet`/`ChatEntity` while the
# hub's chat payloads were ahead of the web, and phase3-web mirrors both.
# Add an entry here only alongside the fix round that will remove it.
PY_ONLY_ALIASES: set[str] = set()


def main() -> int:
    from guardian_hub import schemas_dash as sd
    from guardian_hub.main import create_app

    dash = {n for n in dir(sd) if n[:1].isupper() and hasattr(getattr(sd, n), "model_fields")} - PY_ONLY_ALIASES
    if not TYPES_TS.exists():
        print(f"FAIL: {TYPES_TS} does not exist")
        return 1
    src = TYPES_TS.read_text()
    ts = set(re.findall(r"^export type (\w+)", src, re.M)) - TS_ONLY_ALIASES

    problems: list[str] = []
    for name in sorted(dash - ts):
        problems.append(f"missing from web/src/types.ts: {name}")
    for name in sorted(ts - dash):
        problems.append(f"missing from guardian_hub/schemas_dash.py: {name}")

    schemas = (create_app().openapi().get("components") or {}).get("schemas") or {}
    # pydantic v2 + FastAPI split a model used for both input and output into
    # `<Name>-Input` / `<Name>-Output` components.
    exposed = {re.sub(r"-(Input|Output)$", "", n) for n in schemas}
    for name in sorted((exposed & dash) - ts):
        problems.append(f"served by the API but absent from types.ts: {name}")

    # Field-level parity for every model, not just the ten the test names.
    for name in sorted(dash & ts):
        # Catch the one-liner FIRST. `export type X = { a: string; b: number };`
        # never matches the block regex below at its own closing brace -- that
        # regex runs on to the NEXT type's `\n};`, captures a stranger's
        # fields, and reports drift that does not exist. Three separate
        # changes have now been diagnosed as phantom drift because of it, so
        # the real fault gets its own message.
        decl = re.search(rf"^export type {name} = \{{(.*)$", src, re.M)
        if decl and decl.group(1).strip():
            problems.append(
                f"{name}: declared on one line in types.ts. This script reads the file "
                f"line-wise, so several fields on one line parse as a type with no "
                f"fields. Write one field per line."
            )
            continue
        block = re.search(rf"export type {name} = \{{(.*?)\n\}};", src, re.S)
        if not block:
            problems.append(f"{name}: types.ts entry is not a multi-line object type")
            continue
        ts_fields = set(re.findall(r"^\s{2}(\w+)\??:", block.group(1), re.M))
        py_fields = set(getattr(sd, name).model_fields)
        if ts_fields != py_fields:
            problems.append(
                f"{name}: extra in types.ts={sorted(ts_fields - py_fields)} "
                f"missing from types.ts={sorted(py_fields - ts_fields)}"
            )

    if problems:
        print(f"schemas_dash.py <-> web/src/types.ts: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"schemas_dash.py <-> web/src/types.ts: in sync ({len(dash)} types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
