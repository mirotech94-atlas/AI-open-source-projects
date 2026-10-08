"""The DNS resolver must never exhaust its file descriptors (2026-09-29)."""
import os
import socket
import threading
import time

from guardian_sensor import dnsfilter as d


def _query(name=b"example.com"):
    q = b"".join(bytes([len(p)]) + p for p in name.split(b".")) + b"\x00"
    return b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + q + b"\x00\x01\x00\x01"


def test_log_write_failure_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_ddir", lambda: str(tmp_path / "missing" / "dir"))
    d.write_log({"name": "example.com"})  # the directory does not exist
    assert d.STATE.get("log_errors", 0) >= 1


def test_over_the_ceiling_answers_servfail_without_resolving(monkeypatch):
    calls = []
    monkeypatch.setattr(d, "resolve", lambda pkt, client: calls.append(1) or (b"x", {"name": "a"}))
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(d, "INFLIGHT", sem)
    sem.acquire()                      # the ceiling is full
    try:
        resp, rec = d.bounded_resolve(_query(), "127.0.0.1")
    finally:
        sem.release()
    assert calls == []
    assert resp[3] & 0x0F == 2          # RCODE SERVFAIL
    assert resp[:2] == b"\x12\x34"      # same transaction id


def test_many_queries_keep_descriptors_flat(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_ddir", lambda: str(tmp_path))
    monkeypatch.setattr(d, "resolve", lambda pkt, client: (d.servfail(pkt), {"name": "example.com"}))
    before = d.open_fds()
    for _ in range(3000):
        resp, rec = d.bounded_resolve(_query(), "127.0.0.1")
        d.write_log(rec)
    after = d.open_fds()
    assert before is not None and after - before <= 2


def test_tcp_client_that_never_sends_is_dropped(monkeypatch):
    monkeypatch.setattr(d, "TCP_TIMEOUT_S", 0.3)
    srv = d.TCP(("127.0.0.1", 0), d.TCPHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = socket.create_connection(srv.server_address)
        c.settimeout(3)
        t0 = time.time()
        assert c.recv(10) == b""       # server closed us after its timeout
        assert time.time() - t0 < 2.5
        c.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_fd_limit_is_raised_and_watchdog_trips():
    limit = d.raise_fd_limit()
    assert limit >= min(d.FD_TARGET, limit)
    assert not d.fd_watchdog_tripped(10**6)
    assert d.fd_watchdog_tripped(1)


def test_launch_agent_raises_open_files():
    from guardian_sensor import install
    pl = install._plist("com.guardian.dns", "dns", "dns.log")
    assert pl["SoftResourceLimits"]["NumberOfFiles"] >= 4096
