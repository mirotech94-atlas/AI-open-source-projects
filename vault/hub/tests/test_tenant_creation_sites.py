"""Every place that constructs a Tenant must hand it the shared policy.

`tenant_defaults.new_tenant_config()` is only worth having if nothing creates
a tenant around it. Listing the call sites in a docstring would rot; this
walks the hub's own source instead and fails on a bare `m.Tenant(...)`.

It is written to fail on code that does not exist here yet, which is the
point. `accounts.provision_from_lead` -- the path that turns an approved
signup into a real organization, and so the one that will create almost every
customer tenant -- lives on the unmerged `signup-approval` branch. When that
branch lands, this test goes red until its `m.Tenant(...)` is given a config
too. A comment could not have done that.

`seed.py` is exempt: the demo tenant's config is built wholesale by
`_build_config` and forced on every re-seed (see its note, and CLAUDE.md's
demo rules), so a policy default layered underneath would be overwritten on
the next `seed-demo` and mean nothing.
"""

import ast
import pathlib

HUB = pathlib.Path(__file__).resolve().parents[1] / "guardian_hub"

#: Modules allowed to build a Tenant without the shared policy, each with the
#: reason. Add to this only with one.
EXEMPT = {
    "seed.py": "demo tenant config is forced wholesale by _build_config on every re-seed",
    "importer.py": "a Nexus import replaces config with the imported file's own",
}


def _tenant_constructions() -> list[tuple[str, int, bool]]:
    """(relative path, line, passes a config=) for every `Tenant(...)` call."""
    found = []
    for path in sorted(HUB.rglob("*.py")):
        rel = str(path.relative_to(HUB))
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else "")
            if name != "Tenant":
                continue
            has_config = any(kw.arg == "config" for kw in node.keywords)
            found.append((rel, node.lineno, has_config))
    return found


def test_the_scan_finds_the_known_call_sites():
    """A guard on the guard: an AST walk that silently matched nothing would
    pass this file forever while every tenant came out bare."""
    sites = _tenant_constructions()
    assert len(sites) >= 3, f"expected to find Tenant(...) calls, found {sites}"


def test_every_tenant_is_created_with_the_shared_policy():
    bare = [(rel, line) for rel, line, ok in _tenant_constructions()
            if not ok and pathlib.Path(rel).name not in EXEMPT]
    assert not bare, (
        "these create a Tenant with no config, so the organization starts with "
        f"no shared policy: {bare}. Pass "
        "`config=tenant_defaults.new_tenant_config()`, or add the module to "
        "EXEMPT in this file with the reason."
    )
