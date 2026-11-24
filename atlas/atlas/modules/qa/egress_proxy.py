"""The qa-browser's only way out: an allowlisting CONNECT proxy.

The qa-browser sits on `qa_egress`, an internal compose network with no
route off the host. This proxy is the one other member of that network and
the only one with a second, outbound network. It forwards a CONNECT to port
443 of an allowlisted public host, after resolving it once and only when
every address is public; it dials the pinned address, never a name again.
Anything else (plain HTTP, another port, a name off the list, an address
that is private, loopback, link-local or CGNAT) is answered 403 and closed.
So a WebRTC TURN/TCP relay, a WebSocket or a page script has nowhere to go
but these hosts on 443.

Standard library only; it imports nothing from ATLAS, reads no env file and
holds no secret. The host list must equal `browser_service.ALLOWED` (a test
holds them together).

  python3 egress_proxy.py [port]      (default 3128)
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import sys

PORT = 3128
def _store_host() -> str:
    """The store's host from ACADEMY_PUBLIC_ORIGIN (default agency.example.com).
    Inlined, not imported: this file runs alone in its container."""
    raw = os.environ.get("ACADEMY_PUBLIC_ORIGIN", "").strip().rstrip("/").lower()
    host = raw.removeprefix("https://") if raw.startswith("https://") else ""
    ok = (host and "." in host
          and all(c in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host))
    return host if ok else "agency.example.com"


# Public https hosts only, never a compose service name or an address. The
# agency landing, the store and the client previews are one host.
ALLOWED = frozenset({"agency.example.com", _store_host()})
HEAD_LIMIT = 8192
TIMEOUT_S = 20


def host_allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    if not host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host):
        return False
    return host in ALLOWED


def target(request_line: str) -> tuple[str, int] | None:
    """`CONNECT host:443 HTTP/1.1` -> (host, 443); anything else -> None."""
    parts = request_line.split()
    if len(parts) != 3 or parts[0] != "CONNECT" or not parts[2].startswith("HTTP/"):
        return None
    host, sep, port = parts[1].rpartition(":")
    if not sep or port != "443" or not host_allowed(host):
        return None
    return host.lower().rstrip("."), 443


def public(addresses: list[str]) -> str | None:
    """The first address when every one is global unicast, else None."""
    if not addresses:
        return None
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            return None
        if not ip.is_global or ip.is_multicast:
            return None
    return addresses[0]


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    async def refuse(code: str = "403 Forbidden") -> None:
        writer.write(f"HTTP/1.1 {code}\r\nContent-Length: 0\r\n\r\n".encode())
        await writer.drain()
        writer.close()

    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), TIMEOUT_S)
    except (TimeoutError, asyncio.IncompleteReadError,
            asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    if len(head) > HEAD_LIMIT:
        await refuse()
        return
    goal = target(head.split(b"\r\n", 1)[0].decode("latin-1"))
    if goal is None:
        await refuse()
        return
    try:
        ip = public(await asyncio.wait_for(resolve(goal[0]), TIMEOUT_S))
    except (TimeoutError, OSError):
        ip = None
    if ip is None:
        await refuse()
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(ip, goal[1]), TIMEOUT_S)
    except (TimeoutError, OSError):
        await refuse("502 Bad Gateway")
        return
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def main(port: int) -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", port, limit=HEAD_LIMIT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else PORT))
