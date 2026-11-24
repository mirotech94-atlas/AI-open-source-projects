"""Operational CLI: python -m atlas.cli <command>"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys

from .boot import boot
from .kernel.audit import verify_chain
from .kernel.memory import consolidate
from .kernel.registry import REGISTRY
from .kernel.state import execute_sql

SCHEMA = pathlib.Path(__file__).resolve().parent.parent / "db" / "schema.sql"


async def init_db() -> None:
    """Apply db/schema.sql.

    The path is resolved from the package, not the process working directory,
    so `python -m atlas.cli init-db` works from anywhere. Reading it goes
    through a thread: this runs inside the event loop.
    """
    if not SCHEMA.is_file():
        raise SystemExit(f"schema not found at {SCHEMA}")
    sql = await asyncio.to_thread(SCHEMA.read_text)
    await execute_sql(sql)
    print(f"schema applied from {SCHEMA}")


async def selftest() -> None:
    await boot()
    from .kernel.executor import watch_only

    rule = "-" * 72
    print(f"\n{rule}\nATLAS selftest\n{rule}")

    mode = "WATCH-ONLY — the executor will refuse every action" if watch_only() \
        else "ARMED — Tier 1 actions will execute unattended"
    print(f"\nmode     : {mode}")

    print(f"\nmodules  : {len(REGISTRY.modules)} loaded, "
          f"{len(REGISTRY.disabled)} disabled")
    for name in sorted(REGISTRY.modules):
        module = REGISTRY.modules[name]
        n_checks = sum(1 for k in REGISTRY.checks if k.startswith(f"{name}."))
        n_actions = sum(1 for k in REGISTRY.actions if k.startswith(f"{name}."))
        print(f"  + {name:<16} {n_checks} check(s), {n_actions} action(s)"
              f"  — {getattr(module, 'title', name)}")
    for name in sorted(REGISTRY.disabled):
        print(f"  - {name:<16} DISABLED: {REGISTRY.disabled[name]}")

    print(f"\nchecks   : {len(REGISTRY.checks)}")
    for key in sorted(REGISTRY.checks):
        check = REGISTRY.checks[key]
        every = check.interval.total_seconds()
        pretty = (f"{every/3600:.0f}h" if every >= 3600
                  else f"{every/60:.0f}m" if every >= 60 else f"{every:.0f}s")
        sigma = ("no baseline" if check.baseline_sigma is None
                 else f"{check.baseline_sigma:g}σ")
        print(f"  {key:<34} every {pretty:<5} "
              f"on fail: {check.severity_on_fail.name:<8} {sigma}")

    print(f"\nactions  : {len(REGISTRY.actions)} in the catalog, "
          f"{len(REGISTRY.decisions) - len(REGISTRY.actions)} refused")
    for name in sorted(REGISTRY.decisions):
        decision = REGISTRY.decisions[name]
        mark = ("auto" if decision.allowed and decision.tier == 1
                else "ask " if decision.allowed else "DENY")
        action = REGISTRY.actions.get(name)
        undo = "" if action is None else (
            " [reversible]" if action.rollback else " [irreversible]")
        print(f"  [{mark}] {name:<34} {decision.reason}{undo}")

    # The one step here that needs Postgres. With no database it cannot be
    # read, which is UNKNOWN -- not OK, and not a traceback that hides the
    # listing above. The exit code still says the run did not pass.
    try:
        ok, detail = await verify_chain()
    except Exception as exc:                            # noqa: BLE001
        from .modules._http import explain
        print(f"\naudit    : UNKNOWN — the audit log could not be read "
              f"({explain(exc)})")
        print(f"{rule}\n")
        raise SystemExit(1) from None
    print(f"\naudit    : {'OK' if ok else 'BROKEN'} — {detail}")
    print(f"{rule}\n")
    if not ok:
        raise SystemExit(1)


async def run_checks_once() -> None:
    await boot()
    from .kernel.scheduler import run_once
    for finding in await run_once():
        print(f"[{finding.severity.name}] {finding.title} — {finding.detail}")


async def connect() -> None:
    """Check one module's credentials, or list every module's status.

        python -m atlas.cli connect            # what is wired, what is not
        python -m atlas.cli connect flycheap   # verify one, and run its checks

    Connecting a service is otherwise a guessing game: you paste a token,
    restart, and read a log. This runs the module's own checks against the
    credential and tells you what it actually saw.
    """
    import importlib
    import os

    from .kernel.contracts import Health

    target = sys.argv[2] if len(sys.argv) > 2 else None
    package = "atlas.modules"
    pkg = importlib.import_module(package)
    import pkgutil

    names = sorted(i.name for i in pkgutil.iter_modules(pkg.__path__)
                   if not i.name.startswith("_"))

    if target is None:
        print("\nmodule            credentials")
        print("-" * 72)
        for name in names:
            try:
                instance = getattr(
                    importlib.import_module(f"{package}.{name}"), "MODULE", None)
            except Exception as exc:                    # noqa: BLE001
                print(f"  {name:<16} import failed: {exc}")
                continue
            if instance is None:
                print(f"  {name:<16} no MODULE object")
                continue
            required = list(getattr(instance, "requires_env", ()) or ())
            # Optional vars do not gate loading -- the module runs and reports
            # UNKNOWN for the checks they unlock. Printing "no credentials
            # needed" for those was the misleading half of this table: a
            # module can be loaded, live, and still blind.
            optional = list(getattr(instance, "optional_env", ()) or ())
            missing = [v for v in required if not os.getenv(v)]
            unset = [v for v in optional if not os.getenv(v)]
            if missing:
                state = f"BLOCKED: set {', '.join(missing)}"
            elif unset:
                state = f"partial: {', '.join(unset)} would unlock more"
            elif required or optional:
                state = f"configured ({', '.join(required + optional)})"
            elif getattr(instance, "setup_note", ""):
                state = instance.setup_note
            else:
                state = "no credentials needed"
            print(f"  {name:<16} {state}")
        print("\nVerify one with:  python -m atlas.cli connect <module>")
        print("Set a value with: bootstrap/set-secret.sh VARIABLE_NAME")
        print("  (it prompts without echoing -- never paste a secret into a "
              "chat)\n")
        return

    if target not in names:
        raise SystemExit(f"no such module: {target}. One of: {', '.join(names)}")

    # The comms module asks "can ATLAS reach the owner", and the answer depends on
    # the channels having been imported. Without this it reported "no
    # channels are registered at all" -- a broken reporting path, when the
    # truth was that this command had not loaded them.
    from .boot import load_channels

    load_channels()

    instance = getattr(importlib.import_module(f"{package}.{target}"), "MODULE", None)
    if instance is None:
        raise SystemExit(f"{target} exports no MODULE object")

    required = list(getattr(instance, "requires_env", ()) or ())
    optional = list(getattr(instance, "optional_env", ()) or ())
    missing = [v for v in required if not os.getenv(v)]
    print(f"\n{target} — {getattr(instance, 'title', target)}")
    for var in required:
        print(f"  {var:<28} {'set' if os.getenv(var) else 'MISSING (required)'}")
    for var in optional:
        print(f"  {var:<28} {'set' if os.getenv(var) else 'unset (optional)'}")
    if missing:
        raise SystemExit(
            f"\n{len(missing)} required credential(s) missing. Set them with "
            f"bootstrap/set-secret.sh, then run this again.\n")
    if not required and not optional:
        print("  (this module needs no credentials)")

    try:
        await instance.setup()
    except Exception as exc:
        raise SystemExit(f"\nsetup failed: {type(exc).__name__}: {exc}\n") from exc

    checks = list(instance.checks())
    print(f"\nrunning {len(checks)} check(s)…\n")
    unknown = 0
    for check in checks:
        try:
            obs = await asyncio.wait_for(
                check.run(), timeout=check.timeout.total_seconds())
        except Exception as exc:                        # noqa: BLE001
            print(f"  [UNKNOWN ] {check.name}: raised {type(exc).__name__}: {exc}")
            unknown += 1
            continue
        mark = str(obs.health).upper()
        if obs.health is Health.UNKNOWN:
            unknown += 1
        print(f"  [{mark:<8}] {check.name}: {obs.detail}")

    await instance.teardown()
    print()
    unset = [v for v in optional if not os.getenv(v)]
    if unknown and unset:
        print(f"{unknown} check(s) could not run, and {', '.join(unset)} "
              f"{'is' if len(unset) == 1 else 'are'} unset — that is the "
              f"likely reason.\n"
              f"  bootstrap/set-secret.sh {unset[0]}\n")
    elif unknown:
        # The credential may be fine and the endpoint wrong. Both look the same
        # from outside, so say which question is still open.
        print(f"{unknown} check(s) could not run, and the credentials this "
              f"module needs are all set. So either a credential is being "
              f"rejected or an assumed endpoint is wrong — MODULE_AUDIT.md "
              f"records which endpoints were verified and which were not.\n")
    else:
        print("All checks returned a real measurement.\n")


async def _resolve_homeserver(candidate: str) -> str:
    """Turn whatever was typed into the client API base URL.

    A Matrix *server name* and its *client base URL* are different things:
    `internal.example` is the server name, the API lives at `https://chat.internal.example`, and the
    only correct way to get from one to the other is the well-known lookup.
    Guessing wrong produces a 404 that looks like a broken homeserver.
    """
    import httpx

    candidate = candidate.strip().rstrip("/")
    if not candidate:
        raise SystemExit("no homeserver given")
    # A bare hostname is not a URL. httpx raises UnsupportedProtocol on it.
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        try:
            well_known = await client.get(
                f"{candidate}/.well-known/matrix/client")
            if well_known.status_code == 200:
                base = ((well_known.json().get("m.homeserver") or {})
                        .get("base_url") or "").rstrip("/")
                if base and base != candidate:
                    print(f"  {candidate} delegates to {base}")
                    candidate = base
        except Exception as exc:                        # noqa: BLE001
            # No well-known is normal for a server whose name and API host are
            # the same. The versions check below is what actually decides.
            print(f"  (no delegation found: {type(exc).__name__})")

        try:
            versions = await client.get(f"{candidate}/_matrix/client/versions")
            if versions.status_code != 200:
                raise SystemExit(
                    f"\n{candidate} is not answering the Matrix client API "
                    f"(HTTP {versions.status_code}).\n"
                    "That is usually the server name rather than the API host.\n")
        except SystemExit:
            raise
        except Exception as exc:
            raise SystemExit(f"\nCould not reach {candidate}: {exc}\n") from exc

    return candidate


async def _pick_room_and_write(homeserver: str, token: str, who: str) -> None:
    """List the account's rooms, take a choice, write .env. Never prints the
    token."""

    import httpx

    async with httpx.AsyncClient(timeout=25) as client:
        auth = {"Authorization": f"Bearer {token}"}
        joined = await client.get(
            f"{homeserver}/_matrix/client/v3/joined_rooms", headers=auth)
        if joined.status_code == 401:
            raise SystemExit("\nThat access token was rejected.\n")
        joined.raise_for_status()
        rooms = joined.json().get("joined_rooms", [])

        named: list[tuple[str, str]] = []
        for room in rooms[:60]:
            label = ""
            try:
                state = await client.get(
                    f"{homeserver}/_matrix/client/v3/rooms/{room}/state/m.room.name",
                    headers=auth)
                if state.status_code == 200:
                    label = state.json().get("name", "")
            except httpx.HTTPError:
                label = ""
            named.append((room, label))

    print(f"\nAuthenticated as {who}. Rooms this account is in:\n")
    if not named:
        raise SystemExit(
            "This account is in no rooms. Invite it to one from your own\n"
            "client — a room with just you and it — then run this again.\n")
    for index, (room, label) in enumerate(named, start=1):
        print(f"  {index:>2}. {label or '(unnamed)'}")
        print(f"      {room}")

    # Alerts already go to Slack. Posting them here AS OWNER would put
    # ATLAS's messages under his name in front of colleagues, in rooms that
    # are theirs -- the opposite of "private to me". So the honest default
    # is no alert room at all: Matrix is read-and-draft only, and a reply
    # only ever goes out through an approval. Pick a room only if it is
    # one he shares with nobody.
    choice = input(
        "\nAlert room? Alerts already reach Slack, and posting here would be "
        "under YOUR name\nin a room colleagues read. Leave blank for none "
        "(recommended). [number or blank]: ").strip()
    if not choice or choice in ("0", "none", "n"):
        room_id = ""
        print("  no alert room -- Matrix is read-only plus drafts you approve")
    else:
        try:
            room_id = named[int(choice) - 1][0]
        except (ValueError, IndexError) as exc:
            raise SystemExit("not a valid choice") from exc

    # .env is not visible inside the container: docker compose reads it at
    # start and the running process never sees the file. So this writes to
    # the SERVER's .env over ssh, through envlock.py, the same way every
    # other secret lands. That also means this command is run on the Mac,
    # not inside the container -- it is just HTTPS to chat.internal.example.
    import os as _os
    import subprocess as _subprocess

    host = _atlas_host()
    pairs = (f"MATRIX_HOMESERVER={homeserver}\nMATRIX_USER={who}\n"
             f"MATRIX_TOKEN={token}\nMATRIX_ALERT_ROOM={room_id}\n")
    if host == "local":
        repo = pathlib.Path(__file__).resolve().parent.parent
        env_path = _os.getenv("ATLAS_ENV_PATH") or str(repo / ".env")
        _subprocess.run(  # noqa: ASYNC221 -- a one-shot CLI on the Mac
            [sys.executable, str(repo / "bootstrap" / "envlock.py")],
            input=pairs, text=True, check=True,
            env={**_os.environ, "ATLAS_ENV_PATH": env_path})
        _subprocess.run(  # noqa: ASYNC221
            ["docker", "compose", "-f", "docker-compose.yml",
             "-f", "docker-compose.local.yml", "up", "-d", "api"],
            cwd=_os.getenv("ATLAS_COMPOSE_DIR") or str(repo),
            capture_output=True, check=False)
    else:
        _subprocess.run(  # noqa: ASYNC221
            ["ssh", host, ("cd /opt/atlas && python3 envlock.py && "
                           "docker compose up -d api >/dev/null 2>&1")],
            input=pairs, text=True, check=True)
        env_path = f"{host}:/opt/atlas/.env"

    print(f"\nWritten to {env_path}. The token was not printed.")
    print("Restart and test:")
    print("  docker compose up -d api")
    print("  docker compose exec api python -m atlas.cli test-channels\n")



def _atlas_host() -> str:
    """Where ATLAS's .env lives, by bootstrap/google_login.py default_host()'s
    rule: ATLAS_SSH_HOST if set; else "local" when this machine is not the old
    server (no /opt/atlas) and the checkout runs ATLAS itself
    (docker-compose.local.yml); else the old server, deleted 2026-10-27."""
    explicit = os.getenv("ATLAS_SSH_HOST", "").strip()
    if explicit:
        return explicit
    repo = pathlib.Path(__file__).resolve().parent.parent
    if (not pathlib.Path(os.getenv("ATLAS_OPT_DIR", "/opt/atlas")).exists()
            and (repo / "docker-compose.local.yml").is_file()):
        return "local"
    return "root@atlas.example.com"

MATRIX_SETUP_HELP = """
matrix-setup — wire the Matrix channel and write .env

  --token TOKEN
      Use an access token you already have. Nothing is logged in, no password
      is typed, and ATLAS holds only this token. PREFERRED.

      A token can also be passed in the environment as MATRIX_TOKEN, which
      keeps it out of `ps` output and shell history:
        docker compose exec -e MATRIX_TOKEN="$TOKEN" -it api \\
          python -m atlas.cli matrix-setup

  --admin-token TOKEN --user @atlas:internal.example
      Mint a fresh token for that user through the Synapse admin API. Needs a
      server admin token. No user password is involved.

  --homeserver URL
      Defaults to MATRIX_HOMESERVER from .env, then to prompting. A server
      name is resolved to its client API through .well-known, so `internal.example`
      becomes `https://chat.internal.example` on its own.

  (no flags)
      Interactive password login. Works, but gives ATLAS a session for
      whatever account you name.

