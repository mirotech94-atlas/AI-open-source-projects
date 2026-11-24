"""Regenerate the code-derived part of docs/capabilities.json from the code.

No generator existed: the register was written by hand in the 2026-09-28
read-only phase. This one keeps every hand-written field (verifier, rollback,
readiness, provenance, prod evidence) and refreshes only what code can say:

  * each action's `code_defined_tier`, `module`, `action_name`
  * `enabled` false (with the reason) for a module in the committed
    switched-off list (`atlas.kernel.board_state.SWITCHED_OFF`)
  * a row for an action that is in code and not yet in the file, marked
    `readiness: unverified` and `provenance: generated from code`
  * `removed_from_code: true` on an action row whose action the code no
    longer defines (the row is kept, as history, never deleted)
  * `counts.actions_in_code`

Nothing here executes an action or reaches the network: modules are imported
and `MODULE.actions()` read, with unset `requires_env` variables filled by a
placeholder so every module's catalog is visible.

    python tools/gen_capabilities.py            # rewrite docs/capabilities.json
    python tools/gen_capabilities.py --check    # exit 1 when the file is stale
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "capabilities.json"


def collect() -> dict[str, dict]:
    """action name -> {module, tier}, read from every module's catalog."""
    sys.path.insert(0, str(ROOT))
    pkg = importlib.import_module("atlas.modules")
    found: dict[str, dict] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"atlas.modules.{info.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"skip {info.name}: import failed: {exc}", file=sys.stderr)
            continue
        instance = getattr(mod, "MODULE", None)
        if instance is None:
            continue
        for var in getattr(instance, "requires_env", ()):
            os.environ.setdefault(var, "placeholder")
        try:
            actions = instance.actions()
        except Exception as exc:  # noqa: BLE001
            print(f"skip {info.name}: actions() failed: {exc}", file=sys.stderr)
            continue
        for a in actions:
            found[a.name] = {"module": info.name, "tier": int(a.tier)}
    return found


def build(existing: dict, found: dict[str, dict], today: str) -> dict:
    from atlas.kernel.board_state import SWITCHED_OFF

    off = {k.lower(): v for k, v in SWITCHED_OFF.items()}
    out = json.loads(json.dumps(existing))
    rows = out["capabilities"]
    have = {r["action_name"]: r for r in rows if r.get("kind") == "action"}
    for name, info in sorted(found.items()):
        row = have.get(name)
        if row is None:
            row = {
                "capability_id": f"action:{name}", "kind": "action",
                "module": info["module"],
                "module_path": f"atlas/modules/{info['module']}/",
                "action_name": name, "enabled": True,
                "enabled_evidence": "generated from code; prod state not read",
                "required_credentials_refs": [], "dry_run_handler": None,
                "verification_handler": None, "idempotency_strategy": None,
                "recovery_rollback": None, "scheduler_owner": "other",
                "scheduler_owner_detail": None, "readiness": "unverified",
                "readiness_evidence": "added by tools/gen_capabilities.py; "
                                      "nobody has verified it",
                "provenance": f"generated from code {today}",
            }
            rows.append(row)
        row["code_defined_tier"] = info["tier"]
        row["module"] = info["module"]
        row.pop("removed_from_code", None)
        if info["module"].lower() in off:
            row["enabled"] = False
            row["enabled_evidence"] = (
                "committed switch (atlas/kernel/board_state.py SWITCHED_OFF): "
                + off[info["module"].lower()])
    for name, row in have.items():
        if name not in found:
            row["removed_from_code"] = True
    out["counts"]["actions_in_code"] = len(found)
    out["generated"] = f"{today} tools/gen_capabilities.py (code-derived fields)"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stdout", action="store_true",
                    help="print the new file instead of writing it")
    args = ap.parse_args()
    existing = json.loads(TARGET.read_text())
    today = datetime.now(UTC).date().isoformat()
    new = build(existing, collect(), today)
    if args.stdout:
        sys.stdout.write(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
        return 0
    # `generated` carries a date; compare without it so --check is stable.
    strip = lambda d: {**d, "generated": None}
    if strip(new) == strip(existing):
        print("docs/capabilities.json is current")
        return 0
    if args.check:
        print("docs/capabilities.json is stale; run tools/gen_capabilities.py")
        return 1
    TARGET.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {TARGET.relative_to(ROOT)}: "
          f"{new['counts']['actions_in_code']} actions in code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
