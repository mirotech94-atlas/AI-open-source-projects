"""Data directory resolution for the Vault sensor.

Priority: GUARDIAN_DATA env var > ~/.guardian on macOS > /var/lib/guardian on
Linux (falling back to ~/.guardian if that path is not writable).
"""
import os
import sys


def _default_dir() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser("~/.guardian")
    candidate = "/var/lib/guardian"
    parent = os.path.dirname(candidate)
    if os.path.exists(candidate):
        writable = os.access(candidate, os.W_OK)
    else:
        writable = os.access(parent, os.W_OK)
    if writable:
        return candidate
    return os.path.expanduser("~/.guardian")


def _resolve() -> str:
    env = os.environ.get("GUARDIAN_DATA")
    if env:
        return env
    return _default_dir()


DATA_DIR = _resolve()


def ensure() -> str:
    """Create DATA_DIR (mode 0700) if it doesn't already exist, return it."""
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
    return DATA_DIR


def p(name: str) -> str:
    """Join name onto DATA_DIR."""
    return os.path.join(DATA_DIR, name)
