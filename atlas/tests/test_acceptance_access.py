"""Who may approve, and which tenant a write may reach: P06 (plan p. 14) and
D07 (plan p. 15).

P06: a lower-privilege actor, or the wrong tenant, attempting an approval is
denied without leaking protected details -- on Slack (the approver list) and
on Guardian (the write token's tenant).
D07: Guardian tenant and trust boundaries -- no cross-tenant access, and no
path that writes a device trust label.

All in-suite; docs/acceptance.md is the matrix.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Health
from atlas.kernel.executor import ExecutionRequest
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import guardian
from atlas.modules.guardian import incident

# Imported here, not first inside a test: the Guardian writes now import
# readback.py (same_tenant), and its @verifier registrations made inside the
# `registries` fixture would be rolled back with it while kernel.verify
# remembers the package as loaded -- every later test would find "no
# verifier" for guardian.*.
from atlas.modules.guardian import readback as _readback  # noqa: F401

# Fixtures. pytest registers them by their own names; the aliases keep
# the module names apart from the test parameters that request them.
from tests.acceptance_support import (
    APPROVER,
    client_fixture,  # noqa: F401
    decisions_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
)
from tests.test_ra_c6_guardian_suggest_only import MAC, Hub
from tests.test_slack_events import SECRET, message, post, settle

STRANGER = "USTRANGER"
SECRET_REASON = "rotate the flycheap stripe key sk_live_hidden"


def _signed_form(client, path: str, form: dict):
    body = urllib.parse.urlencode(form)
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:{body}".encode(),
                           hashlib.sha256).hexdigest()
    return client.post(path, content=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})


@pytest.fixture
def guarded(monkeypatch):
    """decide() and the proposal's details, wired to fail the test if a
    stranger's request reaches either; every reply captured."""
    replies: list[tuple[str, bool]] = []

    async def decide(*_a, **_kw):
        raise AssertionError("a non-approver reached decide()")

    async def detail(pid):
        raise AssertionError("a non-approver reached the proposal's details")

    async def reply(_url, text, *, ephemeral=False):
        replies.append((text, ephemeral))

    monkeypatch.setattr("atlas.api.main.decide", decide)
    monkeypatch.setattr("atlas.api.main._proposal_detail", detail)
    monkeypatch.setattr("atlas.api.main._slack_reply", reply)
    return replies


# -------------------------------------------------------------------- P06

@pytest.mark.parametrize("value", ["approve:82", "always:82", "decline:82",
                                   "detail:82", "ack:vpsie.balance"])
def test_P06_a_non_approvers_button_is_denied_without_the_cards_details(
        client, guarded, value):
    r = _signed_form(client, "/webhooks/slack", {"payload": json.dumps({
        "user": {"id": STRANGER}, "response_url": "https://hooks.slack/x",
        "actions": [{"value": value}]})})
    assert r.status_code == 200
    [(text, ephemeral)] = guarded
    assert text == "You are not an approver for ATLAS. Nothing was done."
    assert ephemeral is True             # only the clicker sees the refusal
    assert "82" not in text and "vpsie" not in text


def test_P06_a_non_approvers_slash_command_is_denied_and_decides_nothing(
        client, guarded):
    r = _signed_form(client, "/webhooks/slack/command", {
        "user_id": STRANGER, "text": "approve 82",
        "response_url": "https://hooks.slack/x"})
    assert r.json() == {"response_type": "ephemeral",
                        "text": "You're not an approver for ATLAS. Nothing was done."}
    assert guarded == []


@pytest.mark.parametrize("text", ["approve 82", "yes", "do it", "decline 82",
                                  "what is #82 about?"])
def test_P06_a_non_approvers_typed_words_are_ignored_silently(
        client, decisions, memory_backend, text):
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message(text, user=STRANGER))
    settle()
    # No decision, and no reply at all: an answer would confirm #82 exists.
    assert decided == [] and replied == []


def test_P06_the_same_words_from_the_approver_do_decide(client, decisions,
                                                        memory_backend):
    """The positive control for the test above: the gate is the sender."""
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message("approve 82", user=APPROVER))
    settle()
    assert decided == [(82, "approve", f"slack:{APPROVER}")]


# --------------------------------------------------------- Guardian tenants

READ, WRITE = "read-token-p06", "write-token-p06"
KEY = "dns:beacon:10.0.0.7:c2.example"


