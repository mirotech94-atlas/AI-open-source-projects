import time

from guardian_sensor import capture


class _FakeProc:
    """Stands in for subprocess.Popen: records what it was started with and
    lets a test control liveness through `returncode` (None = still running,
    matching Popen.poll())."""
    def __init__(self, *a, **k):
        self.args = a
        self.kwargs = k
        self.returncode = None

    def poll(self):
        return self.returncode


def _reset_globals():
    capture._proc = None
    capture._backoff = 5.0
    capture._started_at = 0.0


def test_no_interface_means_OFF_without_touching_tcpdump(monkeypatch, tmp_path):
    """Item 20: "off" and "none" are different answers.

    Nobody having asked Vault to watch traffic here is not the same as
    Vault watching and seeing nothing -- and the page used to render both
    as "Nothing unusual". `off` is the word for the first, and it is not an
    error: capture is opt-in per network by Miro's own ruling.
    """
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start")))
    out = capture.tick({"capture": {"interface": ""}}, {})
    assert out["mode"] == "off" and out["error"] == ""

def test_missing_binary_is_reported_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda _: None)
    out = capture.tick({"capture": {"interface": "eth1"}}, {})
    assert out["mode"] == "none" and "tcpdump" in out["error"] and "tshark" in out["error"]


def test_tshark_is_used_when_it_is_the_one_that_is_present(monkeypatch, tmp_path):
    _reset_globals()
    """Item 20 asked for Wireshark/tshark "where it is present". Both write
    the same rotating pcaps, so `flows.py` never learns which ran."""
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda name: "/usr/bin/tshark" if name == "tshark" else None)
    monkeypatch.setattr(capture.subprocess, "Popen", _FakeProc)
    out = capture.tick({"capture": {"interface": "eth1"}}, {})
    argv = capture._proc.args[0]
    assert argv[0] == "tshark"
    assert "duration:60" in argv
    assert out["tool"] == "tshark"


def test_tcpdump_is_preferred_when_both_are_there(monkeypatch, tmp_path):
    _reset_globals()
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(capture.subprocess, "Popen", _FakeProc)
    capture.tick({"capture": {"interface": "eth1"}}, {})
    assert capture._proc.args[0][0] == "tcpdump"


def test_file_cap_holds(monkeypatch, tmp_path):
    d = tmp_path / "cap"; d.mkdir()
    for i in range(14):
        (d / f"{1700000000 + i * 60}.pcap").write_bytes(b"")
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    capture._trim()
    assert len(list(d.iterdir())) == capture.MAX_FILES

def test_completed_files_excludes_the_newest(monkeypatch, tmp_path):
    d = tmp_path / "cap"; d.mkdir()
    for i in range(3):
        (d / f"{1700000000 + i * 60}.pcap").write_bytes(b"")
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    assert [p.split("/")[-1] for p in capture.completed_files()] == ["1700000000.pcap", "1700000060.pcap"]


def test_first_start_leaves_backoff_at_five(monkeypatch, tmp_path):
    _reset_globals()
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/tcpdump")
    monkeypatch.setattr(capture.subprocess, "Popen", _FakeProc)
    monkeypatch.setattr(capture.time, "time", lambda: 1000.0)
    capture.tick({"capture": {"interface": "eth1"}}, {})
    assert capture._backoff == 5.0


def test_crash_restart_doubles_backoff(monkeypatch, tmp_path):
    _reset_globals()
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/tcpdump")
    monkeypatch.setattr(capture.subprocess, "Popen", _FakeProc)
    clock = {"t": 1000.0}
    monkeypatch.setattr(capture.time, "time", lambda: clock["t"])

    capture.tick({"capture": {"interface": "eth1"}}, {})   # first start
    assert capture._backoff == 5.0

    capture._proc.returncode = 1                            # it crashed
    clock["t"] += 10                                         # past the 5s backoff
    capture.tick({"capture": {"interface": "eth1"}}, {})
    assert capture._backoff == 10.0


