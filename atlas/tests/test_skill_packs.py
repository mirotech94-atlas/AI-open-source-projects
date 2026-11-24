"""The domain skill packs in atlas/skills/.

A skill body is injected verbatim into a prompt, so a credential that reaches
one has left the vault — that is the expensive bug here, and it is the kind a
future edit introduces quietly. The rest of these guard the injection budget:
a pack that claims an agent that does not exist never fires, and a pack with
broad triggers silently pushes the kernel's accuracy skills out of the
three-slot window.
"""
from __future__ import annotations

import pathlib
import re

import pytest

import atlas.skills as packs
from atlas.kernel.agents import ROSTER, build_roster
from atlas.kernel.skills import SKILLS, skills_for
from atlas.modules.vault import index as vault_index
from atlas.modules.vault import library as vault_library


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    packs.load_all()


# A body reaches a prompt. Anything that looks like a live secret fails here.
SECRET_PATTERNS = (
    r"glpat-[A-Za-z0-9_-]{6,}",          # gitlab tokens
    r"xox[baprs]-",                       # slack tokens
    r"sk-[A-Za-z0-9]{12,}",               # provider keys
    r"\bcalvin\b",                        # the idrac factory password
    r"CloudCo@\w+",                         # node/service passwords
    r"\b\d{1,3}(\.\d{1,3}){3}\b",         # bare IPv4
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",   # emails / matrix ids
    r"password\s*[:=]\s*\S+",
)


def _pack_skills():
    """Only the skills this package registered, not the kernel's own set.

    Taken from the loader rather than a list written here. A hand-written set
    stops covering the next pack someone adds, and stops silently -- the
    tests still pass, they just stop looking. That is the exact failure this
    file exists to prevent, one level up.
    """
    from atlas.skills import REGISTERED_BY_PACKS

    return [s for s in SKILLS if s.name in REGISTERED_BY_PACKS]


def test_packs_registered() -> None:
    # Every pack in atlas/skills/ registered something, and the loader saw it.
    assert len(_pack_skills()) >= 13
    assert "Reading the CloudCo account" in {s.name for s in _pack_skills()}


@pytest.mark.parametrize("pattern", SECRET_PATTERNS)
def test_no_secrets_in_any_body(pattern: str) -> None:
    rx = re.compile(pattern, re.IGNORECASE)
    for skill in _pack_skills():
        hit = rx.search(skill.body)
        assert hit is None, f"{skill.name!r} leaks {hit.group(0)!r} into a prompt"


def _pack_playbook_texts() -> list[tuple[str, str]]:
    """(filename, playbook text) for every real pack under atlas/skills/ --
    exactly what `index_into_library` writes into `library_entries.notes`
    and the vault `.md` copy. `test_no_secrets_in_any_body` above only ever
    scanned `Skill.body`; the module docstring reaches a prompt too now
    (Task 60), through the vault's semantic recall, and needs the same
    guard."""
    root = pathlib.Path(packs.__file__).resolve().parent
    out = []
    for path in sorted(root.glob("*.py")):
        if path.stem == "__init__" or path.stem.startswith("_"):
            continue
        parsed = packs._playbook_title_and_text(path.read_text(encoding="utf-8"))
        if parsed is not None:
            out.append((path.name, parsed[1]))
    return out


@pytest.mark.parametrize("pattern", SECRET_PATTERNS)
def test_no_secrets_in_any_playbook_text(pattern: str) -> None:
    rx = re.compile(pattern, re.IGNORECASE)
    for name, text in _pack_playbook_texts():
        hit = rx.search(text)
        assert hit is None, f"{name!r}'s playbook leaks {hit.group(0)!r} into the vault"


def test_agents_exist_or_skill_never_fires() -> None:
    build_roster()
    known = set(ROSTER)
    for skill in _pack_skills():
        unknown = set(skill.agents) - known
        assert not unknown, f"{skill.name!r} targets unknown agent(s) {unknown}"


