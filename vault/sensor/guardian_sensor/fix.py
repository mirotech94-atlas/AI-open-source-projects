"""Auto-fix / remediation engine: the catalogue of safe fix actions, the SSH
runner that executes them, and the automatic-remediation pass that decides
which alerts are safe to fix unattended.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import base64
import datetime as dt
import json
import os
import re
import shlex
import threading
import time

from . import paths, util

# Serialises the actual command execution below (never the `flock` wait a
# few lines down, which no live caller still uses with `lock=True` -- see
# runner.py's module docstring for why) against the SAME host: the fast
# queue poller (a dashboard-queued fix/reboot) and the scan's own
# `remediate()` auto-fixes run in different threads of the SAME process, and
# without this they could run a conflicting command against the same host at
# the same moment (e.g. two concurrent apt runs -> a dpkg lock error) or
# interleave their `remediation.jsonl` appends.
#
# Deliberately PER-HOST, not one global lock: `security_updates`/
# `full_upgrade` can legitimately run for up to 1500s (see FIX_ACTIONS), and
# a single process-wide lock would make a dashboard-queued job on a
# DIFFERENT host wait behind that entire run -- exactly the multi-minute
# latency this task exists to remove. Two fixes racing the same host is rare
# (cooldowns + the daily cap already throttle `remediate()`) and briefly
# queuing behind an in-progress fix on that one host is the correct,
# intentional behaviour, not a bug: you would not want two commands hitting
# the same box at once either.
_HOST_LOCKS_GUARD = threading.Lock()   # guards creation of the per-host locks below
_HOST_LOCKS: dict = {}


def _host_lock(host):
    with _HOST_LOCKS_GUARD:
        lk = _HOST_LOCKS.get(host)
        if lk is None:
            lk = _HOST_LOCKS[host] = threading.Lock()
        return lk

APT = "DEBIAN_FRONTEND=noninteractive apt-get -y -q -o DPkg::Lock::Timeout=600 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
# A fix's `arg` lands inside a root shell on a customer host, so each action
# that takes one accepts only a strict shape, checked before any command is
# built (audit 2026-09-28). No shape here means the action takes no arg.
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}")
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9.+:~_-]{0,127}")
_PROCESS = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _is_ip(v: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def _is_net(v: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_network(v, strict=False)
        return True
    except ValueError:
        return False


def _words(pattern: re.Pattern, v: str, *, required: bool) -> bool:
    words = v.split()
    return (bool(words) or not required) and all(pattern.fullmatch(w) for w in words)


def arg_problem(action: str, arg) -> str | None:
    """Why `arg` is not acceptable for `action`, or None when it is."""
    a = "" if arg is None else str(arg)
    if action in ("block_ip", "ban_long"):
        return None if _is_ip(a) else f"{action} needs an IP address"
    if action == "block_subnet":
        return None if _is_net(a) else "block_subnet needs a network like 203.0.113.0/24"
    if action == "restart_service":
        return None if _UNIT.fullmatch(a) else "restart_service needs a plain unit name"
    if action == "restart_container":
        return None if _CONTAINER.fullmatch(a) else "restart_container needs a plain container name"
    if action == "kill_miner":
        return None if _words(_PROCESS, a, required=True) else "kill_miner needs plain process names"
    if action == "security_updates":
        return None if _words(_PACKAGE, a, required=False) else "security_updates takes plain package names"
    return None


FIX_ACTIONS = {
    "enable_fail2ban":  ("enable fail2ban", "apt-get install -y -q fail2ban >/dev/null 2>&1; systemctl enable --now fail2ban >/dev/null 2>&1; systemctl is-active fail2ban", 180),
    "security_updates": ("install pending security updates", "apt-get update -q >/dev/null 2>&1; %s install --only-upgrade %s 2>&1 | tail -2; P2=$(apt-get -s upgrade 2>/dev/null | grep \"^Inst \" | grep -i security | grep -vE \"^Inst (docker|containerd|nginx )\" | awk \"{print \\$2}\" | tr \"\\n\" \" \"); [ -n \"$P2\" ] && %s install --only-upgrade $P2 2>&1 | tail -2; echo remaining=$(apt-get -s upgrade 2>/dev/null | grep \"^Inst \" | grep -ci security)", 1500),
    "enable_unattended": ("turn on automatic security updates", "apt-get install -y -q unattended-upgrades >/dev/null 2>&1; printf 'APT::Periodic::Update-Package-Lists \"1\";\\nAPT::Periodic::Unattended-Upgrade \"1\";\\n' > /etc/apt/apt.conf.d/20auto-upgrades; systemctl enable --now unattended-upgrades >/dev/null 2>&1; systemctl is-enabled unattended-upgrades", 240),
    "kill_miner":       ("kill malware process", "for p in %s; do pkill -9 -x \"$p\"; done; ps -eo comm= | grep -Eic 'xmrig|kdevtmpfsi|kinsing|minerd|tsunami' ", 30),
    "harden_fail2ban":  ("harden fail2ban (3 tries, 24h ban, escalating)", "mkdir -p /etc/fail2ban/jail.d; rm -f /etc/fail2ban/jail.d/nexus.conf; printf '[DEFAULT]\\nbantime = 24h\\nfindtime = 1h\\nmaxretry = 3\\nbantime.increment = true\\nbantime.maxtime = 5w\\n[sshd]\\nenabled = true\\nbantime = 24h\\nfindtime = 1h\\nmaxretry = 3\\n' > /etc/fail2ban/jail.d/zz-nexus.local; systemctl restart fail2ban; sleep 3; echo bantime=$(fail2ban-client get sshd bantime) maxretry=$(fail2ban-client get sshd maxretry)", 60),
    "block_ip":         ("block attacker IP in the firewall", "ufw insert 1 deny from %s to any >/dev/null 2>&1 && echo blocked", 30),
    "block_subnet":     ("block the attacker's whole /24 network", "ufw insert 1 deny from %s to any >/dev/null 2>&1 && echo blocked-subnet", 30),
    "ban_long":         ("fail2ban ban for 30 days", "fail2ban-client set sshd banip %s >/dev/null 2>&1; fail2ban-client set sshd bantime 2592000 >/dev/null 2>&1; echo banned-30d", 30),
    "limit_ssh":        ("rate-limit SSH (6 connections / 30s per IP)", "ufw limit 22/tcp >/dev/null 2>&1 && echo limited", 30),
    "restart_container":("restart container", "docker restart %s >/dev/null 2>&1 && sleep 5 && docker ps --format '{{.Names}}:{{.Status}}' | grep '^%s:'", 90),
    "restart_service":  ("restart service", "systemctl restart %s; sleep 2; systemctl is-active %s", 60),
    "cleanup_disk":     ("free disk space", "apt-get clean; journalctl --vacuum-size=200M >/dev/null 2>&1; docker image prune -f >/dev/null 2>&1; df -P / | awk 'NR==2{print $5}'", 300),
    # manual (dashboard button) only — lock-out or downtime risk
    "disable_ssh_password": ("switch SSH to key-only login", "mkdir -p /etc/ssh/sshd_config.d; printf 'PasswordAuthentication no\\nPermitRootLogin prohibit-password\\n' > /etc/ssh/sshd_config.d/99-nexus.conf; sshd -t && (systemctl reload ssh 2>/dev/null || systemctl reload sshd) && sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '", 30),
    "enable_ufw":       ("enable firewall for listening ports only", "apt-get install -y -q ufw >/dev/null 2>&1; ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null; ufw allow 22/tcp >/dev/null; for p in $(ss -Hltn | awk '{print $4}' | grep -v '^127\\.' | grep -v '^\\[::1\\]' | sed 's/.*://' | sort -un); do ufw allow $p/tcp >/dev/null; done; ufw --force enable; ufw status | head -12", 90),
    "reboot":           ("reboot host", "systemctl reboot", 15),
    "install_debsecan": ("install vulnerability scanner (debsecan)", "%s install debsecan >/dev/null 2>&1; debsecan --suite $(. /etc/os-release; echo $VERSION_CODENAME) --only-fixed 2>/dev/null | wc -l", 300),
    "full_upgrade":     ("full apt upgrade (all packages)", "apt-get update -q >/dev/null 2>&1; B=$(apt-get -s upgrade 2>/dev/null | grep -c \"^Inst \"); %s dist-upgrade 2>&1 | tail -3; A=$(apt-get -s upgrade 2>/dev/null | grep -c \"^Inst \"); echo upgraded=$((B-A)) remaining=$A reboot=$(test -f /var/run/reboot-required && echo yes || echo no)", 1500),
    # Built dynamically by `security_headers_cmd()` below -- the command body
    # here is a placeholder; `apply_fix()` overwrites it per-call with the
    # exact domain and the exact missing headers before it ever runs. See
    # that function's docstring for the refuse/backup/test/restore contract.
    "security_headers": ("add missing security headers (HSTS, X-Content-Type-Options, X-Frame-Options)", "", 60),
}
MANUAL_ONLY = {"reboot"}

#: The one directive per header key this action will EVER add. `csp` is
#: deliberately absent: a Content-Security-Policy has to name the exact
#: script/style/image origins the site actually loads, which only the site's
#: owner knows -- a generic policy this fix could invent on its own would be
#: as likely to break the site as to protect it, and an untested `nginx -t`
#: pass proves the config parses, not that the page still works. Same for
#: an HTTPS redirect: it means editing the plain-HTTP server block, a
#: different one from the block these directives go into, and that is a
#: separate, deliberate change this fix does not make yet. Both stay
#: something only the owner (or a future, separately-reviewed fix) does.
SECURITY_HEADER_DIRECTIVES = {
    "hsts": 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
    "nosniff": 'add_header X-Content-Type-Options "nosniff" always;',
    "frame": 'add_header X-Frame-Options "SAMEORIGIN" always;',
}

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def security_headers_cmd(domain, header_keys):
    """The remote shell command that adds exactly the missing, safe-to-set
    headers (`SECURITY_HEADER_DIRECTIVES`) to the ONE nginx server block
    that answers for `domain` -- the fix for "Security headers present"
    that Vault used to tell the owner it had no way to run, even on a
    host it holds SSH to, because nobody had written it (see `remedy.py`'s
    module docstring / the owner's report).

    Safety, in order:
      1. refuses if `nginx` is not on this host's PATH at all -- never
         guesses at another web server's config syntax;
      2. refuses unless the live config (`nginx -T`) shows EXACTLY ONE
         `server_name` line naming `domain` -- ambiguity (zero matches, or
         more than one server block claiming the name) means Vault
         cannot be sure which block is the right one, and it will not guess;
      3. backs up the one file it is about to touch before changing
         anything;
      4. inserts the missing `add_header` lines right after the matching
         `server_name` line (inside that server block, before any
         `location` that could otherwise shadow them);
      5. runs `nginx -t` against the WHOLE config -- never reloads
         anything untested;
      6. on a failing test: restores the backup byte-for-byte and reports
         the failure, never leaves the broken edit in place;
      7. only on a passing test does it reload nginx, and only then does
         it report success.

    `domain` must already be a bare hostname (validated here again, even
    though the caller validates too) -- it is interpolated into a remote
    shell command, and a value that were not a plain hostname could inject
    a second command into what SSH runs.
    """
    directives = "\n".join(SECURITY_HEADER_DIRECTIVES[k] for k in header_keys
                           if k in SECURITY_HEADER_DIRECTIVES)
    if not directives:
        return "echo 'GUARDIAN_REFUSED: no header this fix knows how to add was requested'; exit 2"
    if not domain or not _HOSTNAME_RE.match(domain):
        return "echo 'GUARDIAN_REFUSED: not a plain hostname, refusing to guess'; exit 2"
    d = shlex.quote(domain)
    b64 = shlex.quote(base64.b64encode(directives.encode()).decode())
    return (
        'command -v nginx >/dev/null 2>&1 || '
        '{ echo "GUARDIAN_REFUSED: nginx is not on this host, I only know how to edit nginx"; exit 2; }; '
        f'D={d}; B64={b64}; '
        'MATCHES=$(nginx -T 2>/dev/null | grep -c "server_name.*$D"); '
        'if [ "$MATCHES" -ne 1 ]; then '
        'echo "GUARDIAN_REFUSED: found $MATCHES server_name matches for $D in the live nginx config, '
        'need exactly 1 to be confident which block is the right one"; exit 2; fi; '
        'CONF=$(nginx -T 2>/dev/null | awk -v d="$D" '
        '\'/^# configuration file/{f=$0; sub(/^# configuration file /,"",f); sub(/:$/,"",f)} '
        '$0 ~ /server_name/ && index($0,d) {print f; exit}\'); '
        'if [ -z "$CONF" ]; then echo "GUARDIAN_REFUSED: could not identify the config file for $D"; exit 2; fi; '
        'TS=$(date +%s); '
        'cp "$CONF" "$CONF.guardian-bak-$TS" || '
        '{ echo "GUARDIAN_REFUSED: could not back up $CONF, refusing to edit it unbacked-up"; exit 2; }; '
        # `hdrs` is exported and read via `ENVIRON`, not passed as a `-v`
        # assignment: a `-v` value that embeds a raw newline (three add_header
        # lines here) is a syntax error on some awk implementations (BSD
        # awk, as shipped on macOS, is one) even though the exact same
        # multi-line value is a perfectly ordinary environment variable.
        'export HDRS=$(echo "$B64" | base64 -d); '
        'awk -v d="$D" \'{print} '
        '$0 ~ /server_name/ && index($0,d) && !ins {print ENVIRON["HDRS"]; ins=1}\' "$CONF" > "$CONF.guardian-tmp" '
        '&& mv "$CONF.guardian-tmp" "$CONF"; '
        'TEST_OUT=$(nginx -t 2>&1); TEST_RC=$?; '
        'if [ "$TEST_RC" -ne 0 ]; then '
        'cp "$CONF.guardian-bak-$TS" "$CONF"; '
        'echo "GUARDIAN_FAILED: nginx -t failed, restored $CONF from backup -- $TEST_OUT"; exit 1; fi; '
        '(systemctl reload nginx 2>/dev/null || nginx -s reload) '
        '&& echo "GUARDIAN_OK: added headers to $CONF for $D" '
        '|| { echo "GUARDIAN_FAILED: config test passed but the reload itself failed for $CONF"; exit 1; }'
    )

#: Actions a recommendation's "yes" alone must never be enough to run --
#: the same disruptive set the hub gates behind a confirm-token before
#: `POST /api/v1/fix` will queue them (`chat/tiers.TIER3_FIXES`; the hub
#: cannot be imported here, so this is that table's sensor-side mirror,
#: same relationship `FIX_ALERT_KEY`/`FIX_LABELS` already have with
#: `guardian_hub/remedy.py`). Without this, `remediate()`'s consent loop
#: below would run one of these unattended on the next scan the moment
#: `state.rec_decisions[key]["decision"] == "yes"` synced down -- reboot
#: included -- with no second confirmation, which is exactly the tier the
#: hub's own `/fix/confirm` step exists to prevent. A "yes" on one of these
#: still only leaves the recommendation queued for chat's tier-3 proposal;
#: it does not run here.
CONSENT_REQUIRES_CONFIRM = {
    "reboot", "full_upgrade", "disable_ssh_password", "block_subnet",
    "ban_long", "limit_ssh", "security_headers",
}

#: Actions that must never run unattended, whatever else permits them.
#:
#: `block_subnet` blocks a whole /24 -- 256 addresses, not one attacker. The
#: owner's own auto-fix policy note lists "block attacker IPs" among the safe
#: unattended actions, and blocking a range of 256 is wider than that. He was
#: asked and chose: anything subnet-wide waits for approval.
#:
#: This is enforced in `go()` below rather than only in the consent sweep,
#: because the attacker auto-response reaches `apply_fix` by a different path
#: and skipped the consent check entirely. A rule enforced in one of two
#: doorways is not a rule.
NEVER_UNATTENDED = {"block_subnet"}
FIX_ALERT_KEY = {"block_subnet": "sec:attacker:{h}:{a}", "ban_long": "sec:attacker:{h}:{a}", "limit_ssh": "bruteforce:{h}", "full_upgrade": "upd:pending:{h}", "disable_ssh_password": "sec:sshpw:{h}", "enable_ufw": "sec:ufw:{h}", "security_updates": "sec:secupd:{h}", "enable_unattended": "sec:unatt:{h}", "enable_fail2ban": "fail2ban:{h}", "block_ip": "sec:attacker:{h}:{a}",
                 "restart_container": "docker:{h}:{a}", "restart_service": "svc:{h}:{a}", "cleanup_disk": "disk:{h}", "harden_fail2ban": "bruteforce:{h}"}
AUTO_COOLDOWN_H = {"block_subnet": 24 * 7, "ban_long": 24 * 7, "limit_ssh": 24 * 30, "install_debsecan": 24 * 7, "full_upgrade": 22, "disable_ssh_password": 24, "enable_ufw": 24, "harden_fail2ban": 24 * 7, "security_updates": 20, "enable_unattended": 24, "restart_container": 1, "restart_service": 1, "cleanup_disk": 6, "block_ip": 24 * 7, "enable_fail2ban": 6, "kill_miner": 0}


def pick_attacker_method(hist, method):
    """Decide the auto-fix response to a repeat SSH attacker.

    hist: {"days": <distinct days this IP has attacked in the last 14 days>,
           "fails": <failed logins in the last 24h>}.
    A non-"auto" method passes straight through. Otherwise: a repeat offender
    seen on 3+ different days gets its whole /24 blocked; a single-day huge
    burst (>=2000 fails) gets a long fail2ban ban; anything else just blocks
    the IP.
    """
    if method != "auto":
        return method
    if hist.get("days", 0) >= 3:
        return "block_subnet"
    if hist.get("fails", 0) >= 2000:
        return "ban_long"
    return "block_ip"


# Progress lines an in-flight `security_updates`/`full_upgrade` apt run
# emits as it works through the package list -- streamed one at a time to
# whatever `progress` callback the caller supplied (fix.apply_fix's
# `on_line` wrapper below counts these against the "Inst " total captured
# before the run).
_PROGRESS_LINE_RE = re.compile(r"^(Get|Unpacking|Setting up|Preparing)(:|\s)")
_PROGRESS_TOTAL_RE = re.compile(r"^PROGRESS_TOTAL=(\d+)$")
_PROGRESS_MARKER = 'T=$(apt-get -s upgrade 2>/dev/null | grep -c "^Inst "); echo PROGRESS_TOTAL=$T; '


def apply_fix(cfg, host, action, arg=None, by="auto", lock=True, runner=None, progress=None):
    """lock=True: wait for the collector's run lock so a manual fix never races the unattended apt run (dpkg lock errors).

    runner(host_cfg, cmd, timeout) executes the command and returns its output;
    it defaults to util.ssh_run (which returns (rc, output) — the rc is used
    to sharpen the ok check when a runner supplies one, but a runner that
    returns a plain output string, as tests may inject, works too).

    `progress(done, total, line)`, when given and `action` is
    "security_updates" or "full_upgrade", is called once per apt progress
    line the run streams back (see `_PROGRESS_LINE_RE`) -- `total` is the
    "Inst " count captured (via `_PROGRESS_MARKER`) before the real command
    runs, `done` is a running count of matched lines. A `runner` that never
    calls its `on_line` argument (the default when `progress` is None)
    simply never fires it, so this is a no-op for every other caller."""
    runner = runner or util.ssh_run
    paths.ensure()
    if lock:
        import fcntl
        lf = open(paths.p(".run.lock"), "w"); t0 = time.time()
        while True:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except OSError:
                if time.time() - t0 > 900: return {"ts": util.now_iso(), "host": host, "action": action, "ok": False, "out": "a scan (with its own apt run) is still holding the lock after 15 min — try again", "by": by}
                time.sleep(3)
    h = util.cfg_host(cfg, host)
    if not h or action not in FIX_ACTIONS: return {"ok": False, "out": "unknown host/action"}
    if (why := arg_problem(action, arg)) is not None:
        return {"ts": util.now_iso(), "host": host, "action": action, "ok": False, "out": f"refused: {why}", "by": by}
    label, cmd, tmo = FIX_ACTIONS[action]
    if action == "security_updates": cmd = cmd % (APT, arg or "", APT)
    elif action in ("full_upgrade", "install_debsecan"): cmd = cmd % APT
    elif action in ("restart_container", "restart_service"): cmd = cmd % (arg, arg)
    elif action == "security_headers":
        # `arg` is "domain|h1,h2,..." (`chat.tiers.fix_args`'s `arg` field for
        # this action) -- the domain the missing headers apply to and which
        # of `SECURITY_HEADER_DIRECTIVES` are actually missing, decided by
        # the caller (the hub already has the site's last scan). Never
        # trusted blind: `security_headers_cmd` re-validates the domain and
        # refuses on the host if it cannot confidently find one server block.
        domain, _, hdr_csv = (arg or "").partition("|")
        cmd = security_headers_cmd(domain, [k.strip() for k in hdr_csv.split(",") if k.strip()])
    elif "%s" in cmd: cmd = cmd % (arg or "")
    if action in ("security_updates", "full_upgrade", "enable_unattended", "enable_fail2ban", "enable_ufw"):
        cmd = "set -o pipefail; " + cmd  # a failing apt behind `| tail` must not look like success

    on_line = None
    if progress is not None and action in ("security_updates", "full_upgrade"):
        cmd = _PROGRESS_MARKER + cmd
        counter = {"total": 0, "done": 0}

        def on_line(line, _counter=counter):
            m = _PROGRESS_TOTAL_RE.match(line)
            if m:
                _counter["total"] = int(m.group(1))
                return
            if _PROGRESS_LINE_RE.match(line):
                _counter["done"] += 1
                progress(_counter["done"], _counter["total"], line)

    # Actual command execution + the remediation.jsonl append: see
    # _host_lock's comment above for why this -- and only this, and only
    # per-host -- is serialised.
    with _host_lock(host):
        result = runner(h, cmd, timeout=tmo, on_line=on_line) if on_line is not None else runner(h, cmd, timeout=tmo)
        rc, out = result if isinstance(result, tuple) else (0, result)
        ok = (rc == 0 and not re.search(r"^E: |Could not get lock|Unable to acquire|dpkg was interrupted", out, re.M)) or action == "reboot"
        if action == "security_headers" and re.search(r"GUARDIAN_(REFUSED|FAILED)", out or ""):
            # Belt and braces alongside `rc`: a test (or a runner shape that
            # never carries a real exit code -- see the `isinstance(result,
            # tuple)` fallback above, which defaults `rc` to 0) must still
            # see this as a failure when the script itself said so.
            ok = False
        rec = {"ts": util.now_iso(), "host": host, "action": action, "label": label, "arg": arg, "ok": ok, "out": out[-400:], "by": by}
        with open(paths.p("remediation.jsonl"), "a") as f: f.write(json.dumps(rec) + "\n")
    return rec


def remediate(res, cfg, state, runner=None):
    """Automatic fixes for things that are safe to do unattended. Everything is logged and pushed to the phone."""
    if not (cfg.get("auto_fix") or {}).get("enabled", True): return []
    done = []; cd = state.setdefault("fix_cooldown", {}); now = dt.datetime.now().astimezone(); th = cfg["thresholds"]
    cap = int((cfg.get("auto_fix") or {}).get("max_per_day", 30)); rp = paths.p("remediation.jsonl")
    today_n = sum(1 for l in open(rp) if l[8:18] == now.date().isoformat()) if os.path.exists(rp) else 0
    def due(key, action):
        last = cd.get(key)
        return not last or (now - dt.datetime.fromisoformat(last)).total_seconds() > AUTO_COOLDOWN_H.get(action, 6) * 3600
    def go(h, action, arg=None, key=None):
        key = key or f"{action}:{h['name']}:{arg or ''}"
        if action in NEVER_UNATTENDED:
            # Not run, and not silently dropped either: record what Vault
            # wanted to do so the dashboard can offer it, and the owner can
            # say yes to a real proposal. `block_subnet` is tier 3 on the hub,
            # so that "yes" goes through the ordinary propose-and-confirm path.
            res.setdefault("awaiting_approval", []).append(
                {"ts": res["ts"], "host": h["name"], "action": action, "arg": arg,
                 "why": "this blocks a whole /24, which is 256 addresses rather "
                        "than one attacker, so I will not do it without your say-so"}
            )
            return
        override = (((cfg.get("auto_fix") or {}).get("overrides") or {}).get(h["name"]) or {}).get(action)
        if override == "manual":
            res.setdefault("awaiting_approval", []).append({"ts": res["ts"], "host": h["name"], "action": action, "arg": arg,
                "why": "this host/action policy requires manual approval"})
            return
        if not due(key, action): return
        if today_n + len(done) >= cap: res["fix_cap_hit"] = True; return
        r = apply_fix(cfg, h["name"], action, arg, by="auto", lock=False, runner=runner); cd[key] = res["ts"]; done.append(r)
    # recommendations Myron accepted with "yes" that have a concrete action:
    # consent given, run them -- except a `CONSENT_REQUIRES_CONFIRM` action
    # (reboot and its siblings), which still waits on the hub's own tier-3
    # confirmation. Those queue and run through the ordinary dashboard-queue
    # path (`run_queue`'s `/api/fix`) once that second "yes" happens; this
    # unattended sweep never runs them, no matter how long "yes" has sat in
    # `rec_decisions`.
    dec = state.get("rec_decisions") or {}
    for r in res.get("recommendations") or []:
        d = dec.get(r["key"]) or {}
        if d.get("decision") == "yes" and d.get("status") in (None, "pending") and r.get("action"):
            a = r["action"]; hh = util.cfg_host(cfg, a.get("host"))
            if a.get("action") in CONSENT_REQUIRES_CONFIRM:
                continue
            if hh and a.get("action") in FIX_ACTIONS:
                if today_n + len(done) >= cap: res["fix_cap_hit"] = True; break
                rec = apply_fix(cfg, a["host"], a["action"], a.get("arg"), by="accepted-recommendation", lock=False, runner=runner); done.append(rec)
                d["status"] = "done" if rec.get("ok") else "failed"; d["result"] = (rec.get("out") or "")[-160:]; dec[r["key"]] = d
    state["rec_decisions"] = dec
    for h in res["ssh"]:
        if not h["reachable"]: continue
        sec = h.get("sec") or {}; n = h["name"]
        if sec.get("miners"): go(h, "kill_miner", " ".join(sec["miners"]))
        if th.get("require_fail2ban") and h.get("fail2ban") != "active": go(h, "enable_fail2ban")
        # fail2ban is on but single IPs still get hundreds of tries a day => ban is too short; tighten it once
        if h.get("fail2ban") == "active" and any(c >= th.get("attacker_block_24h", 300) for c in (sec.get("top_attackers") or {}).values()): go(h, "harden_fail2ban")
        for c, st in [d.split(":", 1) for d in h["docker"] if ":" in d]:
            if "unhealthy" in st or "Restarting" in st: go(h, "restart_container", c)
        for exp in (util.cfg_host(cfg, n).get("expect_docker") or []):
            if not any(d.startswith(exp + ":") and "Up" in d for d in h["docker"]): go(h, "restart_container", exp)
        for svc, st in h["services"].items():
            if st != "active": go(h, "restart_service", svc)
        if h["disk_pct"] >= th["disk_pct_crit"]: go(h, "cleanup_disk")
        pk = [p for p in sec.get("security_updates", []) if not re.match(r"^(docker|containerd|nginx$)", p)]
        if pk: go(h, "security_updates", " ".join(pk), key=f"security_updates:{n}")
        # full upgrade of everything pending, once a day; packages that restart Docker (containers blink) only in the 02:00-05:00 window
        upd = h.get("updates") or {}
        if upd.get("count"):
            touches_docker = any(re.match(r"^(docker|containerd)", p) for p in upd.get("pending", []))
            if not touches_docker or 2 <= now.hour < 5: go(h, "full_upgrade")
        if sec and not sec.get("unattended"): go(h, "enable_unattended")
        if h.get("cve_fixable", -1) == -1 and sec: go(h, "install_debsecan")
        # everything the bot CAN fix, it fixes. Key auth is proven by this very SSH session, so key-only login is safe.
        if sec.get("ssh_password"): go(h, "disable_ssh_password")
        if sec and sec.get("ufw") != "active": go(h, "enable_ufw")
        meth = (cfg.get("auto_fix") or {}).get("attacker_method", "auto"); hist = state.setdefault("attacker_hist", {})
        for ip, c in (sec.get("top_attackers") or {}).items():
            if c < th.get("attacker_block_24h", 300) or meth == "off": continue
            hist[ip] = sorted(set((hist.get(ip) or []) + [res["ts"][:10]]))[-14:]
            m = pick_attacker_method({"days": len(hist[ip]), "fails": c}, meth)
            if m in ("block_ip", "block_subnet") and sec.get("ufw") != "active": m = "ban_long"
            arg = ".".join(ip.split(".")[:3]) + ".0/24" if m == "block_subnet" else ip
            go(h, m, arg, key=f"{m}:{n}:{arg}")
        state["attacker_hist"] = {k: v for k, v in hist.items() if v and v[-1] >= (dt.datetime.now().astimezone() - dt.timedelta(days=30)).date().isoformat()}
    state["fix_cooldown"] = {k: v for k, v in cd.items() if (now - dt.datetime.fromisoformat(v)).total_seconds() < 8 * 86400}
    return done
