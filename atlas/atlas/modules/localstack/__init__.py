"""localstack — is the Spark reachable from ATLAS on the Mac?

ATLAS Agency runs on Miro's Mac; its PRIVATE model (qwen3.8, vLLM :8000) lives
on the Spark, loopback only there, reached through the launchd job
`com.atlas.spark-vllm-tunnel` (bootstrap/mac/install-local.sh).

The embedder (mxbai-embed-large, :8001) is retired (Miro, 2026-11-17): recall
is keyword only, so there is no embeddings check and the tunnel probes only
the model forward.

Watches, read-only (none of them changes `llm.ROUTES`):
  tunnel   GET <base>/health on LOCAL_LLM_BASE_URL (and LOCAL_LLM_BASE_URL_2
           when set), every minute.
  model    which models the server lists: UNKNOWN when the list cannot be
           read, DEGRADED when LOCAL_LLM_MODEL is not served, else OK.
  speed    median tokens a second and first-token time of real local answers
           in ATLAS_SPEED_WINDOW_MIN; UNKNOWN with no sample in the window,
           DEGRADED below ATLAS_SPEED_FLOOR_TPS.
  answers  which model last answered a person (`llm.answer_health`).
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from ...kernel import llm
from ...kernel.contracts import Check, Health, Observation, Severity

PROBE = "ATLAS checks that its memory can be searched by meaning."


def _forwards() -> tuple[tuple[str, str], ...]:
    out = [("model", "LOCAL_LLM_BASE_URL")]
    if os.getenv("LOCAL_LLM_BASE_URL_2", "").strip():
        out.append(("model 2", "LOCAL_LLM_BASE_URL_2"))
    return tuple(out)


def _root(url: str) -> str:
    """`http://h:18000/v1` -> `http://h:18000` (vLLM serves /health there)."""
    url = url.strip().rstrip("/")
    return url.removesuffix("/v1")


async def _health(what: str, var: str) -> str | None:
    """None when the forward answers /health with 200, else the problem."""
    url = os.getenv(var, "").strip()
    if not url:
        return f"{what}: {var} unset"
    if not llm.local_base_url_ok(url):
        return f"{what}: {var} is not a local address; not probed"
    root = _root(url)
    try:
        async with llm._local_client(10) as c:
            r = await c.get(f"{root}/health")
    except Exception as exc:  # noqa: BLE001
        return f"{what}: {root} did not answer ({type(exc).__name__})"
    if r.status_code != 200:
        return f"{what}: {root}/health answered HTTP {r.status_code}"
    return None


async def check_tunnel() -> Observation:
    problems = [p for p in await asyncio.gather(
        *(_health(what, var) for what, var in _forwards())) if p]
    if problems:
        return Observation("tunnel", Health.UNKNOWN, detail="; ".join(problems))
    return Observation("tunnel", Health.OK, float(len(_forwards())), "forwards",
                       "the model forward to the Spark answers")


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


async def check_model() -> Observation:
    """Is LOCAL_LLM_MODEL among the models the server lists? Reads only.
    A list that cannot be read is UNKNOWN, never OK (invariant 7)."""
    want = os.getenv("LOCAL_LLM_MODEL", "").strip() or "qwen3.8:27b"
    bases = [os.getenv("LOCAL_LLM_BASE_URL", "").strip()]
    if os.getenv("LOCAL_LLM_BASE_URL_2", "").strip():
        bases.append(os.getenv("LOCAL_LLM_BASE_URL_2", "").strip())
    unread, wrong, served = [], [], []
    for base in bases:
        names = await llm.served_models(base) if base else None
        if names is None:
            unread.append(_root(base) or "LOCAL_LLM_BASE_URL unset")
        elif want in names:
            served.append(base)
        else:
            wrong.append(f"{_root(base)} serves {sorted(names) or 'nothing'}")
    if wrong:
        return Observation("model", Health.DEGRADED, detail=(
            f"expected {want!r}; " + "; ".join(wrong)
            + (f"; unreadable: {', '.join(unread)}" if unread else "")))
    if not served:
        return Observation("model", Health.UNKNOWN, detail=(
            "the model list cannot be read: " + ", ".join(unread)))
    if unread:
        return Observation("model", Health.DEGRADED, detail=(
            f"{want!r} is served, but unreadable: {', '.join(unread)}"))
    return Observation("model", Health.OK, float(len(served)), "endpoints",
                       f"{want!r} is served")


async def check_speed() -> Observation:
    """Median tok/s and first-token time of real local answers in the window.
    No sample in the window is UNKNOWN: a figure is never remembered, and a
    configured provider is not a measured one."""
    window_min = _float_env("ATLAS_SPEED_WINDOW_MIN", 15.0)
    floor = _float_env("ATLAS_SPEED_FLOOR_TPS", 3.0)
    got = llm.local_speed(window_min * 60.0)
    if got is None:
        return Observation("speed", Health.UNKNOWN, detail=(
            f"no local answer in the last {window_min:g} min to measure"))
    at = datetime.fromtimestamp(got["at"], UTC)
    first = f", first token {got['ttft']:.1f}s" if got["ttft"] is not None else ""
    detail = (f"{got['tps']:.1f} tok/s median of {got['n']} answer(s)"
              f"{first}; newest read {at.astimezone().strftime('%H:%M')}")
    health = Health.DEGRADED if got["tps"] < floor else Health.OK
    if health is Health.DEGRADED:
        detail += f"; below the floor of {floor:g} tok/s"
    return Observation("speed", health, round(got["tps"], 2), "tok/s", detail,
                       at=at)


async def check_answers() -> Observation:
    """Which model last answered a person. Miro, 2026-11-17: every reply is
    qwen3.8 on the Spark first. OK only when the last one was; a fallback is
    DEGRADED and says why; none yet is UNKNOWN (read from the router's own
    record, so nothing is asked and nothing is guessed)."""
    health, detail = llm.answer_health()
    return Observation("answers", health, detail=detail)


class LocalStackModule:
    name = "localstack"
    title = "Local stack (Spark)"
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ("LOCAL_LLM_BASE_URL", "LOCAL_LLM_BASE_URL_2",
                                     "LOCAL_LLM_MODEL", "ATLAS_SPEED_WINDOW_MIN",
                                     "ATLAS_SPEED_FLOOR_TPS", "ATLAS_EMBED_URL",
                                     "ATLAS_EMBED_MODEL", "ATLAS_EMBED_API_KEY")
    setup_note = ("Run bootstrap/mac/install-local.sh (loads the Spark tunnel) "
                  "and set ATLAS_EMBED_URL=http://host.docker.internal:18001/v1.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("tunnel", check_tunnel, timedelta(minutes=1), Severity.NORMAL,
                  "the Mac's tunnel forwards to the Spark's model",
                  baseline_sigma=None, timeout=timedelta(seconds=25)),
            Check("model", check_model, timedelta(minutes=5), Severity.NORMAL,
                  "the Spark serves the model ATLAS is configured to ask for",
                  baseline_sigma=None, timeout=timedelta(seconds=25)),
            Check("speed", check_speed, timedelta(minutes=1), Severity.NORMAL,
                  "the Spark answers fast enough (median tokens a second)",
                  baseline_sigma=None, timeout=timedelta(seconds=5)),
            Check("answers", check_answers, timedelta(minutes=5), Severity.NORMAL,
                  "replies to people are answered by qwen3.8 on the Spark first",
                  baseline_sigma=None, timeout=timedelta(seconds=5)),
        )

    def actions(self):
        return ()

    async def signals(self):
        return ()

    def brief(self) -> str:
        return "Local stack: Spark model and embedder reachable through the tunnel"

    async def agenda(self, within: timedelta):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = LocalStackModule()
