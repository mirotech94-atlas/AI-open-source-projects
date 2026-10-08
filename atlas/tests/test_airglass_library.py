"""Air Glass's free 3D library: search by whole words, CC0/CC-BY only, honest
502s, and a file proxy that fetches only allow-listed hosts, by id, capped."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api import airglass_library as library
from atlas.api import auth

SECRET = "v" * 40
PASSWORD = "a-long-enough-password"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
KH = library.KH_BASE


def ph_files(slug: str, size: int = 1000, res: str = "1k") -> dict:
    base = f"https://dl.polyhaven.org/file/ph-assets/Models/gltf/{res}/{slug}"
    return {"gltf": {res: {"gltf": {
        "url": f"{base}/{slug}_{res}.gltf", "size": size,
        "include": {
            f"{slug}.bin": {"url": f"{base}/{slug}.bin", "size": size},
            f"textures/{slug}_diff_{res}.jpg": {
                "url": f"https://dl.polyhaven.org/file/ph-assets/Models/jpg/{slug}_diff.jpg",
                "size": size},
        }}}}}


LISTING = {
    "covered_car": {"name": "Covered Car", "type": 2, "tags": ["vehicle"],
                    "categories": ["props"], "authors": {"MP": "All"},
                    "download_count": 10, "description": "A car under a cover."},
    "carrot_cake": {"name": "Carrot Cake", "type": 2, "tags": ["food"],
                    "categories": ["food"], "authors": {"X": "All"}, "download_count": 99},
    "ArmChair_01": {"name": "Arm Chair 01", "type": 2, "tags": ["chair"],
                    "categories": ["furniture"], "authors": {"Kirill": "All"},
                    "download_count": 5},
    "giant_tree": {"name": "Giant Car Tree", "type": 2, "tags": [], "categories": [],
                   "authors": {}, "download_count": 1},
}
FILES = {"covered_car": ph_files("covered_car"), "carrot_cake": ph_files("carrot_cake"),
         "ArmChair_01": ph_files("ArmChair_01"),
         "giant_tree": ph_files("giant_tree", size=30 * 1024 * 1024)}

INDEX = [
    {"name": "ToyCar", "label": "Toy Car", "screenshot": "screenshot/screenshot.jpg",
     "tags": ["showcase"], "variants": {"glTF": "ToyCar.gltf", "glTF-Binary": "ToyCar.glb"}},
    {"name": "DamagedHelmet", "label": "Damaged Helmet", "screenshot": "screenshot/s.png",
     "tags": ["core"], "variants": {"glTF-Binary": "DamagedHelmet.glb"}},
    {"name": "SciFiHelmet", "label": "SciFi Helmet", "screenshot": "screenshot/s.jpg",
     "tags": ["core"], "variants": {"glTF": "SciFiHelmet.gltf"}},
    {"name": "DracoHelmet", "label": "Draco Helmet", "screenshot": "screenshot/s.jpg",
     "tags": [], "variants": {"glTF-Draco": "DracoHelmet.gltf"}},
    {"name": "LostHelmet", "label": "Lost Helmet", "screenshot": "screenshot/s.jpg",
     "tags": [], "variants": {"glTF-Binary": "LostHelmet.glb"}},
    {"name": "CarPaintTest", "label": "Car Paint Test", "screenshot": "s.jpg",
     "tags": ["testing"], "variants": {"glTF": "CarPaintTest.gltf"}},
    {"name": "ClearCoatCarPaint", "label": "Clear Coat Car Paint", "screenshot": "s.jpg",
     "tags": ["extension", "testing"], "variants": {"glTF-Binary": "ClearCoatCarPaint.glb"}},
]


def legal(*spdx: str) -> dict:
    return {"legal": [{"spdx": s, "artist": f"artist{i}", "year": "2020"}
                      for i, s in enumerate(spdx)]}


META = {"ToyCar": legal("CC0-1.0"), "DamagedHelmet": legal("CC-BY-4.0", "CC-BY-NC-4.0"),
        "SciFiHelmet": legal("CC-BY-4.0", "CC0-1.0"), "DracoHelmet": legal("CC0-1.0"),
        "CarPaintTest": legal("CC0-1.0")}


class Upstream:
    """A fake of the four hosts. `down` hosts raise ConnectError."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.down: set[str] = set()
        self.override: dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        host, path = req.url.host, req.url.path
        if host in self.down:
            raise httpx.ConnectError("down", request=req)
        for key, fn in self.override.items():
            if key in str(req.url):
                return fn(req)
        if host == "api.polyhaven.com" and path == "/assets":
            return httpx.Response(200, json=LISTING)
        if host == "api.polyhaven.com" and path.startswith("/files/"):
            slug = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=FILES[slug]) if slug in FILES \
                else httpx.Response(404)
        if host == "dl.polyhaven.org":
            return httpx.Response(200, content=b"PH:" + path.encode())
        if host == "cdn.polyhaven.com":
            return httpx.Response(200, content=PNG)
        if host == "raw.githubusercontent.com":
            rel = str(req.url).removeprefix(KH)
            if rel == "model-index.json":
                return httpx.Response(200, json=INDEX)
            name, _, rest = rel.partition("/")
            if rest == "metadata.json":
                return httpx.Response(200, json=META[name]) if name in META \
                    else httpx.Response(500)
            if rest.startswith("screenshot/"):
                return httpx.Response(200, content=b"\xff\xd8\xff\xe0" + b"\x00" * 32)
            return httpx.Response(200, content=b"KH:" + rel.encode())
        return httpx.Response(404)


