"""Task 61: the hub is the sensor's distribution channel.

The bug these cover: `/install` used to hit `_mount_web`'s SPA catch-all and
return `index.html`, so the marketing site's `curl … | sh` line piped HTML
into a shell; and the dashboard's own commands named `get.guardian.sh` (a
third party) and `ghcr.io/guardian/sensor` (unpublished). So the properties
worth locking down are: the routes resolve *before* the catch-all, the script
is real POSIX sh, it names no host but the one that served it, it embeds no
secret, and the checksum it carries actually describes the bytes
`/sensor.whl` returns.
"""
import hashlib
import io
import re
import subprocess
import zipfile

import pytest

from guardian_hub import installer


# --------------------------------------------------------------------------
# routing: before the SPA catch-all
# --------------------------------------------------------------------------

def test_install_is_not_swallowed_by_the_spa_catch_all(client):
    r = client.get("/install")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    # The exact failure mode being guarded: index.html served as a script.
    assert "<!doctype html" not in r.text.lower()
    assert r.text.startswith("#!/bin/sh")


def test_wheel_and_checksum_are_not_swallowed_either(client):
    whl = client.get("/sensor.whl")
    assert whl.status_code == 200
    assert whl.headers["content-type"] == "application/octet-stream"
    assert whl.content[:2] == b"PK"          # a zip, not index.html
    assert 'filename="guardian_sensor-' in whl.headers["content-disposition"]

    sha = client.get("/sensor.whl.sha256")
    assert sha.status_code == 200
    assert sha.headers["content-type"].startswith("text/plain")


def _ordered_paths(app) -> list[str]:
    """Every route path in registration order. `include_router` nests routers
    in this FastAPI version, so a flat `app.routes` read misses them."""
    out: list[str] = []

    def walk(routes):
        for route in routes:
            # This FastAPI version wraps each `include_router` call in an
            # `_IncludedRouter` that keeps its member routes on
            # `.original_router`, so a flat `app.routes` read sees only the
            # wrapper and the two `_mount_web` entries.
            inner = getattr(route, "original_router", None)
            nested = getattr(inner or route, "routes", None)
            if nested:
                walk(nested)
                continue
            path = getattr(route, "path", None)
            if path:
                out.append(path)

    walk(app.routes)
    return out


def test_the_routes_are_registered_ahead_of_the_catch_all(client):
    """Positional, not behavioural: a future reshuffle that moved the public
    router after `_mount_web` would still 200 above (the SPA falls back to
    index.html for any path), so check the order directly. main.py's own
    comment flags this hazard; this is the test for it."""
    paths = _ordered_paths(client.app)
    if "/{path:path}" not in paths:
        pytest.skip("no web/dist built, so there is no catch-all to be shadowed by")
    catch_all = paths.index("/{path:path}")
    for path in ("/install", "/sensor.whl", "/sensor.whl.sha256"):
        assert path in paths, f"{path} is not registered at all"
        assert paths.index(path) < catch_all, f"{path} is registered after the SPA catch-all"


# --------------------------------------------------------------------------
# the script itself
# --------------------------------------------------------------------------

