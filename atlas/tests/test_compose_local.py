"""T1: the Mac override publishes nothing but loopback.

`docker compose -f docker-compose.yml -f docker-compose.local.yml` must never
start the server's Caddy (80/443 on every interface), never publish Postgres
or Redis, and never bind 0.0.0.0 or an empty host IP. The only published
ports are the API on 127.0.0.1:8080, the opt-in shop on 127.0.0.1:8081 and the
opt-in WhatsApp pairing page on 127.0.0.1:8091.

Two proofs: a merge of the two YAML files the way compose merges them (runs
everywhere, no docker needed), and `docker compose config` itself when the
CLI is on PATH.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = ROOT / "docker-compose.yml"
LOCAL = ROOT / "docker-compose.local.yml"
ALLOWED = {("127.0.0.1", "8080"), ("127.0.0.1", "8081"), ("127.0.0.1", "8091")}


def _load(path: pathlib.Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _target(volume: Any) -> str:
    if isinstance(volume, dict):
        return str(volume["target"])
    text = str(volume)
    for mode in (":ro", ":rw"):
        text = text.removesuffix(mode)
    # The source may itself hold a colon (`${VAR:?message}`); the target is
    # always the last field once the mode is gone.
    return text.rsplit(":", 1)[-1]


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Compose's merge for the keys this test reads: `ports` concatenate,
    `volumes` merge by container target, everything else is replaced."""
    services = {k: dict(v) for k, v in base.get("services", {}).items()}
    for name, svc in over.get("services", {}).items():
        merged = services.setdefault(name, {})
        for key, value in svc.items():
            if key == "ports":
                merged["ports"] = list(merged.get("ports", [])) + list(value)
            elif key == "volumes":
                by_target = {_target(v): v for v in merged.get("volumes", [])}
                for v in value:
                    by_target[_target(v)] = v
                merged["volumes"] = list(by_target.values())
            else:
                merged[key] = value
    return services


def _active(services: dict[str, Any], profiles: set[str]) -> dict[str, Any]:
    return {n: s for n, s in services.items()
            if not s.get("profiles") or set(s["profiles"]) & profiles}


def _host_bindings(port: Any) -> tuple[str, str]:
    """(host_ip, published) of a short-syntax port string or a long-syntax
    dict. A missing host IP comes back as ''."""
    if isinstance(port, dict):
        return str(port.get("host_ip") or ""), str(port.get("published") or "")
    parts = str(port).split(":")
    if len(parts) == 3:
        return parts[0], parts[1]
    if len(parts) == 2:
        return "", parts[0]
    return "", ""


def _assert_loopback_only(active: dict[str, Any]) -> None:
    assert "caddy" not in active, "the server Caddy must never start on the Mac"
    for name in ("postgres", "redis"):
        assert not active[name].get("ports"), f"{name} must not be published"
    seen = set()
    for name, svc in active.items():
        for port in svc.get("ports", []) or []:
            host_ip, published = _host_bindings(port)
            assert host_ip == "127.0.0.1", f"{name} publishes {port!r} off loopback"
            assert host_ip not in ("", "0.0.0.0", "::")
            seen.add((host_ip, published))
    assert seen <= ALLOWED, seen
    assert ("127.0.0.1", "8080") in seen


def test_override_exists_and_base_is_unchanged_in_shape():
    over = _load(LOCAL)
    assert over["services"]["caddy"]["profiles"] == ["server"]
    assert over["services"]["shop"]["profiles"] == ["shop"]
    # The override never redeclares api ports: compose concatenates them.
    assert "ports" not in over["services"].get("api", {})


def test_merged_default_and_shop_profiles_publish_loopback_only():
    services = _merge(_load(BASE), _load(LOCAL))
    assert services["caddy"]["profiles"] == ["server"]
    for profiles in (set(), {"shop"}, {"shop", "local-llm"}):
        _assert_loopback_only(_active(services, profiles))
    shop = _active(services, {"shop"})["shop"]
    assert shop["image"].startswith("caddy:2")
    assert shop["ports"] == ["127.0.0.1:8081:80"]
    assert any(_target(v) == "/etc/caddy/Caddyfile" for v in shop["volumes"])
    assert any(_target(v) == "/srv/shop" for v in shop["volumes"])


def test_state_volumes_come_from_atlas_state_dir_without_a_default():
    services = _merge(_load(BASE), _load(LOCAL))
    for name, target in (("postgres", "/var/lib/postgresql/data"),):
        sources = [str(v).removesuffix(":ro").rsplit(":", 1)[0]
                   for v in services[name]["volumes"]
                   if _target(v) == target]
        assert len(sources) == 1
        assert sources[0].startswith("${ATLAS_STATE_DIR:?"), sources[0]
        assert "/var/lib/atlas" not in sources[0]


def test_no_ollama_service_anywhere():
    """The local model is qwen3.8 on the Spark's vLLM, never Ollama."""
    assert "ollama" not in _merge(_load(BASE), _load(LOCAL))


def test_caddyfile_local_serves_the_build_and_proxies_public_to_api():
    text = (ROOT / "Caddyfile.local").read_text(encoding="utf-8")
    assert "http://localhost:8081" in text
    assert "421" in text
    assert "root * /srv/shop/current" in text
    assert "file_server" in text
    assert "handle /public/*" in text
    assert "reverse_proxy api:8080" in text
    assert "header_up Host api:8080" in text