@pytest.fixture
def upstream(monkeypatch) -> Upstream:
    up = Upstream()
    library.clear_cache()

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(up), follow_redirects=False,
                                 headers={"User-Agent": library.USER_AGENT})
    monkeypatch.setattr(library, "_client", client)
    yield up
    library.clear_cache()


@pytest.fixture
def app(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    from atlas.api.main import app as the_app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return the_app


@pytest.fixture
def client(app):
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


# ------------------------------------------------------------------- words

def test_words_split_camel_case_and_underscores_and_plurals():
    assert library.words("ToyCar") == ["toy", "car"]
    assert library.words("covered_car") == ["covered", "car"]
    assert library.words("SciFiHelmet") == ["sci", "fi", "helmet"]
    assert library.words("Chairs") == ["chair"]
    assert library.words("glass") == ["glass"]
    assert library.query_words("show me a car") == ["car"]


def test_score_is_whole_words_only():
    assert library.score(["car"], ["carrot", "cake"], []) == 0
    assert library.score(["car"], ["covered", "car"], []) == 2
    assert library.score(["car"], ["covered"], ["car"]) == 1
    assert library.score(["red", "car"], ["car"], []) == 0


@pytest.mark.parametrize("part", ["../x.bin", "/etc/passwd", "https://evil.example/x",
                                  "a/../../b", "a\\b", "", "x?y=1", "./a.bin"])
def test_safe_part_refuses_anything_but_a_plain_relative_file(part):
    with pytest.raises(library.Refused):
        library.safe_part(part)


def test_safe_part_keeps_a_texture_path():
    assert library.safe_part("textures/Arm_diff_1k.jpg") == "textures/Arm_diff_1k.jpg"


# ------------------------------------------------------------------ search

def test_search_finds_a_car_in_both_sources_by_whole_word(client, upstream):
    r = client.get("/api/airglass/library", params={"q": "car"})
    assert r.status_code == 200
    body = r.json()
    ids = [x["id"] for x in body["results"]]
    assert "polyhaven:covered_car" in ids and "khronos:ToyCar" in ids
    assert "polyhaven:carrot_cake" not in ids            # "carrot" is not "car"
    assert "khronos:CarPaintTest" not in ids             # test scenes are left out
    assert "khronos:ClearCoatCarPaint" not in ids        # a material test sphere
    assert "polyhaven:giant_tree" not in ids             # 90 MB: over the cap
    assert body["sources"] == {"polyhaven": "ok", "khronos": "ok"}
    assert body["read_at"]
    car = next(x for x in body["results"] if x["id"] == "polyhaven:covered_car")
    assert car["license"] == "CC0-1.0" and car["size_bytes"] == 3000
    assert "Poly Haven" in car["attribution"] and car["source"] == "Poly Haven"
    assert car["thumb"].startswith("/api/airglass/library/thumb?id=")
    assert car["url"].startswith("/api/airglass/library/file?id=")
    toy = next(x for x in body["results"] if x["id"] == "khronos:ToyCar")
    assert toy["main"] == "ToyCar.glb" and toy["size_bytes"] is None
    # Poly Haven's ToS 2.4: every call carries ATLAS's own User-Agent.
    assert all(q.headers["user-agent"] == library.USER_AGENT for q in upstream.seen)


def test_khronos_offers_only_models_whose_every_license_is_open(client, upstream):
    body = client.get("/api/airglass/library", params={"q": "helmet"}).json()
    ids = {x["id"]: x for x in body["results"]}
    assert "khronos:DamagedHelmet" not in ids            # has CC-BY-NC
    assert "khronos:DracoHelmet" not in ids              # Draco only: no decoder here
    assert "khronos:LostHelmet" not in ids               # metadata unreadable: unknown
    sci = ids["khronos:SciFiHelmet"]
    assert sci["license"] == "CC-BY-4.0" and sci["credit_required"] is True
    assert "artist0" in sci["attribution"] and sci["main"] == "SciFiHelmet.gltf"


def test_nothing_found_with_both_sources_up_is_an_honest_empty_list(client, upstream):
    body = client.get("/api/airglass/library", params={"q": "heart"}).json()
    assert body["results"] == []
    assert body["sources"] == {"polyhaven": "ok", "khronos": "ok"}


def test_both_sources_unreachable_is_a_502_naming_the_hosts(client, upstream):
    upstream.down = {"api.polyhaven.com", "raw.githubusercontent.com"}
    r = client.get("/api/airglass/library", params={"q": "car"})
    assert r.status_code == 502
    assert "did not answer" in r.json()["detail"]
    assert "api.polyhaven.com" in r.json()["detail"]
    assert "raw.githubusercontent.com" in r.json()["detail"]


def test_one_source_down_is_flagged_not_hidden(client, upstream):
    upstream.down = {"api.polyhaven.com"}
    body = client.get("/api/airglass/library", params={"q": "car"}).json()
    assert body["sources"]["polyhaven"] == "the library at api.polyhaven.com did not answer"
    assert body["sources"]["khronos"] == "ok"
    assert [x["id"] for x in body["results"]] == ["khronos:ToyCar"]


def test_an_error_status_counts_as_not_answering(client, upstream):
    upstream.override["api.polyhaven.com/assets"] = lambda req: httpx.Response(503)
    upstream.override["model-index.json"] = lambda req: httpx.Response(500)
    r = client.get("/api/airglass/library", params={"q": "car"})
    assert r.status_code == 502


def test_a_redirect_is_not_followed(client, upstream):
    upstream.override["api.polyhaven.com/assets"] = lambda req: httpx.Response(
        302, headers={"location": "https://evil.example/assets"})
    upstream.down = {"raw.githubusercontent.com"}
    r = client.get("/api/airglass/library", params={"q": "car"})
    assert r.status_code == 502
    assert not any(q.url.host == "evil.example" for q in upstream.seen)


def test_search_needs_the_session(app, upstream):
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        assert c.get("/api/airglass/library", params={"q": "car"}).status_code in (401, 403)
        assert c.get("/api/airglass/library/file",
                     params={"id": "polyhaven:covered_car"}).status_code in (401, 403)
    assert upstream.seen == []


# ------------------------------------------------------------------- files

def test_file_serves_the_gltf_and_the_parts_it_names(client, upstream):
    r = client.get("/api/airglass/library/file", params={"id": "polyhaven:ArmChair_01"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("model/gltf+json")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content.endswith(b"/ArmChair_01_1k.gltf")
    r = client.get("/api/airglass/library/file", params={
        "id": "polyhaven:ArmChair_01", "part": "textures/ArmChair_01_diff_1k.jpg"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = client.get("/api/airglass/library/file", params={"id": "khronos:ToyCar"})
    assert r.content == b"KH:ToyCar/glTF-Binary/ToyCar.glb"
    assert r.headers["content-type"] == "application/octet-stream"
    r = client.get("/api/airglass/library/file",
                   params={"id": "khronos:SciFiHelmet", "part": "SciFiHelmet.bin"})
    assert r.content == b"KH:SciFiHelmet/glTF/SciFiHelmet.bin"


def test_upstream_content_type_is_never_passed_through(client, upstream):
    upstream.override["dl.polyhaven.org"] = lambda req: httpx.Response(
        200, content=b"<script>alert(1)</script>", headers={"content-type": "text/html"})
    r = client.get("/api/airglass/library/file", params={"id": "polyhaven:ArmChair_01"})
    assert r.headers["content-type"].startswith("model/gltf+json")


@pytest.mark.parametrize("params,status", [
    ({"id": "polyhaven:ArmChair_01", "part": "not_listed.bin"}, 400),
    ({"id": "polyhaven:ArmChair_01", "part": "../../etc/passwd"}, 400),
    ({"id": "polyhaven:ArmChair_01", "part": "https://evil.example/x.bin"}, 400),
    ({"id": "khronos:ToyCar", "part": "../DamagedHelmet/x.glb"}, 400),
    ({"id": "khronos:ToyCar", "part": "page.html"}, 400),
    ({"id": "khronos:DamagedHelmet"}, 404),
    ({"id": "https://evil.example/x.glb"}, 404),
    ({"id": "polyhaven:not_in_listing"}, 404),
    ({"id": "elsewhere:ToyCar"}, 404),
])
def test_file_refuses_what_is_not_a_listed_file_of_a_listed_model(
        client, upstream, params, status):
    r = client.get("/api/airglass/library/file", params=params)
    assert r.status_code == status
    assert not any(q.url.host == "evil.example" for q in upstream.seen)
    assert not any("DamagedHelmet/" in str(q.url) and "metadata" not in str(q.url)
                   for q in upstream.seen)


def test_file_over_the_declared_cap_is_refused(client, upstream, monkeypatch):
    monkeypatch.setattr(library, "MAX_FILE_BYTES", 100)
    upstream.override["dl.polyhaven.org"] = lambda req: httpx.Response(
        200, content=b"x" * 101)
    r = client.get("/api/airglass/library/file", params={"id": "polyhaven:ArmChair_01"})
    assert r.status_code == 413


def test_file_over_the_cap_while_streaming_is_refused(client, upstream, monkeypatch):
    monkeypatch.setattr(library, "MAX_FILE_BYTES", 100)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"y" * 40

    upstream.override["dl.polyhaven.org"] = lambda req: httpx.Response(
        200, content=chunks())
    r = client.get("/api/airglass/library/file", params={"id": "polyhaven:ArmChair_01"})
    assert r.status_code == 413


def test_file_source_down_is_a_502(client, upstream):
    client.get("/api/airglass/library", params={"q": "chair"})   # listing cached
    upstream.down = {"dl.polyhaven.org"}
    r = client.get("/api/airglass/library/file", params={"id": "polyhaven:ArmChair_01"})
    assert r.status_code == 502
    assert r.json()["detail"] == "the library at dl.polyhaven.org did not answer"


async def test_fetch_refuses_a_host_off_the_allow_list(upstream):
    async with library._client() as c:
        for url in ("https://evil.example/x", "http://dl.polyhaven.org/x",
                    "file:///etc/passwd"):
            with pytest.raises(library.Refused):
                await library.fetch(c, url, 1000)
    assert upstream.seen == []


def test_a_listing_url_on_another_host_is_not_offered(client, upstream):
    evil = json.loads(json.dumps(FILES["covered_car"]))
    evil["gltf"]["1k"]["gltf"]["url"] = "https://evil.example/covered_car.gltf"
    upstream.override["/files/covered_car"] = lambda req: httpx.Response(200, json=evil)
    body = client.get("/api/airglass/library", params={"q": "covered car"}).json()
    assert "polyhaven:covered_car" not in [x["id"] for x in body["results"]]


# ------------------------------------------------------------------ thumbs

def test_thumb_is_typed_by_its_own_bytes(client, upstream):
    r = client.get("/api/airglass/library/thumb", params={"id": "polyhaven:ArmChair_01"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = client.get("/api/airglass/library/thumb", params={"id": "khronos:ToyCar"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_thumb_that_is_not_a_picture_is_refused(client, upstream):
    upstream.override["cdn.polyhaven.com"] = lambda req: httpx.Response(
        200, content=b"<html>hi</html>", headers={"content-type": "image/png"})
    r = client.get("/api/airglass/library/thumb", params={"id": "polyhaven:ArmChair_01"})
    assert r.status_code == 502
