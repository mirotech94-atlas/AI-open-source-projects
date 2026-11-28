# Guardian Phase 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A running Guardian hub at guardian.example.com with OTP login, tenants and roles, plus a `guardian-sensor` package that enrols, runs every Nexus check with proven parity, syncs with the hub, and can import all Nexus data.

**Architecture:** Monorepo with three Python/TS units: `hub/` (FastAPI + SQLAlchemy + SQLite, serves the web build), `sensor/` (stdlib-only Python package, Nexus collector ported module by module, parity-tested against recorded Nexus fixtures), `web/` (Vite React TS; in phase 1 only the login screen and a "waiting for first scan" shell). Sensor ↔ hub talk over `/api/v1/sensor/*` with a bearer token obtained by enrolment code.

**Tech Stack:** Python 3.10+ (sensor, must run on the Mac's 3.10.3) / Python 3.13 (hub on the VPS), FastAPI 0.115, SQLAlchemy 2.0, Alembic, APScheduler 3, uvicorn, httpx (tests), pytest; Node 20, Vite 5, React 18, TypeScript 5, React Router 6, TanStack Query 5.

**Spec:** `docs/superpowers/specs/2026-09-09-guardian-design.md`

## Global Constraints

- Product name everywhere is **Guardian**; never "Nexus" in user-facing copy (the importer may mention it).
- Sensor runtime dependencies: **stdlib only** (same rule as Nexus). Hub may use PyPI packages.
- Sensor code must run on Python **3.10** (no `match`, no `tomllib`, no `datetime.UTC`).
- Secrets only in `hub/.env` and the sensor token file (mode 0600); never committed. `.gitignore` covers `.env`, `*.db`, `state/`, `node_modules/`, `dist/`.
- Login: **username + 6-digit email OTP**, no password path. Codes expire 10 min, 5 attempts, one active code per user.
- Roles: viewer / operator / admin per tenant; `is_superadmin` on the user.
- ntfy is the only alert channel (topic per tenant); email is used only for OTP.
- Behaviour parity with Nexus is the acceptance test: identical alerts, score, recommendations and incidents for the same input.
- Nexus reference source: `~/.claude/nexus-agent/checks.py` (1,400 lines), `hubsync.py`, `dns.py`, `ui/server.py`. Copy logic; do not import from that directory.
- Commit after every task with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`, identity `-c user.name=Miro -c user.email=you@example.com`.

---

## File structure

```
Guardian/
  hub/
    pyproject.toml                 # package "guardian-hub", console script `guardian`
    guardian_hub/
      __init__.py
      config.py                    # Settings from env (.env), paths
      db.py                        # engine, SessionLocal, get_db dependency, Base
      models.py                    # all tables from spec §3
      schemas.py                   # pydantic request/response models
      security.py                  # otp hashing, token hashing, session cookie helpers
      mailer.py                    # send_otp(email, code) — SMTP or console
      deps.py                      # current_user, current_tenant, require_role
      routers/
        auth.py                    # /auth/start /auth/verify /auth/logout /me
        tenants.py                 # superadmin tenant CRUD + enrol-code
        sensor.py                  # /sensor/enrol /ingest /edits /queue
        read.py                    # /overview /devices /inbox /scans/latest (phase-1 minimum)
        write.py                   # device label/trust, alert ack, rec decision, run/fix queue
      ingest.py                    # store_scan(): scan → tables (the heart of ingest)
      main.py                      # app factory, static serving, scheduler start
      cli.py                       # guardian user/tenant/selftest/import-nexus
      importer.py                  # import-nexus
      alembic/ + alembic.ini
    tests/
      conftest.py                  # app + sqlite tmp db + client fixtures
      test_auth.py test_tenants.py test_sensor_api.py test_ingest.py test_read.py test_importer.py
  sensor/
    pyproject.toml                 # package "guardian-sensor", console script `guardian-sensor`
    guardian_sensor/
      __init__.py                  # VERSION
      paths.py                     # data dir resolution (~/.guardian or /var/lib/guardian)
      util.py                      # now_iso, IS_MAC, PING_W, ssl ctx, _run_for, load_env
      config.py                    # load/validate config.json (Nexus shape)
      sites.py hosts.py lan.py home.py domains.py dnsstats.py checks.py
      alerts.py                    # build_alerts, security_score, recommendations, correlate_incidents, unusual_hours
      fix.py                       # FIX_ACTIONS, apply_fix, remediate, attacker methods
      notify.py                    # notify ladder
      sync.py                      # hub_pull/hub_push/apply_edit (Nexus hubsync)
      runner.py                    # main(): the Nexus main() flow
      dnsfilter.py                 # Nexus dns.py
      install.py                   # --launchd / --systemd installers, Docker entry
      cli.py                       # guardian-sensor run|enrol|install|dns|selftest
    tests/
      fixtures/                    # copied from Nexus: last.json, state.json, config.json, events.jsonl (secrets stripped)
      test_parity_alerts.py test_fix.py test_sync.py test_dnsfilter.py test_config.py
    Dockerfile entrypoint.sh
  web/                             # Vite React TS (Task 14)
  deploy/
    guardian-hub.service nginx-guardian.conf deploy.sh
  docs/
```

---

### Task 1: Repo scaffold, hub package, settings, test harness

**Files:**
- Create: `.gitignore`, `hub/pyproject.toml`, `hub/guardian_hub/__init__.py`, `hub/guardian_hub/config.py`, `hub/guardian_hub/db.py`, `hub/guardian_hub/main.py`, `hub/tests/conftest.py`, `hub/tests/test_health.py`

**Interfaces:**
- Produces: `guardian_hub.config.settings` (pydantic-settings object with `data_dir: Path`, `database_url: str`, `secret_key: str`, `smtp_host/port/user/password/from`, `base_url`, `dev_console_otp: bool`), `guardian_hub.db.Base`, `get_db()`, `guardian_hub.main.create_app() -> FastAPI`.

- [ ] **Step 1: Write .gitignore and pyproject**

`.gitignore`:
```
.env
*.db
*.db-journal
__pycache__/
*.pyc
.venv/
node_modules/
dist/
state/
data/
*.egg-info/
.pytest_cache/
```

`hub/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "guardian-hub"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "fastapi>=0.115,<1",
  "uvicorn[standard]>=0.30",
  "sqlalchemy>=2.0,<3",
  "alembic>=1.13",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "apscheduler>=3.10,<4",
  "python-multipart>=0.0.9",
  "httpx>=0.27",
]
[project.optional-dependencies]
test = ["pytest>=8", "pytest-asyncio>=0.23"]
[project.scripts]
guardian = "guardian_hub.cli:main"
[tool.setuptools.packages.find]
include = ["guardian_hub*"]
```

- [ ] **Step 2: Write config.py and db.py**

`hub/guardian_hub/config.py`:
```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GUARDIAN_", env_file=".env", extra="ignore")
    data_dir: Path = Path("./data")
    database_url: str = ""          # default: sqlite file inside data_dir
    secret_key: str = "change-me"
    base_url: str = "http://127.0.0.1:8800"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Guardian <guardian@example.com>"
    dev_console_otp: bool = False   # print OTP to stdout instead of emailing (tests/dev)
    otp_ttl_seconds: int = 600
    otp_max_attempts: int = 5
    session_hours: int = 12
    remember_days: int = 30

    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'guardian.db').resolve()}"

settings = Settings()
```

`hub/guardian_hub/db.py`:
```python
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from .config import settings

class Base(DeclarativeBase):
    pass

