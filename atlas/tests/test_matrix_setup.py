"""matrix-setup. A Matrix server name and its client API base URL are
different things, and confusing them produces a 404 that looks like a broken
homeserver."""
from __future__ import annotations

import pytest

from atlas import cli


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code = status
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def fake_client(monkeypatch, routes: dict[str, FakeResponse], seen: list[str]):
    class Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, **_k):
            seen.append(url)
            for fragment, response in routes.items():
                if fragment in url:
                    return response
            return FakeResponse(404)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)


async def test_a_bare_hostname_gets_a_scheme(monkeypatch):
    """httpx raises UnsupportedProtocol on a URL with no scheme, so a typed
    hostname must be normalised before any request is made."""
    seen: list[str] = []
    fake_client(monkeypatch, {"_matrix/client/versions": FakeResponse(200)}, seen)

    result = await cli._resolve_homeserver("chat.example.org")
    assert result == "https://chat.example.org"
    assert all(u.startswith("https://") for u in seen), seen


async def test_a_server_name_is_resolved_to_its_api_host(monkeypatch):
    """example.org is the server name; the API lives at chat.example.org. Only the
    well-known lookup knows that."""
    seen: list[str] = []
    fake_client(monkeypatch, {
        ".well-known/matrix/client": FakeResponse(
            200, {"m.homeserver": {"base_url": "https://chat.example.org"}}),
        "_matrix/client/versions": FakeResponse(200),
    }, seen)

    assert await cli._resolve_homeserver("https://example.org") == "https://chat.example.org"


async def test_a_host_that_does_not_answer_the_api_is_refused(monkeypatch):
    """Better to stop than to write a homeserver into .env that 404s."""
    seen: list[str] = []
    fake_client(monkeypatch, {"_matrix/client/versions": FakeResponse(404)}, seen)

    with pytest.raises(SystemExit, match="not answering the Matrix client API"):
        await cli._resolve_homeserver("https://example.org")


async def test_no_well_known_is_fine_when_the_host_answers_directly(monkeypatch):
    seen: list[str] = []
    fake_client(monkeypatch, {"_matrix/client/versions": FakeResponse(200)}, seen)
    assert await cli._resolve_homeserver("https://matrix.example") == "https://matrix.example"


def test_help_does_not_require_a_password():
    """`--help` used to fall through into the interactive prompt."""
    assert "--token" in cli.MATRIX_SETUP_HELP
    assert "--admin-token" in cli.MATRIX_SETUP_HELP
    # The guidance that matters most.
    assert "dedicated account" in cli.MATRIX_SETUP_HELP
