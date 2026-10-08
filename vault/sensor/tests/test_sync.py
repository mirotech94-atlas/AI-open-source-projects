"""Hub sync tests: HubClient.pull/push/result and sync.apply_edit.

HubClient._req is monkeypatched everywhere here so no test touches the
network. paths.DATA_DIR is monkeypatched to tmp_path so no test touches the
real ~/.guardian.
"""
import json
import os

from guardian_sensor import paths, sync


def _client(monkeypatch, tmp_path, responses):
    """Build a HubClient whose _req(method, path, body=None) is replaced by a
    fake that pops the next canned response for that (method, path) and
    records every call in `calls`."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    client = sync.HubClient("https://hub.example.invalid", "tok123")
    calls = []

    def fake_req(method, path, body=None):
        calls.append((method, path, body))
        key = (method, path.split("?")[0])
        if key not in responses:
            raise AssertionError(f"unexpected request {key}")
        resp = responses[key]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(client, "_req", fake_req)
    return client, calls


# ---------------- pull ----------------

def test_pull_bootstraps_local_config_when_missing(monkeypatch, tmp_path):
    hub_cfg = {"sites": [{"name": "x", "url": "https://x"}], "owner": "Acme"}
    client, calls = _client(monkeypatch, tmp_path, {
        ("GET", "/config"): hub_cfg,
        ("GET", "/edits"): {"edits": [], "last_id": 0},
    })
    state = {}
    result = client.pull(state)
    assert result["config"]["owner"] == "Acme"
    with open(paths.p("config.json")) as f:
        on_disk = json.load(f)
    assert on_disk == hub_cfg


def test_pull_local_config_wins_when_file_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    local_cfg = {"owner": "Local"}
    with open(paths.p("config.json"), "w") as f:
        json.dump(local_cfg, f)
    hub_cfg = {"owner": "FromHub"}
    client, calls = _client(monkeypatch, tmp_path, {
        ("GET", "/config"): hub_cfg,
        ("GET", "/edits"): {"edits": [], "last_id": 0},
    })
    state = {}
    result = client.pull(state)
    assert result["config"]["owner"] == "Local"
    with open(paths.p("config.json")) as f:
        on_disk = json.load(f)
    assert on_disk == local_cfg


def test_pull_applies_edits_and_sets_offset(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(paths.p("config.json"), "w") as f:
        json.dump({"owner": "Acme"}, f)
    edits = [
        {"id": 1, "ts": "2026-09-09T00:00:00", "who": "hub", "path": "/api/device",
         "body": {"mac": "aa:bb", "label": "Printer", "trusted": True}},
        {"id": 2, "ts": "2026-09-09T00:01:00", "who": "hub", "path": "/api/config",
         "body": {"config": {"owner": "NewOwner"}}},
    ]
    client, calls = _client(monkeypatch, tmp_path, {
        ("GET", "/config"): {},
        ("GET", "/edits"): {"edits": edits, "last_id": 2},
    })
    state = {"lan": {"known_macs": {"aa:bb": {"ip": "1.2.3.4"}}}}
    result = client.pull(state)
    assert state["hub_edits_offset"] == 2
    assert state["lan"]["known_macs"]["aa:bb"]["label"] == "Printer"
    assert state["lan"]["known_macs"]["aa:bb"]["trusted"] is True
    # /api/config edit replaced the on-disk raw config
    with open(paths.p("config.json")) as f:
        on_disk = json.load(f)
    assert on_disk == {"owner": "NewOwner"}
    assert result["config"]["owner"] == "NewOwner"
    # the work queue is NOT part of pull() any more (see poll_queue() below) --
    # pull() must not even touch GET /queue.
    assert not any(c[1].startswith("/queue") for c in calls)

    # GET /edits was called with after=<previous offset> (0)
    edits_calls = [c for c in calls if c[1].startswith("/edits")]
    assert edits_calls and "after=0" in edits_calls[0][1]


def test_pull_offset_never_moves_backwards_on_missing_last_id(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(paths.p("config.json"), "w") as f:
        json.dump({}, f)
    client, calls = _client(monkeypatch, tmp_path, {
        ("GET", "/config"): {},
        ("GET", "/edits"): {"edits": []},  # no last_id key at all
    })
    state = {"hub_edits_offset": 7}
    client.pull(state)
    assert state["hub_edits_offset"] == 7


# ---------------- push ----------------

def _write_lines(path, lines):
    with open(path, "w") as f:
        for line in lines:
            f.write(line + "\n")


def test_push_sends_only_new_lines_and_advances_offsets(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    _write_lines(paths.p("events.jsonl"), ['{"a":1}', '{"a":2}', '{"a":3}'])
    _write_lines(paths.p("history.jsonl"), ['{"h":1}'])
    # no remediation.jsonl at all -> treated as zero lines
    state = {"hub_sent": {"events": 1, "history": 0, "fixes": 0}}
    responses = {("POST", "/ingest"): {"ok": True, "stored": True}}
    client, calls = _client(monkeypatch, tmp_path, responses)
    payload = {"version": "0.1.0", "last": {"ts": "now"}, "state": state}
    result = client.push(payload)
    assert result == {"ok": True, "stored": True}
    post_calls = [c for c in calls if c[0] == "POST"]
    assert len(post_calls) == 1
    body = post_calls[0][2]
    assert body["events"] == [{"a": 2}, {"a": 3}]
    assert body["history"] == [{"h": 1}]
    assert body["fixes"] == []
    assert state["hub_sent"]["events"] == 3
    assert state["hub_sent"]["history"] == 1
    assert state["hub_sent"]["fixes"] == 0


def test_push_truncation_resets_offset_to_zero_and_resends(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    # file has 2 lines now but the offset claims 10 were already sent (file was
    # truncated/rotated since) -> must reset to 0 and resend everything
    _write_lines(paths.p("events.jsonl"), ['{"a":1}', '{"a":2}'])
    state = {"hub_sent": {"events": 10, "history": 0, "fixes": 0}}
    client, calls = _client(monkeypatch, tmp_path, {("POST", "/ingest"): {"ok": True}})
    payload = {"version": "0.1.0", "last": {}, "state": state}
    client.push(payload)
    body = calls[0][2]
    assert body["events"] == [{"a": 1}, {"a": 2}]
    assert state["hub_sent"]["events"] == 2


def test_push_dns_queries_capped_and_offset_tracks_file_and_line(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    dns_dir = os.path.join(str(tmp_path), "dns")
    os.makedirs(dns_dir, exist_ok=True)
    import datetime as dt
    today = dt.date.today().isoformat()
    fname = f"queries-{today}.jsonl"
    lines = [json.dumps({"name": f"x{i}.example.com"}) for i in range(7000)]
    _write_lines(os.path.join(dns_dir, fname), lines)
    state = {"hub_sent": {"events": 0, "history": 0, "fixes": 0, "dns": {"file": fname, "off": 0}}}
    client, calls = _client(monkeypatch, tmp_path, {("POST", "/ingest"): {"ok": True}})
    payload = {"version": "0.1.0", "last": {}, "state": state}
    client.push(payload)
    body = calls[0][2]
    assert len(body["dns_queries"]) == 5000
    assert state["hub_sent"]["dns"] == {"file": fname, "off": 5000}
    # second push catches up on the remainder
    client2, calls2 = _client(monkeypatch, tmp_path, {("POST", "/ingest"): {"ok": True}})
    client2.push(payload)
    body2 = calls2[0][2]
    assert len(body2["dns_queries"]) == 2000
    assert state["hub_sent"]["dns"] == {"file": fname, "off": 7000}


def test_push_dns_new_day_resets_offset(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    dns_dir = os.path.join(str(tmp_path), "dns")
    os.makedirs(dns_dir, exist_ok=True)
    import datetime as dt
    today = dt.date.today().isoformat()
    fname = f"queries-{today}.jsonl"
    _write_lines(os.path.join(dns_dir, fname), [json.dumps({"name": "a.com"})])
    # hub_sent still points at yesterday's (now-gone) file
    state = {"hub_sent": {"events": 0, "history": 0, "fixes": 0, "dns": {"file": "queries-2020-01-01.jsonl", "off": 500}}}
    client, calls = _client(monkeypatch, tmp_path, {("POST", "/ingest"): {"ok": True}})
    payload = {"version": "0.1.0", "last": {}, "state": state}
    client.push(payload)
    body = calls[0][2]
    assert body["dns_queries"] == [{"name": "a.com"}]
    assert state["hub_sent"]["dns"] == {"file": fname, "off": 1}


def test_push_failure_does_not_advance_offsets(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync, "RETRY_DELAY", 0)
    _write_lines(paths.p("events.jsonl"), ['{"a":1}'])
    state = {"hub_sent": {"events": 0, "history": 0, "fixes": 0}}

    def fake_req(method, path, body=None):
        raise OSError("network down")

    client = sync.HubClient("https://hub.example.invalid", "tok")
    monkeypatch.setattr(client, "_req", fake_req)
    payload = {"version": "0.1.0", "last": {}, "state": state}
    result = client.push(payload)
    assert result is None or result.get("ok") is not True
    assert state["hub_sent"]["events"] == 0


# ---------------- poll_queue (the fast path, independent of pull()) ----------------

def test_poll_queue_hits_only_the_queue_endpoint(monkeypatch, tmp_path):
    client, calls = _client(monkeypatch, tmp_path, {
        ("GET", "/queue"): {"queued": [{"id": 5, "path": "/api/fix", "body": {}, "who": "hub"}]},
    })
    assert client.poll_queue() == [{"id": 5, "path": "/api/fix", "body": {}, "who": "hub"}]
    assert calls == [("GET", "/queue", None)]


def test_poll_queue_empty_is_an_empty_list_not_none(monkeypatch, tmp_path):
    client, _calls = _client(monkeypatch, tmp_path, {("GET", "/queue"): {"queued": []}})
    assert client.poll_queue() == []


# ---------------- result ----------------

def test_result_posts_queue_result(monkeypatch, tmp_path):
    client, calls = _client(monkeypatch, tmp_path, {("POST", "/queue/42/result"): {"ok": True}})
    client.result(42, True, "did the thing", {"detail": 1})
    assert calls == [("POST", "/queue/42/result", {"ok": True, "out": "did the thing", "result": {"detail": 1}})]


# ---------------- apply_edit ----------------

def test_apply_edit_device_label_and_trusted():
    state = {"lan": {"known_macs": {"aa:bb": {"ip": "1.2.3.4"}}}}
    cfg = {}
    e = {"id": 1, "ts": "t", "who": "hub", "path": "/api/device", "body": {"mac": "aa:bb", "label": "Printer", "trusted": True}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "state"
    assert state["lan"]["known_macs"]["aa:bb"]["label"] == "Printer"
    assert state["lan"]["known_macs"]["aa:bb"]["trusted"] is True


def test_apply_edit_device_unknown_mac_is_noop():
    state = {"lan": {"known_macs": {}}}
    cfg = {}
    e = {"path": "/api/device", "body": {"mac": "zz:zz", "label": "Nope"}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched is None
    assert state["lan"]["known_macs"] == {}


def test_apply_edit_config_notify_local_only(tmp_path, monkeypatch):
    """Fix round 1, finding 1: when the hub flips `notify.local_only` (the
    first-Integration-row flip in `routers/integrations.py`), it writes an
    `/api/config` Edit row exactly like `routers/write.py`'s own
    `update_config` -- so a live sensor's ordinary pull/apply loop picks it
    up with no special-case code. This exercises the full `client.pull`
    path (not just the bare `apply_edit` unit above) end to end, matching
    `test_pull_applies_edits_and_sets_offset`'s pattern."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(paths.p("config.json"), "w") as f:
        json.dump({"owner": "Acme", "notify": {"local_only": False}}, f)
    edits = [
        {"id": 1, "ts": "2026-09-10T00:00:00", "who": "admin", "path": "/api/config",
         "body": {"config": {"owner": "Acme", "notify": {"local_only": True}}}},
    ]
    client, _calls = _client(monkeypatch, tmp_path, {
        ("GET", "/config"): {},
        ("GET", "/edits"): {"edits": edits, "last_id": 1},
    })
    result = client.pull({})
    assert result["config"]["notify"]["local_only"] is True
    with open(paths.p("config.json")) as f:
        on_disk = json.load(f)
    assert on_disk["notify"]["local_only"] is True


