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
"""
from __future__ import annotations

import http.server
import json
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/userinfo.email"
)
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
PORT = int(__import__("os").getenv("ATLAS_OAUTH_PORT", "8765"))
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


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: run this through bootstrap/google-login.sh, which "
                 "explains the console steps first")
    client_id, client_secret = _client(sys.argv[1])
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
        def do_GET(self) -> None:                       # noqa: N802
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

        def log_message(self, *args) -> None:           # noqa: A003
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
        "prompt": "consent",
    })
    print("Opening Google's consent page. Approve both Gmail and Calendar.\n")
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
    with urllib.request.urlopen(
            urllib.request.Request(TOKEN, data=body), timeout=30) as r:
        tokens = json.load(r)

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
    except Exception:                                   # noqa: BLE001
        pass

    print(f"\nAuthorised{' as ' + email if email else ''}.")
    print("Writing to the server (nothing is printed back):\n")

    lines = [f"GOOGLE_CLIENT_ID={client_id}",
             f"GOOGLE_CLIENT_SECRET={client_secret}",
             f"GOOGLE_REFRESH_TOKEN={refresh}"]
    if email:
        lines.append(f"GOOGLE_ACCOUNT_EMAIL={email}")

    import os
    import pathlib
    import subprocess

    host = os.getenv("ATLAS_SSH_HOST", "root@atlas.example.com")
    here = pathlib.Path(__file__).resolve().parent

    # A uniquely named temp file, not a shared one. Two people running this at once --
    # or one person while something else is tidying up -- had the copy
    # removed between the scp and the ssh, and the run died after consent
    # with the token already spent. A one-time consent is exactly the wrong
    # thing to lose to a race over a temp file.
    tmp = f"/opt/atlas/.setenv-{os.getpid()}.py"
    subprocess.run(["scp", "-q", str(here / "setenv.py"), f"{host}:{tmp}"],
                   check=True)
    try:
        subprocess.run(
            ["ssh", host, f"cd /opt/atlas && python3 {tmp} && "
                          f"docker compose up -d api >/dev/null 2>&1"],
            input="\n".join(lines) + "\n", text=True, check=True)
    finally:
        subprocess.run(["ssh", host, f"rm -f {tmp}"], check=False)

    print("\nChecking it works…\n")
    for module in ("mail", "calendar"):
        subprocess.run(
            ["ssh", host, f"cd /opt/atlas && docker compose exec -T api "
                          f"python -m atlas.cli connect {module}"], check=False)


if __name__ == "__main__":
    main()
