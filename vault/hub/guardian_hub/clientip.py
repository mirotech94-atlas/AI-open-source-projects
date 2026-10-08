"""The submitting address of a request, as best this process can know it.

Lifted out of `routers/leads.py` (which still calls it, unchanged) because the
public API on api.guarder.io needs the same answer for the same reasons: its
rate-limit buckets and its audit rows are only meaningful if they name the real
caller, and behind nginx every request arrives from loopback.
"""
import ipaddress

from fastapi import Request

#: Peers whose `X-Real-IP` is believed. `testclient` is Starlette's TestClient,
#: which stands in for "our own nginx" in tests.
_TRUSTED_PEERS = ("127.0.0.1", "::1", "testclient")


def client_ip(request: Request) -> str:
    """Behind nginx every request arrives from 127.0.0.1 -- the same trap the
    `/metrics` block in `deploy/nginx-guardian.conf` documents -- so the real
    address is only in `X-Real-IP`, which every proxy location Vault ships
    sets from `$remote_addr`. That header is trusted ONLY when the immediate
    peer is loopback (i.e. it really is our own nginx): a client talking
    straight to the hub on :8800 can set any header it likes, and trusting it
    there would hand it an unlimited supply of rate-limit buckets.

    The header is parsed as an address rather than taken as-is, because the
    value becomes a rate-limit bucket key and a stored column.
    """
    peer = request.client.host if request.client else ""
    if peer in _TRUSTED_PEERS:
        header = (request.headers.get("x-real-ip") or "").strip()
        try:
            return str(ipaddress.ip_address(header)) if header else peer
        except ValueError:
            return peer
    return peer
