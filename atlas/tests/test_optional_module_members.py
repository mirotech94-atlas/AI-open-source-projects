"""A module that does not implement an optional member is not a failure.

`Module` in contracts.py is a Protocol, so the defaults written on it are
documentation rather than inheritance — no module subclasses it. A module that
omits `agenda()` therefore has no implementation at all, and the kernel called
it unconditionally.

Nothing crashed, because every call site catches. What it cost was the log:
fifteen of eighteen modules have no `agenda()`, so every morning brief logged
fifteen "agenda failed" warnings, and a real failure in one of the three that
do implement it was indistinguishable from the fifteen that never could.
`content` and `tiktok` have no `signals()` and printed a full traceback on
every signals cycle.
"""
from __future__ import annotations

from atlas.kernel.contracts import implements


class Bare:
    """A module like `content`: real, registered, and missing the optionals."""

    name = "bare"


class Full:
    name = "full"

    async def agenda(self, within): return ()
    async def signals(self): return ()
    def brief(self) -> str: return "something"


class Broken:
    name = "broken"

    def brief(self) -> str:
        raise RuntimeError("the collector is down")


def test_a_missing_optional_member_is_reported_as_absent():
    for member in ("agenda", "signals", "inbox", "brief"):
        assert implements(Bare(), member) is False, member


def test_an_implemented_member_is_reported_as_present():
    for member in ("agenda", "signals", "brief"):
        assert implements(Full(), member) is True, member


def test_a_member_that_exists_but_fails_still_counts_as_implemented():
    """The distinction the log depends on: absent is silent, broken is loud."""
    assert implements(Broken(), "brief") is True


def test_a_non_callable_attribute_is_not_an_implementation():
    class Odd:
        brief = "not a method"

    assert implements(Odd(), "brief") is False


async def test_every_real_module_is_either_absent_or_callable():
    """Guards the shape the kernel now relies on across the whole registry."""
    from atlas.kernel.registry import REGISTRY

    REGISTRY.load_all()
    for name, module in REGISTRY.modules.items():
        for member in ("agenda", "signals", "inbox", "brief"):
            attr = getattr(module, member, None)
            assert attr is None or callable(attr), f"{name}.{member}"
