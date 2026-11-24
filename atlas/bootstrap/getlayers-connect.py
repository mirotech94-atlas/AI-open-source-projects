#!/usr/bin/env python3
"""Connect ATLAS's server to GetLayers. Run once on the Mac.

    python3 bootstrap/getlayers-connect.py            # connect
    python3 bootstrap/getlayers-connect.py --dry-run  # only show the endpoints

GetLayers has no API keys; its MCP server speaks MCP OAuth 2.1. The browser
half of that flow cannot run on the headless server, so it runs here:

 1. discover: the MCP URL's RFC 9728 resource metadata names the
    authorization server, whose RFC 8414 metadata names the endpoints
    (`--dry-run` prints them and stops -- before registering anything);
 2. register "ATLAS server" as a public client (RFC 7591), redirect URI
    `http://127.0.0.1:<port>/callback` on a port this script already holds;
 3. open the browser at the authorize endpoint with PKCE S256 + state, and
    take the one callback on that port (state compared in constant time,
    `iss` checked against the issuer, RFC 9207);
 4. exchange the code for tokens;
 5. pipe `GETLAYERS_OAUTH_*=value` lines over ssh's stdin into the server's
    `envlock.py`, exactly as `set-secret.sh` does, and restart the API.

No token is ever printed, logged, put in a command line or a URL. The
callback's request line (which carries the code) is not logged either.
Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.server
import json
import os
import pathlib
import secrets
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, ClassVar

MCP_URL = os.environ.get("GETLAYERS_MCP_URL", "https://mcp.getlayers.ai/mcp")
REPO = pathlib.Path(__file__).resolve().parent.parent
REMOTE = "root@atlas.example.com"


def default_host() -> str:
    """Same rule as google_login.default_host(): ATLAS_SSH_HOST if set; else
    "local" when this machine is not the old server (no /opt/atlas) and this
    checkout runs ATLAS itself; else the old server (deleted 2026-10-27)."""
    explicit = os.environ.get("ATLAS_SSH_HOST", "").strip()
    if explicit:
        return explicit
    opt = pathlib.Path(os.environ.get("ATLAS_OPT_DIR", "/opt/atlas"))
    if not opt.exists() and (REPO / "docker-compose.local.yml").is_file():
        return "local"
    return REMOTE


HOST = default_host()
CLIENT_NAME = "ATLAS server"
CALLBACK_PATH = "/callback"
CALLBACK_TIMEOUT = 300          # seconds to finish signing in
HTTP_TIMEOUT = 20


class ConnectError(Exception):
    """Something went wrong. `str()` never carries a token."""


# ------------------------------------------------------------ pure parts

def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge), RFC 7636: a 43-128 char verifier of
    unreserved characters; challenge = BASE64URL(SHA256(verifier)), no pad."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def resource_metadata_url(mcp_url: str) -> str:
    """RFC 9728 §3.1: the well-known segment goes between host and path."""
    parts = urllib.parse.urlsplit(mcp_url)
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource{path}"


def as_metadata_url(issuer: str) -> str:
    """RFC 8414 §3: the same insertion for the authorization server."""
    parts = urllib.parse.urlsplit(issuer)
    path = parts.path.rstrip("/")
    return (f"{parts.scheme}://{parts.netloc}"
            f"/.well-known/oauth-authorization-server{path}")


def authorize_url(endpoint: str, *, client_id: str, redirect_uri: str,
                  challenge: str, state: str, resource: str,
                  scope: str | None) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
    }
    if scope:
        params["scope"] = scope
    sep = "&" if urllib.parse.urlsplit(endpoint).query else "?"
    return endpoint + sep + urllib.parse.urlencode(params)


def env_lines(values: dict[str, str | None]) -> str:
    """`NAME=value` lines for envlock.py, skipping empty values. Refuses a
    value .env cannot hold (a newline), naming only the variable."""
    out = []
    for name, value in values.items():
        if not value:
            continue
        if "\n" in value or "\r" in value:
            raise ConnectError(f"{name} contains a newline; .env cannot hold it")
        out.append(f"{name}={value}")
    return "".join(line + "\n" for line in out)


def parse_callback(path: str, *, expected_state: str,
                   issuer: str | None) -> str:
    """The authorization code out of the callback's request path, after
    checking state (constant time) and `iss` (RFC 9207) when present."""
    parts = urllib.parse.urlsplit(path)
    if parts.path != CALLBACK_PATH:
        raise ConnectError(f"unexpected callback path {parts.path!r}")
    query = dict(urllib.parse.parse_qsl(parts.query))
    if "error" in query:
        desc = query.get("error_description", "")
        raise ConnectError(f"GetLayers refused the connection: {query['error']}"
                           + (f" ({desc[:200]})" if desc else ""))
    state = query.get("state", "")
    if not hmac.compare_digest(state.encode(), expected_state.encode()):
        raise ConnectError("state mismatch on the callback; nothing was stored")
    iss = query.get("iss")
    if iss is not None and issuer is not None and iss.rstrip("/") != issuer.rstrip("/"):
        raise ConnectError("callback came from a different issuer; nothing was stored")
    code = query.get("code")
    if not code:
        raise ConnectError("callback carried no authorization code")
    return code


# ---------------------------------------------------------------- network

def _tls_context() -> ssl.SSLContext:
    """Verification always on. A python.org Python on macOS ships with no
    CA roots until "Install Certificates.command" has run, and fails every
    https request; fall back to certifi if present, then to the system
    bundle macOS keeps at /etc/ssl/cert.pem -- never to no verification."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0):
        return ctx
    try:
        import certifi  # type: ignore
        ctx.load_verify_locations(certifi.where())
        return ctx
    except (ImportError, OSError):
        pass
    for bundle in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if os.path.exists(bundle):
            ctx.load_verify_locations(bundle)
            break
    return ctx