Use a dedicated account, not your own. If ATLAS posts as you, its messages
appear under your name and nothing it does is distinguishable from you in the
room history. Create @atlas:<server>, invite it to one room, and give it that.
"""


async def matrix_setup() -> None:
    """Wire the Matrix channel. See --help."""
    import getpass

    import httpx

    args = sys.argv[2:]
    if "--help" in args or "-h" in args:
        print(MATRIX_SETUP_HELP)
        return

    def flag(name: str) -> str | None:
        if name in args:
            index = args.index(name)
            if index + 1 < len(args):
                return args[index + 1]
            raise SystemExit(f"{name} needs a value")
        return None

    # A token on the command line ends up in `ps` and in shell history. The
    # environment is the better channel, so it is accepted there too:
    #   docker compose exec -e MATRIX_TOKEN="$TOKEN" api python -m atlas.cli ...
    supplied_token = flag("--token") or os.getenv("MATRIX_TOKEN") or None
    admin_token = flag("--admin-token")
    user = flag("--user") or os.getenv("MATRIX_USER") or ""

    # The scaffold ships MATRIX_HOMESERVER as the SERVER NAME, which is not the
    # API base URL — resolve whatever we are given rather than trusting it.
    candidate = (flag("--homeserver") or os.getenv("MATRIX_HOMESERVER") or "")
    if not candidate:
        candidate = input("Homeserver (e.g. internal.example): ").strip()
    homeserver = await _resolve_homeserver(candidate)

    # --- a token we were handed -----------------------------------------
    if supplied_token:
        async with httpx.AsyncClient(timeout=15) as client:
            who = await client.get(
                f"{homeserver}/_matrix/client/v3/account/whoami",
                headers={"Authorization": f"Bearer {supplied_token}"})
        if who.status_code == 401:
            raise SystemExit("\nThat access token was rejected.\n")
        who.raise_for_status()
        await _pick_room_and_write(
            homeserver, supplied_token, who.json().get("user_id", user or "?"))
        return

    # --- minted through Synapse admin, no user password -------------------
    if admin_token:
        if not user.startswith("@"):
            raise SystemExit("--admin-token needs --user @name:server")
        async with httpx.AsyncClient(timeout=20) as client:
            minted = await client.post(
                f"{homeserver}/_synapse/admin/v1/users/{user}/login",
                headers={"Authorization": f"Bearer {admin_token}"}, json={})
        if minted.status_code in (401, 403):
            raise SystemExit("\nThe admin token was rejected, or it is not a "
                             "server admin.\n")
        if minted.status_code != 200:
            raise SystemExit(f"\nCould not mint a token: HTTP "
                             f"{minted.status_code} {minted.text[:140]}\n")
        await _pick_room_and_write(
            homeserver, minted.json().get("access_token", ""), user)
        return

    # --- last resort: password login -------------------------------------
    print("\nThis logs in with a password and holds a session for that account.")
    print("the owner's decision, 2026-09-22: ATLAS reads as HIS account, @owner:internal.example.")
    print("It never sends on its own -- a reply becomes a draft he approves in")
    print("Slack, and only then goes out, under his name, because ATLAS is")
    print("private to him. Read receipts are his, so \"not seen by me\" is")
    print("literally his. The password is typed here, sent to chat.internal.example once,")
    print("and never stored; ATLAS keeps only the session token.\n")

    user = input("Matrix user [@owner:internal.example]: ").strip() or user or "@owner:internal.example"
    if not user:
        raise SystemExit("no user given")
    # A paste into a getpass prompt on macOS Terminal can arrive as an empty
    # line followed by the value -- the first read comes back "" and the
    # old code exited on it, after he had already pasted. Ask up to three
    # times, and strip the whitespace a paste drags in.
    password = ""
    for attempt in range(3):
        password = getpass.getpass(
            "Password (typed or pasted; nothing will show): " if attempt == 0
            else "Nothing came through -- paste again: ").strip()
        if password:
            break
    if not password:
        raise SystemExit("no password given after three tries")

    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            f"{homeserver}/_matrix/client/v3/login",
            json={"type": "m.login.password",
                  "identifier": {"type": "m.id.user",
                                 "user": user.lstrip("@").split(":")[0]},
                  "password": password,
                  # Its own device, so this session can be revoked without
                  # signing the account's other clients out.
                  "initial_device_display_name": "ATLAS"})
    del password
    if response.status_code == 403:
        raise SystemExit("\nThe homeserver rejected those credentials.\n")
    if response.status_code != 200:
        raise SystemExit(f"\nLogin failed: HTTP {response.status_code} "
                         f"{response.text[:160]}\n")
    body = response.json()
    await _pick_room_and_write(
        homeserver, body.get("access_token", ""), body.get("user_id", user))


async def test_channels() -> None:
    """Send one message through whichever channel is reachable.

        python -m atlas.cli test-channels

    Reports which channels are available and which one actually took the
    message — routing takes the first available channel for the severity,
    so "it sent" is not the same as "it sent where you expected".
    """
    await boot()
    from .kernel.comms import CHANNELS, Outbound, notify
    from .kernel.contracts import Severity

    if not CHANNELS:
        raise SystemExit("No channels are registered at all.")

    print("\nchannel      reachable")
    print("-" * 32)
    for name in sorted(CHANNELS):
        try:
            ok = await CHANNELS[name].available()
        except Exception as exc:                        # noqa: BLE001
            print(f"  {name:<10} error: {exc}")
            continue
        print(f"  {name:<10} {'yes' if ok else 'no'}")

    used = await notify(Outbound(
        title="ATLAS test message",
        body="If you are reading this, ATLAS can reach you. "
             "Nothing is wrong — this was sent by `test-channels`.",
        severity=Severity.NORMAL,
    ))
    print(f"\ndelivered via: {', '.join(used) or 'NOTHING — no channel took it'}\n")


async def warm_agents() -> None:
    """Give every roster agent one real, tool-free run.

        python -m atlas.cli warm-agents

    Fills in the Agents map's LAST RUN and MODEL with the truth, one row per
    specialist, instead of waiting for the owner to happen to ask each one a real
    question. Sequential, on purpose -- this is an operator running it by
    hand, not a stampede of concurrent LLM calls. Never fabricates a result:
    an agent whose model could not be reached prints why, not a made-up
    model name.
    """
    await boot()
    from .kernel.agents import ROSTER, warmup

    for name in sorted(ROSTER):
        result = await warmup(name)
        if result.model:
            print(f"{name:<16} {result.provider}:{result.model} "
                  f"({result.seconds:.1f}s)")
        else:
            print(f"{name:<16} FAILED ({result.seconds:.1f}s) — {result.outcome}")


async def morning_brief() -> None:
    """Build and print the brief without sending it."""
    await boot()
    from .kernel.brief import build
    print((await build()).render())


async def send_brief() -> None:
    await boot()
    from .kernel.brief import send
    brief = await send(force=True)      # asked for by hand: always send
    print(brief.render())


async def set_password() -> None:
    """Hash a dashboard password. The plaintext is never stored or echoed."""
    import getpass

    from .api.auth import hash_password

    first = getpass.getpass("New dashboard password: ")
    if len(first) < 12:
        raise SystemExit("too short — use at least 12 characters")
    if first != getpass.getpass("Again: "):
        raise SystemExit("they did not match")
    print("\nAdd this line to your .env, then restart the API:\n")
    print(f"DASHBOARD_PASSWORD_HASH={hash_password(first)}\n")


async def model() -> None:
    """The learning layer from the box: status, tournament, rollback, backfill.

        atlas model status      who serves, the reliability line, the log
        atlas model tournament  run tonight's bake-off now
        atlas model rollback    restore the previous serving model
        atlas model backfill    log proposals that predate the decision log
    """
    from .kernel.judgment import nightly, registry, status
    from .kernel.judgment.log import backfill

    sub = sys.argv[2] if len(sys.argv) > 2 else "status"
    if sub == "tournament":
        await boot()
        for line in await nightly.run_all(force=True) or ["(nothing changed)"]:
            print(line)
        sub = "status"
    elif sub == "rollback":
        got = await registry.rollback("approve")
        print(f"serving: {got.head} (#{got.id}, Brier {got.brier:.3f})" if got
              else "nothing to roll back to")
        return
    elif sub == "backfill":
        print(f"backfilled {await backfill()} proposal(s)")
        return
    if sub != "status":
        raise SystemExit("atlas model [status|tournament|rollback|backfill]")
    snap = await status.snapshot()
    print(snap["line"])
    for t, srv in snap["serving"].items():
        print(f"{t:8} {srv['head']} since {srv['since'][:16]} Brier {srv['brier']}"
              if srv else f"{t:8} nobody (abstaining)")
    lg = snap["log"]
    print(f"log: {lg.get('total', 0)} rows — {lg.get('approved', 0)} approved, "
          f"{lg.get('denied', 0)} denied, {lg.get('expired', 0)} expired, "
          f"{lg.get('open', 0)} open")
    for t in snap["tournaments"][:10]:
        print(f"[{t['at'][:10]}] {t['line']}")


async def import_reel() -> None:
    """File a finished episode and post it everywhere (a card if a check fails).

        python -m atlas.cli import-reel data/import/<file>.mp4 "<caption>" \\
            [--series "<title>" --part N --of M]

    The caption must end with the AI label. Exits 1 with the reason when the
    file is refused; prints where it posted, or the card number when held.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="atlas.cli import-reel")
    ap.add_argument("path")
    ap.add_argument("caption")
    ap.add_argument("--series")
    ap.add_argument("--part", type=int)
    ap.add_argument("--of", dest="total", type=int)
    a = ap.parse_args(sys.argv[2:])
    await boot()
    from .modules.content.importer import import_reel as run

    done = await run(a.path, a.caption, series=a.series, part=a.part,
                     total_parts=a.total)
    print(("OK " if done.ok else "REFUSED ") + done.detail)
    if not done.ok:
        sys.exit(1)