def test_install_script_is_valid_posix_sh(client, tmp_path):
    script = tmp_path / "install.sh"
    script.write_text(client.get("/install").text)
    # `sh -n` parses without executing -- the check that a stray quote or an
    # unbalanced `case` never reaches a customer's root shell.
    proc = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_install_script_help_runs_and_touches_nothing(client, tmp_path):
    script = tmp_path / "install.sh"
    script.write_text(client.get("/install").text)
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(["sh", str(script), "--help"], capture_output=True, text=True,
                          env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0
    assert "--code" in proc.stdout
    assert list(home.iterdir()) == []       # --help installs nothing


def test_install_script_rejects_an_unknown_argument(client, tmp_path):
    script = tmp_path / "install.sh"
    script.write_text(client.get("/install").text)
    home = tmp_path / "home"
    home.mkdir()
    proc = subprocess.run(["sh", str(script), "--code", "X", "--wipe-everything"],
                          capture_output=True, text=True,
                          env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 1
    assert "unknown argument" in proc.stderr
    assert list(home.iterdir()) == []       # and it bailed before doing anything


def test_install_script_names_only_the_origin_that_served_it(client):
    text = client.get("/install").text
    hosts = {m.group(1) for m in re.finditer(r"https?://([A-Za-z0-9._\-\[\]]+(?::\d+)?)", text)}
    # The request's own host, the `--hub` usage placeholder, and the three
    # loopback spellings the https check exempts. Nothing else: in particular
    # get.guardian.sh / ghcr.io / my.guarder.io must never appear, since none
    # of them is a host this hub's operator owns.
    assert hosts <= {"testserver", "your-hub", "127.0.0.1", "localhost", "[::1]"}, hosts
    for forbidden in ("get.guardian.sh", "ghcr.io", "my.guarder.io"):
        assert forbidden not in text


def test_install_script_embeds_no_secret(client):
    """The enrol code is an argument the human pastes; the script itself is a
    public document served without authentication, so nothing secret may be
    interpolated into it."""
    text = client.get("/install").text
    from guardian_hub.config import settings
    assert settings.secret_key not in text
    assert "GUARDIAN_SECRET_KEY" not in text
    # An enrol code is 8 upper-alnum characters. The script must contain a
    # placeholder, never a real one -- `CODE` starts empty and only `--code`
    # fills it.
    assert re.search(r"^CODE=''$", text, re.M)
    assert "YOUR-ENROL-CODE" in text


def test_install_script_refuses_a_non_https_hub(client):
    text = client.get("/install").text
    assert "must be https" in text
    # loopback is the documented exception, so a developer can test locally
    assert "http://127.0.0.1" in text


def test_install_script_pins_pip_to_the_hub(client):
    """`--no-index` is what makes "the installer contacts this hub and nothing
    else" true rather than aspirational."""
    text = client.get("/install").text
    assert "--no-index" in text
    assert "/sensor.whl" in text


def test_install_over_plain_http_is_refused_for_a_public_host(client):
    """A script piped into a root shell may not travel over plain http. The
    test client's own host is loopback-equivalent, so force a public Host."""
    r = client.get("/install", headers={"Host": "guarder.io"})
    assert r.status_code == 403
    assert "https only" in r.text
    assert "#!/bin/sh" not in r.text


def test_install_uses_the_requests_host_not_the_configured_base_url(client):
    """Rendering from `settings.base_url` would hand a customer who typed one
    hostname a script that downloads from another -- the same class of defect
    as the fabricated domain."""
    r = client.get("/install", headers={"Host": "hub.example.org", "X-Forwarded-Proto": "https"})
    # TestClient does not run uvicorn's ProxyHeadersMiddleware, so the scheme
    # stays http here; what matters is that the HOST came from the request.
    assert r.status_code in (200, 403)
    assert "hub.example.org" in r.text
    assert "127.0.0.1:8800" not in r.text


def test_a_bad_host_is_refused_rather_than_interpolated():
    """The origin lands inside a single-quoted sh string, so a host carrying a
    quote (or a space, or a newline) would break out of it and run. Asserted
    against the gate directly: httpx sanitises the Host header before it can
    reach the app, so a TestClient request cannot express the attack."""
    from guardian_hub.routers.public import _ORIGIN_RE

    for good in ("https://guarder.io", "https://hub.example.org:8443",
                 "http://127.0.0.1:8800", "https://[::1]:443"):
        assert _ORIGIN_RE.match(good), good
    for bad in ("https://evil'; rm -rf /; echo '", "https://a b", "https://a\nb",
                "https://a/b", "ftp://a", "https://a$(id)"):
        assert not _ORIGIN_RE.match(bad), bad


# --------------------------------------------------------------------------
# the wheel
# --------------------------------------------------------------------------

def test_served_checksum_matches_the_served_wheel(client):
    whl = client.get("/sensor.whl").content
    sha_body = client.get("/sensor.whl.sha256").text
    digest, name = sha_body.split()
    assert digest == hashlib.sha256(whl).hexdigest()
    assert name == installer.wheel_filename(installer.sensor_version(installer.sensor_src()))


def test_the_scripts_embedded_checksum_matches_the_served_wheel(client):
    """The installer verifies against the copy baked into the script, so it is
    that copy -- not just the standalone endpoint -- that has to be right."""
    text = client.get("/install").text
    embedded = re.search(r"^WHEEL_SHA256='([0-9a-f]{64})'$", text, re.M)
    assert embedded, "the script carries no checksum"
    assert embedded.group(1) == hashlib.sha256(client.get("/sensor.whl").content).hexdigest()

    name = re.search(r"^WHEEL='([^']+)'$", text, re.M)
    assert name and name.group(1).endswith("-py3-none-any.whl")


def test_the_wheel_is_installable_shaped(client):
    """The one piece pip needs to create the `guardian-sensor` console script
    is `entry_points.txt` -- and `sensor/guardian_sensor/install.py`'s
    `script_path()` looks for exactly that program next to the interpreter, so
    without it the launchd/systemd step silently no-ops."""
    zf = zipfile.ZipFile(io.BytesIO(client.get("/sensor.whl").content))
    names = set(zf.namelist())
    version = installer.sensor_version(installer.sensor_src())
    dist_info = f"guardian_sensor-{version}.dist-info"

    assert "guardian_sensor/__init__.py" in names
    assert "guardian_sensor/cli.py" in names
    for required in ("METADATA", "WHEEL", "RECORD", "entry_points.txt"):
        assert f"{dist_info}/{required}" in names

    entry_points = zf.read(f"{dist_info}/entry_points.txt").decode()
    assert "[console_scripts]" in entry_points
    assert "guardian-sensor = guardian_sensor.cli:main" in entry_points

    # RECORD covers every member, and its own line is hashless (PEP 427).
    record = zf.read(f"{dist_info}/RECORD").decode()
    recorded = {line.split(",")[0] for line in record.strip().splitlines()}
    assert recorded == names
    assert f"{dist_info}/RECORD,," in record

    # Nothing from the source tree that a customer must not receive.
    assert not [n for n in names if "__pycache__" in n or "/tests/" in n or "/build/" in n]


def test_the_build_is_reproducible(client):
    """Two builds of the same source must be byte-identical, or the checksum
    the script carries would disagree with the bytes another worker serves."""
    pkg = installer.sensor_src()
    files = installer._sources(pkg)
    version = installer.sensor_version(pkg)
    assert installer._build(files, version) == installer._build(files, version)


def test_the_wheel_is_cached_on_the_source_fingerprint():
    first = installer.wheel()
    second = installer.wheel()
    assert first is not None
    assert first[1] is second[1]        # same object: served from the cache
