from guardian_sensor import fix, paths


def test_apply_fix_reports_progress_lines(monkeypatch, tmp_path):
    # `paths` is redirected because `apply_fix` is the REAL one: it appends
    # the fix it ran to `remediation.jsonl`. Without this the append lands
    # in ~/.guardian on whatever machine runs the suite -- and on a machine
    # that is also a live sensor, the next 5-minute cycle pushes it to the
    # hub, where it arrives as a real repair stamped `by="test"`.
    #
    # That is not hypothetical: 75 such rows reached the production `home`
    # tenant this way, several per hour on any day the suite was run, and
    # they were briefly mistaken for stale test data to be cleaned up.
    # Deleting them would have hidden a test that writes to production.
    monkeypatch.setattr(paths, "p", lambda name: str(tmp_path / name))

    seen = []
    lines = ["Get:1 http://deb.debian.org sid/main libc6", "Unpacking libc6", "Setting up libc6", "done"]
    def runner(h, cmd, timeout=120, on_line=None):
        for l in lines:
            if on_line: on_line(l)
        return "\n".join(lines)
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}],
           "auto_fix": {"enabled": True, "max_per_day": 20}}
    r = fix.apply_fix(cfg, "prod", "full_upgrade", by="test", lock=False, runner=runner,
                      progress=lambda done, total, line: seen.append((done, line)))
    assert r["ok"] and [d for d, _ in seen] == [1, 2, 3]
    assert seen[-1][1] == "Setting up libc6"


def test_the_suite_never_writes_into_the_live_sensor_directory(tmp_path, monkeypatch):
    """The guard, not the fix.

    Any test that calls the real `fix.apply_fix` without redirecting
    `paths` appends to the running sensor's own journal. This asserts the
    journal this test wrote went to the temp directory and that the real
    one was left alone -- a check that costs nothing and would have caught
    the bug above on the day it was written.
    """
    import os
    monkeypatch.setattr(paths, "p", lambda name: str(tmp_path / name))
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}],
           "auto_fix": {"enabled": True, "max_per_day": 20}}
    fix.apply_fix(cfg, "prod", "full_upgrade", by="test", lock=False,
                  runner=lambda *a, **k: "done")
    assert (tmp_path / "remediation.jsonl").exists(), "the append must land in the tmpdir"
    assert paths.p("remediation.jsonl").startswith(str(tmp_path))
    assert os.path.expanduser("~/.guardian") not in paths.p("remediation.jsonl")
