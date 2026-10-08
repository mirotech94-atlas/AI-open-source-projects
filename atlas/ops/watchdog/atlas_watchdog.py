#!/usr/bin/env python3
"""ATLAS independent watchdog (RA-F4). Standard library only.

Runs every 5 minutes on a host that is NOT the ATLAS box (the FlyCheap VPS),
so a dead ATLAS process, a stalled scheduler or a dead host is still reported:
nothing here depends on ATLAS's own worker, database or Slack bot.

Three checks, each OK / FAIL / UNKNOWN (a check that cannot run is UNKNOWN,
never OK -- ATLAS invariant 7):

  healthz    GET <url> answers 200 within 10 s with {"ok": true}.
  tls        the certificate verifies for the hostname and has more than
             WARN_DAYS left.
  heartbeat  the scheduler cycle and the newest observation are recent. Read
             from the `heartbeat` object in the /healthz body
             ({"cycle_age_s": N, "observation_age_s": N}, docs/watchdog.md):
             an age over the limit (900 s) is FAIL; a null or missing age,
             or no heartbeat object at all (an older ATLAS), is UNKNOWN.

It alerts ONLY when a check's state changes, through a Slack *incoming
webhook* read from a local env file on this host. That is a separate route
from ATLAS's Slack bot token, which is never used here. With no webhook
configured it still runs and logs the checks, then logs
"no alternate route configured" and exits non-zero.

State lives in one JSON file, replaced atomically. If the alert cannot be
delivered the new state is NOT saved, so the next run sees the same
transition and tries again.

Exit codes: 0 all OK; 10 at least one check not OK (alert delivered or
unchanged); 2 no alternate route configured; 3 the alert could not be sent;
4 the watchdog itself crashed. 1 is left to Python's own unhandled-error exit,
so no failure of this script can read as "ran fine".
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("atlas_watchdog")

OK, FAIL, UNKNOWN = "OK", "FAIL", "UNKNOWN"

DEFAULT_URL = "https://atlas.example.com/healthz"
DEFAULT_ENV_FILE = "/etc/atlas-watchdog/watchdog.env"
DEFAULT_STATE = "/var/lib/atlas-watchdog/state.json"
TIMEOUT_S = 10.0
WEBHOOK_PREFIX = "https://hooks.slack.com/"
NO_ROUTE = "no alternate route configured"

EXIT_OK, EXIT_NO_ROUTE, EXIT_SEND_FAILED, EXIT_CRASHED, EXIT_NOT_OK = 0, 2, 3, 4, 10


@dataclass
class Result:
    name: str
    state: str
    detail: str


@dataclass
class Config:
    url: str
    webhook: str | None
    webhook_problem: str | None
    state_path: Path
    tls_warn_days: int
    heartbeat_max_s: int
    label: str


# ------------------------------------------------------------------ config

def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments ignored; quotes stripped."""
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def env_file_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    creds = os.environ.get("CREDENTIALS_DIRECTORY")   # systemd LoadCredential=
    if creds and (Path(creds) / "watchdog.env").exists():
        return Path(creds) / "watchdog.env"
    return Path(os.environ.get("ATLAS_WATCHDOG_ENV_FILE", DEFAULT_ENV_FILE))


def check_webhook(value: str | None) -> tuple[str | None, str | None]:
    """(webhook, problem). Only a Slack incoming-webhook URL is accepted; a bot
    token (ATLAS's route) is refused outright so it cannot be reused here."""
    value = (value or "").strip()
    if not value:
        return None, NO_ROUTE
    if value.startswith(("xoxb-", "xoxp-", "xoxa-", "xapp-")):
        return None, (f"{NO_ROUTE}: ATLAS_WATCHDOG_WEBHOOK holds a Slack token, "
                      "not an incoming-webhook URL; tokens are refused")
    if not value.startswith(WEBHOOK_PREFIX):
        return None, (f"{NO_ROUTE}: ATLAS_WATCHDOG_WEBHOOK is not a "
                      f"{WEBHOOK_PREFIX} incoming-webhook URL")
    return value, None