async def pause_cmd() -> None:
    """The kill switch from the server.

        python -m atlas.cli pause <module|all> [reason ...]
        python -m atlas.cli resume <module|all>
        python -m atlas.cli pauses
    """
    await boot()
    from .kernel import pause
    verb = sys.argv[1]
    if verb == "pauses":
        print(await pause.command("pauses", "cli"))
        return
    if len(sys.argv) < 3:
        print(f"usage: python -m atlas.cli {verb} <module|all> [reason]")
        sys.exit(1)
    scope, why = sys.argv[2], " ".join(sys.argv[3:])
    text = f"{verb} {scope}" + (f" because {why}" if why and verb == "pause" else "")
    print(await pause.command(text, "cli"))


async def shadow_cmd() -> None:
    """Shadow mode (RA-G1, docs/shadow-mode.md) from the server only.

        python -m atlas.cli shadow <module|all> [reason ...]
        python -m atlas.cli unshadow <module|all>
    """
    await boot()
    from .kernel import shadow
    verb = sys.argv[1]
    if len(sys.argv) < 3:
        print(f"usage: python -m atlas.cli {verb} <module|all>"
              + (" [reason]" if verb == "shadow" else ""))
        sys.exit(1)
    target = sys.argv[2]
    if verb == "shadow":
        print(await shadow.shadow(target, "cli", " ".join(sys.argv[3:])))
    else:
        print(await shadow.unshadow(target, "cli"))


