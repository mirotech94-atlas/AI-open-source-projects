"""The self-model: what ATLAS knows about itself, where the owner is, and how
short to be when he is out.

The property that matters here is the same one that matters everywhere else
in this repo: a figure that could not be read is reported as unknown, never
as a number and never as an all-clear. A self-report is the easiest place in
the system to invent something, because nothing outside disagrees with it.
"""
from __future__ import annotations

import ast
import base64
import importlib.util
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import comms, rhythm, selfmodel
from atlas.kernel.contracts import Severity

ROOT = pathlib.Path(__file__).resolve().parent.parent
AVATAR = ROOT / "ui" / "public" / "atlas-avatar.svg"
AVATAR_PNG = ROOT / "ui" / "public" / "atlas-avatar.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_pixels(blob: bytes) -> tuple[int, int, bytes]:
    """(width, height, raw scanlines) from a PNG, by reading its chunks.
    Compared decoded rather than byte for byte, so a different zlib build
    compressing the same pixels differently is not a failure."""
    import struct
    import zlib

    assert blob.startswith(PNG_SIGNATURE)
    pos, width, height, data = len(PNG_SIGNATURE), 0, 0, b""
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        kind = blob[pos + 4:pos + 8]
        body = blob[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
            assert body[8:10] == bytes([8, 2])       # 8-bit RGB
        elif kind == b"IDAT":
            data += body
        pos += 12 + length
    return width, height, zlib.decompress(data)


def _generator():
    spec = importlib.util.spec_from_file_location(
        "render_avatar", ROOT / "tools" / "render_avatar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    with TestClient(app, client=("127.0.0.1", 52001)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


@pytest.fixture(autouse=True)
def _fresh_presence():
    """`presence()` caches for a minute; every test starts without one."""
    rhythm._PRESENCE_CACHE.clear()
    yield
    rhythm._PRESENCE_CACHE.clear()


def _blind(monkeypatch) -> None:
    """Every database read in the self-model fails. Nothing may be guessed."""
    async def boom(*_a, **_k):
        raise RuntimeError("no database")

    monkeypatch.setattr(selfmodel, "fetch_one", boom)
    monkeypatch.setattr(selfmodel, "fetch_all", boom)


# ------------------------------------------------------------- presence

HOME_IP = "203.0.113.7"
AWAY_IP = "198.51.100.4"


def _home_ips(monkeypatch, *values: str) -> None:
    monkeypatch.setenv("HOME_IPS", ", ".join(values) if values else HOME_IP)


async def test_presence_is_unknown_when_no_peers_are_named(monkeypatch):
    monkeypatch.delenv("PRESENCE_PEERS", raising=False)
    monkeypatch.delenv("HOME_IPS", raising=False)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert seen["peers_seen"] == []
    assert seen["peers"] == {}
    assert seen["read_at"]
    assert "PRESENCE_PEERS" in seen["reason"]


async def test_presence_is_unknown_when_home_ips_is_not_set(monkeypatch):
    """PRESENCE_PEERS alone is Task 33's old contract -- Task 51 also needs
    HOME_IPS before it will say True or False. The mesh is still read, so
    the peers' current addresses come back for him to copy from."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    monkeypatch.delenv("HOME_IPS", raising=False)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert "HOME_IPS" in seen["reason"]
    assert seen["peers"]["owner-iphone"]["connection_ip"] == HOME_IP


async def test_presence_is_unknown_when_the_mesh_is_unreachable(monkeypatch):
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch)

    async def unreachable(_path):
        return None, "the mesh did not answer"

    monkeypatch.setattr("atlas.modules.netbird._get", unreachable)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert seen["reason"] == "the mesh did not answer"


async def test_presence_is_true_when_a_named_peer_connects_from_home(monkeypatch):
    monkeypatch.setenv("PRESENCE_PEERS", "the owner-iPhone, studio-mac")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP},
                {"id": "2", "name": "studio-mac", "connected": False},
                {"id": "3", "name": "atlas-box", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is True
    assert seen["peers_seen"] == ["owner-iphone"]
    assert seen["home_peer"] == {"name": "owner-iphone", "ip": HOME_IP}


async def test_presence_is_true_with_a_cidr_in_home_ips(monkeypatch):
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch, "203.0.113.0/24")

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is True


async def test_a_malformed_home_ips_entry_refuses_the_whole_list(monkeypatch):
    """One typo must not silently shrink the home set to whatever else
    happened to parse -- that is a guess about which entries he meant. It
    refuses the list outright and says which entry was the problem."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch, HOME_IP, "nope")

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert "nope" in seen["reason"]


async def test_every_malformed_home_ips_entry_is_named(monkeypatch):
    """Minor from the branch review: with more than one bad entry, every
    one is named, not just the first."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch, HOME_IP, "nope", "also-bad")

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert "nope" in seen["reason"]
    assert "also-bad" in seen["reason"]


async def test_home_ips_of_only_commas_is_unknown_not_open_or_closed(
        monkeypatch):
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    monkeypatch.setenv("HOME_IPS", ", ,")

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None


async def test_an_unparseable_connection_ip_is_unknown_not_away(monkeypatch):
    """A `connection_ip` NetBird did not hand back as a plain address (a
    port suffix, a hostname, garbage) must read the same as no address at
    all -- never "away", and the raw text must not surface in `peers` or in
    `reason` (it is an outside API's own text)."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": "198.51.100.4:51820"}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert seen["peers"]["owner-iphone"]["connection_ip"] is None
    assert "51820" not in seen["reason"]


async def test_presence_is_true_when_one_peer_is_home_and_another_has_no_ip(
        monkeypatch):
    """A confirmed-home peer settles it; an unresolved second peer elsewhere
    does not veto that."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone, studio-mac")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP},
                {"id": "2", "name": "studio-mac", "connected": True}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is True


async def test_presence_is_unknown_when_one_peer_is_away_and_another_has_no_ip(
        monkeypatch):
    """Without a confirmed-home peer, an unresolved one blocks a False
    verdict -- "every listed peer is known to be away" is not true here."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone, studio-mac")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": AWAY_IP},
                {"id": "2", "name": "studio-mac", "connected": True}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None


async def test_presence_is_false_when_connected_from_outside_home_ips(
        monkeypatch):
    """The mesh being reachable and the peer being connected is not enough
    any more -- Task 51's whole point. A phone tethered from a cafe is on
    the mesh from an address that is not home."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": AWAY_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is False
    assert seen["peers_seen"] == []
    assert seen["away_peer"] == {"name": "owner-iphone", "ip": AWAY_IP}


async def test_every_named_peer_offline_is_unknown_not_away(monkeypatch):
    """2026-09-26 22:50: his phone's NetBird app was off while he was home,
    and the call opened "You're out". Off the mesh is not away."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    monkeypatch.delenv("PRESENCE_LAN_LABELS", raising=False)
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": False}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert "not being away" in seen["reason"]
    assert seen["peers_seen"] == []


async def test_a_peer_with_no_connected_field_is_unknown_not_offline(monkeypatch):
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone"}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert "owner-iphone" in seen["reason"]


async def test_a_connected_peer_with_no_connection_ip_is_unknown(monkeypatch):
    """Connected but the mesh names no address is unknown, never a guess in
    either direction: not home, and not "he must be out" either."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert "owner-iphone" in seen["reason"]
    assert seen["peers"]["owner-iphone"]["connection_ip"] is None


async def test_presence_is_cached_for_a_minute(monkeypatch):
    """Every alert asks where he is; the mesh is asked at most once a
    minute."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch)
    calls: list[str] = []

    async def peers(path):
        calls.append(path)
        return [{"id": "1", "name": "owner-iphone", "connected": False}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    first = await rhythm.presence()
    second = await rhythm.presence()
    assert first == second
    assert calls == ["/peers"]

    monkeypatch.setattr(rhythm, "PRESENCE_TTL_S", 0.0)
    await rhythm.presence()
    assert calls == ["/peers", "/peers"]


async def test_changing_home_ips_is_never_served_from_the_peers_cache(
        monkeypatch):
    """The cache key covers HOME_IPS too, not just PRESENCE_PEERS -- setting
    it for the first time must not read back the pre-HOME_IPS answer."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    monkeypatch.delenv("HOME_IPS", raising=False)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    unset = await rhythm.presence()
    assert unset["home"] is None

    _home_ips(monkeypatch)
    now_set = await rhythm.presence()
    assert now_set["home"] is True


async def test_a_named_peer_the_mesh_has_never_heard_of_is_not_a_no(monkeypatch):
    """A typo in PRESENCE_PEERS must not read as "the owner is out"; that is a
    guess dressed as a measurement."""
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphonr")
    _home_ips(monkeypatch)

    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                "connection_ip": HOME_IP}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)
    seen = await rhythm.presence()
    assert seen["home"] is None


async def test_a_mesh_that_hangs_is_unknown_not_a_stall(monkeypatch):
    """`notify()` asks where he is; a NetBird API that never answers must
    not hold an alert for the 25 seconds `get_json` would wait."""
    import asyncio

    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    _home_ips(monkeypatch)
    monkeypatch.setattr(rhythm, "PRESENCE_TIMEOUT_S", 0.05)

    async def hangs(_path):
        await asyncio.sleep(5)
        return [], None

    monkeypatch.setattr("atlas.modules.netbird._get", hangs)
    seen = await rhythm.presence()
    assert seen["home"] is None
    assert "did not answer" in seen["reason"]


# -------------------------------------------------------------- status()

async def test_status_reports_unknown_rather_than_zero_when_blind(monkeypatch):
    _blind(monkeypatch)

    async def no_mesh():
        return {"home": None, "peers_seen": [], "read_at": "now",
                "reason": "no mesh"}

    monkeypatch.setattr(selfmodel, "presence", no_mesh)
    s = await selfmodel.status()

    assert s["errors_since_deploy"] is None
    assert s["checks"] is None
    assert s["memories"] is None
    assert s["lessons_open"] is None
    assert s["proposals_waiting"] is None
    assert s["last_brief_at"] is None
    assert s["presence"]["home"] is None
    # The two things the process always knows about itself.
    assert s["uptime_s"] >= 0
    assert s["started_at"]


async def test_status_presence_is_unknown_when_the_mesh_is_unreachable(monkeypatch):
    _blind(monkeypatch)
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")

    async def unreachable(_path):
        return None, "the mesh did not answer"

    monkeypatch.setattr("atlas.modules.netbird._get", unreachable)
    s = await selfmodel.status()
    assert s["presence"]["home"] is None


async def test_status_presence_survives_presence_itself_raising(monkeypatch):
    _blind(monkeypatch)

    async def explode():
        raise RuntimeError("netbird module blew up")

    monkeypatch.setattr(selfmodel, "presence", explode)
    s = await selfmodel.status()
    assert s["presence"]["home"] is None


# -------------------------------------------------------------- spoken()

async def test_spoken_says_unknown_for_every_field_it_could_not_read(monkeypatch):
    _blind(monkeypatch)

    async def no_mesh():
        return {"home": None, "peers_seen": [], "read_at": "now",
                "reason": "no mesh"}

    monkeypatch.setattr(selfmodel, "presence", no_mesh)
    said = await selfmodel.spoken()

    assert said.startswith("I'm running.")
    assert said.lower().count("unknown") >= 4
    # Nothing may be rendered as a bare zero.
    assert "0 errors" not in said
    assert "0 of 0" not in said


async def test_spoken_reads_back_the_numbers_from_status(monkeypatch):
    async def fake_status():
        return {
            "started_at": "2026-09-24T09:00:00+00:00",
            "uptime_s": 46 * 60,
            "deploy_sha": "abc1234",
            "errors_since_deploy": 2,
            "checks": {"ok": 38, "degraded": 0, "failing": 0, "unknown": 3},
            "last_brief_at": None,
            "memories": 10,
            "lessons_open": 1,
            "proposals_waiting": 0,
            "presence": {"home": True, "peers_seen": ["owner-iphone"],
                         "peers": {"owner-iphone": {"connected": True,
                                                  "connection_ip": "203.0.113.7",
                                                  "home": True},
                                  "atlas.example.com": {"connected": True,
                                                     "connection_ip":
                                                         "198.51.100.1",
                                                     "home": None}},
                         "read_at": "2026-09-24T09:46:00+00:00",
                         "reason": None,
                         "home_peer": {"name": "owner-iphone",
                                      "ip": "203.0.113.7"},
                         "away_peer": None},
        }

    async def newest():
        return "Declined instagram.publish twice: the caption named a client."

    monkeypatch.setattr(selfmodel, "status", fake_status)
    monkeypatch.setattr(selfmodel, "newest_lesson", newest)
    said = await selfmodel.spoken()

    assert "46 minutes" in said
    assert "2 errors since deploy" in said
    assert "38 of 41 checks fine" in said
    assert "3 unmeasured" in said
    assert "Where you are: home (owner-iphone on 203.0.113.7)." in said
    assert ("Peer IPs for HOME_IPS: owner-iphone 203.0.113.7, "
            "atlas.example.com 198.51.100.1.") in said
    assert "Last thing I got wrong: Declined instagram.publish" in said


async def test_spoken_says_he_is_out_when_the_mesh_says_so(monkeypatch):
    _blind(monkeypatch)

    async def away():
        return {"home": False, "peers_seen": [],
                "peers": {"owner-iphone": {"connected": True,
                                          "connection_ip": "198.51.100.4",
                                          "home": False}},
                "read_at": "now", "reason": None,
                "home_peer": None,
                "away_peer": {"name": "owner-iphone", "ip": "198.51.100.4"}}

    monkeypatch.setattr(selfmodel, "presence", away)
    said = await selfmodel.spoken()
    assert ("Where you are: out (owner-iphone connected from 198.51.100.4)."
           in said)
    assert "Peer IPs for HOME_IPS: owner-iphone 198.51.100.4." in said


# ------------------------------------------- what it did, what it holds (G17)

async def test_did_since_deploy_counts_ok_finishes_since_start(monkeypatch):
    from datetime import datetime

    asked: list[tuple[str, tuple]] = []
    at = datetime(2026, 9, 24, 14, 2, tzinfo=selfmodel.UTC)

    async def fetch_one(sql, *args):
        flat = " ".join(sql.split())
        asked.append((flat, args))
        if "execute.finish" in flat and "'true'" in flat:
            return {"n": 3, "last_subject": "vault.reindex", "last_at": at}
        return {"n": 0, "at": None}

    async def fetch_all(sql, *args):
        return []

    async def no_mesh():
        return {"home": None, "peers_seen": [], "read_at": "now", "reason": "x"}

    monkeypatch.setattr(selfmodel, "fetch_one", fetch_one)
    monkeypatch.setattr(selfmodel, "fetch_all", fetch_all)
    monkeypatch.setattr(selfmodel, "presence", no_mesh)
    s = await selfmodel.status()

    assert s["did_since_deploy"] == {"count": 3, "last_subject": "vault.reindex",
                                     "last_at": at.isoformat()}
    sql, args = next(q for q in asked if "execute.finish" in q[0]
                     and "'true'" in q[0])
    assert "at >= $1" in sql and args[0] == selfmodel.STARTED_AT


async def test_did_since_deploy_is_unknown_when_blind(monkeypatch):
    _blind(monkeypatch)

    async def no_mesh():
        return {"home": None, "peers_seen": [], "read_at": "now", "reason": "x"}

    monkeypatch.setattr(selfmodel, "presence", no_mesh)
    s = await selfmodel.status()
    assert s["did_since_deploy"] is None


def _status_with(did, memories):
    async def fake_status():
        return {"uptime_s": 46 * 60, "errors_since_deploy": 0,
                "checks": {"ok": 1, "degraded": 0, "failing": 0, "unknown": 0},
                "did_since_deploy": did, "memories": memories,
                "presence": {"home": True}}
    return fake_status


async def _nothing_wrong():
    return ""


async def test_spoken_says_what_it_did_and_what_it_holds(monkeypatch):
    from datetime import datetime

    last = "2026-09-24T14:02:00+00:00"
    monkeypatch.setattr(selfmodel, "status", _status_with(
        {"count": 3, "last_subject": "vault.reindex", "last_at": last}, 812))
    monkeypatch.setattr(selfmodel, "newest_lesson", _nothing_wrong)
    said = await selfmodel.spoken()
    hhmm = datetime.fromisoformat(last).astimezone().strftime("%H:%M")
    assert (f"Did 3 things since deploy, last vault.reindex at {hhmm}; "
            f"I hold 812 memories.") in said


async def test_spoken_says_one_thing_in_the_singular(monkeypatch):
    monkeypatch.setattr(selfmodel, "status", _status_with(
        {"count": 1, "last_subject": "gitlab.open_issue",
         "last_at": "2026-09-24T09:15:00+00:00"}, 1))
    monkeypatch.setattr(selfmodel, "newest_lesson", _nothing_wrong)
    said = await selfmodel.spoken()
    assert "Did 1 thing since deploy, last gitlab.open_issue at " in said
    assert "I hold 1 memory." in said


async def test_spoken_says_nothing_done_without_a_bare_zero(monkeypatch):
    monkeypatch.setattr(selfmodel, "status", _status_with(
        {"count": 0, "last_subject": None, "last_at": None}, 40))
    monkeypatch.setattr(selfmodel, "newest_lesson", _nothing_wrong)
    said = await selfmodel.spoken()
    assert "Did nothing since deploy; I hold 40 memories." in said


async def test_spoken_says_unknown_for_what_it_did_and_holds(monkeypatch):
    monkeypatch.setattr(selfmodel, "status", _status_with(None, None))
    monkeypatch.setattr(selfmodel, "newest_lesson", _nothing_wrong)
    said = await selfmodel.spoken()
    assert ("What I did since deploy: unknown; memories I hold: unknown."
            in said)
    assert "Did 0" not in said and "0 memories" not in said


# --------------------------------------------------------------- brevity

LONG = ("The nightly render finished. Four clips are waiting on you. "
        "Two of them are over the credit cap. The cap resets on Tuesday. "
        "Nothing else happened.")


def test_shorten_keeps_two_sentences():
    short = comms.shorten(LONG)
    assert short.startswith("The nightly render finished. Four clips are "
                            "waiting on you.")
    assert "credit cap" not in short


def test_shorten_leaves_a_short_body_alone():
    assert comms.shorten("One thing happened.") == "One thing happened."


async def test_brevity_trims_when_he_is_out(monkeypatch):
    async def away():
        return {"home": False, "peers_seen": [], "read_at": "now",
                "reason": None}

    monkeypatch.setattr(comms, "presence", away)
    msg = comms.Outbound(title="Renders", body=LONG, severity=Severity.NORMAL)
    trimmed, may_ring = await comms.brevity(msg)

    assert trimmed.body != LONG
    assert "credit cap" not in trimmed.body
    assert trimmed.body.endswith(" — more on the dashboard")
    assert may_ring is False


@pytest.mark.parametrize("kind", ["digest", "receipt"])
async def test_brevity_never_trims_a_digest_or_a_receipt(monkeypatch, kind):
    """The brief, the weekly, the nightly review, decision receipts and
    call summaries are the message itself; cutting them loses the point."""
    async def away():
        return {"home": False, "peers_seen": [], "read_at": "now",
                "reason": None}

    monkeypatch.setattr(comms, "presence", away)
    msg = comms.Outbound(title="Renders", body=LONG, severity=Severity.NORMAL,
                         kind=kind)
    trimmed, may_ring = await comms.brevity(msg)
    assert trimmed.body == LONG
    assert may_ring is False


def _mesh_says_he_is_out(monkeypatch) -> None:
    monkeypatch.setenv("PRESENCE_PEERS", "owner-iphone")
    monkeypatch.setenv("HOME_IPS", "203.0.113.7")
    monkeypatch.setenv("QUIET_HOURS", "")

    monkeypatch.delenv("PRESENCE_LAN_LABELS", raising=False)

    # Out means connected from somewhere that is not home: an offline peer
    # no longer reads as out (2026-09-26).
    async def peers(_path):
        return [{"id": "1", "name": "owner-iphone", "connected": True,
                 "connection_ip": "198.51.100.4"}], None

    monkeypatch.setattr("atlas.modules.netbird._get", peers)


class _Inbox:
    """A text channel that keeps what it was sent."""
    name = "slack"
    rich = True
    interactive = True

    def __init__(self) -> None:
        self.got: list = []

    async def available(self) -> bool:
        return True

    async def send(self, msg) -> str:
        self.got.append(msg)
        return "ok"


async def test_the_morning_brief_arrives_whole_when_he_is_out(monkeypatch):
    """Regression (review C1): with the mesh saying he is out, the rendered
    brief reaches the channel with its "Waiting on you" section intact,
    while an ordinary alert in the same state is cut and says so."""
    from datetime import UTC, datetime

    from atlas.kernel import brief

    _mesh_says_he_is_out(monkeypatch)
    inbox = _Inbox()
    monkeypatch.setitem(comms.CHANNELS, "slack", inbox)
    monkeypatch.setitem(comms.ROUTING, Severity.NORMAL, ("slack",))

    made = brief.Brief(
        at=datetime(2026, 9, 24, 7, 0, tzinfo=UTC),
        sections=[
            brief.Section("Overnight", ["Revenue $412 from Stripe, read 06:58.",
                                        "2 new peers on the mesh."]),
            brief.Section("Waiting on you", ["#41 instagram.publish — approve?",
                                             "#42 gitlab.open_issue — approve?"]),
            brief.Section("Needs a reply", ["Sara, about Friday."]),
        ],
        watch_only=True)

    async def build():
        return made

    async def store(*_a, **_k):
        return 1

    async def execute_sql(*_a, **_k):
        return None

    async def not_yet():
        return False

    monkeypatch.setattr(brief, "build", build)
    monkeypatch.setattr(brief, "store", store)
    # brief.send() asks the archive whether today's brief already went out
    # (58e60bb); nothing has.
    monkeypatch.setattr(brief, "already_sent_today", not_yet)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)

    assert (await rhythm.presence())["home"] is False
    await brief.send()
    assert len(inbox.got) == 1
    body = inbox.got[0].body
    assert body == made.render()
    assert "Waiting on you" in body
    assert "#42 gitlab.open_issue" in body
    assert "Needs a reply" in body

    await comms.notify(comms.Outbound(title="Renders", body=LONG,
                                      severity=Severity.NORMAL))
    alert = inbox.got[1].body
    assert "credit cap" not in alert
    assert alert.endswith(" — more on the dashboard")


async def test_brevity_leaves_everything_alone_when_he_is_home(monkeypatch):
    async def home():
        return {"home": True, "peers_seen": ["owner-iphone"], "read_at": "now",
                "reason": None}

    monkeypatch.setattr(comms, "presence", home)
    msg = comms.Outbound(title="Renders", body=LONG, severity=Severity.NORMAL)
    trimmed, may_ring = await comms.brevity(msg)
    assert trimmed.body == LONG
    assert may_ring is True


async def test_brevity_does_nothing_when_presence_is_unknown(monkeypatch):
    """Invariant 6. An unreachable mesh is not evidence that he is out."""
    async def unknown():
        return {"home": None, "peers_seen": [], "read_at": "now",
                "reason": "no mesh"}

    monkeypatch.setattr(comms, "presence", unknown)
    msg = comms.Outbound(title="Renders", body=LONG, severity=Severity.NORMAL)
    trimmed, may_ring = await comms.brevity(msg)
    assert trimmed.body == LONG
    assert may_ring is True


async def test_brevity_never_trims_critical(monkeypatch):
    async def away():
        raise AssertionError("CRITICAL must not even ask where he is")

    monkeypatch.setattr(comms, "presence", away)
    msg = comms.Outbound(title="Guardian is down", body=LONG,
                         severity=Severity.CRITICAL)
    trimmed, may_ring = await comms.brevity(msg)
    assert trimmed.body == LONG
    assert may_ring is True


async def test_brevity_leaves_an_approval_card_whole(monkeypatch):
    """A card with buttons is what he reads before he decides. Trimming it
    would have him approve something he was shown half of."""
    async def away():
        return {"home": False, "peers_seen": [], "read_at": "now",
                "reason": None}

    monkeypatch.setattr(comms, "presence", away)
    msg = comms.Outbound(title="Approve?", body=LONG, severity=Severity.NORMAL,
                         buttons=(comms.Button("Approve", "approve:1"),))
    trimmed, may_ring = await comms.brevity(msg)
    assert trimmed.body == LONG
    assert may_ring is False


async def test_brevity_survives_the_mesh_raising(monkeypatch):
    async def explode():
        raise RuntimeError("no mesh")

    monkeypatch.setattr(comms, "presence", explode)
    msg = comms.Outbound(title="Renders", body=LONG, severity=Severity.NORMAL)
    trimmed, may_ring = await comms.brevity(msg)
    assert trimmed.body == LONG
    assert may_ring is True


async def test_notify_does_not_ring_for_a_non_critical_message_when_he_is_out(
        monkeypatch):
    """Belt and braces over ROUTING: `voice` is CRITICAL-only today, and if
    that ever changes an ordinary alert still must not ring him mid-workout.
    """
    sent: list[str] = []

    class Ringer:
        name = "voice"
        rich = False
        interactive = True

        async def available(self) -> bool:
            return True

        async def send(self, msg) -> str:
            sent.append(self.name)
            return "sid"

    async def away():
        return {"home": False, "peers_seen": [], "read_at": "now",
                "reason": None}

    monkeypatch.setattr(comms, "presence", away)
    monkeypatch.setitem(comms.CHANNELS, "voice", Ringer())
    monkeypatch.setitem(comms.ROUTING, Severity.HIGH, ("voice",))
    # Otherwise the call budget could be what kept the phone quiet, and the
    # test would pass without brevity doing anything.
    monkeypatch.setattr("atlas.kernel.escalation.may_call",
                        lambda _sev, **_kw: (True, ""))
    monkeypatch.setenv("QUIET_HOURS", "")

    used = await comms.notify(comms.Outbound(
        title="Renders", body=LONG, severity=Severity.HIGH))
    assert sent == []
    assert used == []


# ----------------------------------------------------------- the voice ear

@pytest.mark.parametrize("said", [
    "how are you",
    "ATLAS, how are you?",
    "are you ok",
    "are you awake",
    "status report",
    "what did you get wrong",
    "what's your status",
    "how's it going",
])
def test_the_self_questions_are_recognised(said):
    from atlas.api import voice
    assert voice.SELF.search(said), said


@pytest.mark.parametrize("said", [
    "how is guardian doing",
    "what did flycheap earn today",
    "open the status report",
    "show me the status report",
    "show status report",
])
def test_an_ordinary_question_is_not_a_self_question(said):
    from atlas.api import voice
    assert voice.SELF.search(said) is None, said


async def test_asking_how_he_is_answers_from_the_self_model(client, monkeypatch):
    async def said():
        return "I'm running. Up 46 minutes, and nothing is on fire."

    monkeypatch.setattr("atlas.kernel.selfmodel.spoken", said)

    async def never(*_a, **_k):
        raise AssertionError("a self question must not reach a specialist")

    monkeypatch.setattr("atlas.kernel.agents.route", never)
    r = client.post("/api/voice/ask", json={"transcript": "how are you"})
    assert r.status_code == 200
    assert "Up 46 minutes" in r.json()["answer"]
    assert r.json()["kind"] == "self"


async def test_a_self_model_that_fails_says_so_without_the_raw_error(
        client, monkeypatch):
    monkeypatch.setenv("NETBIRD_TOKEN", "tok-" + "x" * 30)

    async def broken():
        raise RuntimeError("boom at https://h/api?token=tok-" + "x" * 30
                           + "\nsecond line")

    monkeypatch.setattr("atlas.kernel.selfmodel.spoken", broken)
    r = client.post("/api/voice/ask", json={"transcript": "how are you"})
    assert r.status_code == 200
    error = r.json()["error"]
    assert error.startswith("I could not read my own state")
    assert "x" * 30 not in error
    assert "\n" not in error


async def test_a_self_question_that_asks_for_an_action_is_still_an_action(
        client, monkeypatch):
    """"how are you going to restart it" matches SELF and ACTION_INTENT. An
    action request is answered as one, never reinterpreted."""
    async def fake_route(question, *_a, **_k):
        assert "cannot act from voice" in question
        return "I would restart it by hand."

    async def no_match(_transcript, **_kw):
        return None

    monkeypatch.setattr("atlas.kernel.agents.route", fake_route)
    monkeypatch.setattr("atlas.api.voice._pick_action", no_match)

    async def no_notify(_msg):
        return []

    monkeypatch.setattr("atlas.kernel.comms.notify", no_notify)
    r = client.post("/api/voice/ask",
                    json={"transcript": "how are you going to restart it"})
    assert r.status_code == 200
    assert r.json()["action_request"] is True


# ------------------------------------------------------ the concierge prompt


def test_the_self_status_tool_is_registered_and_reads_only():
    from atlas.kernel.tools import TOOLS
    from atlas.tools import builtin

    builtin.register_all()
    tool = TOOLS["self_status"]
    assert tool.read is not None
    assert tool.action is None


def test_the_self_model_says_in_one_sentence_that_it_is_not_consciousness():
    doc = selfmodel.__doc__ or ""
    assert "not conscious" in doc.lower()


# ----------------------------------------------------------------- the API

async def test_api_self_reports_the_face_and_the_card(client, monkeypatch):
    async def fake_status():
        return {"started_at": "2026-09-24T09:00:00+00:00", "uptime_s": 60,
                "deploy_sha": None, "errors_since_deploy": None,
                "checks": None, "last_brief_at": None, "memories": None,
                "lessons_open": None, "proposals_waiting": None,
                "presence": {"home": None, "peers_seen": [], "read_at": "now",
                             "reason": "no mesh"}}

    monkeypatch.setattr("atlas.kernel.selfmodel.status", fake_status)
    r = client.get("/api/self")
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"]
    assert body["presence"]["home"] is None
    assert body["avatar"]["png"] is True
    assert body["avatar"]["url"] == "/api/self/avatar.png"


async def test_a_missing_face_does_not_hide_the_self_model(client, monkeypatch,
                                                           tmp_path):
    from atlas.api import selfmodel as api_self

    monkeypatch.setattr(api_self, "AVATAR_PATHS", (tmp_path / "nope.png",))
    monkeypatch.setattr(api_self, "_PNG", [])

    async def fake_status():
        return {"started_at": "2026-09-24T09:00:00+00:00", "uptime_s": 60}

    monkeypatch.setattr("atlas.kernel.selfmodel.status", fake_status)
    r = client.get("/api/self")
    assert r.status_code == 200
    assert r.json()["avatar"] == "missing"
    assert r.json()["uptime_s"] == 60


async def test_the_vcard_carries_the_number_and_a_photo(client, monkeypatch):
    monkeypatch.setenv("TWILIO_FROM", "+15550100")
    monkeypatch.delenv("SCREENING_FROM", raising=False)
    r = client.get("/api/self/atlas.vcf")
    assert r.status_code == 200
    assert "vcard" in r.headers["content-type"].lower()
    card = r.text
    assert card.startswith("BEGIN:VCARD")
    assert card.rstrip().endswith("END:VCARD")
    assert "FN:ATLAS" in card
    assert "+15550100" in card
    assert re.search(r"^PHOTO;ENCODING=b;TYPE=PNG:", card, re.MULTILINE)


async def test_the_vcard_is_folded_so_ios_accepts_it(client, monkeypatch):
    """RFC 2426/6350: CRLF line ends, no line over 75 octets plus the
    folding space; the photo decodes back to the committed PNG."""
    monkeypatch.setenv("TWILIO_FROM", "+15550100")
    raw = client.get("/api/self/atlas.vcf").content
    assert raw.endswith(b"\r\n")
    lines = raw.split(b"\r\n")[:-1]
    assert all(len(line) <= 76 for line in lines)
    assert not any(b"\n" in line for line in lines)

    unfolded = raw.replace(b"\r\n ", b"").decode()
    photo = next(line for line in unfolded.split("\r\n")
                 if line.startswith("PHOTO;"))
    assert base64.b64decode(photo.split(":", 1)[1]) == AVATAR_PNG.read_bytes()


async def test_the_screening_line_is_a_second_number(client, monkeypatch):
    monkeypatch.setenv("TWILIO_FROM", "+15550100")
    monkeypatch.setenv("SCREENING_FROM", "+15550199")
    card = client.get("/api/self/atlas.vcf").text
    assert len(re.findall(r"^TEL", card, re.MULTILINE)) == 2
    assert "+15550199" in card


async def test_the_vcard_says_so_when_there_is_no_number(client, monkeypatch):
    monkeypatch.delenv("TWILIO_FROM", raising=False)
    monkeypatch.delenv("SCREENING_FROM", raising=False)
    card = client.get("/api/self/atlas.vcf").text
    assert not re.search(r"^TEL", card, re.MULTILINE)
    assert "TWILIO_FROM" in card          # the NOTE says why there is none


async def test_the_avatar_endpoint_serves_something_a_phone_can_show(client):
    r = client.get("/api/self/avatar.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(PNG_SIGNATURE)
    assert r.content == AVATAR_PNG.read_bytes()


# ---------------------------------------------------------------- the avatar

def test_the_committed_avatar_is_what_the_generator_produces():
    """Otherwise the SVG in the repo is an artefact nobody can reproduce."""
    assert _generator().render() == AVATAR.read_text()


def test_the_committed_png_is_what_the_generator_produces():
    """The contact photo is rasterised by the same file, in pure Python:
    no cairo, no PIL, nothing on the box to go missing."""
    committed = _png_pixels(AVATAR_PNG.read_bytes())
    assert committed == _png_pixels(_generator().render_png())
    width, height, _ = committed
    assert (width, height) == (512, 512)


def test_the_png_is_a_bust_on_black_with_an_amber_face():
    width, height, raw = _png_pixels(AVATAR_PNG.read_bytes())
    stride = 1 + width * 3
    rows = [raw[y * stride + 1:(y + 1) * stride] for y in range(height)]
    assert all(raw[y * stride] == 0 for y in range(height))   # filter: none

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        return tuple(rows[y][x * 3:x * 3 + 3])

    assert pixel(0, 0) == (0, 0, 0)                  # black corners
    assert pixel(width - 1, 0) == (0, 0, 0)
    lit = [pixel(x, y) for y in range(height) for x in range(width)
           if pixel(x, y) != (0, 0, 0)]
    amber = [p for p in lit if p[0] > p[2]]          # red over blue
    cyan = [p for p in lit if p[2] > p[0]]
    assert len(cyan) > 5000
    assert len(amber) > 200


@pytest.mark.parametrize("tree", ["atlas", "tools"])
def test_nothing_anywhere_imports_an_image_library(tree: str):
    """The PNG is committed and drawn in pure Python; no file in the
    application or its tools reaches for cairo or PIL."""
    for path in sorted((ROOT / tree).rglob("*.py")):
        for mod in _imports_of(path):
            head = mod.split(".")[0]
            assert "cairo" not in head.lower() and head != "PIL", (path, mod)


async def test_a_git_that_hangs_is_killed(monkeypatch):
    import asyncio

    killed: list[bool] = []

    class Hung:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(5)
            return b"", b""

        def kill(self):
            killed.append(True)

        async def wait(self):
            return -9

    async def spawn(*_a, **_k):
        return Hung()

    monkeypatch.delenv("ATLAS_DEPLOY_SHA", raising=False)
    monkeypatch.setattr(selfmodel, "_SHA", [])
    monkeypatch.setattr(selfmodel, "GIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await selfmodel.deploy_sha() is None
    assert killed == [True]


def test_the_avatar_is_a_particle_bust_in_the_right_colours():
    svg = AVATAR.read_text()
    assert 'viewBox="0 0 1024 1024"' in svg
    assert "#39d6ff" in svg               # cyan points
    assert "#ffb347" in svg               # the amber face cluster
    assert svg.count("<circle") > 5000


def test_the_avatar_survives_a_base64_round_trip():
    raw = AVATAR.read_bytes()
    assert base64.b64decode(base64.b64encode(raw)) == raw


# ------------------------------------------------------------- hard limits

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                mod = f"atlas.kernel.{mod}" if mod else "atlas.kernel"
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


@pytest.mark.parametrize("rel", [
    "atlas/kernel/selfmodel.py",
    "atlas/api/selfmodel.py",
    "tools/render_avatar.py",
])
def test_the_self_model_has_no_path_to_the_executor(rel: str):
    """A thing that describes itself has no business acting."""
    for mod in _imports_of(ROOT / rel):
        assert "executor" not in mod, f"{rel} imports {mod}"



# ------------------------------------------- a stale OK is not "fine" (9e)

async def test_a_stale_ok_is_counted_unmeasured_in_how_are_you(monkeypatch):
    """The self-model counted raw latest health, so an OK from three
    intervals ago -- a dead scheduler -- was spoken as fine."""
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)

    async def fetch_all(sql, *args):
        return [
            {"health": "ok", "at": now - timedelta(minutes=2), "interval_s": 300},
            {"health": "ok", "at": now - timedelta(hours=5), "interval_s": 300},
            {"health": None, "at": None, "interval_s": 300},
            {"health": "sideways", "at": now, "interval_s": 300},
            {"health": "failing", "at": now, "interval_s": 60},
        ]

    monkeypatch.setattr(selfmodel, "fetch_all", fetch_all)
    counts = await selfmodel._checks()
    assert counts == {"ok": 1, "degraded": 0, "failing": 1, "unknown": 3}
    line = selfmodel._checks_line(counts)
    assert line.startswith("1 of 5 checks fine") and "3 unmeasured" in line
