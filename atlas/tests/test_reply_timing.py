"""Reply timing (Part 0): one row per answered turn, no text, never blocks.

Proved here:
  * a WhatsApp turn and a portal /ask turn each write one `reply_timings` row
    with channel, total_ms and per-stage ms, and the row holds no message text;
  * a failed timing write never blocks or changes the reply;
  * `ops/latency_report.py` prints p50/p95 per channel and
    `target = round(0.28 * baseline p50)`; with fewer than MIN_SAMPLES rows it
    says UNKNOWN and exits 2 (invariant 7).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import pathlib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, whatsapp
from atlas.kernel import reply_timing
from atlas.kernel.llm import Completion

TOKEN = "t" * 32
URL = "https://agency.example.com/webhooks/twilio/whatsapp"
OWNER = "+19175550100"
PASSWORD = "a-long-enough-password"


def _sign(params: dict) -> str:
    data = URL + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(
        hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


def _post(c, body="how are things", sender=f"whatsapp:{OWNER}"):
    params = {"From": sender, "To": "whatsapp:+14155238886", "Body": body,
              "MessageSid": "SM1234567890ab"}
    return c.post("/webhooks/twilio/whatsapp", content=urlencode(params),
                  headers={"Content-Type": "application/x-www-form-urlencoded",
                           "X-Twilio-Signature": _sign(params),
                           "X-Atlas-Public": "1"})


def _app(monkeypatch):
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return app


@pytest.fixture
def wa_client(monkeypatch, memory_backend):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("MMT_OWNER_WHATSAPP", OWNER)
    monkeypatch.delenv("TWILIO_WHATSAPP_URL", raising=False)
    whatsapp._LIMITER.clear()
    seen: set[str] = set()

    async def claim(action: str, key: str) -> str:
        if (action, key) in seen:
            return "in doubt"
        seen.add((action, key))
        return "claimed"
    monkeypatch.setattr("atlas.kernel.idempotency.claim", claim)
    with TestClient(_app(monkeypatch), client=("172.18.0.5", 4000)) as c:
        yield c


@pytest.fixture
def portal(monkeypatch, memory_backend):
    from atlas.api import voice
    monkeypatch.setenv("SESSION_SECRET", "v" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    fresh = datetime.now(UTC) - timedelta(minutes=1)
    rows = [{"module": "cloudco", "check": "balance", "interval_s": 300,
             "health": "ok", "detail": "fine", "at": fresh}]

    async def fetch_all(sql, *a):
        return [{"action": "gitlab.open_issue"}] if "FROM proposals" in sql else rows
    monkeypatch.setattr(voice, "fetch_all", fetch_all)

    async def think(need, system, messages, **kw):
        return Completion(text="Fine, as of 7:52.", provider="groq", model="m1")
    monkeypatch.setattr("atlas.kernel.llm.think", think)
    monkeypatch.setattr("atlas.kernel.missions.voice_agent", lambda _t: None)
    with TestClient(_app(monkeypatch), client=("127.0.0.1", 52000)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


@pytest.fixture
def writes(monkeypatch):
    rows: list[tuple] = []

    async def fake(sql, *args):
        rows.append((" ".join(sql.split()), args))
        return "OK"
    monkeypatch.setattr(reply_timing, "execute_sql", fake)
    return rows


# ------------------------------------------------------------------ module

async def test_a_timer_records_channel_total_and_stages_and_no_text(writes):
    timer = reply_timing.begin("whatsapp")
    with reply_timing.stage("recall"):
        await asyncio.sleep(0.01)
    with reply_timing.stage("model"):
        await asyncio.sleep(0.02)
    reply_timing.note(route="chat", model="local-1")
    await reply_timing.record(timer)
    assert len(writes) == 1
    sql, args = writes[0]
    assert "reply_timings" in sql
    channel, route, total, stages_json, model, ok = args
    assert (channel, route, model, ok) == ("whatsapp", "chat", "local-1", True)
    stages = json.loads(stages_json)
    assert set(stages) == {"recall", "live_read", "model", "other"}
    assert stages["model"] >= 15 and stages["recall"] >= 5
    assert total >= stages["model"] + stages["recall"]
    assert all(isinstance(v, int) for v in stages.values())


async def test_an_unknown_stage_goes_to_other_and_text_is_never_stored(writes):
    timer = reply_timing.begin("portal")
    with reply_timing.stage("the user said hello"):
        pass
    await reply_timing.record(timer)
    _sql, args = writes[0]
    assert "hello" not in repr(args)
    assert set(json.loads(args[3])) == {"recall", "live_read", "model", "other"}


async def test_a_failed_write_never_raises_or_blocks(monkeypatch):
    async def boom(*_a):
        raise RuntimeError("db down")
    monkeypatch.setattr(reply_timing, "execute_sql", boom)
    timer = reply_timing.begin("portal")
    await reply_timing.record(timer)            # must not raise
    reply_timing.schedule(reply_timing.begin("portal"))
    await reply_timing.drain()


async def test_a_stage_with_no_timer_is_a_noop():
    reply_timing._CURRENT.set(None)
    with reply_timing.stage("model"):
        pass
    reply_timing.note(route="chat")


async def test_an_unknown_route_falls_back_to_chat(writes):
    timer = reply_timing.begin("whatsapp")
    reply_timing.note(route="not-a-route")
    await reply_timing.record(timer)
    assert writes[0][1][1] == "chat"


# ------------------------------------------------------------------ wiring

def test_a_whatsapp_turn_writes_one_row(monkeypatch, writes, wa_client):
    async def fake(text: str) -> str:
        reply_timing.note(route="chat")
        return "All fine."
    monkeypatch.setattr(whatsapp, "answer", fake)
    r = _post(wa_client)
    assert r.status_code == 200 and "All fine." in r.text
    asyncio.run(reply_timing.drain())
    rows = [w for w in writes if "reply_timings" in w[0]]
    assert len(rows) == 1
    assert rows[0][1][0] == "whatsapp"
    assert "how are things" not in repr(rows[0])


def test_a_non_owner_whatsapp_message_writes_no_row(writes, wa_client):
    r = _post(wa_client, "hi", sender="whatsapp:+10000000000")
    assert r.status_code == 200
    asyncio.run(reply_timing.drain())
    assert not [w for w in writes if "reply_timings" in w[0]]


def test_a_portal_ask_writes_one_row_and_the_reply_is_unchanged(writes, portal):
    body = portal.post("/api/voice/ask",
                       json={"transcript": "how is cloudco looking"}).json()
    assert body["answer"] == "Fine, as of 7:52."
    asyncio.run(reply_timing.drain())
    rows = [w for w in writes if "reply_timings" in w[0]]
    assert len(rows) == 1 and rows[0][1][0] == "portal"
    assert "cloudco" not in repr(rows[0][1])


def test_a_failing_timing_write_leaves_the_portal_reply_alone(monkeypatch, portal):
    async def boom(*_a):
        raise RuntimeError("db down")
    monkeypatch.setattr(reply_timing, "execute_sql", boom)
    r = portal.post("/api/voice/ask", json={"transcript": "how is cloudco looking"})
    assert r.status_code == 200 and r.json()["answer"] == "Fine, as of 7:52."
    asyncio.run(reply_timing.drain())


# ------------------------------------------------------------------ report

def _report():
    path = pathlib.Path(__file__).resolve().parents[1] / "ops" / "latency_report.py"
    spec = importlib.util.spec_from_file_location("latency_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_prints_p50_p95_and_the_28_percent_target():
    rpt = _report()
    series = {"whatsapp": [1000] * 60 + [3000] * 40, "portal": [500] * 100}
    out = rpt.build(series, voice_history=[], window_days=7)
    ch = out["channels"]["whatsapp"]
    assert ch["n"] == 100 and ch["p50"] == 1000 and ch["p95"] == 3000
    assert ch["target_p50"] == round(0.28 * 1000)
    assert out["channels"]["portal"]["target_p50"] == 140
    assert out["status"] == "ok"
    text = rpt.render(out)
    assert "p50" in text and "280" in text
    assert rpt.exit_code(out) == 0


def test_report_is_unknown_and_exits_2_with_too_few_rows():
    rpt = _report()
    out = rpt.build({"whatsapp": [1000] * 99, "portal": [500] * 100},
                    voice_history=[], window_days=7)
    assert out["status"] == "unknown"
    assert out["channels"]["whatsapp"]["target_p50"] is None
    assert "UNKNOWN" in rpt.render(out)
    assert rpt.exit_code(out) == 2


def test_report_keeps_the_voice_history_as_a_separate_series():
    rpt = _report()
    out = rpt.build({"whatsapp": [], "portal": []}, voice_history=[900] * 150,
                    window_days=7)
    assert out["voice_history"]["n"] == 150
    assert out["channels"]["portal"]["n"] == 0
    assert "historical" in rpt.render(out).lower()


async def test_the_total_is_frozen_when_scheduled_not_when_written(writes):
    timer = reply_timing.begin("portal")
    reply_timing.schedule(timer)
    await asyncio.sleep(0.05)                   # the loop is slow to run the insert
    await reply_timing.drain()
    assert len(writes) == 1 and writes[0][1][2] < 40


@pytest.mark.parametrize("body", ["forget the tutor rate", "restore",
                                  "remember that the rent is due on the 1st",
                                  "correct tutor rate: it is 40"])
def test_a_memory_command_from_another_number_reaches_nothing(
        monkeypatch, writes, wa_client, body):
    """Forget / restore / remember / correct run only for the verified owner."""
    from atlas.kernel import memory_extract, owner_memory
    touched: list = []

    async def never(*a, **_k):
        touched.append(a)
        raise AssertionError("a memory command ran for a stranger")
    monkeypatch.setattr(owner_memory, "execute", never)
    monkeypatch.setattr(owner_memory, "fetch_all", never)
    monkeypatch.setattr(memory_extract, "schedule", lambda *a: touched.append(a))
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    r = _post(wa_client, body, sender="whatsapp:+10000000000")
    assert r.status_code == 200 and touched == []