def load_config(env_file: str | None, state: str | None) -> Config:
    path = env_file_path(env_file)
    values = read_env_file(path)
    try:
        mode = path.stat().st_mode
        if mode & 0o077:
            log.warning("%s is readable by group/other; chmod 600 it", path)
    except OSError:
        pass

    def get(key: str, default: str) -> str:
        return os.environ.get(key) or values.get(key) or default

    webhook, problem = check_webhook(
        os.environ.get("ATLAS_WATCHDOG_WEBHOOK") or values.get("ATLAS_WATCHDOG_WEBHOOK"))
    return Config(
        url=get("ATLAS_WATCHDOG_URL", DEFAULT_URL),
        webhook=webhook,
        webhook_problem=problem,
        state_path=Path(state or get("ATLAS_WATCHDOG_STATE", DEFAULT_STATE)),
        tls_warn_days=int(get("ATLAS_WATCHDOG_TLS_WARN_DAYS", "14")),
        heartbeat_max_s=int(get("ATLAS_WATCHDOG_HEARTBEAT_MAX_S", "900")),
        label=get("ATLAS_WATCHDOG_LABEL", socket.gethostname()),
    )


def redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "<webhook>") if secret else text


# ------------------------------------------------------------------ checks

def fetch_healthz(url: str) -> tuple[Result, dict[str, Any] | None]:
    """One GET. Returns the healthz result and the parsed body (for heartbeat)."""
    start = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "atlas-watchdog/1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status = resp.status
            raw = resp.read(65536)
    except urllib.error.HTTPError as e:
        return Result("healthz", FAIL, f"HTTP {e.code}"), None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        return Result("healthz", FAIL, f"unreachable: {reason}"), None
    elapsed = time.monotonic() - start
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        body = None
    if status != 200:
        return Result("healthz", FAIL, f"HTTP {status}"), body
    if elapsed > TIMEOUT_S:
        return Result("healthz", FAIL, f"slow: {elapsed:.1f}s > {TIMEOUT_S:.0f}s"), body
    if not isinstance(body, dict) or body.get("ok") is not True:
        return Result("healthz", FAIL, "200 but body is not {\"ok\": true}"), body
    return Result("healthz", OK, f"200 in {elapsed:.1f}s"), body


def check_heartbeat(healthz: Result, body: dict[str, Any] | None,
                    max_age_s: int) -> Result:
    """Scheduler liveness from the /healthz `heartbeat` field.

    Shape (docs/watchdog.md): {"heartbeat": {"cycle_age_s": int|null,
    "observation_age_s": int|null}}. Both ages are required. An age over
    `max_age_s` is FAIL, and a proven FAIL wins over an unknown partner;
    otherwise anything that cannot prove both ages recent is UNKNOWN, never
    OK (ATLAS sends null when it could not establish an age)."""
    if healthz.state != OK or body is None:
        return Result("heartbeat", UNKNOWN, "healthz did not answer, so no heartbeat")
    hb = body.get("heartbeat")
    if not isinstance(hb, dict):
        return Result("heartbeat", UNKNOWN, "no heartbeat field in /healthz")
    ages: dict[str, int] = {}
    unknown: list[str] = []
    for key in ("cycle_age_s", "observation_age_s"):
        value = hb.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            unknown.append(f"{key} is {value!r}" if key in hb else f"{key} missing")
            continue
        ages[key] = int(value)
    stale = {k: v for k, v in ages.items() if v > max_age_s}
    if stale:
        parts = ", ".join(f"{k}={v}s" for k, v in sorted(stale.items()))
        extra = f"; {', '.join(unknown)}" if unknown else ""
        return Result("heartbeat", FAIL,
                      f"stale: {parts} (limit {max_age_s}s){extra}")
    if unknown:
        return Result("heartbeat", UNKNOWN, ", ".join(unknown))
    parts = ", ".join(f"{k}={v}s" for k, v in sorted(ages.items()))
    return Result("heartbeat", OK, parts)


def peer_cert(host: str, port: int) -> dict[str, Any]:
    ctx = ssl.create_default_context()
    with (socket.create_connection((host, port), timeout=TIMEOUT_S) as sock,
          ctx.wrap_socket(sock, server_hostname=host) as tls):
        return tls.getpeercert() or {}