def make_engine(url: str | None = None):
    url = url or settings.db_url()
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    eng = create_engine(url, future=True, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return eng

engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 3: Write main.py with a health route and the test harness**

`hub/guardian_hub/main.py`:
```python
from fastapi import FastAPI
from .config import settings

def create_app() -> FastAPI:
    app = FastAPI(title="Guardian", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/api/v1/health")
    def health():
        return {"ok": True, "product": "Guardian", "version": app.version}

    return app

app = create_app()
```

`hub/tests/conftest.py`:
```python
import os, pytest
os.environ["GUARDIAN_DEV_CONSOLE_OTP"] = "1"
os.environ["GUARDIAN_SECRET_KEY"] = "test-secret"

@pytest.fixture()
def client(tmp_path, monkeypatch):
    os.environ["GUARDIAN_DATABASE_URL"] = f"sqlite:///{tmp_path/'t.db'}"
    import importlib
    from guardian_hub import config, db
    importlib.reload(config); importlib.reload(db)
    from guardian_hub import models  # noqa: F401  (registers tables; exists from Task 2 on)
    db.Base.metadata.create_all(db.engine)
    from guardian_hub.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app()) as c:
        yield c
```
(For Task 1 the `models` import line is commented out; Task 2 uncomments it.)

`hub/tests/test_health.py`:
```python
def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["product"] == "Guardian"
```

- [ ] **Step 4: Create venv, install, run**

```bash
cd hub && python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[test]' && pytest -q
```
Expected: `1 passed`. (The Mac has Python 3.10.3 at /usr/local/bin/python3; the VPS runs 3.13. Both are supported.)

- [ ] **Step 5: Commit**
```bash
git add -A && git commit -m "hub: scaffold, settings, db, health route, test harness"
```

---

### Task 2: Data model and migrations

**Files:**
- Create: `hub/guardian_hub/models.py`, `hub/alembic.ini`, `hub/alembic/env.py`, `hub/alembic/versions/0001_initial.py`, `hub/tests/test_models.py`
- Modify: `hub/tests/conftest.py` (uncomment models import)

**Interfaces:**
- Produces the ORM classes below; every later task uses these exact names and columns.

- [ ] **Step 1: Write the failing test**

`hub/tests/test_models.py`:
```python
from guardian_hub import models as m

def test_tables_exist(client):
    from guardian_hub.db import engine
    from sqlalchemy import inspect
    names = set(inspect(engine).get_table_names())
    for t in ["tenant","user","membership","otp","session","sensor","site","host","device","scan",
              "metric","event","alert","incident","recommendation","fix","dns_query","dns_rule",
              "edit","report","integration","audit","heartbeat","queue"]:
        assert t in names, t
```

- [ ] **Step 2: Run it** — `pytest -q tests/test_models.py` → FAIL (`no module models`).

- [ ] **Step 3: Write models.py**

```python
import uuid, datetime as dt
from sqlalchemy import String, Integer, Float, Boolean, Text, JSON, ForeignKey, DateTime, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

def uid() -> str: return uuid.uuid4().hex
def utcnow() -> dt.datetime: return dt.datetime.now(dt.timezone.utc)

class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    brand: Mapped[dict] = mapped_column(JSON, default=dict)          # {name, accent}
    ntfy_topic: Mapped[str] = mapped_column(String(120), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)         # Nexus-shaped config.json (sites, ssh_hosts, lan, thresholds, auto_fix, checks, heartbeats, dns, home)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)       # retention, report_schedule, profile{owner,email,address}
    created: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class User(Base):
    __tablename__ = "user"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # viewer|operator|admin

class Otp(Base):
    __tablename__ = "otp"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    expires: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)

class Session(Base):
    __tablename__ = "session"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)   # opaque cookie value: 32 random bytes hex
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_label: Mapped[str] = mapped_column(String(200), default="")
    expires: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Sensor(Base):
    __tablename__ = "sensor"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="sensor")
    token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    enrol_code: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True)
    enrol_expires: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    last_seen: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|revoked

class Site(Base):
    __tablename__ = "site"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500))
    public: Mapped[bool] = mapped_column(Boolean, default=False)
    last: Mapped[dict] = mapped_column(JSON, default=dict)   # latest check_site result

class Host(Base):
    __tablename__ = "host"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(200))
    last: Mapped[dict] = mapped_column(JSON, default=dict)   # latest check_ssh result (security facts, ports, cves…)

class Device(Base):
    __tablename__ = "device"
    __table_args__ = (UniqueConstraint("tenant_id", "mac"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    mac: Mapped[str] = mapped_column(String(17))
    ip: Mapped[str] = mapped_column(String(45), default="")
    name: Mapped[str] = mapped_column(String(120), default="")
    vendor: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(24), default="unknown")
    label: Mapped[str] = mapped_column(String(120), default="")
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    always_on: Mapped[bool] = mapped_column(Boolean, default=False)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    parent: Mapped[str | None] = mapped_column(String(17), nullable=True)
    risk: Mapped[int] = mapped_column(Integer, default=0)
    presence: Mapped[str] = mapped_column(String(12), default="NEW")
    ports: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen: Mapped[str] = mapped_column(String(32), default="")
    last_seen: Mapped[str] = mapped_column(String(32), default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)   # everything else Nexus keeps in known_macs[mac]

class Scan(Base):
    __tablename__ = "scan"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    ts: Mapped[str] = mapped_column(String(32), index=True)
    sensor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON)   # full last.json-shaped payload

class Metric(Base):
    __tablename__ = "metric"
    __table_args__ = (Index("ix_metric_lookup", "tenant_id", "name", "entity", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    ts: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))      # latency_ms, uptime, disk_pct, mem_pct, load, speed_down, dns_ms, live_devices, score…
    entity: Mapped[str] = mapped_column(String(120), default="")   # site name / host name / '' for global
    value: Mapped[float | None] = mapped_column(Float, nullable=True)

class Event(Base):
    __tablename__ = "event"
    __table_args__ = (Index("ix_event_t", "tenant_id", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    ts: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(32))      # join|leave|port_open|port_closed|wan|fix|scan|rotated…
    entity: Mapped[str] = mapped_column(String(120), default="")   # mac / host / site
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

class Alert(Base):
    __tablename__ = "alert"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(200))
    sev: Mapped[str] = mapped_column(String(8))        # CRIT|WARN|INFO
    msg: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[str] = mapped_column(String(12), default="")
    first_seen: Mapped[str] = mapped_column(String(32))
    last_seen: Mapped[str] = mapped_column(String(32))
    count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="open")   # open|acked|snoozed|resolved
    resolved_by: Mapped[str] = mapped_column(String(24), default="")  # auto|fix|human
    snoozed_until: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fix: Mapped[str] = mapped_column(String(200), default="")

class Incident(Base):
    __tablename__ = "incident"
    __table_args__ = (UniqueConstraint("tenant_id", "iid"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    iid: Mapped[str] = mapped_column(String(32))       # Nexus stable incident id
    entity: Mapped[str] = mapped_column(String(120))
    sev: Mapped[str] = mapped_column(String(8))
    story: Mapped[str] = mapped_column(Text)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    alert_keys: Mapped[list] = mapped_column(JSON, default=list)
    opened: Mapped[str] = mapped_column(String(32))
    closed: Mapped[str | None] = mapped_column(String(32), nullable=True)

class Recommendation(Base):
    __tablename__ = "recommendation"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {action, host, arg} or null
    decision: Mapped[str] = mapped_column(String(8), default="")   # ''|yes|no
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(12), default="")    # ''|queued|done|failed|needs-you
    last_seen: Mapped[str] = mapped_column(String(32))

class Fix(Base):
    __tablename__ = "fix"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(200), default="")
    arg: Mapped[str | None] = mapped_column(String(200), nullable=True)
    by: Mapped[str] = mapped_column(String(64), default="auto")
    ok: Mapped[bool] = mapped_column(Boolean)
    out: Mapped[str] = mapped_column(Text, default="")
    reboot_needed: Mapped[bool] = mapped_column(Boolean, default=False)

class DnsQuery(Base):
    __tablename__ = "dns_query"
    __table_args__ = (Index("ix_dnsq", "tenant_id", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    ts: Mapped[str] = mapped_column(String(32))
    client: Mapped[str] = mapped_column(String(45))
    name: Mapped[str] = mapped_column(String(255))
    qtype: Mapped[str] = mapped_column(String(8))
    blocked: Mapped[bool] = mapped_column(Boolean)
    list: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ms: Mapped[int] = mapped_column(Integer, default=0)

class DnsRule(Base):
    __tablename__ = "dns_rule"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "domain"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8))       # allow|block
    domain: Mapped[str] = mapped_column(String(255))

class Edit(Base):
    __tablename__ = "edit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    who: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(80))      # Nexus edit path: /api/device, /api/device/forget, /api/device/merge, /api/device/quarantine, /api/ack, /api/alert, /api/config, /api/rec, /api/attacker, /api/maintenance, /api/profile
    body: Mapped[dict] = mapped_column(JSON)

class Queue(Base):
    __tablename__ = "queue"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    who: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(80))      # /api/fix | /api/run | /api/speedtest | /api/reboot-plan
    body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|done|failed
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class Report(Base):
    __tablename__ = "report"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(12))      # weekly|monthly|manual
    period_days: Mapped[int] = mapped_column(Integer)
    preset: Mapped[str] = mapped_column(String(32), default="")
    sections: Mapped[list] = mapped_column(JSON, default=list)
    created: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    path: Mapped[str] = mapped_column(String(300))
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    share_expires: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Integration(Base):
    __tablename__ = "integration"
    __table_args__ = (UniqueConstraint("tenant_id", "kind"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))      # ntfy|slack|teams|webhook
    secret: Mapped[str] = mapped_column(Text, default="")   # topic / webhook url
    routes: Mapped[dict] = mapped_column(JSON, default=dict) # {"CRIT": true, "WARN": true, "INFO": false}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="")
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")
    body: Mapped[dict] = mapped_column(JSON, default=dict)

class Heartbeat(Base):
    __tablename__ = "heartbeat"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    token: Mapped[str] = mapped_column(String(64))
    expect_minutes: Mapped[int] = mapped_column(Integer, default=1500)
    last_ping: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Alembic**

```bash
cd hub && alembic init alembic
```
Edit `alembic/env.py`: set `from guardian_hub.db import Base; from guardian_hub import models; target_metadata = Base.metadata` and `config.set_main_option("sqlalchemy.url", settings.db_url())` (import `settings` from `guardian_hub.config`). Then:
```bash
alembic revision --autogenerate -m "initial" && alembic upgrade head
```
Uncomment the `models` import in `tests/conftest.py`.

- [ ] **Step 5: Run tests** — `pytest -q` → `2 passed`.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "hub: data model + initial migration"`.

---

### Task 3: OTP login, sessions, /me

**Files:**
- Create: `hub/guardian_hub/security.py`, `hub/guardian_hub/mailer.py`, `hub/guardian_hub/schemas.py`, `hub/guardian_hub/deps.py`, `hub/guardian_hub/routers/__init__.py`, `hub/guardian_hub/routers/auth.py`, `hub/tests/test_auth.py`
- Modify: `hub/guardian_hub/main.py` (include router)

**Interfaces:**
- Produces: `security.hash_code(code:str)->str`, `security.new_token()->str`, `security.hash_token(t)->str`, `mailer.send_otp(email, code)`, `mailer.LAST_CODES: dict[email,code]` (filled only when `dev_console_otp`), `deps.current_user(request, db) -> User`, `deps.current_session`, `deps.require_role(min_role: str)` returning a dependency that yields `(user, tenant, role)`, `deps.ROLE_ORDER = {"viewer":0,"operator":1,"admin":2}`.
- Routes: `POST /api/v1/auth/start {username}` → 200 `{sent:true}` always (no user enumeration), `POST /api/v1/auth/verify {username, code, remember?}` → sets cookie `guardian_session` and returns `{user:{username,display_name,is_superadmin}, tenants:[{id,slug,name,role}]}`, `POST /api/v1/auth/logout`, `GET /api/v1/me` (same body as verify + `active_tenant`), `POST /api/v1/me/tenant {tenant_id}` switches the active tenant.

- [ ] **Step 1: Write the failing tests**

`hub/tests/test_auth.py`:
```python
from guardian_hub import mailer
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

def seed_user(username="miro", email="m@example.com", tenant_slug="home", role="admin"):
    db = SessionLocal()
    t = m.Tenant(slug=tenant_slug, name="Miro Home Network Security")
    u = m.User(username=username, email=email, display_name="Miro")
    db.add_all([t, u]); db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=t.id, role=role)); db.commit()
    return u.id, t.id

def login(client, username="miro", email="m@example.com"):
    assert client.post("/api/v1/auth/start", json={"username": username}).json() == {"sent": True}
    code = mailer.LAST_CODES[email]
    r = client.post("/api/v1/auth/verify", json={"username": username, "code": code})
    assert r.status_code == 200, r.text
    return r.json()

def test_start_unknown_user_is_silent(client):
    assert client.post("/api/v1/auth/start", json={"username": "nobody"}).json() == {"sent": True}

def test_full_login(client):
    seed_user()
    body = login(client)
    assert body["user"]["username"] == "miro"
    assert body["tenants"][0]["role"] == "admin"
    me = client.get("/api/v1/me").json()
    assert me["active_tenant"]["slug"] == "home"

def test_wrong_code_and_lockout(client):
    seed_user()
    client.post("/api/v1/auth/start", json={"username": "miro"})
    for _ in range(5):
        r = client.post("/api/v1/auth/verify", json={"username": "miro", "code": "000000"})
        assert r.status_code == 401
    good = mailer.LAST_CODES["m@example.com"]
    assert client.post("/api/v1/auth/verify", json={"username": "miro", "code": good}).status_code == 401  # burnt

def test_logout(client):
    seed_user(); login(client)
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/me").status_code == 401
```

- [ ] **Step 2: Run** → FAIL (404s).

- [ ] **Step 3: Implement**

`security.py`:
```python
import hashlib, hmac, secrets
from .config import settings

def hash_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()

def new_token() -> str: return secrets.token_hex(32)
def hash_token(t: str) -> str: return hashlib.sha256(t.encode()).hexdigest()
def new_code() -> str: return f"{secrets.randbelow(10**6):06d}"
def new_enrol_code() -> str: return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
```

`mailer.py`:
```python
import smtplib, ssl
from email.message import EmailMessage
from .config import settings

LAST_CODES: dict[str, str] = {}

def send_otp(email: str, code: str) -> None:
    if settings.dev_console_otp or not settings.smtp_host:
        LAST_CODES[email] = code
        print(f"[guardian] OTP for {email}: {code}", flush=True)
        return
    msg = EmailMessage()
    msg["Subject"] = f"{code} is your Guardian sign-in code"
    msg["From"] = settings.smtp_from; msg["To"] = email
    msg.set_content(f"Your Guardian sign-in code is {code}. It expires in {settings.otp_ttl_seconds//60} minutes.\nIf you did not request it, ignore this email.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        if settings.smtp_user: s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)
```

`schemas.py` (phase-1 subset):
```python
from pydantic import BaseModel, Field
class AuthStart(BaseModel): username: str = Field(min_length=1, max_length=64)
class AuthVerify(BaseModel):
    username: str; code: str = Field(min_length=6, max_length=6); remember: bool = False
class TenantOut(BaseModel): id: str; slug: str; name: str; role: str
class UserOut(BaseModel): username: str; display_name: str; is_superadmin: bool
class MeOut(BaseModel): user: UserOut; tenants: list[TenantOut]; active_tenant: TenantOut | None = None
class SwitchTenant(BaseModel): tenant_id: str
```

`deps.py`:
```python
import datetime as dt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession
from .db import get_db
from . import models as m

COOKIE = "guardian_session"
ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}

def current_session(request: Request, db: DbSession = Depends(get_db)) -> m.Session:
    tok = request.cookies.get(COOKIE)
    s = db.get(m.Session, tok) if tok else None
    if not s or s.expires < dt.datetime.now(dt.timezone.utc):
        raise HTTPException(401, "not signed in")
    s.last_seen = dt.datetime.now(dt.timezone.utc); db.commit()
    return s

def current_user(s: m.Session = Depends(current_session), db: DbSession = Depends(get_db)) -> m.User:
    return db.get(m.User, s.user_id)

def memberships(db: DbSession, user: m.User) -> list[tuple[m.Tenant, str]]:
    if user.is_superadmin:
        return [(t, "admin") for t in db.query(m.Tenant).order_by(m.Tenant.name)]
    rows = db.query(m.Membership, m.Tenant).join(m.Tenant, m.Tenant.id == m.Membership.tenant_id).filter(m.Membership.user_id == user.id).all()
    return [(t, mb.role) for mb, t in rows]

def require_role(min_role: str):
    def dep(s: m.Session = Depends(current_session), db: DbSession = Depends(get_db)):
        user = db.get(m.User, s.user_id)
        for t, role in memberships(db, user):
            if t.id == s.tenant_id:
                if ROLE_ORDER[role] < ROLE_ORDER[min_role]:
                    raise HTTPException(403, f"{min_role} role required")
                return user, t, role
        raise HTTPException(403, "no active tenant")
    return dep

def require_superadmin(user: m.User = Depends(current_user)) -> m.User:
    if not user.is_superadmin: raise HTTPException(403, "superadmin only")
    return user
```

`routers/auth.py`:
```python
import datetime as dt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DbSession
from ..db import get_db
from .. import models as m, schemas as sc, security, mailer
from ..config import settings
from ..deps import COOKIE, current_session, current_user, memberships

router = APIRouter(prefix="/api/v1", tags=["auth"])
_now = lambda: dt.datetime.now(dt.timezone.utc)

@router.post("/auth/start")
def start(body: sc.AuthStart, db: DbSession = Depends(get_db)):
    u = db.query(m.User).filter(m.User.username == body.username.strip().lower()).first()
    if u:
        db.query(m.Otp).filter(m.Otp.user_id == u.id, m.Otp.used == False).update({"used": True})
        code = security.new_code()
        db.add(m.Otp(user_id=u.id, code_hash=security.hash_code(code), expires=_now() + dt.timedelta(seconds=settings.otp_ttl_seconds)))
        db.commit(); mailer.send_otp(u.email, code)
    return {"sent": True}

def _me(db, u, active_id):
    ts = [sc.TenantOut(id=t.id, slug=t.slug, name=t.name, role=r) for t, r in memberships(db, u)]
    act = next((t for t in ts if t.id == active_id), ts[0] if ts else None)
    return sc.MeOut(user=sc.UserOut(username=u.username, display_name=u.display_name, is_superadmin=u.is_superadmin), tenants=ts, active_tenant=act)

@router.post("/auth/verify", response_model=sc.MeOut)
def verify(body: sc.AuthVerify, request: Request, response: Response, db: DbSession = Depends(get_db)):
    u = db.query(m.User).filter(m.User.username == body.username.strip().lower()).first()
    otp = db.query(m.Otp).filter(m.Otp.user_id == u.id, m.Otp.used == False).order_by(m.Otp.expires.desc()).first() if u else None
    if not otp or otp.expires < _now():
        raise HTTPException(401, "code expired or not requested")
    otp.attempts += 1
    if otp.attempts > settings.otp_max_attempts or otp.code_hash != security.hash_code(body.code):
        if otp.attempts >= settings.otp_max_attempts: otp.used = True
        db.commit(); raise HTTPException(401, "wrong code")
    otp.used = True
    ttl = dt.timedelta(days=settings.remember_days) if body.remember else dt.timedelta(hours=settings.session_hours)
    ms = memberships(db, u)
    s = m.Session(id=security.new_token(), user_id=u.id, tenant_id=ms[0][0].id if ms else None,
                  device_label=(request.headers.get("user-agent") or "")[:200], expires=_now() + ttl)
    db.add(s); db.commit()
    response.set_cookie(COOKIE, s.id, max_age=int(ttl.total_seconds()), httponly=True, samesite="lax", secure=settings.base_url.startswith("https"), path="/")
    return _me(db, u, s.tenant_id)

@router.post("/auth/logout")
def logout(response: Response, s: m.Session = Depends(current_session), db: DbSession = Depends(get_db)):
    db.delete(s); db.commit(); response.delete_cookie(COOKIE, path="/"); return {"ok": True}

@router.get("/me", response_model=sc.MeOut)
def me(s: m.Session = Depends(current_session), u: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    return _me(db, u, s.tenant_id)

@router.post("/me/tenant", response_model=sc.MeOut)
def switch(body: sc.SwitchTenant, s: m.Session = Depends(current_session), u: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    if body.tenant_id not in {t.id for t, _ in memberships(db, u)}: raise HTTPException(403, "not a member")
    s.tenant_id = body.tenant_id; db.commit(); return _me(db, u, s.tenant_id)
```

In `main.py` add `from .routers import auth` and `app.include_router(auth.router)`.

- [ ] **Step 4: Run** — `pytest -q` → all pass.
- [ ] **Step 5: Commit** — `git commit -am "hub: OTP login, sessions, /me, roles deps"`.

---

### Task 4: Tenants, users, memberships (superadmin API + CLI)

**Files:**
- Create: `hub/guardian_hub/routers/tenants.py`, `hub/guardian_hub/cli.py`, `hub/tests/test_tenants.py`
- Modify: `main.py` (include router)

**Interfaces:**
- Routes (superadmin): `GET/POST /api/v1/tenants`, `PATCH /api/v1/tenants/{id}` (name, brand, ntfy_topic, settings), `POST /api/v1/tenants/{id}/users {username,email,display_name,role}` (creates user if missing + membership), `DELETE /api/v1/tenants/{id}/users/{username}`, `POST /api/v1/tenants/{id}/enrol-code` → `{code, expires}`.
- CLI: `guardian tenant add SLUG "Name"`, `guardian user add USERNAME EMAIL --tenant SLUG --role admin [--superadmin]`, `guardian enrol-code SLUG`, `guardian migrate` (alembic upgrade head).

- [ ] **Step 1: Tests**

```python
from tests.test_auth import seed_user, login
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

def make_super(client):
    uid, tid = seed_user()
    db = SessionLocal(); db.get(m.User, uid).is_superadmin = True; db.commit()
    login(client); return tid

def test_tenant_crud_and_users(client):
    make_super(client)
    r = client.post("/api/v1/tenants", json={"slug": "dental", "name": "Northwind Dental"}); assert r.status_code == 200
    tid = r.json()["id"]
    r = client.post(f"/api/v1/tenants/{tid}/users", json={"username": "ann", "email": "ann@x.com", "display_name": "Ann", "role": "viewer"})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    r = client.post(f"/api/v1/tenants/{tid}/enrol-code"); assert len(r.json()["code"]) == 8
    assert any(t["slug"] == "dental" for t in client.get("/api/v1/tenants").json())

def test_non_super_forbidden(client):
    seed_user(); login(client)
    assert client.get("/api/v1/tenants").status_code == 403
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement router**

```python
import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession
from ..db import get_db
from .. import models as m, security
from ..deps import require_superadmin

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"], dependencies=[Depends(require_superadmin)])

class TenantIn(BaseModel):
    slug: str; name: str; brand: dict = {}; ntfy_topic: str = ""
class TenantPatch(BaseModel):
    name: str | None = None; brand: dict | None = None; ntfy_topic: str | None = None; settings: dict | None = None; config: dict | None = None
class UserIn(BaseModel):
    username: str; email: str; display_name: str = ""; role: str = "viewer"

def _out(t: m.Tenant): return {"id": t.id, "slug": t.slug, "name": t.name, "brand": t.brand, "ntfy_topic": t.ntfy_topic, "settings": t.settings}

@router.get("")
def list_tenants(db: DbSession = Depends(get_db)): return [_out(t) for t in db.query(m.Tenant).order_by(m.Tenant.name)]

@router.post("")
def create(body: TenantIn, db: DbSession = Depends(get_db)):
    if db.query(m.Tenant).filter_by(slug=body.slug).first(): raise HTTPException(409, "slug exists")
    t = m.Tenant(slug=body.slug, name=body.name, brand=body.brand, ntfy_topic=body.ntfy_topic); db.add(t); db.commit(); return _out(t)

@router.patch("/{tid}")
def patch(tid: str, body: TenantPatch, db: DbSession = Depends(get_db)):
    t = db.get(m.Tenant, tid) or _404()
    for k, v in body.model_dump(exclude_none=True).items(): setattr(t, k, v)
    db.commit(); return _out(t)

@router.post("/{tid}/users")
def add_user(tid: str, body: UserIn, db: DbSession = Depends(get_db)):
    if body.role not in ("viewer", "operator", "admin"): raise HTTPException(422, "bad role")
    t = db.get(m.Tenant, tid) or _404()
    u = db.query(m.User).filter_by(username=body.username.lower()).first()
    if not u:
        u = m.User(username=body.username.lower(), email=body.email, display_name=body.display_name); db.add(u); db.flush()
    mb = db.query(m.Membership).filter_by(user_id=u.id, tenant_id=t.id).first()
    if mb: mb.role = body.role
    else: db.add(m.Membership(user_id=u.id, tenant_id=t.id, role=body.role))
    db.commit(); return {"username": u.username, "role": body.role}

@router.delete("/{tid}/users/{username}")
def remove_user(tid: str, username: str, db: DbSession = Depends(get_db)):
    u = db.query(m.User).filter_by(username=username.lower()).first() or _404()
    db.query(m.Membership).filter_by(user_id=u.id, tenant_id=tid).delete(); db.commit(); return {"ok": True}

@router.post("/{tid}/enrol-code")
def enrol_code(tid: str, db: DbSession = Depends(get_db)):
    t = db.get(m.Tenant, tid) or _404()
    s = m.Sensor(tenant_id=t.id, enrol_code=security.new_enrol_code(), enrol_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15))
    db.add(s); db.commit(); return {"code": s.enrol_code, "expires": s.enrol_expires.isoformat(), "sensor_id": s.id}