async def shadow_report_cmd() -> None:
    """Read-only: what shadowed modules would have run, against what ran.

        python -m atlas.cli shadow-report --since 48h
        python -m atlas.cli shadow-report --since 2026-09-29T09:00Z
    Exits 1 when a run started inside a shadow window (the rollout blocker).
    """
    from .kernel import shadow
    args = sys.argv[2:]
    since_text = "48h"
    if args[:1] == ["--since"] and len(args) >= 2:
        since_text = args[1]
    elif args:
        print("usage: python -m atlas.cli shadow-report [--since 48h|<ISO time>]")
        sys.exit(1)
    try:
        since = shadow.parse_since(since_text)
    except ValueError:
        print(f"--since {since_text!r}: use 48h, 2d, 90m or an ISO timestamp")
        sys.exit(1)
    rep = await shadow.report(since)
    print(shadow.render(rep))
    if rep.unauthorized:
        sys.exit(1)


async def restore_fence_cmd() -> None:
    """Right after restoring the database from a backup (R12,
    docs/restore-drill.md): record the restore and fence it, so nothing the
    dump held as queued or pending is sent or run again blindly.

        python -m atlas.cli restore-fence <dump file name>
    """
    await boot()
    from .kernel import restore
    if len(sys.argv) < 3:
        print("usage: python -m atlas.cli restore-fence <dump file name>")
        sys.exit(1)
    await restore.mark(sys.argv[2], "cli")
    got = await restore.apply()
    print(f"restore fenced: {len(got.outbox)} queued send(s) to reconcile, "
          f"{len(got.cards)} card(s) held")


