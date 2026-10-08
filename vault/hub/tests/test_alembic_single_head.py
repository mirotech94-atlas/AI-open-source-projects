"""The migration history must have exactly one head.

This is not tidiness. `guardian migrate` calls `alembic upgrade head`, and
Alembic refuses to run that at all when more than one head exists --
"Multiple head revisions are present" -- so a second head breaks every
deployment of the branch it appears on. It fails closed, which is the good
version of the bug, but it fails for everyone until someone notices.

It already happened once here. Two lines of work forked at
`0017_tenant_foreign_keys` and each numbered its own revisions without
seeing the other, so the tree ended up with two 0018s through two 0022s and
no way to upgrade. `0023_merge_heads` rejoined them.

The failure mode is specific to how this repository works: several sessions
develop on separate branches and the histories are merged later, at which
point nothing complains — the files sit side by side quite happily and only
`upgrade head` objects, which nobody runs until deploy. So the check has to
live in the test suite, where merging two branches runs it automatically.

It deliberately reads the scripts rather than a database: a fresh test
database is built with `create_all` and never walks the revision chain, so
the suite would otherwise never notice a broken one.
"""
import pathlib

from alembic.config import Config
from alembic.script import ScriptDirectory

HUB = pathlib.Path(__file__).resolve().parents[1]


def _scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(HUB / "alembic.ini")))


def test_there_is_exactly_one_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, (
        "alembic has %d heads (%s) — `guardian migrate` cannot run until they are "
        "joined by a merge revision" % (len(heads), ", ".join(sorted(heads)))
    )


def test_every_revision_is_reachable_from_that_head():
    """A revision nobody can walk to is a schema change that will never be
    applied, which is worse than a missing one: the code that needs the
    column ships and the column does not."""
    scripts = _scripts()
    head = scripts.get_heads()[0]
    reachable = {r.revision for r in scripts.iterate_revisions(head, "base")}
    everything = {r.revision for r in scripts.walk_revisions()}
    assert everything - reachable == set(), (
        "unreachable revisions: %s" % sorted(everything - reachable)
    )


def test_the_chain_has_no_duplicate_revision_ids():
    """Two files claiming the same id silently shadow one another, and which
    one wins depends on directory order."""
    ids = [r.revision for r in _scripts().walk_revisions()]
    assert len(ids) == len(set(ids)), "duplicate revision ids: %s" % sorted(
        {i for i in ids if ids.count(i) > 1}
    )
