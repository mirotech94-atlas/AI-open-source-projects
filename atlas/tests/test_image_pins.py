"""What the images install is pinned, not whatever the registry serves that
day (audit #27)."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pip_block() -> str:
    text = (ROOT / "Dockerfile").read_text()
    m = re.search(r"^RUN pip install(?:[^\n]*\\\n)*[^\n]*", text, re.MULTILINE)
    assert m, "the Dockerfile's pip install"
    return m.group(0)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _constraints() -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in (ROOT / "constraints.txt").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!_-]+)", line)
        assert m, f"not an exact pin: {raw!r}"
        pins[_norm(m.group(1))] = m.group(2)
    return pins


def test_the_image_installs_against_the_constraints_file():
    block = _pip_block()
    assert "-c constraints.txt" in block
    dockerfile = (ROOT / "Dockerfile").read_text()
    copy = re.search(r"^COPY [^\n]*constraints\.txt[^\n]*$", dockerfile, re.MULTILINE)
    assert copy and dockerfile.index(copy.group(0)) < dockerfile.index(block)
    assert "constraints.txt" not in (ROOT / ".dockerignore").read_text()


def test_every_top_level_package_is_pinned_exactly():
    pins = _constraints()
    names = [re.match(r"[A-Za-z0-9._-]+", spec).group(0)
             for spec in re.findall(r'"([^"]+)"', _pip_block())]
    assert len(names) >= 10
    missing = [n for n in names if _norm(n) not in pins]
    assert not missing, f"add these to constraints.txt: {missing}"


def test_no_compose_service_floats_on_latest():
    compose = (ROOT / "docker-compose.yml").read_text()
    images = re.findall(r"^\s*image:\s*(\S+)", compose, re.MULTILINE)
    assert images
    for image in images:
        assert not image.endswith(":latest") and ":latest@" not in image, image
        assert ":" in image.split("/")[-1], f"{image} has no tag"


def test_no_ollama_image_is_pulled_at_all():
    """The local model is qwen3.8 on the Spark's vLLM (2026-10-28); there is
    no Ollama container left to pin."""
    compose = (ROOT / "docker-compose.yml").read_text()
    assert not re.search(r"^\s*image:\s*ollama/", compose, re.MULTILINE)
