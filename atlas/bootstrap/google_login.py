"""The OAuth consent half of connecting Gmail and Calendar.

Run through bootstrap/google-login.sh, which explains the console steps.

No third-party OAuth library: the loopback flow is three HTTP calls and a
one-request web server, and a dependency here would be a dependency the API
container carries for something only ever run on a laptop.

The scopes are the narrowest that cover what the modules actually do, which
was read out of them rather than guessed:

  gmail.modify     /threads and /messages read, /drafts create and delete.
                   NOT mail.google.com, which would also permit permanent
                   deletion -- deletion is Tier 3 in ATLAS and the token
                   should not be able to do what the tier forbids.
  calendar.events  events list, insert and delete on the primary calendar.
                   Not `calendar`, which would also grant calendar creation
                   and sharing.
  webmasters.readonly
                   Search Console reads for the SEO agent (analytics, sitemaps,
                   URL inspection). Read only: not `webmasters`, which could
                   add or remove properties and sitemaps. On by default;
                   ATLAS_GOOGLE_SEARCH_CONSOLE=0 leaves it out.

`--reconsent` (no client file) re-runs the consent with the client ATLAS
already has (GOOGLE_CLIENT_ID/SECRET from its .env): the way to add a scope
to a refresh token granted before it existed.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# python.org builds on macOS ship no CA bundle, so every https call fails with
# CERTIFICATE_VERIFY_FAILED; the system bundle is always there.
if not os.environ.get("SSL_CERT_FILE") and os.path.exists("/etc/ssl/cert.pem"):
    os.environ["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"

SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


# Registering a site (add property, verify it, submit a sitemap) needs write
# scopes. They are OFF by default and asked for only with
# ATLAS_GOOGLE_SEARCH_CONSOLE_WRITE=1; the seo.gsc_* actions that use them are
# Tier 2 (a card Miro approves) and never touch DNS.
WRITE_SCOPES = ("https://www.googleapis.com/auth/webmasters",
                "https://www.googleapis.com/auth/siteverification")


def default_scopes(search_console: bool = True, write: bool = False) -> str:
    """The Gmail + Calendar consent, plus Search Console read unless off, plus
    the two registration scopes only when `write` is asked for."""
    scopes = ["https://www.googleapis.com/auth/gmail.modify",
              "https://www.googleapis.com/auth/calendar.events",
              "https://www.googleapis.com/auth/userinfo.email"]
    if search_console:
        scopes.append(SEARCH_CONSOLE_SCOPE)
        if write:
            scopes.extend(WRITE_SCOPES)
    return " ".join(scopes)


SEARCH_CONSOLE = os.getenv("ATLAS_GOOGLE_SEARCH_CONSOLE", "1").strip().lower() not in {
    "0", "off", "false", "no"}
SEARCH_CONSOLE_WRITE = os.getenv(
    "ATLAS_GOOGLE_SEARCH_CONSOLE_WRITE", "0").strip().lower() in {
    "1", "on", "true", "yes"}
SCOPES = default_scopes(SEARCH_CONSOLE, SEARCH_CONSOLE_WRITE)
RECONSENT = "--reconsent"
# ATLAS_GOOGLE_YOUTUBE=CLIPS (a channel key from youtube/api.py CHANNELS)
# authorises a YouTube channel instead: `youtube.upload` for the upload and
# `youtube` for playlists, thumbnails and privacy -- the Academy tutorials
# need all three (Miro, 2026-10-09). `youtube.force-ssl` since 2026-10-13:
# the pinned quiz + lab comment and caption uploads (the Academy growth
# plan) need it, and `yt-analytics.readonly` (final review, 2026-10-13) for
# the growth log's analytics read. Written as YOUTUBE_<KEY>_* so the
# Gmail/Calendar token is never overwritten by a channel's.
YOUTUBE_KEY = os.getenv("ATLAS_GOOGLE_YOUTUBE", "").strip().upper()
if YOUTUBE_KEY:
    SCOPES = ("https://www.googleapis.com/auth/youtube.upload "
              "https://www.googleapis.com/auth/youtube "
              "https://www.googleapis.com/auth/youtube.force-ssl "
              "https://www.googleapis.com/auth/yt-analytics.readonly "
              "https://www.googleapis.com/auth/userinfo.email")
# ATLAS_GOOGLE_MAILBOX=CLIPS connects one more Gmail mailbox for ATLAS to
# read (mail/accounts.py): `gmail.modify` and the address only, no Calendar.
# Mail accounts refresh with the SHARED client (GOOGLE_CLIENT_ID/SECRET), so
# that pair is read from the server when none is given, and the result is
# written as MAIL_<KEY>_KIND/EMAIL/REFRESH_TOKEN with <key> added to
# MAIL_ACCOUNTS -- never over the GOOGLE_* token. Public mail is
# owner@example.com; the "clips" mailbox the Academy support reader reads.
MAILBOX_KEY = os.getenv("ATLAS_GOOGLE_MAILBOX", "").strip().upper()
if MAILBOX_KEY:
    SCOPES = ("https://www.googleapis.com/auth/gmail.modify "
              "https://www.googleapis.com/auth/userinfo.email")
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"


# A FIXED port, not a random one.
#
# A Desktop-app client accepts any loopback port, so a random one is fine for
# those. A Web-application client does not: Google matches the redirect URI
# exactly, including the port, against the list registered in the console.
# A random port can never match, and the failure is `redirect_uri_mismatch`
# after you have already consented.
#
# So: one port, printed, and registered once. This works for both client
# types, which means nobody has to know which one they made.
PORT = int(os.getenv("ATLAS_OAUTH_PORT", "8765"))
REDIRECT = f"http://localhost:{PORT}/"


def _port_free() -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", PORT))
            return True
        except OSError:
            return False


def _client(path: str) -> tuple[str, str]:
    """The id and secret, from a downloaded JSON or given directly.

    Google shows both on screen once and the secret can never be viewed
    again, so people copy them rather than download the file. Accepting the
    pair directly saves a round trip back to a dialog that is already closed.
    """
    if not path.endswith(".json") and path.endswith(".apps.googleusercontent.com"):
        secret = sys.argv[2] if len(sys.argv) > 2 else ""
        if not secret:
            sys.exit("give the client secret as the second argument")
        return path, secret

    with open(path) as fh:
        blob = json.load(fh)
    section = blob.get("installed") or blob.get("web")
    if not section:
        sys.exit("that JSON has neither an 'installed' nor a 'web' client in "
                 "it — download the OAuth client, not the service account key")
    if "installed" not in blob:
        print(f"This is a Web application client, so the redirect URI must "
              f"be registered exactly. Check {REDIRECT} is in the console's "
              f"'Authorized redirect URIs' list before continuing.",
              file=sys.stderr)
    return section["client_id"], section["client_secret"]


KEY_RE = __import__("re").compile(r"^[A-Z][A-Z0-9_]{0,30}$")


def merged_accounts(current: str, key: str) -> str:
    """MAIL_ACCOUNTS with `key` (lower case) added once, order kept."""
    names = [n.strip().lower() for n in current.split(",") if n.strip()]
    if key.lower() not in names:
        names.append(key.lower())
    return ",".join(names)


def mailbox_lines(key: str, email: str, refresh: str, current_accounts: str) -> list[str]:
    """What the mailbox mode writes: the account's three values, and the
    whole MAIL_ACCOUNTS list (envlock sets values; it cannot append)."""
    pre = f"MAIL_{key.upper()}_"
    return [f"{pre}KIND=google", f"{pre}EMAIL={email}", f"{pre}REFRESH_TOKEN={refresh}",
            f"MAIL_ACCOUNTS={merged_accounts(current_accounts, key)}"]


REPO = pathlib.Path(__file__).resolve().parent.parent
ENVLOCK = REPO / "bootstrap" / "envlock.py"
REMOTE = "root@atlas.example.com"
# Always both files, from the checkout: another directory or a missing -f is
# another compose project, i.e. a second Postgres (as in set-secret.sh).
LOCAL_COMPOSE = ["docker", "compose", "-f", "docker-compose.yml",
                 "-f", "docker-compose.local.yml"]


def default_host(repo: pathlib.Path = REPO,
                 opt: pathlib.Path = pathlib.Path("/opt/atlas")) -> str:
    """ATLAS_SSH_HOST if set; else "local" when this machine is not the old
    server (no /opt/atlas) and the checkout runs ATLAS itself
    (docker-compose.local.yml); else the old server."""
    explicit = os.getenv("ATLAS_SSH_HOST", "").strip()
    if explicit:
        return explicit
    if not opt.exists() and (repo / "docker-compose.local.yml").is_file():
        return "local"
    return REMOTE


def _local_env() -> dict[str, str]:
    return {**os.environ,
            "ATLAS_ENV_PATH": os.getenv("ATLAS_ENV_PATH") or str(REPO / ".env")}


def _compose_dir() -> str:
    return os.getenv("ATLAS_COMPOSE_DIR") or str(REPO)


def _server_value(host: str, name: str) -> str:
    """One value from ATLAS's .env, through envlock (never printed)."""
    if host == "local":
        r = subprocess.run([sys.executable, str(ENVLOCK), name], env=_local_env(),
                           capture_output=True, text=True, check=False)
    else:
        r = subprocess.run(["ssh", host,
                            f"cd /opt/atlas && python3 bootstrap/envlock.py {name}"],
                           capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def write_values(host: str, lines: list[str]) -> None:
    """NAME=value lines into .env over envlock's stdin, then restart the api.

    Values go over stdin only: never in argv, `ps` or a history. envlock
    prints names and lengths, nothing else.
    """
    data = "\n".join(lines) + "\n"
    if host == "local":
        subprocess.run([sys.executable, str(ENVLOCK)], input=data, text=True,
                       env=_local_env(), check=True)
        r = subprocess.run([*LOCAL_COMPOSE, "up", "-d", "api"], cwd=_compose_dir(),
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            print("docker compose up failed; is OrbStack running? The values "
                  "are saved in .env.", file=sys.stderr)
        return
    # Straight into the server's own envlock, over stdin. The old path
    # copied setenv.py to /opt/atlas, but that shim now looks for envlock.py
    # beside itself and found none -- and failed AFTER consent, losing a
    # one-time token (2026-10-10). envlock.py is always at bootstrap/.
    subprocess.run(
        ["ssh", host, ("cd /opt/atlas && python3 bootstrap/envlock.py && "
                       "docker compose up -d api >/dev/null 2>&1")],
        input=data, text=True, check=True)


def run_check(host: str, module: str) -> None:
    """`atlas.cli connect <module>` in the api container."""
    if host == "local":
        subprocess.run([*LOCAL_COMPOSE, "exec", "-T", "api", "python", "-m",
                        "atlas.cli", "connect", module], cwd=_compose_dir(), check=False)
        return
    subprocess.run(
        ["ssh", host, (f"cd /opt/atlas && docker compose exec -T api "
                       f"python -m atlas.cli connect {module}")], check=False)


def main() -> None:
    host = default_host()
    if MAILBOX_KEY and YOUTUBE_KEY:
        sys.exit("set ATLAS_GOOGLE_MAILBOX or ATLAS_GOOGLE_YOUTUBE, not both")
    if MAILBOX_KEY and not KEY_RE.match(MAILBOX_KEY):
        sys.exit("ATLAS_GOOGLE_MAILBOX must be a short name such as CLIPS")
    if MAILBOX_KEY and len(sys.argv) < 2:
        # The shared client, as the server has it: a mailbox refreshed with
        # any other client would fail on the server.
        client_id = _server_value(host, "GOOGLE_CLIENT_ID")
        client_secret = _server_value(host, "GOOGLE_CLIENT_SECRET")
        if not (client_id and client_secret):
            sys.exit("could not read GOOGLE_CLIENT_ID/SECRET from the server; pass "
                     "the shared client as arguments instead")
    elif YOUTUBE_KEY and len(sys.argv) < 2:
        # Re-consent for a channel already connected (2026-10-13: a new scope):
        # its own uploader client, as the server has it.
        client_id = _server_value(host, f"YOUTUBE_{YOUTUBE_KEY}_CLIENT_ID")
        client_secret = _server_value(host, f"YOUTUBE_{YOUTUBE_KEY}_CLIENT_SECRET")
        if not (client_id and client_secret):
            sys.exit(f"could not read YOUTUBE_{YOUTUBE_KEY}_CLIENT_ID/SECRET from the "
                     "server; pass the channel's client as arguments instead")
    elif len(sys.argv) >= 2 and sys.argv[1] == RECONSENT:
        # Re-consent with the client ATLAS already has: the way an old
        # refresh token gains a scope (webmasters.readonly, 2026-10-29).
        client_id = _server_value(host, "GOOGLE_CLIENT_ID")
        client_secret = _server_value(host, "GOOGLE_CLIENT_SECRET")
        if not (client_id and client_secret):
            sys.exit("could not read GOOGLE_CLIENT_ID/SECRET from ATLAS's .env; pass "
                     "the client JSON instead: ./bootstrap/google-login.sh "
                     "<client_secret_….json>")
    elif len(sys.argv) < 2:
        sys.exit("usage: run this through bootstrap/google-login.sh, which "
                 "explains the console steps first")
    else:
        client_id, client_secret = _client(sys.argv[1])
    # Refuse before consent, not after: a refresh token is shown once.
    if host == "local" and not pathlib.Path(_local_env()["ATLAS_ENV_PATH"]).is_file():
        sys.exit(f"{_local_env()['ATLAS_ENV_PATH']} does not exist; create it from "
                 ".env.example (chmod 600) first")
    if not _port_free():
        sys.exit(f"port {PORT} is already in use. Free it, or set "
                 f"ATLAS_OAUTH_PORT to another port and register that "
                 f"redirect URI instead.")
    port, redirect = PORT, REDIRECT
    print(f"\nIf your OAuth client is a *Web application*, this exact URI "
          f"must be listed\nunder 'Authorized redirect URIs' in the console:"
          f"\n\n    {REDIRECT}\n\nA Desktop app client needs no redirect "
          f"URI at all.\n")
    state = secrets.token_urlsafe(24)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            # The state check is the point of state: without it, any page
            # could drive this loopback server while it is listening.
            if params.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch")
                return
            got["code"] = params.get("code", [""])[0]
            got["error"] = params.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font:16px system-ui;padding:40px'>"
                b"<p>Done. You can close this tab and go back to the "
                b"terminal.</p></body></html>")

        def log_message(self, *args) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # Both are required to get a refresh token at all: without
        # access_type=offline Google returns only an access token, and
        # without prompt=consent it returns none on a re-authorisation
        # because you have already approved these scopes once.
        "access_type": "offline",
        "prompt": "consent select_account",
    })
    what = ("YouTube for the channel you pick" if YOUTUBE_KEY else
            f"Gmail for the {MAILBOX_KEY.lower()} mailbox (sign in as that account)"
            if MAILBOX_KEY else "Gmail, Calendar and Search Console (read only)"
            if SEARCH_CONSOLE else "both Gmail and Calendar")
    print(f"Opening Google's consent page. Approve {what}.\n")
    print(f"If nothing opens, paste this into a browser:\n{url}\n")
    webbrowser.open(url)

    server.serve_forever_timeout = 300
    for _ in range(300):
        if got:
            break
        threading.Event().wait(1)
    server.server_close()

    if got.get("error"):
        sys.exit(f"Google refused: {got['error']}")
    if not got.get("code"):
        sys.exit("no consent came back within five minutes")

    body = urllib.parse.urlencode({
        "code": got["code"], "client_id": client_id,
        "client_secret": client_secret, "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(TOKEN, data=body), timeout=30) as r:
            tokens = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e)
        except Exception:  # noqa: BLE001
            detail = {}
        why = detail.get("error", f"HTTP {e.code}")
        hint = {
            "invalid_client": "the client id or secret is wrong (a "
                "placeholder, a typo, or a rotated secret). Copy the current "
                "secret from the console, or pass the downloaded client JSON.",
            "redirect_uri_mismatch": f"add {REDIRECT} to Authorized redirect "
                "URIs (Web application clients only).",
            "invalid_grant": "the consent code expired or was used; run this "
                "again.",
        }.get(why, detail.get("error_description", ""))
        sys.exit(f"Google refused the token exchange: {why}. {hint}\n"
                 f"(secret given: {len(client_secret)} chars, starts "
                 f"'{client_secret[:6]}'; nothing was written)")

    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit("Google returned no refresh token. That happens when these "
                 "scopes were already approved — revoke ATLAS at "
                 "https://myaccount.google.com/permissions and run this again.")

    # Whose mailbox is this? The modules use it to tell "he replied last"
    # from "still owed a reply", and getting it wrong makes every thread
    # look unanswered.
    email = ""
    try:
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            email = json.load(r).get("email", "")
    except Exception:  # noqa: BLE001
        email = ""                                      # said as unknown below

    print(f"\nAuthorised{' as ' + email if email else ''}.")
    print(f"Writing to {'the local .env' if host == 'local' else host} "
          "(nothing is printed back):\n")

    lines = [f"GOOGLE_CLIENT_ID={client_id}",
             f"GOOGLE_CLIENT_SECRET={client_secret}",
             f"GOOGLE_REFRESH_TOKEN={refresh}"]
    if MAILBOX_KEY:
        # The account's own address is required: the unreplied check matches
        # the sender against it (mail/accounts.py), so no email, no write.
        if not email:
            sys.exit("Google did not say which address consented; nothing was "
                     "written. Run this again.")
        current = _server_value(host, "MAIL_ACCOUNTS")
        lines = mailbox_lines(MAILBOX_KEY, email, refresh, current)
    elif YOUTUBE_KEY:
        pre = f"YOUTUBE_{YOUTUBE_KEY}_"
        lines = [f"{pre}CLIENT_ID={client_id}", f"{pre}CLIENT_SECRET={client_secret}",
                 f"{pre}REFRESH_TOKEN={refresh}"]
    elif email:
        lines.append(f"GOOGLE_ACCOUNT_EMAIL={email}")

    write_values(host, lines)

    print("\nChecking it works…\n")
    checks = (() if YOUTUBE_KEY else ("mail",) if MAILBOX_KEY
              else ("mail", "calendar", "seo") if SEARCH_CONSOLE
              else ("mail", "calendar"))
    for module in checks:
        run_check(host, module)

if __name__ == "__main__":
    main()
