"""Defensive HTTP for modules that talk to admin APIs.

Two jobs, both of which exist because of things that are easy to get wrong in
every single module and expensive to get wrong even once:

  * **Never leak a secret into an Observation.** Several APIs take their token
    as a query parameter, and an httpx exception stringifies the full URL. That
    detail string goes into Postgres and then into a reasoner prompt. The
    reasoner is supposed to see "the flycheap admin API returned 401", never the
    key. Everything that can reach an Observation goes through `redact` first.

  * **A shape we did not expect is UNKNOWN, not OK and not FAILING.** These
    modules were written against admin APIs whose responses have not been
    confirmed. A missing field must not be defaulted into a number that reads as
    a healthy — or an alarming — measurement. `expect_keys` and `number` say so
    explicitly.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

# Environment variables whose VALUES must never appear in an Observation.
_SECRET_HINT = re.compile(
    r"(TOKEN|KEY|SECRET|PASSWORD|PASSWD|PASS|CREDENTIAL|WEBHOOK|DSN|DATABASE_URL)",
    re.IGNORECASE)

# Env vars whose VALUE is a secret-bearing URL although the name says only
# "URL" — so `_SECRET_HINT` cannot see them. The whole URL is masked, and so
# are its pieces (long path segments, query values), because a redirect or a
# re-encoded request line carries the token without the rest of the URL.
SECRET_URL_ENV: frozenset[str] = frozenset({"BRIGHTSPACE_ICS_URL"})

# Query parameters that carry credentials, redacted by name as a second net in
# case a secret reaches us from somewhere other than the environment. `x_a`..
# `x_d` are D2L Valence's ID-key parameters (app id, user id, two signatures).
_SENSITIVE_PARAM = re.compile(
    r"([?&](?:access_token|token|api[_-]?key|key|apikey|password|passwd|sig|"
    r"signature|auth|secret|x_[a-d])=)[^&\s\"']+",
    re.IGNORECASE)

# Bearer/Basic values that might appear in a stringified header dump.
_AUTH_HEADER = re.compile(
    r"((?:bearer|basic|private-token)\s+)[A-Za-z0-9._\-+/=]{6,}", re.IGNORECASE)


def secret_values() -> list[str]:
    """Current environment values that must never be printed."""
    out = []
    for name, value in os.environ.items():
        if value and len(value) >= 8 and _SECRET_HINT.search(name):
            out.append(value)
        elif value and name in SECRET_URL_ENV:
            out.extend(_url_pieces(value))
    # Longest first, so a value containing another is masked whole.
    return sorted(set(out), key=len, reverse=True)


def _url_pieces(url: str) -> list[str]:
    parts = urlsplit(url)
    pieces = [url, url.split("://", 1)[-1]]
    if parts.path and parts.path != "/":
        pieces.append(parts.path)
    if parts.query:
        pieces.append(parts.query)
    pieces += [seg for seg in parts.path.split("/") if len(seg) >= 12]
    pieces += [v for _k, v in parse_qsl(parts.query) if len(v) >= 6]
    return [p for p in pieces if p]


def redact(text: Any) -> str:
    """Strip anything credential-shaped out of a string bound for an Observation."""
    result = str(text)
    for value in secret_values():
        if value in result:
            result = result.replace(value, "<redacted>")
    result = _SENSITIVE_PARAM.sub(r"\1<redacted>", result)
    result = _AUTH_HEADER.sub(r"\1<redacted>", result)
    return result


def redact_stored(text: Any) -> str:
    """The net for free text ATLAS stores for good (the audit chain, an
    observation's detail): every current secret VALUE, and credential query
    parameters, masked. Not the Bearer/Basic heuristic `redact` adds -- in
    stored prose "basic health check" is not a header, and a hash-chained
    row cannot be corrected afterwards."""
    result = str(text)
    for value in secret_values():
        if value in result:
            result = result.replace(value, "<redacted>")
    return _SENSITIVE_PARAM.sub(r"\1<redacted>", result)


def explain(exc: BaseException) -> str:
    """A useful, secret-free description of why a call failed."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hint = {
            401: "credentials rejected — check the module's token",
            403: "authenticated but not permitted",
            404: ("not found — a wrong path, or the provider serves nothing "
                  "for this request (an LLM router with no endpoint for the "
                  "model and its tools answers 404 too)"),
            429: "rate limited",
        }.get(code, "")
        path = redact(str(exc.request.url.path))
        return f"HTTP {code} from {path}{' — ' + hint if hint else ''}"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    if isinstance(exc, httpx.HTTPError):
        return f"unreachable: {redact(type(exc).__name__)}"
    return redact(f"{type(exc).__name__}: {exc}")


