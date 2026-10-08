"""No customer-visible string says the old product name.

Python only, and deliberately so: `ast` can tell a string literal from a
comment and a regex cannot. The web side is covered by its own suite and by
review; claiming otherwise here would be a guard that looks stronger than it
is.

Docstrings are `ast.Constant` strings like any other, and this codebase is
unusually full of them -- the house style is a paragraph explaining why. None
of that prose is customer-visible and the rebrand put it out of scope, so
`_docstrings` skips it rather than dragging it into the diff.

The allowlist is the spec's never-change table. Everything on it is an
identifier that happens to contain the word: a cookie, a Home Assistant entity
id, a User-Agent, an OpenAPI extension field, a package, a path, a service
label. Adding an entry here is a claim that something is an identifier -- make
the same claim in the spec, and never rename it instead.

`test_the_guard_still_sees_an_offender` is not ceremony. The web-side twin of
this guard shipped to production disarmed: a commit that meant to rewrite prose
rewrote one token of the scan as well, and because the assertion was inlined
there was nothing that could notice. It stayed green through a full 2153-test
run. A guard with no test of its own cannot tell "nothing is wrong" from "I can
no longer see anything", so `_offenders` gets fed a known offender here.

See docs/superpowers/specs/2026-09-24-vault-rebrand-design.md.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
TREES = [ROOT / "hub" / "guardian_hub", ROOT / "sensor" / "guardian_sensor"]

#: Capitalised strings that are NOT copy.
#:
#: Both entries are the SAME thing seen twice: the old product name as it was
#: written into data that already exists. `demoscrub` matches it against a real
#: capture in `tests/fixtures/`, which is history and does not get rewritten
#: when a product is renamed; `importer` matches it against Site rows a
#: previous run of `guardian migrate` already relabelled. Rename either and the
#: match silently stops happening -- the scrub falls through to a generic name
#: that collides with another site, and migrate loses its only way to correct
#: tenants stuck on the old label.
ALLOWED: tuple[str, ...] = (
    "Guardian public",
)


def _docstrings(tree: ast.AST) -> set[int]:
    """Line numbers of every module, class and function docstring in `tree`."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(body[0].value.lineno)
    return out


def _offenders(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node.lineno in skip:
            continue
        text = node.value
        # Only CAPITALISED `Guardian` is copy. Every identifier here spells it
        # lower case -- the session cookie, the Home Assistant entity ids, the
        # Prometheus metric names, the log prefixes, the User-Agents, the seeded
        # fixture values, the asset filenames. Flagging lower case too would
        # mean an allowlist chasing thirty-odd identifier families, which rots
        # into noise and eventually gets a real regression waved through.
        if "Guardian" not in text or any(a in text for a in ALLOWED):
            continue
        out.append((node.lineno, text[:90]))
    return out


def test_no_customer_visible_string_says_the_old_name():
    bad = []
    for tree in TREES:
        for f in sorted(tree.rglob("*.py")):
            for lineno, text in _offenders(f):
                bad.append(f"{f.relative_to(ROOT)}:{lineno}: {text!r}")
    assert not bad, (
        "These strings still say the old product name:\n  " + "\n  ".join(bad)
        + "\n\nThe product is Vault. If one of these is an identifier rather "
          "than copy, add it to ALLOWED here AND to the never-change table in "
          "docs/superpowers/specs/2026-09-24-vault-rebrand-design.md -- do not "
          "rename it.")


def test_the_guard_still_sees_an_offender(tmp_path):
    """`_offenders` flags copy, ignores docstrings, comments and lower case.

    See this module's docstring for why this exists. Each case below is one
    way the scan could silently stop working: dropping the `ast.Constant`
    branch, filtering the wrong node kind, or comparing against the wrong
    spelling of the word.
    """
    f = tmp_path / "sample.py"
    f.write_text(
        '"""A module docstring naming Guardian, which is internal prose."""\n'
        '# A comment naming Guardian, which ast never sees.\n'
        'COPY = "Guardian found a problem"\n'
        'KEY = "guardian.theme"\n'
        'def g():\n'
        '    """A function docstring naming Guardian."""\n'
        '    return 1\n',
        encoding="utf-8")
    found = _offenders(f)
    assert [text for _, text in found] == ["Guardian found a problem"], (
        "The scan no longer flags a plain customer-visible string. Whatever "
        "changed in `_offenders` has disarmed this guard -- the web-side twin "
        "shipped that way. Fix the scan, do not relax this case.")


def test_an_allowed_identifier_is_not_flagged(tmp_path):
    """`ALLOWED` still exempts the one entry on it, and only that one."""
    f = tmp_path / "sample.py"
    f.write_text('A = "Guardian public (example.invalid)"\n', encoding="utf-8")
    assert _offenders(f) == []


def test_the_scrubbed_demo_never_shows_the_old_name():
    """The fixtures are a real capture and stay untouched -- what the PUBLIC
    demo shows is `demoscrub.scrub`'s OUTPUT, so that is what gets read.

    Scanning the raw capture instead would be wrong twice over: it is history
    and does not get rewritten when a product is renamed, and the file it
    would flag (`Guardian public (safehome.example.com)`) is a LOOKUP KEY the
    scrub matches against. Rewriting it there is the bug that would have put
    two sites called "Online booking" on the public demo. The scrub's output
    is the only thing about this capture a customer can read, and the only
    thing this guard has any business asserting on.
    """
    import json

    from guardian_hub import demoscrub, seed

    last = json.loads((seed._FIXTURES / "last.json").read_text(encoding="utf-8"))
    state = json.loads((seed._FIXTURES / "state.json").read_text(encoding="utf-8"))
    # `scrub` rewrites both documents IN PLACE and is deterministic but not
    # idempotent, so these must be a fresh load -- see its own docstring.
    demoscrub.scrub(last, state)

    blob = json.dumps([last, state], ensure_ascii=False)
    assert "Guardian" not in blob, (
        "The seeded demo still shows the old product name somewhere a visitor "
        "can read it. The fixtures are a real capture and are history -- fix "
        "the mapping in demoscrub.py, never the capture.")