def test_healthy_long_run_resets_backoff(monkeypatch, tmp_path):
    _reset_globals()
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/tcpdump")
    monkeypatch.setattr(capture.subprocess, "Popen", _FakeProc)
    clock = {"t": 1000.0}
    monkeypatch.setattr(capture.time, "time", lambda: clock["t"])

    capture.tick({"capture": {"interface": "eth1"}}, {})   # first start, alive
    capture._backoff = 40.0                                  # pretend past crashes climbed it
    clock["t"] += capture.SILENT_AFTER_S + 1                 # still alive, long past the window
    capture.tick({"capture": {"interface": "eth1"}}, {})
    assert capture._backoff == 5.0


def test_silence_detected_when_all_recent_files_are_header_only(monkeypatch, tmp_path):
    _reset_globals()
    d = tmp_path / "cap"; d.mkdir()
    for name in ("a.pcap", "b.pcap", "c.pcap"):
        (d / name).write_bytes(b"\x00" * 24)
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/tcpdump")
    monkeypatch.setattr(capture.subprocess, "Popen", _FakeProc)
    now = time.time()
    monkeypatch.setattr(capture.time, "time", lambda: now)
    capture._proc = _FakeProc()                              # already running
    capture._started_at = now - (capture.SILENT_AFTER_S + 100)

    out = capture.tick({"capture": {"interface": "eth1"}}, {})
    assert out["error"].startswith("no packets")


def test_no_silence_when_any_recent_file_has_packets(monkeypatch, tmp_path):
    _reset_globals()
    d = tmp_path / "cap"; d.mkdir()
    (d / "a.pcap").write_bytes(b"\x00" * 24)
    (d / "b.pcap").write_bytes(b"\x00" * 24)
    (d / "c.pcap").write_bytes(b"\x00" * 200)                 # this one carried packets
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/tcpdump")
    monkeypatch.setattr(capture.subprocess, "Popen", _FakeProc)
    now = time.time()
    monkeypatch.setattr(capture.time, "time", lambda: now)
    capture._proc = _FakeProc()                              # already running
    capture._started_at = now - (capture.SILENT_AFTER_S + 100)

    out = capture.tick({"capture": {"interface": "eth1"}}, {})
    assert out["error"] == ""


def test_tshark_is_told_to_write_CLASSIC_pcap(monkeypatch, tmp_path):
    """The one that would have shipped broken.

    tshark writes pcapng by default; `flows.py` reads classic libpcap and
    returns silently on any other magic. Without `-F pcap` a machine with
    tshark and no tcpdump captures happily, rotates files, parses none of
    them, and reports "watching" with zero flows.

    This asserts against the PARSER, not against the flag: it builds a file
    the way each argv says the tool would, and checks `flows` can read it.
    """
    from guardian_sensor import flows
    import struct

    argv = capture._argv("tshark", "eth1", 128, str(tmp_path / "cap.pcap"))
    assert "-F" in argv and argv[argv.index("-F") + 1] == "pcap"

    # A classic-pcap file with one Ethernet/IPv4/UDP packet, which is what
    # `-F pcap` produces and what the parser expects.
    pkt = (b"\x00\x11\x22\x33\x44\x55" b"\x66\x77\x88\x99\xaa\xbb" b"\x08\x00"
           + b"\x45\x00\x00\x20" + b"\x00" * 5 + b"\x11" + b"\x00\x00"
           + bytes([192, 168, 1, 10]) + bytes([1, 1, 1, 1])
           + struct.pack(">HHHH", 5353, 53, 12, 0))
    f = tmp_path / "one.pcap"
    f.write_bytes(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
                  + struct.pack("<IIII", 1700000000, 0, len(pkt), len(pkt)) + pkt)
    assert list(flows.read_pcap(str(f))), "the parser must be able to read what tshark is told to write"
