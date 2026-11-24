#!/usr/bin/env python3
"""Loopback-only proxy that forwards ONE thing: GET /healthz to the ATLAS api.

The reverse tunnel to the Spark points here, not at the api, so the Spark can
read liveness and nothing else (every other path and method is refused here and
never reaches the api, which may trust loopback callers). No request header or
body is forwarded; the upstream sees a fresh request with Host 127.0.0.1:8080.
Env: ATLAS_PROXY_PORT [18081], ATLAS_UPSTREAM [http://127.0.0.1:8080].
"""  # noqa: EXE001
from __future__ import annotations

import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

UPSTREAM = os.getenv("ATLAS_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/healthz":          # exact: no query, no sub-path
            return self._send(404, b"not found")
        req = urllib.request.Request(UPSTREAM + "/healthz",
                                     headers={"Host": "127.0.0.1:8080"})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                self._send(r.status, r.read(65536), "application/json")
        except urllib.error.HTTPError as e:
            self._send(e.code, b"upstream error")
        except Exception:  # noqa: BLE001
            self._send(502, b"upstream unreachable")

    def _refuse(self) -> None:
        self._send(404, b"not found")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _refuse

    def log_message(self, *a) -> None:  # quiet
        pass


def make_server(port: int) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), Handler)      # loopback only


if __name__ == "__main__":
    make_server(int(os.getenv("ATLAS_PROXY_PORT", "18081"))).serve_forever()