_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def parse_sites_add(args: list[str]) -> tuple[str, str, str, str]:
    """(slug, hostname, kind, source) from `sites-add` arguments, or
    ValueError naming what is wrong. Only 'demo' and 'app' rows are added
    by hand: a 'built' row comes from `agency.build_site`."""
    from .modules.agency import hosting
    if len(args) != 4:
        raise ValueError("usage: python -m atlas.cli sites-add <slug> <hostname> "
                         "<demo|app> <source>")
    slug, host, kind, source = args[0], args[1].strip().lower().rstrip("."), \
        args[2], args[3].strip()
    problem = hosting.slug_problem(slug)
    if problem:
        raise ValueError(problem)
    if kind not in ("demo", "app"):
        raise ValueError(f"kind {kind!r}: only 'demo' or 'app' (a built site's row "
                         "comes from agency.build_site)")
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_HOST_LABEL.fullmatch(x)
                                                     for x in labels) \
            or labels[-1].isdigit():
        raise ValueError(f"hostname {host[:80]!r} is not a DNS name")
    if not source or len(source) > 200 or any(c in source for c in "\r\n\0"):
        raise ValueError("source: one line, at most 200 characters "
                         "(e.g. repo@commit)")
    return slug, host, kind, source


async def sites_add_cmd() -> None:
    """Register a client demo or app (operator, on the host). Writes the
    registry row only; nothing is published until the card is approved.

        python -m atlas.cli sites-add examplehair client-site.example.com \\
            demo "royal-hairstyles-site-demo@176c272"
    The site's up/cert check starts after the next api restart.
    """
    from .modules.agency import _store, demo_host
    try:
        slug, host, kind, source = parse_sites_add(sys.argv[2:])
    except ValueError as exc:
        print(exc)
        sys.exit(1)
    path = str(demo_host.INTAKE_ROOT / slug) if kind == "demo" else ""
    added = await _store.add_site_row(slug, host, kind, source, path)
    if added is None:
        print(f"{slug}: a row already exists; nothing changed")
        sys.exit(1)
    print(f"added {kind} {slug} at {host} (row {added}); its check starts after "
          "the next api restart")


