"""Read-back verifiers for the comms families (RA-D1): mail labels and
drafts, the Mac actions, messages.draft and fiverr.draft_buyer_reply.

Gmail is faked at `httpx.AsyncClient.send` (GET only) with the module's
token helper faked; the DB is faked at `state.fetch_one` / `state.fetch_all`
(SELECT only). The Mac actions can never read back verified by design: their
tests pin unknown on a clean job, contradicted on a job that disagrees.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re

import httpx
import pytest

from atlas.kernel import state
from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult
from atlas.modules import mail

SECRET = "sk-rad1-comms-supersecret-0123456789"

ACTIONS = ("mail.apply_label_map", "mail.apply_labels", "mail.draft_nudge",
           "mail.draft_reply", "mac.add_reminder", "mac.music_control",
           "mac.open_app", "mac.play_music", "messages.draft",
           "fiverr.draft_buyer_reply")


class Net:
    """Routes by a regex on the URL path; GET only."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, tuple]] = []
        self.seen: list[str] = []
        self.auth: list[str] = []

    def on(self, pattern: str, status: int = 200, body=None, *, raises=None) -> None:
        self.routes.insert(0, (pattern, (status, body, raises)))

    async def send(self, client, request: httpx.Request, **_kw) -> httpx.Response:
        assert request.method == "GET", (
            f"a verifier tried to write: {request.method} {request.url}")
        path = request.url.path
        self.seen.append(path)
        self.auth.append(request.headers.get("authorization", ""))
        for pattern, (status, body, raises) in self.routes:
            if re.search(pattern, path):
                if raises is not None:
                    raise raises
                return httpx.Response(status, json=body, request=request)
        return httpx.Response(404, json={"error": "no route"}, request=request)


