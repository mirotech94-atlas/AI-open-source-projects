"""Symmetric encryption for integration secrets (webhook URLs, bot tokens).

The key is derived from `settings.secret_key`, so nothing extra has to be
configured and no key material is stored in the database next to the
ciphertext. `decrypt` returns "" instead of raising on any value it cannot
open (rotated `secret_key`, hand-edited row, plaintext left by an older
build) so the UI degrades to "reconfigure this integration" rather than
500-ing a whole settings page.
"""

import base64, hashlib

from cryptography.fernet import Fernet

from .config import settings


def key() -> bytes:
    """Fernet key: urlsafe-base64 of sha256(secret_key). Computed per call so
    a settings change in-process (tests, `guardian` CLI) is picked up."""
    return base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return Fernet(key()).encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return Fernet(key()).decrypt(token.encode()).decode()
    except Exception:
        # InvalidToken on a wrong key, binascii.Error / ValueError on
        # non-base64 input, UnicodeDecodeError on garbage plaintext.
        return ""


def encrypt_json(value: dict) -> str:
    """Encrypt a whole `{name: plaintext}` dict as ONE Fernet blob.

    Used for `Integration.secrets_enc` (fix round 1, ruling a): every
    `kind="secret"` field beyond `fields[0]` is stored here rather than in
    the plaintext `config` JSON column, so a provider with two or three
    credentials (Home Assistant's MQTT password *and* long-lived REST
    token, PagerDuty's routing key *and* webhook signing secret) keeps all
    of them encrypted at rest. Empty dict -> "" (no ciphertext row at all),
    matching `encrypt("")`."""
    import json
    if not value:
        return ""
    return encrypt(json.dumps(value, sort_keys=True))


def decrypt_json(token: str) -> dict:
    """Inverse of `encrypt_json`. Returns `{}` — never raises — on a
    rotated key, hand-edited row, or anything that isn't a JSON object, for
    the same "degrade to reconfigure-this-integration" reason `decrypt`
    swallows `InvalidToken`."""
    import json
    raw = decrypt(token)
    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}