def parse_sites_doc(args: list[str]) -> tuple[str, str, str, str, str]:
    """`<slug> <kind> <title> <body-file> [client-text-file]` -> the
    agency.draft_doc arguments. Raises ValueError with the usage line."""
    usage = ("usage: sites-doc <slug> <brief|handoff|report|proposal|client_guide> "
             "<title> <body-file> [client-text-file]")
    if len(args) not in (4, 5):
        raise ValueError(usage)
    slug, kind, title, body_file = args[:4]
    try:
        body = pathlib.Path(body_file).read_text(encoding="utf-8")
        client = (pathlib.Path(args[4]).read_text(encoding="utf-8")
                  if len(args) == 5 else "")
    except OSError as exc:
        raise ValueError(f"{exc}\n{usage}") from exc
    return slug, kind, title, body, client


async def sites_doc_cmd() -> None:
    """Draft a client-site document and raise its Tier ASK publish card
    (operator, on the host). The same store-and-propose path the sites
    agent's sites_draft_doc tool takes; nothing is published here -- the
    approved card hands it to the coding agent, which makes the Claude Docs
    artifact.

        python -m atlas.cli sites-doc examplehair brief \\
            "examplehair site brief" brief.md client-email.txt
    """
    from .modules.agency import sitedocs
    try:
        slug, kind, title, body, client = parse_sites_doc(sys.argv[2:])
    except ValueError as exc:
        print(exc)
        sys.exit(1)
    await boot()          # the policy decisions a card is checked against
    result = await sitedocs.draft_doc(slug, kind, title, body, client)
    print(result.detail)
    if not result.ok:
        sys.exit(1)