class Tenants:
    """The hub as two tokens see it. `accounts` maps a token to the account
    `GET /v1/whoami` names for it. Every request is recorded."""

    def __init__(self, accounts: dict[str, str]) -> None:
        self.accounts = accounts
        self.gets: list[str] = []
        self.posts: list[str] = []

    async def get_json(self, path, *, base_url=None, headers=None, params=None):
        token = str((headers or {}).get("Authorization", "")).removeprefix("Bearer ")
        self.gets.append(path)
        if path == "/v1/whoami":
            account = self.accounts.get(token)
            if account is None:
                return None, "HTTP 401"
            return {"account_slug": account, "scope": "read", "role": "owner"}, None
        if path.startswith("/v1/alerts/"):
            return {"key": KEY, "status": "open"}, None
        raise AssertionError(f"unexpected read {path}")

    async def post_json(self, path, **_kw):
        self.posts.append(path)
        if path.endswith("/scans"):
            return {"id": "job-7", "status": "queued"}, None
        return {"key": KEY, "status": "acked"}, None


@pytest.fixture
def tenants(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", READ)
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", WRITE)

    def install(accounts: dict[str, str]) -> Tenants:
        hub = Tenants(accounts)
        monkeypatch.setattr(guardian, "get_json", hub.get_json)
        monkeypatch.setattr("atlas.modules._http.get_json", hub.get_json)
        monkeypatch.setattr(incident, "post_json", hub.post_json)
        return hub
    return install


def _run(name: str, **params):
    action = next(a for a in guardian.MODULE.actions() if a.name == name)
    return action.run(**params)


CALLS = [("guardian.alert_op", {"alert_key": KEY, "op": "ack"}),
         ("guardian.rescan", {"network": "office", "alert_key": KEY})]


async def test_P06_an_approved_guardian_card_is_refused_on_the_wrong_tenant(
        tenants, monkeypatch, memory_backend, registries):
    """Miro approves "ack alert k" on the card his read token built; the
    write token is another tenant's. The approval does not carry across
    tenants, and the refusal names neither account."""
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    hub = tenants({READ: "acme-home", WRITE: "globex-office"})
    for action in guardian.MODULE.actions():
        REGISTRY.actions[action.name] = action
        REGISTRY.decisions[action.name] = classify(action)
    executor._recent.clear()
    out = await executor.execute(ExecutionRequest(
        "guardian.alert_op", {"alert_key": KEY, "op": "ack"},
        "the beacon is a false positive", approved_by=f"slack:{APPROVER}"))
    assert not out.ok
    assert out.detail.startswith("tenant not confirmed, so nothing was sent")
    assert "acme" not in out.detail and "globex" not in out.detail
    assert hub.posts == []
    assert not any(p.startswith("/v1/alerts/") for p in hub.gets), \
        "the other tenant's alert status is not even read"


# -------------------------------------------------------------------- D07

@pytest.mark.parametrize("name,params", CALLS)
@pytest.mark.parametrize("accounts,why", [
    ({READ: "acme-home", WRITE: "globex-office"}, "different Guardian accounts"),
    ({READ: "acme-home"}, "could not confirm"),        # the write whoami fails
])
async def test_D07_no_guardian_write_crosses_a_tenant(tenants, name, params,
                                                      accounts, why):
    hub = tenants(accounts)
    out = await _run(name, **params)
    assert not out.ok and why in out.detail
    assert hub.posts == []
    assert "acme" not in out.detail and "globex" not in out.detail


@pytest.mark.parametrize("name,params", CALLS)
async def test_D07_one_tenant_writes_once_the_positive_control(tenants, name, params):
    hub = tenants({READ: "acme-home", WRITE: "acme-home"})
    out = await _run(name, **params)
    assert out.ok, out.detail
    assert len(hub.posts) == 1


async def test_D07_no_path_writes_a_device_trust_label(tenants, monkeypatch):
    """Behaviour, not text: every Guardian write the catalog holds (with the
    write token set) runs, and the incident reading whose plan suggests a
    trust change is taken -- and not one request reaches the device route."""
    hub = tenants({READ: "acme-home", WRITE: "acme-home"})
    names = {a.name for a in guardian.MODULE.actions()}
    assert names == {"guardian.contain", "guardian.alert_op", "guardian.rescan"}
    assert not any("trust" in n or "device" in n for n in names)
    for name, params in [*CALLS, ("guardian.contain",
                                  {"alert_id": KEY, "target": "10.0.0.7",
                                   "kind": "isolate_host"})]:
        await _run(name, **params)
    assert hub.posts == ["/v1/alerts/dns%3Abeacon%3A10.0.0.7%3Ac2.example/ack",
                         "/v1/scans"]

    # The suggestion path: a beacon the hub names by mac.
    suggest = Hub()
    monkeypatch.setattr(guardian, "get_json", suggest.get_json)
    monkeypatch.setattr(incident, "post_json", suggest.post_json)
    obs = await guardian.check_open_incidents()
    assert obs.health is Health.DEGRADED
    assert "ATLAS changes no trust label" in obs.detail and MAC in obs.detail
    assert suggest.writes == []
    assert not any("/devices" in p for p in hub.gets + hub.posts)
    assert not any(r["path"].startswith("/v1/devices") for r in suggest.reads)
