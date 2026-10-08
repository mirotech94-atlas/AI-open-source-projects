"""The Fios router handshake is derived from the SPA bundle and unproven
against a live router; these tests prove the code matches the DERIVED
formula and builds the right requests, not that the router accepts them."""
import hashlib

from guardian_sensor import router_fios as rf


def _md5(s): return hashlib.md5(s.encode()).hexdigest()
def _sha512(s): return hashlib.sha512(s.encode()).hexdigest()


def test_arcmd5_is_sha512_of_md5_hex():
    assert rf.arcmd5("admin") == _sha512(_md5("admin"))
    # a fixed vector: MD5("") is well known, so this pins the whole chain
    assert rf.arcmd5("") == _sha512("d41d8cd98f00b204e9800998ecf8427e")


def test_login_password_salts_the_arcmd5_with_the_login_token():
    tok = "abc123"
    assert rf.login_password("correct horse", tok) == _sha512(tok + rf.arcmd5("correct horse"))


def test_empty_salt_falls_back_to_arcmd5_like_the_bundle():
    assert rf.login_password("pw", "") == rf.arcmd5("pw")


def test_login_fields_shape_matches_the_bundle():
    f = rf.login_fields("pw", "SALT", username="admin", keep_login=True)
    assert f["luci_username"] == rf.arcmd5("admin")
    assert f["luci_password"] == rf.login_password("pw", "SALT")
    assert f["luci_token"] == "SALT" and f["luci_view"] == "Desktop" and f["luci_keep_login"] == "1"


def test_blank_username_falls_back_to_the_gateways_own_default_admin():
    """The router's login page (chunk 5929: `loginForm.username: "admin"`)
    has no username field at all -- it ALWAYS sends ArcMD5("admin"). Sending
    ArcMD5("") for a blank username is what made a live run 403 on
    2026-09-15 with a known-good password."""
    f = rf.login_fields("pw", "SALT")
    assert f["luci_username"] == rf.arcmd5("admin")
    assert rf.login_fields("pw", "SALT", username="")["luci_username"] == rf.arcmd5("admin")
    # An explicit username still wins.
    assert rf.login_fields("pw", "SALT", username="owner")["luci_username"] == rf.arcmd5("owner")
    # ...and the documented fallback omits the field entirely
    f2 = rf.login_fields("pw", "SALT", omit_empty_username=True)
    assert "luci_username" not in f2


def test_disable_remote_admin_body_turns_the_four_keys_off():
    body = rf.disable_remote_admin_body("TOK")
    assert body["token"] == "TOK"
    assert body["remote_ipv4_enable"] == "0" and body["remote_ipv6_enable"] == "0"
    assert body["https_ipv4_enable"] == "0" and body["https_ipv6_enable"] == "0"


def test_the_runner_wires_in_the_router_credential_test():
    # The scaffolding is now wired: the /api/router/test queue branch fetches
    # the credential from the hub and calls test_credential. (This replaces the
    # earlier "nothing imports this" guard, deleted deliberately on wiring.)
    import pathlib
    runner = (pathlib.Path(rf.__file__).parent / "runner.py").read_text()
    assert "router_fios" in runner
    assert "/api/router/test" in runner
    assert "test_credential" in runner
    assert "/api/router/disable-remote-admin" in runner
    assert "disable_remote_admin" in runner