def test_apply_edit_rec_yes():
    state = {}
    cfg = {}
    e = {"ts": "2026-09-09T00:00:00", "who": "alice", "path": "/api/rec", "body": {"key": "k1", "decision": "yes"}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "state"
    entry = state["rec_decisions"]["k1"]
    assert entry["decision"] == "yes" and entry["by"] == "alice" and entry["status"] == ""


def test_apply_edit_rec_no():
    state = {}
    cfg = {}
    e = {"ts": "t", "who": "bob", "path": "/api/rec", "body": {"key": "k2", "decision": "no"}}
    sync.apply_edit(e, state, cfg)
    assert state["rec_decisions"]["k2"]["decision"] == "no"


def test_apply_edit_rec_clear_removes_entry():
    state = {"rec_decisions": {"k1": {"decision": "yes"}}}
    cfg = {}
    e = {"path": "/api/rec", "body": {"key": "k1", "decision": "clear"}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "state"
    assert "k1" not in state["rec_decisions"]


def test_apply_edit_attacker_sets_config_method():
    state = {}
    cfg = {"auto_fix": {"enabled": True}}
    e = {"path": "/api/attacker", "body": {"method": "block_subnet"}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "config"
    assert cfg["auto_fix"]["attacker_method"] == "block_subnet"


def test_apply_edit_profile_sets_owner_and_profile():
    state = {}
    cfg = {}
    e = {"path": "/api/profile", "body": {"owner": "Acme Corp", "profile": {"tier": "pro"}}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "config"
    assert cfg["owner"] == "Acme Corp"
    assert cfg["profile"] == {"tier": "pro"}


def test_apply_edit_ack_and_maintenance():
    state = {}
    cfg = {}
    e = {"ts": "2026-09-09T00:00:00", "path": "/api/ack", "body": {"key": "alert1", "note": "seen"}}
    sync.apply_edit(e, state, cfg)
    assert state["acked"]["alert1"]["note"] == "seen"
    e2 = {"path": "/api/maintenance", "body": {"hours": 2}}
    touched = sync.apply_edit(e2, state, cfg)
    assert touched == "state"
    assert state["maintenance_until"]


def test_apply_edit_alert_ack_sets_acked_state():
    # Fix C1: the hub's /api/alert Edit body is {"key", "action", "hours"}
    # -- apply_edit reads b.get("action") (not "op").
    state = {"alerts_db": {"a1": {"key": "a1", "state": "open"}}}
    cfg = {}
    e = {"ts": "2026-09-09T00:00:00", "path": "/api/alert", "body": {"key": "a1", "action": "ack"}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "state"
    assert state["alerts_db"]["a1"]["state"] == "acked"
    assert state["acked"]["a1"]["ts"] == "2026-09-09T00:00:00"


def test_apply_edit_alert_snooze_sets_snooze_until_and_clears_ack():
    state = {"alerts_db": {"a1": {"key": "a1", "state": "open"}}, "acked": {"a1": {"ts": "t"}}}
    cfg = {}
    e = {"path": "/api/alert", "body": {"key": "a1", "action": "snooze", "hours": 4}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "state"
    assert state["alerts_db"]["a1"]["state"] == "snoozed"
    assert state["alerts_db"]["a1"]["snooze_until"]
    assert "a1" not in state["acked"]


def test_apply_edit_alert_resolve_and_reopen():
    state = {"alerts_db": {"a1": {"key": "a1", "state": "open"}}}
    cfg = {}
    e = {"ts": "2026-09-09T00:00:00", "path": "/api/alert", "body": {"key": "a1", "action": "resolve"}}
    sync.apply_edit(e, state, cfg)
    assert state["alerts_db"]["a1"]["state"] == "resolved"
    assert state["alerts_db"]["a1"]["resolved_by"] == "human"

    e2 = {"path": "/api/alert", "body": {"key": "a1", "action": "reopen"}}
    touched = sync.apply_edit(e2, state, cfg)
    assert touched == "state"
    assert state["alerts_db"]["a1"]["state"] == "open"
    assert "snooze_until" not in state["alerts_db"]["a1"]


def test_apply_edit_alert_unknown_key_is_noop():
    state = {"alerts_db": {}}
    cfg = {}
    e = {"path": "/api/alert", "body": {"key": "nope", "action": "ack"}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched is None


def test_apply_edit_config_replaces_config_dict():
    # Fix C2: the hub's /api/config Edit body wraps the config as
    # {"config": {...}} -- apply_edit reads b.get("config").
    state = {}
    cfg = {"owner": "old", "sites": []}
    new_cfg = {"owner": "new", "sites": [{"name": "x"}]}
    e = {"path": "/api/config", "body": {"config": new_cfg}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "config"
    assert cfg == new_cfg


def test_apply_edit_device_quarantine_on_and_off():
    # Fix I1: the hub's /api/device/quarantine Edit body is
    # {"mac", "on"} -- apply_edit reads b.get("on", True).
    state = {"lan": {"known_macs": {"aa:bb": {"ip": "1.2.3.4", "trusted": True}}}}
    cfg = {}
    e = {"path": "/api/device/quarantine", "body": {"mac": "aa:bb", "on": True}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched == "state"
    assert state["lan"]["known_macs"]["aa:bb"]["quarantined"] is True
    assert state["lan"]["known_macs"]["aa:bb"]["trusted"] is False

    e2 = {"path": "/api/device/quarantine", "body": {"mac": "aa:bb", "on": False}}
    sync.apply_edit(e2, state, cfg)
    assert state["lan"]["known_macs"]["aa:bb"]["quarantined"] is False


def test_apply_edit_unknown_path_is_noop():
    state = {}
    cfg = {}
    e = {"path": "/api/nonsense", "body": {}}
    touched = sync.apply_edit(e, state, cfg)
    assert touched is None


def test_push_skips_unparseable_lines_but_still_counts_them(monkeypatch, tmp_path):
    # one corrupt write must never wedge the offset: the bad line is dropped
    # from the payload, but the offset still moves past it.
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    _write_lines(paths.p("events.jsonl"), ['{"a":1}', 'not json at all', '{"a":3}'])
    state = {"hub_sent": {"events": 0, "history": 0, "fixes": 0}}
    client, calls = _client(monkeypatch, tmp_path, {("POST", "/ingest"): {"ok": True}})
    client.push({"version": "0.1.0", "last": {}, "state": state})
    assert calls[0][2]["events"] == [{"a": 1}, {"a": 3}]
    assert state["hub_sent"]["events"] == 3
