

def test_a_module_can_be_switched_off_by_name(monkeypatch):
    """Miro, 2026-09-27: "we don't need YouTube". Off by choice is recorded
    as such, so it never reads UNKNOWN and is never silently absent."""
    from atlas.kernel.registry import Registry
    monkeypatch.setenv("ATLAS_DISABLED_MODULES", "youtube, Voice")
    reg = Registry()
    reg.load_all()
    assert "youtube" not in reg.modules and "voice" not in reg.modules
    assert reg.disabled["youtube"] == "switched off (ATLAS_DISABLED_MODULES)"
    assert not any(k.startswith("youtube.") for k in reg.checks)
