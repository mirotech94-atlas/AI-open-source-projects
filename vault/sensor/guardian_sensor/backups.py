"""Verifying that the backups someone already has are actually working.

The MSP spec calls this the cheapest feature with the highest consequence,
and it is careful about the framing: this is NOT backup software. It checks
that whatever the customer already runs is still running, because the
common failure is not "nobody set up backups", it is "backups stopped
fourteen months ago and the job still exits zero".

WHAT IS OBSERVABLE, AND WHAT IS NOT

Observable over SSH, which is what this collects:

* did a new artifact appear inside the expected window
* is its size within a sane band of the run before it -- a backup that
  suddenly drops to a tenth of its usual size is the classic silent
  failure, and it is invisible to any check that only asks "did the job
  run?"
* is the destination reachable, and does it have headroom

NOT observable, and deliberately not faked: whether the backup can actually
be restored. The spec wants "Last verified restore: 3 September" and is
right that it is the most valuable line in a compliance pack -- but a
restore test means running a customer-supplied command, and Vault's
strongest security claim is that it runs a fixed allow-list and not a
shell. Recording a date an operator typed would be worse: a control
answered from what someone asserted is not evidence, which is exactly the
reasoning `controls.encryption_at_rest` uses to stay outside scope.

So `backup_verification` becomes PARTIAL, not evidenced. Vault can prove
the job is producing plausible artifacts to a reachable destination. It
cannot prove they restore, and the pack must say so.
"""
import datetime as dt

from . import util

#: A job that has not produced anything in this multiple of its own period
#: is late. 1.5 rather than 1.0 because a nightly job that normally lands at
#: 02:00 and slips to 03:30 after a slow week is not a failure, and a check
#: that pages for that gets muted within a fortnight.
LATE_FACTOR = 1.5

#: A new artifact smaller than this fraction of the previous one is treated
#: as a silent failure rather than a small backup. Chosen loose: real
#: backups fluctuate, and the signature being caught here is the order-of-
#: magnitude collapse (a dump that now contains only an error message), not
#: a 20% week.
SHRINK_FRACTION = 0.5

#: Destination fullness at which a backup is about to start failing.
FULL_PCT = 90

#: One shell call per host, whatever the job count: `find` the newest file
#: under each path and report its age, size and the filesystem's fullness.
#: `-printf` is GNU; the BSD fallback keeps a macOS or BSD host from
#: reporting a false "no artifact" when the directory is fine.
_REMOTE = r'''
for P in %PATHS%; do
  if [ ! -d "$P" ]; then echo "$P|missing||"; continue; fi
  LINE=$(find "$P" -type f -printf '%T@ %s\n' 2>/dev/null | sort -rn | head -1)
  if [ -z "$LINE" ]; then
    LINE=$(find "$P" -type f -exec stat -f '%m %z' {} \; 2>/dev/null | sort -rn | head -1)
  fi
  PCT=$(df -P "$P" 2>/dev/null | awk 'NR==2{gsub(/%/,"",$5); print $5}')
  if [ -z "$LINE" ]; then echo "$P|empty||$PCT"; else echo "$P|ok|$LINE|$PCT"; fi
done
'''


def _parse(line: str) -> dict | None:
    parts = line.split("|")
    if len(parts) < 4:
        return None
    path, status, stamp, pct = parts[0], parts[1], parts[2].strip(), parts[3].strip()
    out = {"path": path, "status": status,
           "free_pct": (100 - int(pct)) if pct.isdigit() else None}
    if status == "ok" and stamp:
        bits = stamp.split()
        if len(bits) >= 2:
            try:
                out["mtime"] = float(bits[0])
                out["bytes"] = int(bits[1])
            except ValueError:
                out["status"] = "unreadable"
    return out


def check_backups(cfg, state):
    """Every configured backup job, judged against its own last run.

    Returns `[{name, host, path, age_hours, bytes, ...}]`. A job Vault
    could not reach reports `reachable: False` and NO verdict -- an
    unreachable host has not told us the backup is late, and saying it did
    would be the unmeasured-reads-as-failed mirror of the over-claiming this
    codebase keeps guarding against.
    """
    jobs = cfg.get("backups") or []
    if not jobs:
        return []
    hosts = {h["name"]: h for h in (cfg.get("ssh_hosts") or [])}
    prev = (state.get("backups") or {})
    now = dt.datetime.now().astimezone()

    by_host: dict[str, list[dict]] = {}
    for job in jobs:
        by_host.setdefault(job.get("host") or "", []).append(job)

    out = []
    for host_name, host_jobs in by_host.items():
        host = hosts.get(host_name)
        seen: dict[str, dict] = {}
        if host:
            paths = " ".join(f'"{j["path"]}"' for j in host_jobs if j.get("path"))
            try:
                rc, text = util.ssh_run(host, _REMOTE.replace("%PATHS%", paths), timeout=45)
            except Exception:
                rc, text = 1, ""
            if rc == 0:
                for line in (text or "").splitlines():
                    got = _parse(line)
                    if got:
                        seen[got["path"]] = got

        for job in host_jobs:
            name = job.get("name") or job.get("path") or "backup"
            row = {"name": name, "host": host_name, "path": job.get("path") or "",
                   "every_hours": int(job.get("every_hours", 24))}
            found = seen.get(job.get("path") or "")
            if host is None or not found:
                # Not measured. No verdict of any kind.
                row.update(reachable=False, note="host or destination not reachable this sweep")
                out.append(row)
                continue

            row["reachable"] = True
            row["free_pct"] = found.get("free_pct")
            row["destination_full"] = (found.get("free_pct") is not None
                                       and found["free_pct"] <= (100 - FULL_PCT))
            if found["status"] != "ok" or "mtime" not in found:
                row.update(present=False, note={"missing": "backup directory does not exist",
                                                "empty": "no backup artifact found"}
                           .get(found["status"], "could not read the newest artifact"))
                out.append(row)
                continue

            age_h = max(0.0, (now.timestamp() - found["mtime"]) / 3600.0)
            last_bytes = (prev.get(name) or {}).get("bytes")
            row.update(
                present=True,
                age_hours=round(age_h, 1),
                bytes=found["bytes"],
                newest=dt.datetime.fromtimestamp(found["mtime"]).astimezone().isoformat(timespec="seconds"),
                late=age_h > row["every_hours"] * LATE_FACTOR,
                previous_bytes=last_bytes,
                shrank=bool(last_bytes and found["bytes"] < last_bytes * SHRINK_FRACTION),
                too_small=bool(job.get("min_bytes") and found["bytes"] < int(job["min_bytes"])),
            )
            out.append(row)

    # Remember sizes for the next sweep's shrink comparison. Only successful
    # reads: a missed sweep must not become next sweep's "it shrank".
    state["backups"] = {**prev, **{r["name"]: {"bytes": r["bytes"]}
                                   for r in out if r.get("bytes")}}
    return out