_TLS: ssl.SSLContext | None = None


def _request(url: str, *, data: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=data, headers={
        "Accept": "application/json", "User-Agent": "atlas-getlayers-connect",
        **(headers or {})})
    global _TLS
    if _TLS is None:
        _TLS = _tls_context()
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=_TLS) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ConnectError(f"could not reach {urllib.parse.urlsplit(url).netloc}: "
                           f"{exc.reason}") from None
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, None


def _get_json(url: str) -> dict[str, Any]:
    status, body = _request(url)
    if status != 200 or not isinstance(body, dict):
        raise ConnectError(f"{url} answered HTTP {status}")
    return body


def discover(mcp_url: str) -> dict[str, Any]:
    resource_meta = _get_json(resource_metadata_url(mcp_url))
    servers = resource_meta.get("authorization_servers") or []
    if not servers:
        raise ConnectError("resource metadata names no authorization server")
    issuer = str(servers[0]).rstrip("/")
    meta = _get_json(as_metadata_url(issuer))
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not str(meta.get(key, "")).startswith("https://"):
            raise ConnectError(f"authorization server metadata has no https {key}")
    if "S256" not in (meta.get("code_challenge_methods_supported") or ["S256"]):
        raise ConnectError("authorization server does not offer PKCE S256")
    return {
        "resource": resource_meta.get("resource") or mcp_url,
        "issuer": meta.get("issuer") or issuer,
        "authorization_endpoint": meta["authorization_endpoint"],
        "token_endpoint": meta["token_endpoint"],
        "registration_endpoint": meta["registration_endpoint"],
        "scopes": meta.get("scopes_supported") or [],
        "auth_methods": meta.get("token_endpoint_auth_methods_supported") or [],
        "iss_param": bool(meta.get("authorization_response_iss_parameter_supported")),
    }


def register(endpoints: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    method = "none" if "none" in endpoints["auth_methods"] else "client_secret_post"
    body = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
    }
    if "mcp" in endpoints["scopes"]:
        body["scope"] = "mcp"
    status, reply = _request(endpoints["registration_endpoint"],
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
    if status not in (200, 201) or not isinstance(reply, dict) \
            or not reply.get("client_id"):
        raise ConnectError(f"client registration answered HTTP {status}")
    return reply


def exchange(endpoints: dict[str, Any], client: dict[str, Any], *, code: str,
             verifier: str, redirect_uri: str) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client["client_id"],
        "code_verifier": verifier,
        "resource": endpoints["resource"],
    }
    if client.get("client_secret"):
        form["client_secret"] = client["client_secret"]
    status, reply = _request(
        endpoints["token_endpoint"], data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200 or not isinstance(reply, dict):
        err = reply.get("error") if isinstance(reply, dict) else None
        raise ConnectError(f"code exchange answered HTTP {status}"
                           + (f" ({err})" if isinstance(err, str) else ""))
    if not reply.get("access_token"):
        raise ConnectError("token response carried no access token")
    if not reply.get("refresh_token"):
        raise ConnectError("GetLayers issued no refresh token, so the server could "
                           "not keep the connection alive; nothing was stored")
    return reply


# ------------------------------------------------------- the callback

class _Callback(http.server.BaseHTTPRequestHandler):
    result: ClassVar[dict[str, str]] = {}
    done: ClassVar[threading.Event] = threading.Event()

    def do_GET(self) -> None:
        if urllib.parse.urlsplit(self.path).path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result["path"] = self.path
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<p>ATLAS: GetLayers answered. You can close this tab "
                         b"and go back to the terminal.</p>")
        type(self).done.set()

    def log_message(self, format: str, *args: Any) -> None:
        # The default prints the request line -- `?code=...` included.
        return


