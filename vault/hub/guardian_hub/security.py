import hashlib, hmac, re, secrets
from .config import settings

def hash_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()

def new_token() -> str: return secrets.token_hex(32)
def hash_token(t: str) -> str: return hashlib.sha256(t.encode()).hexdigest()
def new_code() -> str: return f"{secrets.randbelow(10**6):06d}"
#: No O/0 and no I/1. A code is read aloud down a phone line and typed at the
#: other end, and an alphabet containing both members of either pair means a
#: mistyped code can be a DIFFERENT code that also exists.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_enrol_code() -> str:
    """The code shown on a screen. Eight characters, fifteen minutes."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def new_install_code() -> str:
    """The code that goes in an e-mail, and lives days rather than minutes.

    Sixteen characters rather than eight, and the reason is the lookup in
    `routers/sensor.enrol`: it matches on the code ALONE, with no tenant, so
    every unredeemed code on the platform is guessable at the same time. A
    fifteen-minute window keeps that set nearly empty. A seven-day window --
    which an e-mailed code needs, because the delay between "the manager signed
    up" and "the person with server access read their inbox" is the whole point
    of sending one -- does not.

    So this is a different credential rather than the same one held open
    longer. 32**16 is about 1.2e24 against 32**8's 1.1e12, which absorbs the
    longer life with room to spare, and the extra length costs the reader
    nothing: nobody types this. It is embedded in the install command the
    e-mail carries.
    """
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(16))


# --------------------------------------------------------------------------
# Credential redaction for prose
# --------------------------------------------------------------------------
#
# Alert messages are written by the sensor and stored on `Alert.msg`, and
# from there they reach push notifications, chat integrations, built reports
# and shareable `/r/<token>` links. The sensor no longer puts a heartbeat
# token in one (`sensor/guardian_sensor/alerts.py`) -- but rows written
# before that fix are still in the database, and a hub on an older sensor
# build still receives them. This is the belt to that fix's braces: one
# helper, used wherever stored prose is handed onward.

_HB_TOKEN_RE = re.compile(r"(/hb/[^/\s`'\"]+/)[^\s`'\"]+")

#: What replaces the credential. Deliberately a sentence fragment rather
#: than "***": the reader needs to know where the real command is.
HB_TOKEN_PLACEHOLDER = r"\1<token — see Security → Heartbeats>"


def redact_prose(text: str) -> str:
    """Strip the one credential shape that has ever been written into an
    alert message: a heartbeat ping URL with its token attached."""
    if not text or "/hb/" not in text:
        return text
    return _HB_TOKEN_RE.sub(HB_TOKEN_PLACEHOLDER, text)