async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = 25.0,
    base_url: str = "",
) -> tuple[Any, str | None]:
    """Shared body of `get_json` and `post_json`. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=timeout,
                                     base_url=base_url or "") as client:
            response = await client.request(
                method, url, headers=headers, params=params,
                json=json_body, data=data)
            response.raise_for_status()
    except Exception as exc:                            # noqa: BLE001
        return None, explain(exc)

    try:
        return response.json(), None
    except ValueError:
        body = redact(response.text[:120].replace("\n", " "))
        ctype = response.headers.get("content-type", "unknown")
        return None, (f"expected JSON, got {ctype} — the assumed endpoint is "
                      f"probably wrong. First bytes: {body!r}")


async def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 25.0,
    base_url: str = "",
) -> tuple[Any, str | None]:
    """GET and parse JSON. Returns `(data, None)` or `(None, reason)`.

    Never raises. A caller turning `reason` into an Observation should use
    `Health.UNKNOWN`: we did not measure the thing, so we do not know.
    """
    return await _request("GET", url, headers=headers, params=params,
                          timeout=timeout, base_url=base_url)


async def post_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = 25.0,
    base_url: str = "",
) -> tuple[Any, str | None]:
    """POST and parse JSON. Same contract as `get_json`, including never
    raising and never leaking a credential into `reason`.

    It exists because some APIs worth probing are POST-only — Slack's
    `auth.test` is the one this repo needs — and reaching for `httpx`
    directly to work around that is how a call ends up bypassing `redact()`
    on its way into an Observation.
    """
    return await _request("POST", url, headers=headers, params=params,
                          json_body=json_body, data=data,
                          timeout=timeout, base_url=base_url)


async def get_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    max_bytes: int,
    timeout: float = 60.0,
) -> tuple[bytes | None, str | None]:
    """GET a binary body -- a voice note, a media file -- up to `max_bytes`.

    Same contract as `get_json`: never raises, and a failure comes back as a
    reason that has been through `explain()`, so a token carried in the URL
    path (e.g. `/file/bot<token>/...`) never reaches a
    log line or a stored row. A body larger than `max_bytes` is abandoned
    mid-stream rather than read into memory whole.
    """
    try:
        async with (httpx.AsyncClient(timeout=timeout) as client,
                    client.stream("GET", url, headers=headers,
                                  params=params) as response):
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    return None, f"file too large (over {max_bytes} bytes)"
                chunks.append(chunk)
    except Exception as exc:                            # noqa: BLE001
        return None, explain(exc)
    return b"".join(chunks), None


def expect_keys(data: Any, *keys: str, where: str = "response") -> str | None:
    """Return a reason if `data` is not a dict containing every key."""
    if not isinstance(data, dict):
        return (f"{where} was {type(data).__name__}, expected an object with "
                f"{', '.join(keys)}")
    missing = [k for k in keys if k not in data]
    if missing:
        present = ", ".join(sorted(data)[:8]) or "nothing"
        return (f"{where} is missing {', '.join(missing)} — the assumed shape is "
                f"wrong. Got: {present}")
    return None


def number(data: dict, key: str, *, where: str = "response") -> tuple[float | None, str | None]:
    """Read a numeric field without inventing a default.

    Defaulting a missing field to 0 or 1e9 turns a shape mismatch into a
    confident measurement, which is how a broken collector ends up reporting a
    number nobody should trust.
    """
    if key not in data:
        return None, f"{where} has no {key!r}"
    raw = data[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None, f"{where}[{key!r}] is {type(raw).__name__}, expected a number"
    try:
        return float(raw), None
    except (TypeError, ValueError):
        return None, f"{where}[{key!r}] is not numeric: {redact(raw)!r}"


def first_list(data: Any, *candidates: str) -> tuple[list | None, str | None]:
    """Find a list under any of `candidates`, or explain what was there."""
    if isinstance(data, list):
        return data, None
    if not isinstance(data, dict):
        return None, f"expected a list or object, got {type(data).__name__}"
    for key in candidates:
        if isinstance(data.get(key), list):
            return data[key], None
    present = ", ".join(sorted(data)[:8]) or "nothing"
    return None, (f"no list under {' or '.join(candidates)} — the assumed shape "
                  f"is wrong. Got: {present}")


def env_missing(*names: str) -> list[str]:
    return [n for n in names if not os.getenv(n)]


def iter_chunks(items: Iterable, size: int) -> Iterable[list]:
    chunk: list = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


# ------------------------------------------------------------------ logging

class RedactingFilter(logging.Filter):
    """Runs `redact` over a record's message and arguments before any handler
    formats it. httpx logs every request line at INFO with the full URL —
    query-string tokens, a feed URL's path token, Valence signatures — and
    passes that URL as an `httpx.URL`, not a str, so non-str arguments are
    stringified and replaced whenever redaction changes them.

    Tracebacks too: `log.exception` attaches `exc_info`, and an httpx error's
    message carries its URL -- a query-string token printed in the
    traceback's last line. The traceback is formatted HERE, redacted into
    `exc_text` (which every stdlib Formatter prints in place of formatting
    it again), and `exc_info` is dropped so no later formatter can render
    the raw one. `stack_info` is redacted the same way."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
            elif record.args:
                record.args = tuple(_redact_arg(a) for a in record.args)
            if record.exc_info and record.exc_info[0] is not None:
                record.exc_text = redact(
                    logging.Formatter().formatException(record.exc_info))
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        # Logging from inside a logging filter would recurse; a record that
        # could not be redacted is still better emitted than silently lost.
        except Exception:                               # noqa: BLE001, S110
            pass
        return True


def _redact_arg(arg: Any) -> Any:
    if arg is None or isinstance(arg, (bool, int, float)):
        return arg
    text = str(arg)
    cleaned = redact(text)
    return cleaned if (cleaned != text or isinstance(arg, str)) else arg


def install_log_redaction() -> RedactingFilter:
    """Idempotent. On the root logger and each of its handlers (a logger's own
    filters do not run for records propagated from children; a handler's do),
    and on `httpx`/`httpcore` themselves, which are also raised to WARNING so
    their per-request INFO lines are not emitted at all."""
    root = logging.getLogger()
    existing = next((f for f in root.filters if isinstance(f, RedactingFilter)), None)
    flt = existing or RedactingFilter()
    targets: list[logging.Filterer] = [root, *root.handlers]
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        targets.append(lg)
    for target in targets:
        if not any(isinstance(f, RedactingFilter) for f in target.filters):
            target.addFilter(flt)
    return flt
