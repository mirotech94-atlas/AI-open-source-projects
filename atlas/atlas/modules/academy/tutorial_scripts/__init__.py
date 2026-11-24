"""Tutorial scripts written one episode per module (Miro, 2026-10-15: "make
sure videos are being posted automatically with no stopping").

Each module is `<series>_<NN>.py` (for example `n8n_03.py`) and defines
`EPISODE: Episode`, the full scripted episode filled into its planned entry
(`tutorial_series.scripted(<planned>, ...)`), and optionally `LAB_FILES`, the
lab files its lab scene links ({name: text}). `tutorial_series` merges them
at the end of its assembly (`tutorial_series.from_scripts`), where the inline
episodes are filled; an episode here that collides with an inline one, or
names a number with no planned entry, is an error, never silently dropped.

A scheduled writer adds one module a day per series running short of
scripts (docs/academy-script-writer.md). Nothing is imported at package
import time: the modules import from `tutorial_series`, which imports this
package while it is still being built, so discovery runs inside the calls.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tutorial_series import Episode

MODULE_NAME = re.compile(r"^(?P<series>[a-z][a-z0-9]*)_(?P<number>\d{2})$")


def _modules(key: str) -> list[tuple[int, ModuleType]]:
    """(number in the module name, module) for each script of series `key`."""
    out: list[tuple[int, ModuleType]] = []
    for info in pkgutil.iter_modules(__path__):
        got = MODULE_NAME.match(info.name)
        if not got or got["series"] != key:
            continue
        out.append((int(got["number"]),
                    importlib.import_module(f"{__name__}.{info.name}")))
    return out


def names() -> list[str]:
    """Every module in the package, by name (a test holds each to MODULE_NAME
    and a known series, so a misnamed script is never silently skipped)."""
    return sorted(info.name for info in pkgutil.iter_modules(__path__))


def for_series(key: str) -> tuple[Episode, ...]:
    """The series' episodes written here, sorted by number. A module whose
    number disagrees with its EPISODE, or that has none, is an error."""
    eps = []
    for number, mod in _modules(key):
        ep = getattr(mod, "EPISODE", None)
        if ep is None:
            raise ValueError(f"{mod.__name__} defines no EPISODE")
        if ep.number != number:
            raise ValueError(f"{mod.__name__} holds episode #{ep.number}, "
                             f"not #{number}")
        if not ep.scripted:
            raise ValueError(f"{mod.__name__}: episode #{number} has no scenes")
        eps.append(ep)
    return tuple(sorted(eps, key=lambda e: e.number))


def lab_files(key: str) -> dict[str, str]:
    """Every lab file the series' script modules add, by name."""
    out: dict[str, str] = {}
    for _number, mod in _modules(key):
        for name, text in (getattr(mod, "LAB_FILES", None) or {}).items():
            if name in out:
                raise ValueError(f"lab file {name} is defined twice")
            out[name] = text
    return out