def test_accuracy_skills_still_win_their_ground() -> None:
    """The kernel's correctness skills must survive the three-slot window."""
    top = [s.name for s in skills_for("how much revenue today", "research")]
    assert top[0] == "Never guess a number"

    triage = [s.name for s in skills_for("the api is down with 5xx", "qa")]
    assert "Incident triage" in triage


def test_domain_skills_fire_on_their_own_ground() -> None:
    expected = {
        ("corosync ring1 is dead and quorum dropped", "qa"):
            "Proxmox cluster state",
        ("power event, every service is crashlooping", "qa"):
            "Pod power event",
        ("the lld and installation guide disagree", "qa"):
            "Deployment documents",
        ("cannot reach the pod, the tunnel is down", "qa"):
            "Unreachable is not faulty",
    }
    for (task, agent), name in expected.items():
        got = [s.name for s in skills_for(task, agent)]
        assert name in got, f"{name!r} did not fire for {task!r}; got {got}"


def test_bodies_do_not_instruct_actions() -> None:
    """Watch-only phase: skills shape judgement, they never hand out commands."""
    forbidden = ("sudo ", "rm -rf", "systemctl start", "systemctl restart",
                 "docker-compose up", "DROP TABLE", "DELETE FROM")
    for skill in _pack_skills():
        for token in forbidden:
            assert token.lower() not in skill.body.lower(), \
                f"{skill.name!r} contains an executable instruction: {token!r}"


# --------------------------------------------- library seeding (Task 60)
#
# the owner: "there should also be some stuff on there [the knowledge base] based
# on his previous skills from agents that he has as well." `_playbook_title_
# and_text` parses a pack's source statically (ast, no import -- it never
# depends on `load_all()` having already run and never risks a module's
# import side effects firing twice); `index_into_library` seeds one
# library_entries row per pack (kind='skill') and drops a `.md` copy under
# the vault directory for `vault.reindex` to pick up on its own schedule --
# this never touches `vault_documents` itself.

_SAMPLE_SKILL_SOURCE = '''"""Sample — a made-up skill pack for testing.

More detail about what it watches, kept only for this test.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Sample judgement call",
    triggers=("sample",),
    agents=("ops",),
    priority=50,
    body="""
Do the sample thing carefully, always checking twice.
"""))
'''


def test_playbook_title_and_text_reads_the_docstring_and_skill_bodies() -> None:
    title, text = packs._playbook_title_and_text(_SAMPLE_SKILL_SOURCE)
    assert title == "Sample — a made-up skill pack for testing."
    assert "More detail about what it watches" in text
    assert "### Sample judgement call" in text
    assert "Do the sample thing carefully" in text


def test_playbook_title_and_text_is_none_without_a_docstring() -> None:
    assert packs._playbook_title_and_text("x = 1\n") is None


async def test_index_into_library_seeds_a_row_and_a_vault_copy(
        tmp_path, monkeypatch) -> None:
    (tmp_path / "sample.py").write_text(_SAMPLE_SKILL_SOURCE, encoding="utf-8")
    (tmp_path / "__init__.py").write_text('"""package."""\n', encoding="utf-8")

    seeded: list[tuple[str, str, str]] = []

    async def fake_seed_skill(title, source, notes):
        seeded.append((title, source, notes))
        return True

    vault_root = tmp_path / "vault"
    vault_root.mkdir()          # the vault directory already exists
    monkeypatch.setattr(vault_library, "seed_skill", fake_seed_skill)
    monkeypatch.setattr(vault_index, "vault_dir", lambda: vault_root)

    changed = await packs.index_into_library(tmp_path)

    assert changed == 1
    assert len(seeded) == 1
    title, source, notes = seeded[0]
    assert title == "Sample — a made-up skill pack for testing."
    assert source == "atlas/skills/sample.py"
    assert "### Sample judgement call" in notes

    copy = vault_root / "skills" / "sample.md"
    assert copy.exists()
    assert "Sample judgement call" in copy.read_text(encoding="utf-8")