@pytest.mark.skipif(shutil.which("docker") is None, reason="no docker CLI")
def test_docker_compose_config_agrees(tmp_path):
    version = subprocess.run(["docker", "compose", "version"], capture_output=True,
                             check=False)
    if version.returncode != 0:
        pytest.skip("docker compose plugin not available")
    for path in (BASE, LOCAL):
        shutil.copy(path, tmp_path / path.name)
    (tmp_path / ".env").write_text("ATLAS_STATE_DIR=/tmp/atlas-state-test\n")
    out = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "-f",
         "docker-compose.local.yml", "--profile", "shop", "config",
         "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr[-500:]
    services = json.loads(out.stdout)["services"]
    assert "shop" in services
    _assert_loopback_only(services)


# ------------------------------------------------------------ qa-browser
# Phase 4 (six agents): screenshots and PDFs for QA, opt-in, inside the compose
# network only. The api image already ships Playwright for modules/browser, so
# "the api has no Playwright" is not asserted; the QA service is held apart.

def _qa() -> dict[str, Any]:
    return _merge(_load(BASE), _load(LOCAL))["qa-browser"]


def test_qa_browser_is_opt_in_and_publishes_nothing():
    qa = _qa()
    assert qa["profiles"] == ["qa-browser"]
    assert not qa.get("ports"), "qa-browser must never publish a port"
    assert not qa.get("expose"), "qa-browser answers on a Unix socket only"
    services = _merge(_load(BASE), _load(LOCAL))
    for profiles in ({"qa-browser"}, {"qa-browser", "shop"}):
        _assert_loopback_only(_active(services, profiles))
    assert "qa-browser" not in _active(services, set())


def test_qa_browser_has_no_env_file_no_secret_and_only_its_script():
    qa = _qa()
    assert "env_file" not in qa
    assert qa["environment"] == {"QA_PROXY": "http://qa-proxy:3128"}
    assert qa["image"] == "mcr.microsoft.com/playwright/python:v1.48.0-noble"
    volumes = [str(v) for v in qa["volumes"]]
    assert volumes == ["./atlas/modules/qa/browser_service.py:/qa/browser_service.py:ro",
                       "qa_sock:/run/qa"]
    assert qa["read_only"] is True and qa["user"] == "pwuser"
    assert "no-new-privileges:true" in qa["security_opt"]


def test_qa_browser_is_alone_on_its_own_network():
    """No route to the api, Postgres, Redis or the mesh."""
    services = _merge(_load(BASE), _load(LOCAL))
    assert _qa()["networks"] == ["qa_egress"]
    for name, svc in services.items():
        if name in ("qa-browser", "qa-proxy"):
            continue
        nets = svc.get("networks") or ["default"]
        assert "qa_egress" not in nets, f"{name} joins the qa-browser network"
        assert not svc.get("network_mode"), name
    assert "qa_egress" in _load(LOCAL)["networks"]


def test_the_qa_network_is_internal_and_its_only_exit_is_the_proxy():
    """The egress deny is in the compose file: qa_egress has no gateway off
    the host, and qa-proxy (CONNECT to allowlisted hosts on 443 only) is the
    one member with a second network."""
    local = _load(LOCAL)
    assert local["networks"]["qa_egress"]["internal"] is True
    assert not local["networks"]["qa_out"].get("internal")
    services = _merge(_load(BASE), local)
    proxy = services["qa-proxy"]
    assert proxy["profiles"] == ["qa-browser"]
    assert sorted(proxy["networks"]) == ["qa_egress", "qa_out"]
    assert not proxy.get("ports") and not proxy.get("expose")
    assert "env_file" not in proxy and not proxy.get("environment")
    assert [str(v) for v in proxy["volumes"]] == [
        "./atlas/modules/qa/egress_proxy.py:/qa/egress_proxy.py:ro"]
    assert proxy["read_only"] is True and "no-new-privileges:true" in proxy["security_opt"]
    for name, svc in services.items():
        if name != "qa-proxy":
            assert "qa_out" not in (svc.get("networks") or []), name
    assert "qa-proxy" not in _active(services, set())


def test_only_the_api_shares_the_socket_volume_read_only():
    services = _merge(_load(BASE), _load(LOCAL))
    sharing = sorted(n for n, svc in services.items()
                     if any(str(v).startswith("qa_sock:") for v in svc.get("volumes") or []))
    assert sharing == ["api", "qa-browser"]
    api_volumes = [str(v) for v in _load(LOCAL)["services"]["api"]["volumes"]]
    assert "qa_sock:/run/qa:ro" in api_volumes


def test_the_api_gets_qa_browser_url_only_from_env():
    api = _load(LOCAL)["services"]["api"]
    assert api["environment"]["QA_BROWSER_URL"] == "${QA_BROWSER_URL:-}"


def test_the_service_script_imports_nothing_from_atlas():
    text = (ROOT / "atlas/modules/qa/browser_service.py").read_text(encoding="utf-8")
    assert "from atlas" not in text and "import atlas" not in text
    assert "from ..." not in text and "from .." not in text