def _wait_for_code(server: http.server.HTTPServer, *, state: str,
                   issuer: str | None) -> str:
    _Callback.result = {}
    _Callback.done = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not _Callback.done.wait(CALLBACK_TIMEOUT):
            raise ConnectError(f"no answer from the browser in {CALLBACK_TIMEOUT // 60}"
                               " min; nothing was stored")
    finally:
        server.shutdown()
        server.server_close()
    return parse_callback(_Callback.result["path"], expected_state=state,
                          issuer=issuer)


# ----------------------------------------------------------- the server

def push_to_server(lines: str, host: str) -> None:
    """Over stdin, never argv -- the same path set-secret.sh takes. "local"
    writes this checkout's .env through envlock.py and restarts the api with
    both compose files; anything else is an ssh target."""
    if host == "local":
        env = {**os.environ,
               "ATLAS_ENV_PATH": os.environ.get("ATLAS_ENV_PATH") or str(REPO / ".env")}
        out = subprocess.run([sys.executable, str(REPO / "bootstrap" / "envlock.py")],
                             input=lines, text=True, capture_output=True,
                             env=env, check=False)
        if out.returncode != 0:
            raise ConnectError("writing the local .env failed "
                               f"(exit {out.returncode}); nothing is connected")
        subprocess.run(["docker", "compose", "-f", "docker-compose.yml",
                        "-f", "docker-compose.local.yml", "up", "-d", "api"],
                       cwd=os.environ.get("ATLAS_COMPOSE_DIR") or str(REPO),
                       capture_output=True, check=False)
        return
    compose_dir = os.environ.get("ATLAS_COMPOSE_DIR", "/opt/atlas")
    out = subprocess.run(["ssh", host, "python3 /opt/atlas/bootstrap/envlock.py"],
                         input=lines, text=True, capture_output=True, check=False)
    if out.returncode != 0:
        raise ConnectError("writing .env on the server failed "
                           f"(ssh exit {out.returncode}); nothing is connected")
    restart = f"cd '{compose_dir}' && docker compose up -d api >/dev/null 2>&1"
    subprocess.run(["ssh", host, restart], check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the discovered endpoints and stop (registers nothing)")
    ap.add_argument("--host", default=HOST, help=f"ssh target (default {HOST})")
    ap.add_argument("--mcp-url", default=MCP_URL)
    ap.add_argument("--no-browser", action="store_true",
                    help="print the sign-in URL instead of opening it")
    args = ap.parse_args(argv)

    try:
        endpoints = discover(args.mcp_url)
        if args.dry_run:
            print(f"resource        {endpoints['resource']}")
            print(f"issuer          {endpoints['issuer']}")
            print(f"authorize       {endpoints['authorization_endpoint']}")
            print(f"token           {endpoints['token_endpoint']}")
            print(f"registration    {endpoints['registration_endpoint']}")
            print(f"scopes          {', '.join(endpoints['scopes']) or '(none listed)'}")
            return 0

        server = http.server.HTTPServer(("127.0.0.1", 0), _Callback)
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}{CALLBACK_PATH}"
        try:
            client = register(endpoints, redirect_uri)
        except BaseException:
            server.server_close()
            raise
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        url = authorize_url(
            endpoints["authorization_endpoint"], client_id=client["client_id"],
            redirect_uri=redirect_uri, challenge=challenge, state=state,
            resource=endpoints["resource"],
            scope="mcp" if "mcp" in endpoints["scopes"] else None)
        print("\nSign in to GetLayers in the browser window that opens"
              " (Full Stack Lifetime account).", file=sys.stderr)
        if args.no_browser or not webbrowser.open(url):
            print(f"Open this URL:\n{url}", file=sys.stderr)
        code = _wait_for_code(server, state=state,
                              issuer=endpoints["issuer"] if endpoints["iss_param"]
                              else None)
        tokens = exchange(endpoints, client, code=code, verifier=verifier,
                          redirect_uri=redirect_uri)
        lines = env_lines({
            "GETLAYERS_OAUTH_CLIENT_ID": client["client_id"],
            "GETLAYERS_OAUTH_REFRESH_TOKEN": tokens["refresh_token"],
            "GETLAYERS_OAUTH_CLIENT_SECRET": client.get("client_secret"),
            "GETLAYERS_OAUTH_TOKEN_URL": endpoints["token_endpoint"],
        })
        push_to_server(lines, args.host)
        try:
            expiry = f"{int(tokens['expires_in']) // 60} min"
        except (KeyError, TypeError, ValueError):
            expiry = "an unstated time"
        del tokens, lines, code, verifier
    except ConnectError as exc:
        print(f"getlayers-connect: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled; nothing was stored", file=sys.stderr)
        return 130
    print(f"connected; access token expires in {expiry}; refresh token stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