@pytest.fixture(autouse=True)
def net(monkeypatch):
    fake = Net()

    async def send(client, request, **kw):
        return await fake.send(client, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return fake


class DB:
    def __init__(self) -> None:
        self.one: dict[str, dict | None] = {}
        self.all: dict[str, list[dict]] = {}
        self.raises: Exception | None = None
        self.sql: list[str] = []


@pytest.fixture(autouse=True)
def db(monkeypatch):
    fake = DB()

    def _check(sql: str) -> str:
        assert sql.lstrip().upper().startswith("SELECT"), f"a verifier wrote: {sql}"
        fake.sql.append(sql)
        if fake.raises is not None:
            raise fake.raises
        return re.search(r"FROM\s+(\w+)", sql).group(1)

    async def fetch_one(sql, *args):
        return fake.one.get(_check(sql))

    async def fetch_all(sql, *args):
        return fake.all.get(_check(sql), [])

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    return fake


@pytest.fixture
def gmail(monkeypatch):
    """Mail switched on; the token helper returns SECRET (never a POST)."""
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", SECRET)

    async def token(*_a, **_k):
        return SECRET

    monkeypatch.setattr(mail, "_access_token", token)


@pytest.fixture
def mac_on(monkeypatch):
    monkeypatch.setenv("MAC_AGENT_TOKEN", SECRET)


def _no_secret(got: v.Verification) -> None:
    assert SECRET not in json.dumps(got.as_audit(), default=str)


def sha12(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode()).hexdigest()[:12]


@pytest.mark.parametrize("action", ACTIONS)
def test_each_action_has_a_verifier(action):
    assert v.has_verifier(action)


# ================================================================== mail labels

def _label(lid="Label_1", name="Receipts"):
    return {"id": lid, "name": name, "type": "user"}


def _thread(tid, *labels):
    return {"id": tid, "messages": [{"id": f"{tid}m1", "labelIds": ["INBOX"]},
                                    {"id": f"{tid}m2", "labelIds": list(labels)}]}


LB_PARAMS = {"thread_ids": "t1, t2", "label": "receipts"}
LB_RESULT = ActionResult(True, "labelled", evidence={"threads": ["t1", "t2"],
                                                    "label_id": "Label_1",
                                                    "created": False},
                         rollback_token="Label_1:t1,t2")


async def test_apply_labels_verified(net, gmail):
    net.on(r"/labels/Label_1$", body=_label())
    net.on(r"/threads/t1$", body=_thread("t1", "Label_1"))
    net.on(r"/threads/t2$", body=_thread("t2", "Label_1", "UNREAD"))
    got = await v.verify("mail.apply_labels", LB_PARAMS, LB_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["label_id"] == "Label_1"
    assert all(a == f"Bearer {SECRET}" for a in net.auth)
    _no_secret(got)


async def test_apply_labels_contradicted_when_a_thread_lacks_the_label(net, gmail):
    net.on(r"/labels/Label_1$", body=_label())
    net.on(r"/threads/t1$", body=_thread("t1", "Label_1"))
    net.on(r"/threads/t2$", body=_thread("t2"))
    got = await v.verify("mail.apply_labels", LB_PARAMS, LB_RESULT)
    assert got.state is v.State.CONTRADICTED
    assert "t2" in got.reason
    _no_secret(got)


async def test_apply_labels_contradicted_on_a_renamed_or_missing_label(net, gmail):
    net.on(r"/labels/Label_1$", body=_label(name="Other"))
    got = await v.verify("mail.apply_labels", LB_PARAMS, LB_RESULT)
    assert got.state is v.State.CONTRADICTED and "Other" in got.reason
    net.on(r"/labels/Label_1$", status=404, body={})
    got = await v.verify("mail.apply_labels", LB_PARAMS, LB_RESULT)
    assert got.state is v.State.CONTRADICTED and "not in the mailbox" in got.reason


async def test_apply_labels_unknown_without_an_id(net, gmail):
    got = await v.verify("mail.apply_labels", LB_PARAMS, ActionResult(True, "x"))
    assert got.state is v.State.UNKNOWN
    assert net.seen == []


async def test_apply_labels_disabled_before_any_network(net, monkeypatch):
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
    got = await v.verify("mail.apply_labels", LB_PARAMS, LB_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.DISABLED
    assert net.seen == []


MAP_PARAMS = {"mapping": json.dumps({"receipts": ["t1"], "fyi": "t3"})}
MAP_RESULT = ActionResult(True, "labelled",
                          evidence={"labels": {"receipts": ["t1"], "fyi": ["t3"]},
                                    "label_ids": {"receipts": "Label_1",
                                                  "fyi": "Label_2"}},
                          rollback_token="Label_1:t1|Label_2:t3:new")


async def test_apply_label_map_verified(net, gmail):
    net.on(r"/labels/Label_1$", body=_label())
    net.on(r"/labels/Label_2$", body=_label("Label_2", "FYI"))
    net.on(r"/threads/t1$", body=_thread("t1", "Label_1"))
    net.on(r"/threads/t3$", body=_thread("t3", "Label_2"))
    got = await v.verify("mail.apply_label_map", MAP_PARAMS, MAP_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert set(got.evidence) == {"receipts", "fyi"}
    _no_secret(got)


async def test_apply_label_map_reads_ids_from_the_token_of_an_older_run(net, gmail):
    net.on(r"/labels/Label_1$", body=_label())
    net.on(r"/labels/Label_2$", body=_label("Label_2", "FYI"))
    net.on(r"/threads/t1$", body=_thread("t1", "Label_1"))
    net.on(r"/threads/t3$", body=_thread("t3", "Label_2"))
    older = ActionResult(True, "x", evidence={"labels": MAP_RESULT.evidence["labels"]},
                         rollback_token=MAP_RESULT.rollback_token)
    got = await v.verify("mail.apply_label_map", MAP_PARAMS, older)
    assert got.state is v.State.VERIFIED, got.reason


async def test_apply_label_map_contradicted_when_a_group_did_not_land(net, gmail):
    net.on(r"/labels/Label_1$", body=_label())
    net.on(r"/threads/t1$", body=_thread("t1", "Label_1"))
    partial = ActionResult(True, "x", evidence={"labels": {"receipts": ["t1"]},
                                                "label_ids": {"receipts": "Label_1"}},
                           rollback_token="Label_1:t1")
    got = await v.verify("mail.apply_label_map", MAP_PARAMS, partial)
    assert got.state is v.State.CONTRADICTED
    assert "fyi" in got.reason
    _no_secret(got)


async def test_apply_label_map_unknown_and_disabled(net, gmail, monkeypatch):
    got = await v.verify("mail.apply_label_map", MAP_PARAMS, ActionResult(True, "x"))
    assert got.state is v.State.UNKNOWN
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN")
    got = await v.verify("mail.apply_label_map", MAP_PARAMS, MAP_RESULT)
    assert got.failure_class is v.FailureClass.DISABLED
    assert net.seen == []


# ================================================================== mail drafts

BODY = "Thanks, Friday works for me."


def _draft(did="r-1", thread="th1", body=BODY, to='"Ann Buyer" <Ann@Example.com>',
           subject="Re: Meeting"):
    data = base64.urlsafe_b64encode((body + "\r\n").encode()).decode()
    return {"id": did, "message": {
        "id": "m9", "threadId": thread, "labelIds": ["DRAFT"],
        "payload": {"headers": [{"name": "To", "value": to},
                                {"name": "Subject", "value": subject}],
                    "body": {"data": data}}}}


DR_PARAMS = {"thread_id": "th1", "body": BODY}
DR_RESULT = ActionResult(True, "saved", evidence={
    "draft_id": "r-1", "thread_id": "th1", "to_sha256": sha12("ann@example.com"),
    "subject_sha256": sha12("Re: Meeting"), "body_sha256": sha12(BODY)},
    rollback_token="r-1")


async def test_draft_reply_verified(net, gmail):
    net.on(r"/drafts/r-1$", body=_draft(to="Ann Buyer <ann@example.com>"))
    got = await v.verify("mail.draft_reply", DR_PARAMS, DR_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["body_sha256"] == sha12(BODY)
    dumped = json.dumps(got.as_audit())
    assert "ann@example.com" not in dumped.lower() and BODY not in dumped
    _no_secret(got)


@pytest.mark.parametrize("draft, what", [
    (_draft(body="something else entirely"), "body"),
    (_draft(thread="th2"), "thread"),
    (_draft(to="mallory@example.net"), "recipient"),
    (_draft(subject="Re: Other"), "subject"),
])
async def test_draft_reply_contradicted(net, gmail, draft, what):
    net.on(r"/drafts/r-1$", body=draft)
    got = await v.verify("mail.draft_reply", DR_PARAMS, DR_RESULT)
    assert got.state is v.State.CONTRADICTED
    assert what in got.reason
    _no_secret(got)


async def test_draft_reply_contradicted_when_the_draft_is_gone(net, gmail):
    net.on(r"/drafts/r-1$", status=404, body={})
    got = await v.verify("mail.draft_reply", DR_PARAMS, DR_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_draft_reply_unknown_without_an_id_and_when_off(net, gmail, monkeypatch):
    got = await v.verify("mail.draft_reply", DR_PARAMS, ActionResult(True, "x"))
    assert got.state is v.State.UNKNOWN
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN")
    got = await v.verify("mail.draft_reply", DR_PARAMS, DR_RESULT)
    assert got.failure_class is v.FailureClass.DISABLED
    assert net.seen == []


@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"),
                                 httpx.ReadTimeout("slow")])
async def test_mail_raising_read_is_unknown(net, gmail, exc):
    net.on(r"/drafts/r-1$", raises=exc)
    got = await v.verify("mail.draft_reply", DR_PARAMS, DR_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.TRANSIENT
    _no_secret(got)


async def test_mail_auth_error_is_unknown_auth(net, gmail):
    net.on(r"/labels/Label_1$", status=401, body={})
    got = await v.verify("mail.apply_labels", LB_PARAMS, LB_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.AUTH


NUDGE_RESULT = ActionResult(True, "saved", evidence=dict(DR_RESULT.evidence),
                            rollback_token="th1:r-1")


async def test_draft_nudge_verified(net, gmail, db):
    net.on(r"/drafts/r-1$", body=_draft())
    db.one["mail_followups"] = {"nudged_at": "2026-09-28T10:00:00+00:00"}
    got = await v.verify("mail.draft_nudge", {"thread_id": "th1"}, NUDGE_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["draft"]["draft_id"] == "r-1"
    _no_secret(got)


async def test_draft_nudge_contradicted(net, gmail, db):
    net.on(r"/drafts/r-1$", body=_draft())
    db.one["mail_followups"] = {"nudged_at": None}
    got = await v.verify("mail.draft_nudge", {"thread_id": "th1"}, NUDGE_RESULT)
    assert got.state is v.State.CONTRADICTED and "followup" in got.reason
    got = await v.verify("mail.draft_nudge", {"thread_id": "other"}, NUDGE_RESULT)
    assert got.state is v.State.CONTRADICTED
    _no_secret(got)


async def test_draft_nudge_unknown(net, gmail, db):
    got = await v.verify("mail.draft_nudge", {"thread_id": "th1"},
                         ActionResult(True, "x"))
    assert got.state is v.State.UNKNOWN
    # no body hash recorded: the draft leg cannot compare, so not verified
    net.on(r"/drafts/r-1$", body=_draft())
    db.one["mail_followups"] = {"nudged_at": "2026-09-28"}
    got = await v.verify("mail.draft_nudge", {"thread_id": "th1"},
                         ActionResult(True, "x", rollback_token="th1:r-1"))
    assert got.state is v.State.UNKNOWN


async def test_draft_nudge_raising_select_is_unknown(net, gmail, db):
    net.on(r"/drafts/r-1$", body=_draft())
    db.raises = RuntimeError(f"db down {SECRET}")
    got = await v.verify("mail.draft_nudge", {"thread_id": "th1"}, NUDGE_RESULT)
    assert got.state is v.State.UNKNOWN
    _no_secret(got)


# ================================================================== mac

MAC_CASES = [
    ("mac.add_reminder", {"title": " Call mum ", "due": "2026-09-29T17:00:00+00:00"},
     "add_reminder", {"title": "Call mum", "due": "2026-09-29 17:00"}),
    ("mac.open_app", {"app": "Notes"}, "open_app", {"app": "Notes"}),
    ("mac.music_control", {"command": "pause"}, "music_control", {"command": "pause"}),
    ("mac.play_music", {"query": "lofi"}, "music_search", {"query": "lofi"}),
]


def _job(op, params, status="done", ok=True):
    return {"op": op, "params": json.dumps(params), "status": status, "ok": ok}


def _mac_result():
    return ActionResult(True, "done", evidence={"job": 9},
                        rollback_token="x-apple-reminder://ABC-1")


@pytest.mark.parametrize("action, params, op, stored", MAC_CASES)
async def test_mac_clean_job_is_unknown_never_verified(net, db, mac_on, action,
                                                       params, op, stored):
    db.one["mac_jobs"] = _job(op, stored)
    got = await v.verify(action, params, _mac_result())
    assert got.state is v.State.UNKNOWN
    assert got.reason and got.evidence["job"] == 9 and got.evidence["status"] == "done"
    assert net.seen == []
    _no_secret(got)


@pytest.mark.parametrize("action, params, op, stored", MAC_CASES)
async def test_mac_contradicted_on_a_job_that_disagrees(net, db, mac_on, action,
                                                        params, op, stored):
    db.one["mac_jobs"] = _job("quit_app", stored)
    got = await v.verify(action, params, _mac_result())
    assert got.state is v.State.CONTRADICTED
    assert got.failure_class is v.FailureClass.POLICY
    db.one["mac_jobs"] = _job(op, {k: "different" for k in stored})
    got = await v.verify(action, params, _mac_result())
    assert got.state is v.State.CONTRADICTED
    db.one["mac_jobs"] = _job(op, stored, status="unknown", ok=None)
    got = await v.verify(action, params, _mac_result())
    assert got.state is v.State.CONTRADICTED
    db.one["mac_jobs"] = _job(op, stored, ok=False)
    got = await v.verify(action, params, _mac_result())
    assert got.state is v.State.CONTRADICTED
    _no_secret(got)


@pytest.mark.parametrize("action, params, op, stored", MAC_CASES)
async def test_mac_unknown_cases(net, db, mac_on, monkeypatch, action, params, op,
                                 stored):
    got = await v.verify(action, params, ActionResult(True, "x"))
    assert got.state is v.State.UNKNOWN and "no job id" in got.reason
    got = await v.verify(action, params, _mac_result())      # row missing
    assert got.state is v.State.UNKNOWN
    db.raises = RuntimeError("db down")
    got = await v.verify(action, params, _mac_result())
    assert got.state is v.State.UNKNOWN
    db.raises = None
    monkeypatch.delenv("MAC_AGENT_TOKEN")
    db.sql.clear()
    got = await v.verify(action, params, _mac_result())
    assert got.failure_class is v.FailureClass.DISABLED
    assert db.sql == []


# ================================================================== messages.draft

def _msg_result(ids=(1, 2), drafts=("Sure, see you then!", "On it.")):
    return ActionResult(True, "drafted", evidence={
        "ids": list(ids), "draft_hashes": {str(i): sha12(d) for i, d in zip(ids, drafts,
                                                                            strict=True)}},
        rollback_token=",".join(map(str, ids)))


async def test_messages_draft_verified(net, db):
    db.all["messages_inbound"] = [{"id": 1, "draft": "Sure, see you then!",
                                   "replied_at": None},
                                  {"id": 2, "draft": "On it.", "replied_at": None}]
    got = await v.verify("messages.draft", {}, _msg_result())
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["read"] == {"1": sha12("Sure, see you then!"),
                                    "2": sha12("On it.")}
    assert "Sure, see you" not in json.dumps(got.as_audit())
    assert net.seen == []
    _no_secret(got)


@pytest.mark.parametrize("rows, why", [
    ([{"id": 1, "draft": "Sure, see you then!", "replied_at": None}], "not in"),
    ([{"id": 1, "draft": "Sure, see you then!", "replied_at": None},
      {"id": 2, "draft": None, "replied_at": None}], "no draft"),
    ([{"id": 1, "draft": "Sure, see you then!", "replied_at": None},
      {"id": 2, "draft": "changed", "replied_at": None}], "differs"),
])
async def test_messages_draft_contradicted(db, rows, why):
    db.all["messages_inbound"] = rows
    got = await v.verify("messages.draft", {}, _msg_result())
    assert got.state is v.State.CONTRADICTED
    assert why in got.reason
    _no_secret(got)


async def test_messages_draft_unknown(db, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_TOKEN", SECRET)
    got = await v.verify("messages.draft", {}, ActionResult(True, "nothing"))
    assert got.state is v.State.UNKNOWN
    db.all["messages_inbound"] = [{"id": 1, "draft": "x", "replied_at": None}]
    got = await v.verify("messages.draft", {},
                         ActionResult(True, "x", evidence={"ids": [1]}))
    assert got.state is v.State.UNKNOWN            # no hashes recorded
    db.raises = RuntimeError(f"db down {SECRET}")
    got = await v.verify("messages.draft", {}, _msg_result())
    assert got.state is v.State.UNKNOWN
    _no_secret(got)


async def test_messages_draft_run_records_hashes(monkeypatch):
    from atlas.modules import messages

    async def nothing():
        return None

    async def rows(*_a):
        return [{"id": 5, "platform": "instagram", "sender": "s", "text": "hi"}]

    async def write(*_a):
        return None

    class Reply:
        text = "  Hello there!  "

    async def think(*_a, **_k):
        return Reply()

    monkeypatch.setattr(messages, "_sync", nothing)
    monkeypatch.setattr(messages, "fetch_all", rows)
    monkeypatch.setattr(messages, "execute_sql", write)
    monkeypatch.setattr(messages, "think", think)
    result = await messages.draft_replies()
    assert result.evidence["ids"] == [5]
    assert result.evidence["draft_hashes"] == {"5": sha12("Hello there!")}


# ================================================================== fiverr

FV_PARAMS = {"conversation": "buyer_joe", "body": "Hi Joe, yes I can do that by Friday."}


def _fv_row(**over):
    return {"id": 42, "source": "fiverr", "external_id": "buyer_joe",
            "draft": FV_PARAMS["body"], "status": "drafted", **over}


FV_RESULT = ActionResult(True, "ready", evidence={"draft_id": 42,
                                                  "draft_sha256": sha12(FV_PARAMS["body"])},
                         rollback_token="buyer_joe")


async def test_fiverr_verified(net, db):
    db.one["messages"] = _fv_row()
    got = await v.verify("fiverr.draft_buyer_reply", FV_PARAMS, FV_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence == {"draft_id": 42, "status": "drafted",
                            "draft_sha256": sha12(FV_PARAMS["body"])}
    assert net.seen == []
    _no_secret(got)


@pytest.mark.parametrize("row, why", [
    (None, "not in messages"),
    (_fv_row(external_id="someone_else"), "conversation"),
    (_fv_row(draft="another text"), "differs"),
    (_fv_row(source="gmail"), "not a Fiverr"),
])
async def test_fiverr_contradicted(db, row, why):
    db.one["messages"] = row
    got = await v.verify("fiverr.draft_buyer_reply", FV_PARAMS, FV_RESULT)
    assert got.state is v.State.CONTRADICTED
    assert why in got.reason
    _no_secret(got)


async def test_fiverr_unknown(db, monkeypatch):
    monkeypatch.setenv("FIVERR_SESSION_TOKEN", SECRET)
    got = await v.verify("fiverr.draft_buyer_reply", FV_PARAMS,
                         ActionResult(True, "ready", rollback_token="buyer_joe"))
    assert got.state is v.State.UNKNOWN
    db.raises = RuntimeError(f"db down {SECRET}")
    got = await v.verify("fiverr.draft_buyer_reply", FV_PARAMS, FV_RESULT)
    assert got.state is v.State.UNKNOWN
    _no_secret(got)


async def test_fiverr_run_records_the_row_id_and_hash(monkeypatch):
    from atlas.modules import fiverr
    seen = []

    async def fetch_one(sql, *args):
        seen.append(sql)
        return {"id": 42}

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    result = await fiverr.draft_buyer_reply("buyer_joe", FV_PARAMS["body"])
    assert result.ok and result.rollback_token == "buyer_joe"
    assert result.evidence == {"draft_id": 42, "draft_sha256": sha12(FV_PARAMS["body"])}
    assert "RETURNING id" in seen[0]


async def test_mail_draft_reply_run_records_hashes(net, monkeypatch):
    async def token(*_a, **_k):
        return SECRET

    async def api(path, account=None, **_p):
        return {"messages": [{"payload": {"headers": [
            {"name": "From", "value": "Ann <ann@example.com>"},
            {"name": "Subject", "value": "Meeting"},
            {"name": "Message-ID", "value": "<x@y>"}]}}]}

    async def post(self, url, **_k):
        return httpx.Response(200, json={"id": "r-1"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(mail, "_access_token", token)
    monkeypatch.setattr(mail, "_api", api)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = await mail.draft_reply("th1", BODY)
    assert result.rollback_token == "r-1"
    assert result.evidence == {"draft_id": "r-1", "thread_id": "th1",
                               "to_sha256": sha12("ann@example.com"),
                               "subject_sha256": sha12("Re: Meeting"),
                               "body_sha256": sha12(BODY)}