def _404(): raise HTTPException(404, "not found")
```

`cli.py`:
```python
import argparse, sys
from .db import SessionLocal, engine, Base
from . import models as m, security

def main(argv=None):
    ap = argparse.ArgumentParser(prog="guardian"); sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    t = sub.add_parser("tenant"); ts = t.add_subparsers(dest="tcmd", required=True)
    ta = ts.add_parser("add"); ta.add_argument("slug"); ta.add_argument("name")
    ts.add_parser("list")
    u = sub.add_parser("user"); us = u.add_subparsers(dest="ucmd", required=True)
    ua = us.add_parser("add"); ua.add_argument("username"); ua.add_argument("email"); ua.add_argument("--tenant"); ua.add_argument("--role", default="admin"); ua.add_argument("--superadmin", action="store_true"); ua.add_argument("--name", default="")
    e = sub.add_parser("enrol-code"); e.add_argument("slug")
    sub.add_parser("selftest")            # Task 12
    imp = sub.add_parser("import-nexus"); imp.add_argument("src"); imp.add_argument("--tenant-map", default="myron=home,demo=demo")  # Task 11
    a = ap.parse_args(argv)
    if a.cmd == "migrate":
        from alembic.config import Config; from alembic import command
        import pathlib; command.upgrade(Config(str(pathlib.Path(__file__).resolve().parent.parent / "alembic.ini")), "head"); return
    db = SessionLocal()
    if a.cmd == "tenant" and a.tcmd == "add":
        db.add(m.Tenant(slug=a.slug, name=a.name)); db.commit(); print("ok")
    elif a.cmd == "tenant":
        for t in db.query(m.Tenant): print(t.slug, t.name, t.id)
    elif a.cmd == "user":
        usr = db.query(m.User).filter_by(username=a.username.lower()).first() or m.User(username=a.username.lower(), email=a.email, display_name=a.name)
        usr.is_superadmin = usr.is_superadmin or a.superadmin; db.add(usr); db.flush()
        if a.tenant:
            t = db.query(m.Tenant).filter_by(slug=a.tenant).first() or sys.exit("no such tenant")
            if not db.query(m.Membership).filter_by(user_id=usr.id, tenant_id=t.id).first(): db.add(m.Membership(user_id=usr.id, tenant_id=t.id, role=a.role))
        db.commit(); print("ok")
    elif a.cmd == "enrol-code":
        import datetime as dt
        t = db.query(m.Tenant).filter_by(slug=a.slug).first() or sys.exit("no such tenant")
        s = m.Sensor(tenant_id=t.id, enrol_code=security.new_enrol_code(), enrol_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15)); db.add(s); db.commit(); print(s.enrol_code)
    elif a.cmd == "selftest":
        from .selftest import run; sys.exit(run())
    elif a.cmd == "import-nexus":
        from .importer import import_nexus; import_nexus(a.src, dict(p.split("=") for p in a.tenant_map.split(","))); print("imported")
