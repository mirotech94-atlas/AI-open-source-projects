"""Dashboard auth. Mesh membership must not be the same thing as identity."""
from __future__ import annotations

import pytest

from atlas.api import auth

SECRET = "x" * 40


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    auth._attempts.clear()
    yield
    auth._attempts.clear()


def test_a_weak_or_missing_secret_fails_closed(monkeypatch):
    """Without a strong secret anyone reaching the port could mint a cookie."""
    for value in ("", "short", "x" * 31):
        monkeypatch.setenv("SESSION_SECRET", value)
        with pytest.raises(RuntimeError, match="at least 32"):
            auth.issue()


def test_a_round_tripped_session_is_readable():
    session = auth.read(auth.issue("owner"))
    assert session is not None and session.user == "owner"


def test_a_tampered_cookie_is_rejected():
    token = auth.issue("owner")
    # Flip a character in the payload, leaving the structure intact.
    body, _, sig = token.rpartition(".")
    tampered = (body[:-1] + ("A" if body[-1] != "A" else "B")) + "." + sig
    assert auth.read(tampered) is None


def test_a_cookie_signed_with_another_secret_is_rejected(monkeypatch):
    token = auth.issue("owner")
    monkeypatch.setenv("SESSION_SECRET", "y" * 40)
    assert auth.read(token) is None


def test_no_cookie_is_no_session():
    assert auth.read(None) is None
    assert auth.read("") is None
    assert auth.read("garbage") is None


def test_an_expired_cookie_is_rejected(monkeypatch):
    token = auth.issue("owner")
    monkeypatch.setattr(auth, "MAX_AGE", -1)
    assert auth.read(token) is None


# ------------------------------------------------------------- password

def test_the_right_password_verifies(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("correct horse"))
    assert auth.verify_password("correct horse") is True


def test_the_wrong_password_does_not(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("correct horse"))
    assert auth.verify_password("battery staple") is False


def test_no_configured_hash_refuses_every_login(monkeypatch):
    """An unset password must not mean 'no password required'."""
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
    assert auth.configured() is False
    assert auth.verify_password("") is False
    assert auth.verify_password("anything") is False


def test_a_malformed_stored_hash_refuses_rather_than_raising(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "not-an-argon2-hash")
    assert auth.verify_password("anything") is False


def test_the_hash_is_argon2_and_does_not_contain_the_password(monkeypatch):
    hashed = auth.hash_password("hunter2")
    assert hashed.startswith("$argon2")
    assert "hunter2" not in hashed


def test_repeated_failures_are_rate_limited(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("correct horse"))
    assert auth.rate_limited() is False
    for _ in range(auth.MAX_ATTEMPTS):
        auth.verify_password("wrong")
    assert auth.rate_limited() is True


# ------------------------- surviving a Docker Compose .env round trip

def test_a_hash_mangled_by_compose_is_reported_not_silently_rejected(monkeypatch):
    """Compose interpolates `$` in a .env file, and an argon2 hash is mostly `$`.

    The mangled value used to be indistinguishable from a wrong password: every
    correct password simply failed, forever, with nothing in the logs to say
    why.
    """
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH_B64", raising=False)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "")       # what compose leaves
    assert auth.configured() is False
    assert auth.verify_password("anything") is False

    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "argon2idv=19m=65536")
    assert auth.configured() is False, "a mangled hash must not read as configured"


def test_the_base64_form_survives_and_works(monkeypatch):
    import base64

    hashed = auth.hash_password("correct horse battery")
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH_B64",
                       base64.b64encode(hashed.encode()).decode())
    assert auth.configured() is True
    assert auth.verify_password("correct horse battery") is True
    assert auth.verify_password("wrong") is False


def test_malformed_base64_fails_closed(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH_B64", "!!!not base64!!!")
    assert auth.configured() is False
    assert auth.verify_password("anything") is False


def test_the_base64_form_wins_when_both_are_set(monkeypatch):
    import base64

    good = auth.hash_password("the real one")
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "")       # mangled by compose
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH_B64",
                       base64.b64encode(good.encode()).decode())
    assert auth.verify_password("the real one") is True