async def shop_build() -> None:
    """Build the ATLAS Academy storefront into `<build root>/<stamp>`
    (ACADEMY_BUILD_DIR, default data/academy/build), keep it, and point
    `<build root>/current` at it. Nothing is published: it never writes
    under AGENCY_PUBLISH_DIR. The local `shop` service serves `current`;
    academy.publish_shop_remote (Tier 2) pushes it to the store VM.

        python -m atlas.cli shop-build
    """
    from datetime import UTC, datetime

    from .modules.academy import publish
    from .modules.agency import hosting
    build = publish.build_root()
    why = hosting.root_problem(hosting.PUBLISH_ROOT, build)
    if why is not None:
        print(f"REFUSED: {why}")
        sys.exit(1)
    reviews = await publish._approved_reviews()
    videos = await publish.public_videos()
    lessons = await publish.published_lessons()
    read_at = datetime.now(UTC)
    if videos is None:
        print(f"REFUSED: {publish.VIDEOS_UNREADABLE}")
        sys.exit(1)
    stamp = publish._stamp()
    try:
        out = await asyncio.to_thread(publish._build, stamp, reviews, videos, lessons)
        await asyncio.to_thread(publish.point_current, build, stamp)
    except Exception as exc:                            # noqa: BLE001
        print(f"REFUSED: build failed: {type(exc).__name__}: {str(exc)[:200]}")
        sys.exit(1)
    files = await asyncio.to_thread(lambda: sum(1 for p in out.rglob("*")
                                                if p.is_file()))
    print(f"built {files} files at {out.resolve()} ({build / 'current'} -> {stamp}); "
          f"{len(videos)} public video(s), {len(lessons)} lesson(s), "
          f"{len(reviews)} review(s), DB read at "
          f"{read_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")