async def test_index_into_library_never_creates_a_missing_vault_directory(
        tmp_path, monkeypatch) -> None:
    """Invariant 7: a misconfigured VAULT_DIR must keep reading UNKNOWN, not
    turn into a healthy directory just because something wrote a file into
    it. The library row is still seeded -- that half doesn't need the vault
    at all."""
    (tmp_path / "sample.py").write_text(_SAMPLE_SKILL_SOURCE, encoding="utf-8")

    seeded: list[tuple[str, str, str]] = []

    async def fake_seed_skill(title, source, notes):
        seeded.append((title, source, notes))
        return True

    vault_root = tmp_path / "vault"      # deliberately never created
    monkeypatch.setattr(vault_library, "seed_skill", fake_seed_skill)
    monkeypatch.setattr(vault_index, "vault_dir", lambda: vault_root)

    changed = await packs.index_into_library(tmp_path)

    assert changed == 1
    assert len(seeded) == 1               # the row was still written
    assert not vault_root.exists()        # but nothing created the directory

    obs = await vault_index.check_index()
    from atlas.kernel.contracts import Health
    assert obs.health is Health.UNKNOWN


async def test_index_into_library_skips_init_and_underscore_files(
        tmp_path, monkeypatch) -> None:
    (tmp_path / "__init__.py").write_text('"""package."""\n', encoding="utf-8")
    (tmp_path / "_private.py").write_text(_SAMPLE_SKILL_SOURCE, encoding="utf-8")

    async def unreachable(title, source, notes):
        raise AssertionError("must not seed __init__ or a leading-underscore file")

    monkeypatch.setattr(vault_library, "seed_skill", unreachable)
    monkeypatch.setattr(vault_index, "vault_dir", lambda: tmp_path / "vault")

    changed = await packs.index_into_library(tmp_path)
    assert changed == 0


async def test_index_into_library_is_idempotent_across_two_runs(
        tmp_path, monkeypatch) -> None:
    """Task 60: 'after the seeding runs twice, one row per skill.'"""
    (tmp_path / "sample.py").write_text(_SAMPLE_SKILL_SOURCE, encoding="utf-8")

    store: dict[str, dict] = {}

    async def fake_seed_skill(title, source, notes):
        existing = store.get(source)
        if existing and existing["title"] == title and existing["notes"] == notes:
            return False
        store[source] = {"title": title, "notes": notes}
        return True

    monkeypatch.setattr(vault_library, "seed_skill", fake_seed_skill)
    monkeypatch.setattr(vault_index, "vault_dir", lambda: tmp_path / "vault")

    first = await packs.index_into_library(tmp_path)
    second = await packs.index_into_library(tmp_path)

    assert first == 1
    assert second == 0            # unchanged the second time -- no re-write
    assert len(store) == 1        # one row, not two


async def test_index_into_library_logs_and_continues_when_one_pack_fails(
        tmp_path, monkeypatch) -> None:
    (tmp_path / "sample.py").write_text(_SAMPLE_SKILL_SOURCE, encoding="utf-8")
    (tmp_path / "broken.py").write_text(_SAMPLE_SKILL_SOURCE.replace(
        "Sample judgement call", "Broken judgement call"), encoding="utf-8")

    seeded: list[str] = []

    async def flaky_seed_skill(title, source, notes):
        if "broken" in source:
            raise RuntimeError("db is down")
        seeded.append(source)
        return True

    monkeypatch.setattr(vault_library, "seed_skill", flaky_seed_skill)
    monkeypatch.setattr(vault_index, "vault_dir", lambda: tmp_path / "vault")

    changed = await packs.index_into_library(tmp_path)

    assert changed == 1
    assert seeded == ["atlas/skills/sample.py"]