```

- [ ] **Step 4: Run tests** → pass. **Step 5: Commit** `hub: tenants/users API + CLI`.

---

### Task 5: Sensor enrolment, ingest, edits and queue endpoints

**Files:**
- Create: `hub/guardian_hub/routers/sensor.py`, `hub/guardian_hub/ingest.py`, `hub/tests/test_sensor_api.py`, `hub/tests/test_ingest.py`, `hub/tests/fixtures/last.json`, `hub/tests/fixtures/state.json` (copy from `~/.claude/nexus-agent/`, then strip: `hub_sent`, any token/topic strings; keep everything else)
- Modify: `main.py`

**Interfaces:**
- `POST /api/v1/sensor/enrol {code, name, platform, version}` → `{token, tenant:{slug,name}, sensor_id}` (token shown once).
- Sensor auth dependency `sensor_auth(request, db) -> (Sensor, Tenant)` from `Authorization: Bearer`.
- `POST /api/v1/sensor/ingest` body: `{"last": <last.json>, "state": {"lan": {"known_macs": …, "transitions": …}, "alerts_db": …, "incidents": …, "rec_decisions": …, "sec": …}, "events": [ … new event lines … ], "history": [ … new history lines … ], "fixes": [ … new remediation lines … ], "dns_queries": [ … ]}` → `{ok:true, stored:{events:n,…}}`.
- `GET /api/v1/sensor/edits?after=<edit id>` → `{edits:[{id, ts, who, path, body}], last_id}`; `GET /api/v1/sensor/queue` → `{queued:[{id,path,body}]}` and marks them `running`; `POST /api/v1/sensor/queue/{id}/result {ok, out, result}`.
- `GET /api/v1/sensor/config` → the tenant's Nexus-shaped config JSON (sensor uses it as its `config.json`; local file wins if the hub has none).
- `ingest.store_scan(db, tenant, sensor, payload) -> dict` implements: insert Scan (score from `last.score.total`, health), upsert Site/Host `.last`, upsert Device rows from `state.lan.known_macs` (all keys not in columns go to `extra`), upsert Alert rows from `state.alerts_db` (status/first/last/count/resolved_by) merging `last.alerts[*].fix/why/confidence`, upsert Incident from `state.incidents`, upsert Recommendation from `last.recommendations` + `state.rec_decisions`, append Fix rows, append Event rows (types from Nexus events.jsonl `type`), append Metric rows from each history line: `latency_ms` per site (`sites[i].ms`), `up` per site (1/0), `disk_pct/mem_pct/load` per host, global `live_devices`, `dns_ms`, `speed_down/speed_up`, `score`; append DnsQuery rows.

- [ ] **Step 1: Tests**

`test_sensor_api.py`:
```python
import json, pathlib
from tests.test_tenants import make_super
FX = pathlib.Path(__file__).parent / "fixtures"