async def rebuild_tutorials() -> None:
    """Rebuild academy_tutorial_posts / _comments rows the restored database
    lacks from what YouTube shows (read-only). --dry-run inserts nothing.

        python -m atlas.cli rebuild-tutorials --dry-run
        python -m atlas.cli rebuild-tutorials --apply
    """
    args = sys.argv[2:]
    if sorted(args) not in (["--dry-run"], ["--apply"]):
        print("usage: python -m atlas.cli rebuild-tutorials --dry-run|--apply")
        sys.exit(1)
    from .modules._http import install_log_redaction
    from .modules.academy import rebuild
    install_log_redaction()
    report = await rebuild.run(apply=args == ["--apply"])
    for line in report.lines:
        print(line)
    if not report.ok:
        sys.exit(1)


async def channel_study() -> None:
    """Read-only study of one YouTube channel, written to `reports`.

        python -m atlas.cli channel-study --channel clips|ecc
    """
    import argparse

    ap = argparse.ArgumentParser(prog="atlas.cli channel-study")
    ap.add_argument("--channel", required=True, choices=("clips", "ecc"))
    a = ap.parse_args(sys.argv[2:])
    await boot()
    from .modules._http import install_log_redaction
    from .modules.youtube import study
    install_log_redaction()
    rid, text = await study.run(a.channel)
    print(text)
    print(f"report {rid}" if rid is not None else "no report written")


async def research_cmd() -> None:
    """Seed the academy competitor list from a saved YouTube search export.
    The file is opened read-only and never written; a second run adds nothing.

        python -m atlas.cli research seed --file .claude/competitors-2026-10-10.json
    """
    import argparse

    ap = argparse.ArgumentParser(prog="atlas.cli research")
    ap.add_argument("sub", choices=("seed",))
    ap.add_argument("--file", required=True)
    a = ap.parse_args(sys.argv[2:])
    await boot()
    from .modules.research import seed
    out = await seed.import_seed(a.file)
    print(f"added {out['added']}, already listed {out['skipped']}")


async def cameras_forget() -> None:
    """Delete stored household face data. the owner's own command: not an action, so
    no model can run it.

        python -m atlas.cli cameras-forget all
        python -m atlas.cli cameras-forget person <slot 1-5>
    """
    args = sys.argv[2:]
    if args[:1] == ["all"] and len(args) == 1:
        scope, slot = "all", None
    elif args[:1] == ["person"] and len(args) == 2 and args[1] in "12345" and len(args[1]) == 1:
        scope, slot = "slot", int(args[1])
    else:
        print("usage: python -m atlas.cli cameras-forget all | person <1-5>")
        sys.exit(1)
    await boot()
    from .modules.cameras import people
    removed = await people.REPO.forget(scope, slot)
    print(f"deleted {removed} face group(s) and their stored vectors"
          + (f" for person slot {slot}" if slot else ""))


async def memory_wipe_cmd() -> None:
    """Hard-delete remembered notes. Typed by hand only; never reachable from
    chat, the dashboard or a model.

        python -m atlas.cli memory-wipe --hidden          # notes already hidden
        python -m atlas.cli memory-wipe --id 12 --id 40
        python -m atlas.cli memory-wipe --everything --yes
    """
    import argparse

    ap = argparse.ArgumentParser(prog="atlas.cli memory-wipe")
    ap.add_argument("--hidden", action="store_true")
    ap.add_argument("--id", type=int, action="append", dest="ids")
    ap.add_argument("--everything", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(sys.argv[2:])
    await boot()
    from .kernel import memory_wipe
    try:
        n = await memory_wipe.wipe(hidden=a.hidden, ids=a.ids,
                                   everything=a.everything, confirmed=a.yes)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    print(f"wiped {n} note(s)")


COMMANDS = {
    "cameras-forget": cameras_forget,
    "memory-wipe": memory_wipe_cmd,
    "channel-study": channel_study,
    "research": research_cmd,
    "init-db": init_db,
    "shop-build": shop_build,
    "rebuild-tutorials": rebuild_tutorials,
    "sites-add": sites_add_cmd,
    "sites-doc": sites_doc_cmd,
    "model": model,
    "selftest": selftest,
    "check": run_checks_once,
    "brief": morning_brief,
    "warm-agents": warm_agents,
    "send-brief": send_brief,
    "consolidate-memory": consolidate,
    "set-password": set_password,
    "connect": connect,
    "matrix-setup": matrix_setup,
    "test-channels": test_channels,
    "import-reel": import_reel,
    "pause": pause_cmd,
    "resume": pause_cmd,
    "pauses": pause_cmd,
    "shadow": shadow_cmd,
    "unshadow": shadow_cmd,
    "shadow-report": shadow_report_cmd,
    "restore-fence": restore_fence_cmd,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    fn = COMMANDS.get(cmd)
    if fn is None:
        print(f"commands: {', '.join(COMMANDS)}")
        sys.exit(1)
    asyncio.run(fn())
