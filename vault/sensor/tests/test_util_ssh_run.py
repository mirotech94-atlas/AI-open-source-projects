import subprocess
import time

from guardian_sensor import util


def test_ssh_run_streaming_timeout_kills_child_and_streams_partial_output(monkeypatch):
    """`util.ssh_run`'s streaming path (Task 29 fix round finding 5) had no
    test of its own. A real child process (not a mock) prints two lines
    then sleeps well past the requested timeout -- the same shape as a
    stalled `apt` prompt with nothing left to print, which the read loop
    alone can't catch (it only checks the clock after a line arrives).
    This proves the `threading.Timer` wall-clock kill actually fires AND
    that every line streamed before the kill survives via `on_line`, even
    though the final return value is the sentinel "timeout" string."""
    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # Swap the real ssh invocation for a short-lived python child that
        # behaves like a stalling upgrade: emit output, then block.
        script = (
            "import sys, time\n"
            "print('line1'); sys.stdout.flush()\n"
            "print('line2'); sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        return real_popen(["python3", "-u", "-c", script], stdout=kwargs.get("stdout"),
                           stderr=kwargs.get("stderr"), text=kwargs.get("text"),
                           bufsize=kwargs.get("bufsize"))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    seen = []
    t0 = time.monotonic()
    rc, out = util.ssh_run({"target": "irrelevant"}, "irrelevant", timeout=1, on_line=seen.append)
    elapsed = time.monotonic() - t0

    assert rc == -1
    assert out == "timeout"
    assert seen == ["line1", "line2"]
    # The kill actually fired -- this would be ~30s if it didn't.
    assert elapsed < 10
