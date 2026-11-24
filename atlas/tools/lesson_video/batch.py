"""Resumable batch: render and gate every lesson in queue.json, staging only.

    python tools/lesson_video/batch.py [--workers 3] [--retries 2]

State lives in data/academy-staging/video/batch-state.json (id -> status), so a
restart skips what already passed its gate. Progress is written to PROGRESS.md.
A lesson that fails its gate is rendered again, at most `retries` more times,
then listed as failed. Output only ever goes to data/academy-staging/video/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "data" / "academy-staging" / "video"
STATE = OUT / "batch-state.json"
PROGRESS = OUT / "PROGRESS.md"
MIN_FREE_GB = 12
LOCK = threading.Lock()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def free_gb() -> float:
    return shutil.disk_usage(OUT).free / 1e9


def write_progress(queue: list[dict], state: dict, t0: float) -> None:
    todo = [q for q in queue if not q["pilot"]]
    done = [q for q in todo if state.get(q["id"], {}).get("status") == "pass"]
    bad = [q for q in todo if state.get(q["id"], {}).get("status") == "failed"]
    left = [q for q in todo if q not in done and q not in bad]
    secs = sum(state[q["id"]].get("render_s", 0) for q in done)
    mins = sum(q["audio_s"] for q in done) / 60
    rest = sum(q["audio_s"] for q in left) / 60
    eta = ""
    if mins > 0:
        wall = (time.time() - t0) / 60
        eta = f"{rest * wall / mins / 60:.1f} h"
    size = sum(state[q["id"]].get("size_mb", 0) for q in done)
    lines = ["# Lesson video batch (staging)", "",
             f"- updated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- done (gate pass): {len(done)} / {len(todo)}",
             f"- failed after retries: {len(bad)}",
             f"- left: {len(left)}",
             f"- ETA: {eta or 'unknown yet'} (this run's pace)",
             f"- size so far: {size / 1024:.2f} GB, render cpu time {secs / 3600:.1f} h",
             f"- disk free: {free_gb():.0f} GB", ""]
    if bad:
        lines += ["## Failed", ""] + [
            f"- {q['id']}: {state[q['id']].get('why', '')}" for q in bad] + [""]
    lines += ["## Done", ""] + [f"- {q['id']} ({state[q['id']].get('size_mb', 0)} MB, "
                                f"{state[q['id']].get('render_s', 0):.0f} s)" for q in done]
    PROGRESS.write_text("\n".join(lines) + "\n")


def run_one(q: dict, retries: int) -> dict:
    py = sys.executable
    env = dict(os.environ, PATH="/Users/mac/.local/bin:" + os.environ.get("PATH", ""))
    why = ""
    for attempt in range(retries + 1):
        while free_gb() < MIN_FREE_GB:
            time.sleep(60)
        cmd = [py, str(HERE / "motion.py"), q["dir"], "--id", q["id"]]
        if q["senior"]:
            cmd.append("--senior")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3 * 3600)  # noqa: PLW1510
        except subprocess.TimeoutExpired:
            why = "render timed out"
            continue
        if r.returncode != 0:
            why = "render error: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200]
            continue
        info = json.loads(r.stdout.strip().splitlines()[-1])
        mp4 = OUT / f"lesson-{q['id']}.mp4"
        g = subprocess.run([py, str(HERE / "gate.py"), str(mp4)], capture_output=True,  # noqa: PLW1510
                           text=True, env=env, timeout=1800)
        try:
            gj = json.loads(g.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            gj = {"pass": False}
        if gj.get("pass"):
            return {"status": "pass", "render_s": info["render_s"], "size_mb": info["size_mb"],
                    "attempts": attempt + 1, "gate": gj}
        why = "gate: " + ",".join(k for k, v in gj.items() if v is False)
    return {"status": "failed", "why": why, "attempts": retries + 1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--retries", type=int, default=2)
    a = ap.parse_args()
    queue = json.loads((OUT / "queue.json").read_text())
    state = load_state()
    t0 = time.time()
    todo = [q for q in queue if not q["pilot"]
            and state.get(q["id"], {}).get("status") not in ("pass", "failed")]

    def job(q: dict) -> None:
        res = run_one(q, a.retries)
        with LOCK:
            state[q["id"]] = res
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1))
            os.replace(tmp, STATE)
            write_progress(queue, state, t0)

    write_progress(queue, state, t0)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(job, todo))
    write_progress(queue, state, t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