def check_tls(url: str, warn_days: int, now: float | None = None) -> Result:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return Result("tls", UNKNOWN, f"not an https URL: {url}")
    try:
        cert = peer_cert(parts.hostname, parts.port or 443)
    except ssl.SSLCertVerificationError as e:
        return Result("tls", FAIL, f"certificate does not verify: {e.verify_message}")
    except ssl.SSLError as e:
        return Result("tls", FAIL, f"TLS handshake failed: {e.reason or e}")
    except (OSError, TimeoutError) as e:
        return Result("tls", UNKNOWN, f"could not connect to check TLS: {e}")
    not_after = cert.get("notAfter")
    if not not_after:
        return Result("tls", UNKNOWN, "certificate has no notAfter")
    expires = ssl.cert_time_to_seconds(not_after)
    days = (expires - (now if now is not None else time.time())) / 86400
    when = datetime.fromtimestamp(expires, UTC).strftime("%Y-%m-%d")
    if days < 0:
        return Result("tls", FAIL, f"certificate EXPIRED {when}")
    if days < warn_days:
        return Result("tls", FAIL, f"certificate expires in {days:.0f} days ({when})")
    return Result("tls", OK, f"{days:.0f} days left ({when})")


def run_checks(cfg: Config) -> list[Result]:
    healthz, body = fetch_healthz(cfg.url)
    return [
        healthz,
        check_tls(cfg.url, cfg.tls_warn_days),
        check_heartbeat(healthz, body, cfg.heartbeat_max_s),
    ]


# ------------------------------------------------------------------- state

def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, results: list[Result], previous: dict[str, Any]) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    checks = {}
    old = previous.get("checks", {})
    for r in results:
        prior = old.get(r.name, {})
        since = prior.get("since") if prior.get("state") == r.state else now
        checks[r.name] = {"state": r.state, "detail": r.detail, "since": since or now}
    data = {"at": now, "checks": checks}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def changes(results: list[Result], previous: dict[str, Any]) -> list[Result]:
    """Checks whose state differs from the last saved run. With no saved state
    (first run) the baseline is OK, so only a non-OK check is announced."""
    old = previous.get("checks", {})
    return [r for r in results if old.get(r.name, {}).get("state", OK) != r.state]


# ------------------------------------------------------------------- alert

def message(label: str, url: str, changed: list[Result], results: list[Result]) -> str:
    host = urlsplit(url).hostname or url
    bad = [r for r in changed if r.state != OK]
    head = "ATLAS watchdog: " + ("PROBLEM" if bad else "recovered") + f" ({host})"
    lines = [head]
    for r in changed:
        lines.append(f"- {r.name} {r.state}: {r.detail}")
    rest = [r for r in results if r not in changed and r.state != OK]
    for r in rest:
        lines.append(f"- still {r.state}: {r.name}")
    lines.append(f"from {label}, independent of ATLAS")
    return "\n".join(lines)


def post_slack(webhook: str, text: str) -> None:
    data = json.dumps({"text": text}).encode()
    req = urllib.request.Request(webhook, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack webhook answered HTTP {resp.status}")


# -------------------------------------------------------------------- main

def run(cfg: Config) -> int:
    results = run_checks(cfg)
    for r in results:
        log.info("%s %s: %s", r.name, r.state, r.detail)
    healthy = all(r.state == OK for r in results)

    if cfg.webhook is None:
        # The checks still ran and are in the log, but with nowhere to send a
        # transition the state is not advanced: once a webhook is configured,
        # the first run announces whatever is wrong at that moment.
        log.error("%s", cfg.webhook_problem or NO_ROUTE)
        return EXIT_NO_ROUTE

    previous = load_state(cfg.state_path)
    changed = changes(results, previous)
    if changed:
        text = message(cfg.label, cfg.url, changed, results)
        try:
            post_slack(cfg.webhook, text)
        except Exception as e:  # noqa: BLE001 -- any failure means "not delivered"
            log.error("alert NOT delivered, state kept for retry: %s",
                      redact(f"{type(e).__name__}: {e}", cfg.webhook))
            return EXIT_SEND_FAILED
        log.info("alert delivered: %s", ", ".join(f"{r.name}->{r.state}" for r in changed))
    save_state(cfg.state_path, results, previous)
    return EXIT_OK if healthy else EXIT_NOT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--env-file", help=f"default: $CREDENTIALS_DIRECTORY/watchdog.env "
                                       f"or {DEFAULT_ENV_FILE}")
    ap.add_argument("--state", help=f"default: {DEFAULT_STATE}")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)
    try:
        return run(load_config(args.env_file, args.state))
    except Exception:  # a broken watchdog must never exit "fine"
        log.exception("watchdog crashed")
        return EXIT_CRASHED


if __name__ == "__main__":
    sys.exit(main())
