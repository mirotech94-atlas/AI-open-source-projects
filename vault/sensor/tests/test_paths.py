import importlib
import os
import stat


def test_data_dir_honours_guardian_data_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIAN_DATA", str(tmp_path / "custom"))
    from guardian_sensor import paths

    importlib.reload(paths)
    try:
        assert paths.DATA_DIR == str(tmp_path / "custom")
    finally:
        monkeypatch.delenv("GUARDIAN_DATA", raising=False)
        importlib.reload(paths)


def test_ensure_creates_dir_mode_0700(tmp_path, monkeypatch):
    target = tmp_path / "guardian"
    monkeypatch.setenv("GUARDIAN_DATA", str(target))
    from guardian_sensor import paths

    importlib.reload(paths)
    try:
        paths.ensure()
        assert target.is_dir()
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o700
    finally:
        monkeypatch.delenv("GUARDIAN_DATA", raising=False)
        importlib.reload(paths)


def test_p_joins_onto_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIAN_DATA", str(tmp_path))
    from guardian_sensor import paths

    importlib.reload(paths)
    try:
        assert paths.p("config.json") == str(tmp_path / "config.json")
    finally:
        monkeypatch.delenv("GUARDIAN_DATA", raising=False)
        importlib.reload(paths)


def _patch_linux(monkeypatch, paths_mod, *, exists, writable):
    """Force paths._default_dir onto its Linux branch with controlled
    os.path.exists()/os.access() results for the /var/lib/guardian check."""
    monkeypatch.setattr(paths_mod.sys, "platform", "linux")

    real_exists = os.path.exists
    real_access = os.access

    def fake_exists(path):
        if path == "/var/lib/guardian":
            return exists
        return real_exists(path)

    def fake_access(path, mode):
        if path in ("/var/lib/guardian", "/var/lib"):
            return writable
        return real_access(path, mode)

    monkeypatch.setattr(paths_mod.os.path, "exists", fake_exists)
    monkeypatch.setattr(paths_mod.os, "access", fake_access)


def test_default_dir_linux_writable_existing_dir_uses_var_lib(monkeypatch):
    from guardian_sensor import paths

    _patch_linux(monkeypatch, paths, exists=True, writable=True)
    assert paths._default_dir() == "/var/lib/guardian"


def test_default_dir_linux_unwritable_existing_dir_falls_back_home(monkeypatch):
    from guardian_sensor import paths

    _patch_linux(monkeypatch, paths, exists=True, writable=False)
    assert paths._default_dir() == os.path.expanduser("~/.guardian")


def test_default_dir_linux_missing_dir_writable_parent_uses_var_lib(monkeypatch):
    from guardian_sensor import paths

    _patch_linux(monkeypatch, paths, exists=False, writable=True)
    assert paths._default_dir() == "/var/lib/guardian"


def test_default_dir_linux_missing_dir_unwritable_parent_falls_back_home(monkeypatch):
    from guardian_sensor import paths

    _patch_linux(monkeypatch, paths, exists=False, writable=False)
    assert paths._default_dir() == os.path.expanduser("~/.guardian")