def enrol(client, tid):
    code = client.post(f"/api/v1/tenants/{tid}/enrol-code").json()["code"]
    r = client.post("/api/v1/sensor/enrol", json={"code": code, "name": "mac", "platform": "darwin", "version": "0.1.0"})
    assert r.status_code == 200, r.text
    return r.json()["token"]

def test_enrol_once(client):
    tid = make_super(client)
    code = client.post(f"/api/v1/tenants/{tid}/enrol-code").json()["code"]
    assert client.post("/api/v1/sensor/enrol", json={"code": code, "name": "a", "platform": "linux", "version": "1"}).status_code == 200
    assert client.post("/api/v1/sensor/enrol", json={"code": code, "name": "a", "platform": "linux", "version": "1"}).status_code == 404

def test_ingest_and_queue(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    payload = {"last": json.load(open(FX/"last.json")), "state": json.load(open(FX/"state.json")), "events": [], "history": [], "fixes": [], "dns_queries": []}
    r = client.post("/api/v1/sensor/ingest", json=payload, headers=H); assert r.status_code == 200, r.text
    assert r.json()["stored"]["devices"] >= 20
    assert client.post("/api/v1/sensor/ingest", json=payload, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/v1/sensor/queue", headers=H).json() == {"queued": []}
    assert client.get("/api/v1/sensor/edits", headers=H).json()["edits"] == []
    assert client.get("/api/v1/sensor/config", headers=H).status_code == 200
```

`test_ingest.py`:
```python
import json, pathlib
from tests.test_sensor_api import enrol
from tests.test_tenants import make_super
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
FX = pathlib.Path(__file__).parent / "fixtures"

def test_store_scan_tables(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = json.load(open(FX/"last.json")); state = json.load(open(FX/"state.json"))
    hist = [{"ts": last["ts"], "sites": [{"name": s["name"], "ms": s.get("ms"), "up": s.get("ok")} for s in last["sites"]], "hosts": [{"name": h["name"], "disk_pct": h.get("disk_pct"), "mem_pct": h.get("mem_pct"), "load": h.get("load")} for h in last["ssh"]], "live": 9, "score": last["score"]["total"]}]
    ev = [{"ts": last["ts"], "type": "join", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.250", "vendor": "Test"}]
    r = client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": ev, "history": hist, "fixes": [], "dns_queries": []}, headers=H)
    assert r.status_code == 200
    db = SessionLocal()
    assert db.query(m.Scan).count() == 1
    assert db.query(m.Alert).count() == len(state["alerts_db"])
    assert db.query(m.Device).filter_by(tenant_id=tid).count() == len(state["lan"]["known_macs"])
    assert db.query(m.Event).count() == 1
    assert db.query(m.Metric).filter_by(name="latency_ms").count() == len(last["sites"])
    assert db.query(m.Recommendation).count() == len(last["recommendations"])
    # idempotent on re-ingest of the same scan ts
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    assert db.query(m.Scan).count() == 1
```

- [ ] **Step 2: Copy fixtures** — `cp ~/.claude/nexus-agent/last.json ~/.claude/nexus-agent/state.json hub/tests/fixtures/` then `python3 - <<'EOF'` removing `state["hub_sent"]`, `state["hub_edits_offset"]`, `state["pending_actions"]`, and `last["hub_sync"]`. Grep the two files for `ntfy`, `token`, `@` and confirm nothing secret remains (ntfy topic id counts as secret → replace with `TOPIC`).

- [ ] **Step 3: Implement `ingest.py`**

```python
import datetime as dt
from sqlalchemy.orm import Session as DbSession
from . import models as m

DEVICE_COLS = {"ip","name","vendor","label","trusted","always_on","quarantined","parent","risk","presence","ports","first_seen","last_seen"}
_now = lambda: dt.datetime.now(dt.timezone.utc)

def _upsert(db, model, keys: dict, values: dict):
    row = db.query(model).filter_by(**keys).first()
    if not row:
        row = model(**keys); db.add(row)
    for k, v in values.items(): setattr(row, k, v)
    return row

def store_scan(db: DbSession, tenant: m.Tenant, sensor: m.Sensor | None, p: dict) -> dict:
    last = p.get("last") or {}; state = p.get("state") or {}; ts = last.get("ts") or _now().isoformat(timespec="seconds")
    out = {"scan": 0, "devices": 0, "alerts": 0, "events": 0, "metrics": 0, "fixes": 0, "dns": 0, "recs": 0, "incidents": 0}
    if not db.query(m.Scan).filter_by(tenant_id=tenant.id, ts=ts).first():
        db.add(m.Scan(tenant_id=tenant.id, ts=ts, sensor_id=sensor.id if sensor else None,
                      score=(last.get("score") or {}).get("total"), health=last.get("health") or {}, raw=last)); out["scan"] = 1
    for s in last.get("sites") or []:
        _upsert(db, m.Site, {"tenant_id": tenant.id, "name": s.get("name") or s.get("url")}, {"url": s.get("url", ""), "last": s})
    for h in last.get("ssh") or []:
        _upsert(db, m.Host, {"tenant_id": tenant.id, "name": h.get("name")}, {"address": h.get("host") or h.get("addr") or "", "last": h})
    for mac, d in ((state.get("lan") or {}).get("known_macs") or {}).items():
        vals = {k: d.get(k) for k in DEVICE_COLS if k in d}
        vals["extra"] = {k: v for k, v in d.items() if k not in DEVICE_COLS}
        vals["kind"] = d.get("kind") or vals["extra"].get("kind") or "unknown"
        for k in ("trusted","always_on","quarantined"): vals[k] = bool(vals.get(k))
        vals["risk"] = int(vals.get("risk") or 0)
        for k in ("ip","name","vendor","label","presence","first_seen","last_seen"): vals[k] = vals.get(k) or ""
        vals["ports"] = vals.get("ports") or {}
        _upsert(db, m.Device, {"tenant_id": tenant.id, "mac": mac}, vals); out["devices"] += 1
    fixmap = {a["key"]: a for a in (last.get("alerts") or [])}
    for key, a in (state.get("alerts_db") or {}).items():
        cur = fixmap.get(key, {})
        _upsert(db, m.Alert, {"tenant_id": tenant.id, "key": key}, {
            "sev": a.get("sev") or cur.get("sev") or "INFO", "msg": a.get("msg") or cur.get("msg") or key,
            "why": cur.get("why") or a.get("why") or "", "evidence": cur.get("evidence") or {}, "confidence": str(cur.get("confidence") or a.get("confidence") or ""),
            "first_seen": a.get("first_seen") or ts, "last_seen": a.get("last_seen") or ts, "count": int(a.get("count") or 1),
            "status": a.get("status") or "open", "resolved_by": a.get("resolved_by") or "", "snoozed_until": a.get("snoozed_until"), "fix": cur.get("fix") or ""}); out["alerts"] += 1
    for iid, inc in (state.get("incidents") or {}).items():
        _upsert(db, m.Incident, {"tenant_id": tenant.id, "iid": str(iid)}, {"entity": inc.get("entity") or "", "sev": inc.get("sev") or "WARN", "story": inc.get("story") or "",
                "actions": inc.get("actions") or [], "alert_keys": inc.get("alerts") or inc.get("keys") or [], "opened": inc.get("opened") or ts, "closed": inc.get("closed")}); out["incidents"] += 1
    decisions = state.get("rec_decisions") or {}
    for r in last.get("recommendations") or []:
        d = decisions.get(r["key"]) or {}
        _upsert(db, m.Recommendation, {"tenant_id": tenant.id, "key": r["key"]}, {"priority": r.get("priority") or "low", "title": r.get("title") or r.get("msg") or r["key"],
                "why": r.get("why") or "", "action": r.get("action"), "decision": d.get("decision") or "", "decided_by": d.get("by") or "", "status": d.get("status") or "", "last_seen": ts}); out["recs"] += 1
    for f in p.get("fixes") or []:
        db.add(m.Fix(tenant_id=tenant.id, ts=f.get("ts") or ts, host=f.get("host") or "-", action=f.get("action") or "", label=f.get("label") or "", arg=f.get("arg"),
                     by=f.get("by") or "auto", ok=bool(f.get("ok")), out=str(f.get("out") or "")[:4000], reboot_needed=bool(f.get("reboot")))); out["fixes"] += 1
    for e in p.get("events") or []:
        db.add(m.Event(tenant_id=tenant.id, ts=e.get("ts") or ts, type=e.get("type") or "event", entity=e.get("mac") or e.get("host") or e.get("site") or "", payload=e)); out["events"] += 1
    for h in p.get("history") or []:
        hts = h.get("ts") or ts
        def met(name, entity, value):
            if value is None: return
            db.add(m.Metric(tenant_id=tenant.id, ts=hts, name=name, entity=entity, value=float(value))); out["metrics"] += 1
        for s in h.get("sites") or []:
            met("latency_ms", s.get("name") or "", s.get("ms")); met("up", s.get("name") or "", 1 if s.get("up") else 0)
        for hh in h.get("hosts") or []:
            for k in ("disk_pct", "mem_pct", "load"): met(k, hh.get("name") or "", hh.get(k))
        met("live_devices", "", h.get("live")); met("dns_ms", "", h.get("dns_ms")); met("score", "", h.get("score"))
        sp = h.get("speed") or {}
        met("speed_down", "", sp.get("down")); met("speed_up", "", sp.get("up"))
    for q in p.get("dns_queries") or []:
        db.add(m.DnsQuery(tenant_id=tenant.id, ts=q.get("ts") or ts, client=q.get("client") or "", name=q.get("name") or "", qtype=str(q.get("type") or ""),
                          blocked=bool(q.get("blocked")), list=q.get("list"), ms=int(q.get("ms") or 0))); out["dns"] += 1
    if sensor: sensor.last_seen = _now(); sensor.status = "active"
    db.commit(); return out
```

- [ ] **Step 4: Implement `routers/sensor.py`**

```python
import datetime as dt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession
from ..db import get_db
from .. import models as m, security
from ..ingest import store_scan

router = APIRouter(prefix="/api/v1/sensor", tags=["sensor"])
_now = lambda: dt.datetime.now(dt.timezone.utc)

class EnrolIn(BaseModel): code: str; name: str = "sensor"; platform: str = ""; version: str = ""

def sensor_auth(request: Request, db: DbSession = Depends(get_db)):
    h = request.headers.get("authorization") or ""
    if not h.lower().startswith("bearer "): raise HTTPException(401, "sensor token required")
    s = db.query(m.Sensor).filter_by(token_hash=security.hash_token(h[7:].strip()), status="active").first()
    if not s: raise HTTPException(401, "bad sensor token")
    return s, db.get(m.Tenant, s.tenant_id)

@router.post("/enrol")
def enrol(body: EnrolIn, db: DbSession = Depends(get_db)):
    s = db.query(m.Sensor).filter_by(enrol_code=body.code.strip().upper(), status="pending").first()
    if not s or (s.enrol_expires and s.enrol_expires < _now()): raise HTTPException(404, "code invalid or expired")
    tok = security.new_token()
    s.token_hash = security.hash_token(tok); s.enrol_code = None; s.status = "active"; s.name = body.name; s.platform = body.platform; s.version = body.version; s.last_seen = _now()
    t = db.get(m.Tenant, s.tenant_id); db.commit()
    return {"token": tok, "tenant": {"slug": t.slug, "name": t.name}, "sensor_id": s.id}

@router.post("/ingest")
def ingest(payload: dict, auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth
    if "version" in payload: s.version = str(payload["version"])
    return {"ok": True, "stored": store_scan(db, t, s, payload)}

@router.get("/config")
def config(auth=Depends(sensor_auth)):
    s, t = auth; return t.config or {}

@router.get("/edits")
def edits(after: int = 0, auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth
    rows = db.query(m.Edit).filter(m.Edit.tenant_id == t.id, m.Edit.id > after).order_by(m.Edit.id).limit(500).all()
    return {"edits": [{"id": e.id, "ts": e.ts.isoformat(), "who": e.who, "path": e.path, "body": e.body} for e in rows], "last_id": rows[-1].id if rows else after}

@router.get("/queue")
def queue(auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth
    rows = db.query(m.Queue).filter_by(tenant_id=t.id, status="queued").order_by(m.Queue.ts).all()
    for q in rows: q.status = "running"
    db.commit(); return {"queued": [{"id": q.id, "path": q.path, "body": q.body, "who": q.who} for q in rows]}

class QResult(BaseModel): ok: bool; out: str = ""; result: dict | None = None

@router.post("/queue/{qid}/result")
def qresult(qid: str, body: QResult, auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth; q = db.get(m.Queue, qid)
    if not q or q.tenant_id != t.id: raise HTTPException(404)
    q.status = "done" if body.ok else "failed"; q.result = {"out": body.out[:4000], **(body.result or {})}; db.commit(); return {"ok": True}
```

Include the router in `main.py`.

- [ ] **Step 5: Run** → pass. **Step 6: Commit** `hub: sensor enrol/ingest/edits/queue + store_scan`.

---

### Task 6: Dashboard read + write API (phase-1 minimum) with audit

**Files:**
- Create: `hub/guardian_hub/routers/read.py`, `hub/guardian_hub/routers/write.py`, `hub/guardian_hub/audit.py`, `hub/tests/test_read_write.py`
- Modify: `main.py`

**Interfaces:**
- Read (viewer+): `GET /api/v1/overview` → `{ts, score, health, alerts:{crit,warn,info,open}, devices:{total,online,unverified}, sites:[{name,ok,ms,cert_days}], hosts:[{name,ok,load,disk_pct,mem_pct,reboot_required}], verdict:str, next_action:{label,path,body}|null}`; `GET /api/v1/scans/latest` → raw last; `GET /api/v1/devices` → device rows; `GET /api/v1/inbox?view=alerts|incidents|recs&status=open` → rows; `GET /api/v1/events?limit=`; `GET /api/v1/metrics?name=&entity=&hours=`.
- Write: `POST /api/v1/devices/{mac}` (operator) body `{label?,trusted?,always_on?,parent?,quarantined?}` → updates Device AND appends Edit `path="/api/device"` with Nexus body shape `{mac, label, trusted, always_on}` (quarantine → `/api/device/quarantine`, parent → `/api/device/merge`); `POST /api/v1/alerts/{key}/{ack|snooze|resolve|reopen}` (operator) → Alert status + Edit `/api/alert {key, op, hours?}`; `POST /api/v1/recs/{key} {decision: yes|no|clear}` (operator) → Recommendation + Edit `/api/rec {key, decision}`; `POST /api/v1/run` and `/api/v1/speedtest` (operator) → Queue rows; `POST /api/v1/fix {host, action, arg?}` (admin) → Queue `/api/fix`; `POST /api/v1/config` (admin) → Tenant.config + Edit `/api/config`; `POST /api/v1/profile` (admin) → Tenant.settings.profile + Edit `/api/profile`.
- `audit.log(db, tenant_id, user, role, action, target, ip, body)`.
- Verdict rules (copy of the Nexus five-questions logic): CRIT open → "Something needs you now: {first crit msg}"; else WARN open → "Mostly fine. {n} warnings to review."; else unverified devices → "{n} unknown device(s) on your Wi-Fi."; else "Everything is fine. Guardian is watching."; next_action = ack first CRIT / label first unverified device / null.

- [ ] **Step 1: Tests** (`test_read_write.py`)

```python
import json, pathlib
from tests.test_tenants import make_super
from tests.test_sensor_api import enrol
from tests.test_auth import seed_user, login
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
FX = pathlib.Path(__file__).parent / "fixtures"

def seeded(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    client.post("/api/v1/sensor/ingest", json={"last": json.load(open(FX/"last.json")), "state": json.load(open(FX/"state.json")), "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    return tid, H

def test_overview_and_lists(client):
    seeded(client)
    o = client.get("/api/v1/overview").json()
    assert o["score"] is not None and "verdict" in o and o["devices"]["total"] >= 20
    assert len(client.get("/api/v1/devices").json()) >= 20
    assert client.get("/api/v1/inbox?view=alerts").status_code == 200

def test_device_label_creates_edit(client):
    tid, H = seeded(client)
    mac = client.get("/api/v1/devices").json()[0]["mac"]
    r = client.post(f"/api/v1/devices/{mac}", json={"label": "Kitchen TV", "trusted": True}); assert r.status_code == 200
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    assert e and e[-1]["path"] == "/api/device" and e[-1]["body"]["label"] == "Kitchen TV"
    assert SessionLocal().query(m.Audit).filter_by(action="device.update").count() == 1

def test_viewer_cannot_write(client):
    tid, H = seeded(client)
    client.post("/api/v1/auth/logout")
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer"); login(client, "ann", "a@x.com")
    assert client.post("/api/v1/run").status_code == 403

def test_fix_queues(client):
    tid, H = seeded(client)
    assert client.post("/api/v1/fix", json={"host": "prod", "action": "security_updates"}).status_code == 200
    q = client.get("/api/v1/sensor/queue", headers=H).json()["queued"]
    assert q and q[0]["path"] == "/api/fix"
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `audit.py`, `read.py`, `write.py` exactly per the interface block (write handlers: validate role via `require_role`, mutate the table row, `db.add(m.Edit(...))` / `db.add(m.Queue(...))`, `audit.log(...)`, commit, return the updated row). Unverified devices = `not trusted and not label and presence != "ARCHIVED" and extra.get("unverified")` or, if absent, the count in `last.home.unverified`.
- [ ] **Step 4: Run** → pass. **Step 5: Commit** `hub: overview/devices/inbox reads, edits+queue writes, audit`.

---

### Task 7: Sensor package skeleton, util, config, paths, CLI

**Files:**
- Create: `sensor/pyproject.toml`, `sensor/guardian_sensor/{__init__,paths,util,config,cli}.py`, `sensor/tests/test_config.py`, `sensor/tests/fixtures/config.json` (copy of Nexus config.json with `roles`, `public_url`, `heartbeats` tokens replaced by placeholders)

**Interfaces:**
- `guardian_sensor.VERSION = "0.1.0"`; `paths.DATA_DIR` (env `GUARDIAN_DATA` → default `~/.guardian` on macOS, `/var/lib/guardian` on Linux, created 0700); `paths.p(name)`; `util.now_iso()`, `util.IS_MAC`, `util.PING_W(ms)`, `util.SSL_CTX`, `util.run_for(cmd:list, secs) -> str`, `util.load_env() -> dict` (reads `DATA_DIR/.env`), `util.ssh_run(host_cfg, cmd, timeout)`; `config.load(path=None) -> dict` applying Nexus defaults for every missing key (`thresholds` defaults copied from Nexus config.json values, `auto_fix.enabled=True`, `lan.enabled=True`, `dns.enabled=False`).
- CLI `guardian-sensor run [--once] [--no-lan]`, `enrol CODE --hub URL`, `install --launchd|--systemd`, `dns`, `selftest`, `version`.

- [ ] **Step 1: Test**
```python
from guardian_sensor import config
def test_defaults(tmp_path):
    (tmp_path/"config.json").write_text('{"sites":[],"ssh_hosts":[]}')
    c = config.load(tmp_path/"config.json")
    assert c["thresholds"]["cert_days_warn"] == 21 and c["auto_fix"]["enabled"] is True and c["lan"]["enabled"] is True
```
(Use the values actually in `~/.claude/nexus-agent/config.json`.)
- [ ] **Step 2–4:** implement; `cd sensor && /usr/local/bin/python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[test]' && pytest -q` → pass.
- [ ] **Step 5: Commit** `sensor: package skeleton, config defaults, cli`.

---

### Task 8: Port the collectors — sites, hosts, lan, home, domains, dnsstats, checks

**Files:**
- Create: `sensor/guardian_sensor/{sites,hosts,lan,home,domains,dnsstats,checks}.py`, `sensor/tests/test_collectors.py`

**Interfaces (signatures identical to Nexus):**
- `sites.check_site(s, th)`, `sites.site_security(host, hdrs)`, `sites.admin_probe(sites, state)`, `sites.external_sites(cfg, sites, state)` ← checks.py lines 18–80, 428–494.
- `hosts.REMOTE`, `hosts.check_ssh(h, th)`, `hosts.mac_updates()` ← lines 81–166.
- `lan.lan_sweep(cfg)`, `lan.lan_ports(ips)`, `lan.LAN_PORTS/RISKY_PORTS`, `lan.mdns_names()`, `lan.mac_vendor(mac)`, `lan.host_name(ip, names)`, `lan.risk_score(d, unverified)`, `lan.presence_state(d, online, now, flapping, base)`, `lan.default_gateway()`, `lan.my_lan_ip()` ← lines 226–238, 312–369, 402–425, 615–693.
- `home.wan_ip()`, `home.wan_path(cfg)`, `home.speed_test()`, `home.dns_check()`, `home.wifi_info()`, `home.gateway_ports(gw)`, `home.external_exposure(cfg, wan)`, `home.home_security(cfg, res, state)`, `home.rogue_checks(cfg, wifi, on_home_lan, state)`, `home.tailscale_status()`, `home.STRONG_WIFI/EXPOSURE_PORTS` ← lines 177–213, 299–311, 370–384, 560–593, 694–769.
- `domains.domain_expiry(hosts, state)`, `domains.email_security(domains, state)` ← 385–401, 451–469.
- `dnsstats.dns_analyse(cfg, state)`, `dnsstats.DGA_RE` ← 495–540 (reads `paths.p("dns")` instead of `HERE/dns`).
- `checks.extra_checks(cfg)`, `checks.heartbeats_check(cfg, state)` ← 541–559, 594–605.

- [ ] **Step 1: Tests** — pure functions get real tests; network functions get import + signature tests:
```python
from guardian_sensor import lan, sites, hosts, home, domains, dnsstats, checks
def test_presence_machine():
    d = {"first_seen": "2026-09-01T00:00:00-04:00", "last_seen": "2026-09-09T00:00:00-04:00", "presence": "ONLINE"}
    assert lan.presence_state(d, True, "2026-09-09T00:05:00-04:00", False, "2026-08-01T00:00:00-04:00") == "ONLINE"
    assert lan.presence_state(d, True, "2026-09-09T00:05:00-04:00", True, "2026-08-01T00:00:00-04:00") == "FLAPPING"
def test_risk_score_untrusted_risky_port():
    assert lan.risk_score({"trusted": False, "ports": {"23": "x"}}, True) > lan.risk_score({"trusted": True, "ports": {}}, False)
def test_dga():
    assert dnsstats.DGA_RE.match("x7f9q2m4k8z1p3.xyz") and not dnsstats.DGA_RE.match("apple.com")
def test_site_security_headers():
    r = sites.site_security("example.com", {"strict-transport-security": "max-age=1", "x-content-type-options": "nosniff"})
    assert r["hsts"] is True
```
(Adjust the expected keys after reading the Nexus function; the assertion must express the real Nexus behaviour, not be weakened.)
- [ ] **Step 2–4:** copy each function verbatim from the line ranges, replace `HERE` with `paths.DATA_DIR`, `notify(` calls with `from .notify import notify`, keep `IS_MAC` shims. Run tests → pass. Run `python -c "import guardian_sensor.sites, guardian_sensor.hosts, guardian_sensor.lan, guardian_sensor.home, guardian_sensor.domains, guardian_sensor.dnsstats, guardian_sensor.checks"` under Python 3.10.
- [ ] **Step 5: Commit** `sensor: port collectors (sites, hosts, lan, home, domains, dns stats, checks)`.

---

### Task 9: Port alerts engine, fix engine, notify — with parity tests

**Files:**
- Create: `sensor/guardian_sensor/{alerts,fix,notify}.py`, `sensor/tests/test_parity_alerts.py`, `sensor/tests/test_fix.py`, `sensor/tests/fixtures/{last.json,state.json}` (same stripped fixtures as hub)

**Interfaces:**
- `alerts.build_alerts(res, cfg, state)`, `alerts.security_score(res, cfg, state)`, `alerts.recommendations(res, cfg, state)`, `alerts.correlate_incidents(res, state)`, `alerts.unusual_hours(res, state)` ← checks.py 770–1087.
- `fix.FIX_ACTIONS, MANUAL_ONLY, FIX_ALERT_KEY, AUTO_COOLDOWN_H, APT`, `fix.apply_fix(cfg, host, action, arg=None, by="auto", lock=True, runner=None)`, `fix.remediate(res, cfg, state, runner=None)` ← 1088–1207. `runner(host_cfg, cmd, timeout) -> str` defaults to `util.ssh_run`; tests inject a fake.
- `notify.notify(title, msg, priority="default", actions=None, click=None, sev=None, env=None) -> list[str]` ← 239–298 (ntfy → macOS → Slack → Teams → webhook; routes from `cfg.integrations.routes`). Product strings: title prefix "Guardian".

- [ ] **Step 1: Parity test**
```python
import json, pathlib, copy
from guardian_sensor import alerts
FX = pathlib.Path(__file__).parent / "fixtures"
def load():
    return json.load(open(FX/"last.json")), json.load(open(FX/"config.json")), json.load(open(FX/"state.json"))
def test_build_alerts_parity():
    last, cfg, state = load()
    got = alerts.build_alerts(copy.deepcopy(last), cfg, copy.deepcopy(state))
    exp = {a["key"]: a["sev"] for a in last["alerts"]}
    assert {a["key"]: a["sev"] for a in got if not a["key"].startswith("lan:unusual-hour")} == exp
def test_score_parity():
    last, cfg, state = load()
    assert alerts.security_score(copy.deepcopy(last), cfg, copy.deepcopy(state))["total"] == last["score"]["total"]
def test_recommendations_parity():
    last, cfg, state = load()
    assert {r["key"] for r in alerts.recommendations(copy.deepcopy(last), cfg, copy.deepcopy(state))} == {r["key"] for r in last["recommendations"]}
```
The fixture `last.json` was produced by Nexus from the same state, so the ported functions must reproduce it. If a key differs, the port is wrong — fix the port, never the expectation (exception: keys that depend on wall-clock `now`, which the test may pin with `monkeypatch` of `util.now_iso`).

`test_fix.py`:
```python
from guardian_sensor import fix
def test_apply_fix_uses_runner_and_cooldown():
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return "ok"
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}], "auto_fix": {"enabled": True, "max_per_day": 20}}
    r = fix.apply_fix(cfg, "prod", "enable_ufw", by="test", lock=False, runner=runner)
    assert r["ok"] and r["action"] == "enable_ufw" and calls
def test_attacker_method_auto():
    assert fix.pick_attacker_method({"days": 3, "fails": 100}, "auto") == "block_subnet"
    assert fix.pick_attacker_method({"days": 1, "fails": 2500}, "auto") == "ban_long"
    assert fix.pick_attacker_method({"days": 1, "fails": 400}, "auto") == "block_ip"
```
(`pick_attacker_method` is the extracted decision helper from Nexus `remediate`; extract it so it is testable.)

- [ ] **Step 2–4:** port, run, pass under Python 3.10. **Step 5: Commit** `sensor: alerts/score/recs/incidents parity, fix engine, notify ladder`.

---

### Task 10: Sync + runner + DNS filter + installers

**Files:**
- Create: `sensor/guardian_sensor/{sync,runner,dnsfilter,install}.py`, `sensor/Dockerfile`, `sensor/entrypoint.sh`, `sensor/tests/{test_sync,test_dnsfilter}.py`

**Interfaces:**
- `sync.HubClient(url, token)` with `.pull(state) -> {"config":…, "edits":[…], "queued":[…]}` (GET config, GET edits?after=state.hub_edits_offset, GET queue), `.push(payload)` (POST ingest with `last/state/events/history/fixes/dns_queries/version`; `state.hub_sent = {"events": n_lines, "history": n_lines, "fixes": n_lines, "dns": n_lines}` line offsets, truncation-safe: if file smaller than offset → offset = 0), `.result(qid, ok, out, result)`. `sync.apply_edit(e, state, config)` ← Nexus `hubsync.apply_edit` (paths listed in the `Edit` model comment; `/api/rec` sets `state.rec_decisions[key]`; `/api/attacker` sets `config.auto_fix.attacker_method`; `/api/profile` sets `config.owner/profile`).
- `runner.run_once(cfg, state, args) -> res` = Nexus `main()` body (lines 1259–end) with: `hub_pull` → `HubClient.pull` + `apply_edit` loop; queued items executed (`/api/fix` → `fix.apply_fix`, `/api/speedtest` → `home.speed_test`, `/api/run` → no-op, `/api/reboot-plan` → `fix.apply_fix(..., "reboot")` only inside the window) and each reported via `.result`; writes `last.json`, `state.json`, appends `history.jsonl/events.jsonl/remediation.jsonl` in DATA_DIR exactly as Nexus; `hub_push` at the end. `runner.main(argv)` = lock + loop (`--once` runs one cycle; default sleeps 300 s between cycles).
- `dnsfilter` = Nexus `dns.py` with `DDIR = paths.p("dns")` and config from `config.load()`; `dnsfilter.main()` entry for `guardian-sensor dns`.
- `install.launchd()` writes `~/Library/LaunchAgents/com.guardian.sensor.plist` (+ `com.guardian.dns.plist` if `dns.enabled`) running `<venv>/bin/guardian-sensor run`, KeepAlive; `install.systemd()` writes `/etc/systemd/system/guardian-sensor.service` (+ `guardian-dns.service`), `systemctl enable --now`. Dockerfile: `python:3.12-slim`, `pip install .`, entrypoint `guardian-sensor run` with `RUN_DNS=1` starting the filter too; requires `--network host`.

- [ ] **Step 1: Tests** — `test_sync.py`: fake `urllib` via monkeypatched `HubClient._req` recording calls; assert `push` sends only new lines and advances `state.hub_sent`; assert truncation resets offset. `test_dnsfilter.py`: build a DNS A query packet for `ads.example.com` with `struct`, put `example.com` into custom block, assert `dnsfilter.is_blocked("ads.example.com") == "custom"` and `blocked_response()` returns an A record 0.0.0.0 with rcode 0.
- [ ] **Step 2–4:** implement, run, pass. Then a real dry run on the Mac against the local hub (Task 13 provides the hub; if executing earlier, use `uvicorn guardian_hub.main:app --port 8800` locally): `guardian-sensor enrol CODE --hub http://127.0.0.1:8800` then `guardian-sensor run --once` → hub `/api/v1/overview` shows the scan.
- [ ] **Step 5: Commit** `sensor: hub sync, runner loop, DNS filter, installers, Docker`.

---

### Task 11: import-nexus

**Files:**
- Create: `hub/guardian_hub/importer.py`, `hub/tests/test_importer.py`

**Interfaces:**
- `import_nexus(src_dir: str, tenant_map: dict[str,str])`: `src_dir` is a copy of `/opt/nexus-hub` (`hub.json`, `tenants/<t>/{config,state,last}.json`, `history.jsonl`, `events.jsonl`, `remediation.jsonl`, `audit.jsonl`, `reports/*.html`). For each Nexus tenant id → Guardian slug: create Tenant (name, brand, ntfy_topic, config = config.json minus `roles`/`public_url`, settings.profile from `config.owner`), users from `hub.json.users` (email unknown → `<username>@invalid.local`, flagged in output so Miro sets real emails), then `store_scan` with `last`, `state`, all history lines, all events lines, all remediation lines; copy reports into `data/reports/<slug>/` and insert Report rows (kind from filename weekly/monthly).

- [ ] **Step 1: Test** builds a fake `src_dir` in `tmp_path` from the fixtures (hub.json with tenant `myron`, users `miro` admin) and asserts Tenant slug `home` exists with ≥20 devices, a Scan, metrics > 0, and user `miro` membership admin.
- [ ] **Step 2–4:** implement, pass. **Step 5: Commit** `hub: import-nexus`.

---

### Task 12: `guardian selftest`

**Files:** `hub/guardian_hub/selftest.py`, `hub/tests/test_selftest.py`

**Interfaces:** `run(base_url=settings.base_url) -> int` printing `ok/FAIL` lines: health 200; login page 200; `/api/v1/me` 401 without cookie; DB reachable and migrated (alembic head == current); at least one tenant; latest scan age < 25 min for each tenant with an active sensor (else FAIL naming the tenant); `data/reports` writable; ntfy test push if `GUARDIAN_NTFY_TEST_TOPIC` set. Returns 0 when all ok. Test: run against the TestClient app via `httpx` transport → contains "health" ok and returns 1 (no tenants) then 0 after seeding.

- [ ] Steps: test → implement → pass → commit `hub: selftest`.

---

### Task 13: Deploy hub at guardian.example.com and enrol the Mac sensor

**Files:** `deploy/guardian-hub.service`, `deploy/nginx-guardian.conf`, `deploy/deploy.sh`

- [ ] **Step 1:** `deploy.sh` (run from the Mac): rsync `hub/` (excluding `.venv`, `data`, `tests`) and `web/dist/` (if present) to `root@203.0.113.10:/opt/guardian/`, then over ssh: `python3 -m venv /opt/guardian/venv && /opt/guardian/venv/bin/pip install -e /opt/guardian/hub`, write `/opt/guardian/hub/.env` only if missing (GUARDIAN_SECRET_KEY random, GUARDIAN_DATA_DIR=/opt/guardian/data, GUARDIAN_BASE_URL=https://guardian.example.com, SMTP_* left for Miro), `guardian migrate`, `systemctl daemon-reload && systemctl enable --now guardian-hub`.
- [ ] **Step 2:** `guardian-hub.service`: `ExecStart=/opt/guardian/venv/bin/uvicorn guardian_hub.main:app --host 127.0.0.1 --port 8800 --workers 1`, `WorkingDirectory=/opt/guardian/hub`, `EnvironmentFile=/opt/guardian/hub/.env`, `Restart=always`, `User=root` (same as nexus-hub today).
- [ ] **Step 3:** nginx server block for `guardian.example.com` → `proxy_pass http://127.0.0.1:8800`, acme.sh webroot `/var/www/acme` (same as safehome: `acme.sh --issue -d guardian.example.com -w /var/www/acme --server letsencrypt` then `--install-cert` with `reloadcmd "systemctl reload nginx"`). DNS: add the A record for guardian.example.com → 203.0.113.10 at the example.com DNS (cloudhost ns) — if the panel needs Miro, use `safehome.example.com/guardian/` path prefix temporarily? No: use a temporary self-signed cert on the IP is worse. **Decision:** create the A record via the same place safehome's was created (check `dig safehome.example.com` NS and the cloudhost DNS API used earlier in this project); if that needs credentials Miro has not given, stop and report.
- [ ] **Step 4:** On the VPS: `guardian tenant add home "Miro Home Network Security"`, `guardian user add miro you@example.com --tenant home --role admin --superadmin --name Miro`, `guardian enrol-code home`. Until SMTP is configured set `GUARDIAN_DEV_CONSOLE_OTP=1` and read the code from `journalctl -u guardian-hub` (Miro must supply SMTP creds; note this in the report).
- [ ] **Step 5:** On the Mac: `cd sensor && pip install -e .` into its venv, `guardian-sensor enrol CODE --hub https://guardian.example.com`, copy `~/.claude/nexus-agent/config.json` to `~/.guardian/config.json` and `.env` (NTFY_TOPIC, GROQ key) to `~/.guardian/.env`, `guardian-sensor run --once`, then `guardian-sensor install --launchd` (the Nexus collector keeps running; both use different data dirs; **do not** start guardian-dns on :53 yet).
- [ ] **Step 6:** `guardian selftest` on the VPS → all ok; `curl https://guardian.example.com/api/v1/health`.
- [ ] **Step 7:** Commit `deploy: hub service, nginx, deploy script`.

---

### Task 14: Web login + waiting shell (Vite React TS)

**Files:** `web/` (Vite scaffold: `package.json`, `vite.config.ts` with `base: "/"` and dev proxy `/api → http://127.0.0.1:8800`), `web/src/{main.tsx,App.tsx,api.ts,pages/Login.tsx,pages/Home.tsx,styles/tokens.css}`; hub `main.py` mounts `web/dist` at `/` with SPA fallback (`/api/*` first).

**Interfaces:** `api.ts`: `authStart(username)`, `authVerify(username, code, remember)`, `me()`, `overview()` using `fetch` with `credentials: "include"`. Login page: username → code step → redirect `/`. Home page (phase-1 placeholder): tenant name, score, verdict sentence, "last scan N min ago", list of open alerts; if no scan yet: enrol instructions with the copy-able command `guardian-sensor enrol <CODE> --hub <base_url>` and a "Generate enrol code" button (superadmin/admin), polling `/api/v1/overview` every 10 s. Design tokens from the spec §8 (blue/white, Manrope/Plex Mono, calm light + console dark by `prefers-color-scheme` and `data-theme`), shield logo SVG inline. This is a placeholder shell; the real design system is phase 2 — keep the page small.

- [ ] **Steps:** `npm create vite@latest web -- --template react-ts`; add pages; `npm run build`; hub serves `web/dist`; Playwright not yet — verify in the in-app browser: login with console OTP, see overview after the Mac sensor's first push. Commit `web: login + waiting shell`.

---

### Task 15: Phase closeout

- [ ] Run all tests: hub `pytest -q`, sensor `pytest -q` (under Python 3.10), `guardian selftest` on the VPS, browser screenshot of `https://guardian.example.com` after login showing the real score.
- [ ] Push the repo to `aicode.example.com` GitLab (`ai/guardian`) using the existing token workflow from memory `reference_aicode_gitlab_instance`.
- [ ] Update memory `project_guardian_app.md` with: hub path, service names, ports, enrol procedure, what Miro still needs to supply (SMTP creds for real OTP email, DNS record if blocked).
