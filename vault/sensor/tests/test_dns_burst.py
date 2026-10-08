"""A burst of queries cannot exhaust the DNS filter's open files.

The server started one thread per query with no limit, and every thread held
an upstream socket (DoH or UDP) for up to four seconds while it waited. One
device on the owner's network sent bursts of several hundred lookups, and the
filter hit macOS's default 256-file limit 196 times: the query log was the
first `open()` to fail, but upstream sockets failed the same way, which is
every device on the network getting SERVFAIL until the burst drained.

These tests drive the real UDP server class with a resolver that is slow on
purpose, so concurrency is observable, and count how many run at once.
"""
import socket
import threading
import time

from guardian_sensor import dnsfilter


def test_a_burst_never_runs_more_than_the_cap_at_once(monkeypatch):
    running = 0
    peak = 0
    lock = threading.Lock()

    def slow_resolve(pkt, client):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return b"", {"name": None}

    monkeypatch.setattr(dnsfilter, "resolve", slow_resolve)
    monkeypatch.setattr(dnsfilter, "MAX_INFLIGHT", 8)
    monkeypatch.setattr(dnsfilter, "INFLIGHT", threading.BoundedSemaphore(8))
    server = dnsfilter.UDP(("127.0.0.1", 0), dnsfilter.UDPHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for i in range(120):
            s.sendto(b"\x00" * 20, server.server_address)
        deadline = time.time() + 10
        while time.time() < deadline:
            with lock:
                if running == 0 and peak:
                    break
            time.sleep(0.05)
        time.sleep(0.3)
    finally:
        server.shutdown()
        server.server_close()
    assert peak > 1, "the server never ran queries concurrently -- the test proves nothing"
    assert peak <= 8, f"{peak} queries ran at once against a cap of 8"

