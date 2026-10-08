"""No alert, recommendation or score line may carry a credential.

An alert message is not a private place. It reaches push notifications,
Discord/Telegram/PagerDuty/webhook integrations, the built PDF/HTML
reports, and reports are shareable through unauthenticated `/r/<token>`
links. So anything Vault writes into prose must be safe to read aloud
to a stranger.

The test is sentinel-based rather than shape-based: every secret-ish value
in the fixture config and in the fixture scan result is replaced with a
distinctive, obviously-fake marker, the whole alert/recommendation engine
is run over that, and the marker must appear nowhere in the produced text.
That catches *any* interpolation of a configured secret, including one
added later under a name this test has never heard of -- where a regex
over prose would both miss cases and flag innocent numbers.

A second, cheaper assertion covers the specific mistake this file was
written for: a `/hb/<name>/<token>` URL with the token still in it.
"""

import copy
import json
import pathlib
import re

from guardian_sensor import alerts

FX = pathlib.Path(__file__).parent / "fixtures"

#: Deliberately not token-shaped and deliberately self-describing: if this
#: ever shows up in a real message, the message says out loud that it is a
#: test artefact rather than looking like a working credential.
SENTINEL = "SENTINEL-NOT-A-REAL-TOKEN-000"

#: Config/result keys whose value is a credential rather than a fact about
#: the network. `path`, `file` and the like are deliberately absent: the
#: `.env` permissions alert prints a file path on purpose, and a path is
#: not a secret.
SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api_?key|apikey|access_key|topic|webhook|routing_key)",
    re.IGNORECASE,
)

#: `/hb/<name>/<something>` with a token still attached. The fixed alert
#: names the heartbeat but never the credential, so no message may match.
HB_URL_RE = re.compile(r"/hb/[^/\s`'\"]+/[^\s`'\"]+")


def _seed_secrets(obj):
    """Recursively replace every secret-ish leaf with the sentinel."""
    if isinstance(obj, dict):
        return {
            k: (SENTINEL if (SECRET_KEY_RE.search(str(k)) and not isinstance(v, (dict, list)))
                else _seed_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_seed_secrets(v) for v in obj]
    return obj


def _load():
    with open(FX / "last.json") as f:
        res = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    return _seed_secrets(res), _seed_secrets(cfg), _seed_secrets(state)


def _all_prose():
    """Every user-visible string the alert engine produces for the seeded
    inputs: alert messages, recommendation titles and their 'why', and the
    score deductions' explanations."""
    res, cfg, state = _load()
    lines: list[str] = []
    for a in alerts.build_alerts(copy.deepcopy(res), cfg, copy.deepcopy(state)):
        lines.append(str(a.get("msg") or ""))
    for r in alerts.recommendations(copy.deepcopy(res), cfg, copy.deepcopy(state)):
        lines.append(str(r.get("title") or ""))
        lines.append(str(r.get("why") or ""))
    score = alerts.security_score(copy.deepcopy(res), cfg, copy.deepcopy(state))
    for d in score.get("deductions") or []:
        lines.append(str(d.get("why") or ""))
    return [l for l in lines if l]


def test_no_seeded_secret_reaches_any_message():
    offenders = [l for l in _all_prose() if SENTINEL in l]
    assert offenders == [], f"a configured secret was interpolated into prose: {offenders}"


def test_no_heartbeat_url_carries_its_token():
    offenders = [l for l in _all_prose() if HB_URL_RE.search(l)]
    assert offenders == [], f"a heartbeat ping URL (with its token) reached prose: {offenders}"


def test_the_setup_alert_still_says_what_to_do():
    """Removing the credential must not remove the instruction: a user told
    only "this never pinged" is worse off than before."""
    res, cfg, state = _load()
    msgs = {a["key"]: a["msg"] for a in alerts.build_alerts(copy.deepcopy(res), cfg, copy.deepcopy(state))}
    setup = next((v for k, v in msgs.items() if k.endswith(":setup") and k.startswith("hb:")), None)
    assert setup, "the fixture no longer produces a heartbeat setup alert"
    assert "never pinged" in setup
    assert "Heartbeats" in setup, "the alert must point at where the command actually is"
