"""SSH host health/security collector + macOS software-updates collector.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import os
import re
import subprocess

from . import util

REMOTE = r'''
H=$(hostname); U=$(uptime | sed "s/.*up //;s/,  *[0-9]* user.*//"); L=$(cut -d" " -f1 /proc/loadavg); C=$(nproc 2>/dev/null || echo 1)
D=$(df -P / | awk "NR==2{print \$5}" | tr -d %); M=$(free -m | awk "NR==2{printf \"%d %d\", \$3, \$2}")
F=$(journalctl -u ssh -u sshd --since "24 hours ago" 2>/dev/null | grep -c "Failed password")
F2=$(systemctl is-active fail2ban 2>/dev/null | head -1); F2=${F2:-absent}; B=$(fail2ban-client status sshd 2>/dev/null | grep "Currently banned" | awk "{print \$NF}")
R=$( [ -f /var/run/reboot-required ] && echo yes || echo no )
APTS=$(apt-get -s upgrade 2>/dev/null | grep "^Inst "); UPD=$(echo "$APTS" | grep -c "^Inst "); SECUPD=$(echo "$APTS" | grep -i security | awk "{print \$2}" | tr "\n" ",")
UPDL=$(echo "$APTS" | awk "{print \$2}" | head -80 | tr "\n" ","); KVER=$(uname -r); KINST=$(ls /boot/vmlinuz-* 2>/dev/null | sed "s/.*vmlinuz-//" | sort -V | tail -1); OSN=$(. /etc/os-release; echo $PRETTY_NAME | tr " " "_")
LASTAPT=$(grep -h "^End-Date" /var/log/apt/history.log 2>/dev/null | tail -1 | awk "{print \$2}")
HSSH=$(cat /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | md5sum | cut -c1-10); HKEY=$(cat /root/.ssh/authorized_keys 2>/dev/null | md5sum | cut -c1-10)
HCRON=$( (crontab -l 2>/dev/null; cat /etc/crontab /etc/cron.d/* 2>/dev/null) | md5sum | cut -c1-10); HUFW=$(ufw status numbered 2>/dev/null | md5sum | cut -c1-10); HCMP=$(cat /opt/*/docker-compose.yml /opt/*/docker-compose.yaml 2>/dev/null | md5sum | cut -c1-10)
DKI=$(docker images --format "{{.Repository}}:{{.Tag}}={{.CreatedSince}}" 2>/dev/null | grep -v "<none>" | head -6 | tr "\n" ",")
if command -v debsecan >/dev/null 2>&1; then UPGPK=" $(apt list --upgradable 2>/dev/null | awk -F/ "NR>1{print \$1}" | tr "\n" " ") "; CVERAW=$(debsecan --suite $(. /etc/os-release; echo $VERSION_CODENAME) --only-fixed --format summary 2>/dev/null | grep "^CVE"); CVEALL=$(echo "$CVERAW" | awk -v u="$UPGPK" "index(u, \" \" \$2 \" \")"); CVENOUP=$(echo "$CVERAW" | awk -v u="$UPGPK" "!index(u, \" \" \$2 \" \")" | grep -c "^CVE"); CVE=$(echo "$CVEALL" | grep -c "^CVE"); CVEL=$(echo "$CVEALL" | awk "/^CVE/{print \$1\" \"\$2\" \"\$NF}" | sort -u | head -15 | tr "\n" "~"); else CVE=-1; CVENOUP=0; CVEL=""; fi
SSHPW=$(sshd -T 2>/dev/null | awk "/^passwordauthentication /{print \$2}"); SSHRT=$(sshd -T 2>/dev/null | awk "/^permitrootlogin /{print \$2}")
UFW=$(ufw status 2>/dev/null | head -1 | awk "{print \$2}"); UNATT=$(dpkg -l unattended-upgrades 2>/dev/null | grep -c "^ii"); AUTOUP=$(grep -c "Unattended-Upgrade \"1\"" /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null)
PRELOAD=$( [ -s /etc/ld.so.preload ] && echo yes || echo no ); MINER=$(ps -eo comm= | grep -Ei "xmrig|kdevtmpfsi|kinsing|minerd|tsunami|\.x$" | sort -u | tr "\n" ",")
CRONC=$(grep -rlE "curl|wget" /etc/cron* /var/spool/cron 2>/dev/null | sort | tr "\n" ","); AK=$(cat /root/.ssh/authorized_keys 2>/dev/null | grep -c "^ssh")
TMPX=$(find /tmp /var/tmp /dev/shm -maxdepth 2 -type f -perm -u+x 2>/dev/null | head -5 | tr "\n" ",")
TOPIP=$(journalctl -u ssh -u sshd --since "24 hours ago" 2>/dev/null | grep "Failed password" | grep -oE "from [0-9.]+" | sort | uniq -c | sort -rn | head -3 | awk "{printf \"%s:%s,\", \$3, \$1}")
USERS=$(awk -F: "\$3>=1000 && \$7!~/nologin|false/{print \$1}" /etc/passwd | sort | tr "\n" ","); LASTIP=$(last -n 8 -i 2>/dev/null | grep -v "^reboot\|^wtmp\|^$" | awk "{print \$3}" | sort -u | tr "\n" ",")
DK=$(docker ps --format "{{.Names}}:{{.Status}}" 2>/dev/null | tr "\n" ";")
SV=""; for s in %SERVICES%; do SV="$SV$s=$(systemctl is-active $s 2>/dev/null | head -1);"; done
PORTS=$(ss -Hltn 2>/dev/null | awk "{print \$4}" | grep -v "^127\." | grep -v "^\[::1\]" | sed "s/.*://" | sort -un | tr "\n" ",")
CERTS=""; for f in /etc/letsencrypt/live/*/fullchain.pem /etc/letsencrypt/live/*/cert.pem; do [ -f "$f" ] || continue; CN=$(openssl x509 -in "$f" -noout -subject 2>/dev/null | sed "s/.*CN *= *//; s/,.*//"); ED=$(openssl x509 -in "$f" -noout -enddate 2>/dev/null | cut -d= -f2); [ -n "$CN" ] && [ -n "$ED" ] && CERTS="$CERTS$CN@$ED;"; done
echo "host=$H|up=$U|load=$L|cores=$C|disk=$D|mem=$M|failed24h=$F|fail2ban=$F2|banned=${B:-0}|reboot=$R|updates=$UPD|docker=$DK|services=$SV|ports=$PORTS|sshpw=$SSHPW|sshroot=$SSHRT|ufw=$UFW|unatt=$UNATT|autoup=$AUTOUP|secupd=$SECUPD|preload=$PRELOAD|miner=$MINER|cronc=$CRONC|authkeys=$AK|tmpx=$TMPX|topip=$TOPIP|users=$USERS|lastip=$LASTIP|updl=$UPDL|kver=$KVER|kinst=$KINST|os=$OSN|lastapt=$LASTAPT|drift=$HSSH:$HKEY:$HCRON:$HUFW:$HCMP|dki=$DKI|cve=$CVE|cvenoup=$CVENOUP|cvel=$CVEL|certs=$CERTS"
'''


def parse_certs(raw):
    """`name@Dec 25 10:00:00 2026 GMT;...` -> `{hostname: "YYYY-MM-DD"}`.

    Feeds the renewed-but-not-served check: certbot succeeded, the service
    was never reloaded, and the host goes on presenting the old certificate
    until it expires. The renewal timer reports success and anything that
    reads the FILE says all is well, so the only way to see it is to hold
    both vantage points at once -- which Vault does, reading the served
    certificate over TLS and the file over SSH.

    The NEWEST expiry wins when a host holds several for one name. Some
    layouts leave the previous file behind after a renewal, and the question
    being asked is what the host could be serving, not what it has lying
    around.

    Anything unparsable is skipped rather than raised or guessed at: a host
    without openssl or without certbot has nothing to say about
    certificates, and that is different from saying there is nothing newer.
    """
    import datetime as _dt

    out = {}
    for item in (raw or "").split(";"):
        name, _, ends = item.partition("@")
        name, ends = name.strip(), ends.strip()
        if not name or not ends:
            continue
        try:
            when = _dt.datetime.strptime(ends, "%b %d %H:%M:%S %Y %Z").date().isoformat()
        except ValueError:
            continue
        if name not in out or when > out[name]:
            out[name] = when
    return out


def check_ssh(h, th):
    out = {"name": h["name"], "target": h["target"], "reachable": False, "optional": h.get("optional", False)}
    if str(h["target"]).startswith("-"):  # would be read as an ssh option
        out["error"] = "refused: the SSH target is not a plain user@host"; return out
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new"]
    if h.get("key"):
        cmd += ["-i", os.path.expanduser(h["key"])]
    remote = REMOTE.replace("%SERVICES%", " ".join(h.get("expect_services", [])))
    try:
        r = subprocess.run(cmd + ["--", h["target"], remote], capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        out["error"] = "timeout"; return out
    line = [l for l in r.stdout.splitlines() if l.startswith("host=")]
    if r.returncode != 0 or not line:
        out["error"] = (r.stderr.strip().splitlines() or ["no output"])[-1][:120]; return out
    kv = dict(p.split("=", 1) for p in line[-1].split("|") if "=" in p)
    out["reachable"] = True
    out["hostname"] = kv.get("host"); out["uptime"] = kv.get("up")
    cores = max(1, int(kv.get("cores", "1") or 1))
    out["load1"] = float(kv.get("load", 0) or 0); out["load_per_core"] = round(out["load1"] / cores, 2)
    out["disk_pct"] = int(kv.get("disk", 0) or 0)
    used, tot = (kv.get("mem", "0 0").split() + ["0", "0"])[:2]
    out["mem_pct"] = round(100 * int(used) / max(1, int(tot)))
    out["failed_ssh_24h"] = int(kv.get("failed24h", 0) or 0)
    # What certificates this host HOLDS, against what it is actually
    # serving -- see `parse_certs` and `alerts._cert_stale_served`.
    out["certs_on_disk"] = parse_certs(kv.get("certs"))
    out["fail2ban"] = kv.get("fail2ban"); out["banned"] = kv.get("banned")
    out["reboot_required"] = kv.get("reboot") == "yes"
    out["pending_updates"] = int(kv.get("updates", 0) or 0)
    out["docker"] = [d for d in kv.get("docker", "").split(";") if d]
    out["services"] = dict(p.split("=", 1) for p in kv.get("services", "").split(";") if "=" in p)
    out["ports"] = sorted({int(p) for p in kv.get("ports", "").split(",") if p.strip().isdigit()})
    lst = lambda k: [x for x in kv.get(k, "").split(",") if x]
    top = {}
    for x in lst("topip"):
        if ":" in x:
            ip, n = x.rsplit(":", 1)
            if n.isdigit(): top[ip] = int(n)
    out["sec"] = {"ssh_password": kv.get("sshpw") == "yes", "ssh_root": kv.get("sshroot"), "ufw": kv.get("ufw") or "absent",
                  "unattended": kv.get("unatt") == "1" and kv.get("autoup") == "1", "security_updates": lst("secupd"),
                  "preload": kv.get("preload") == "yes", "miners": lst("miner"), "cron_net": lst("cronc"), "authorized_keys": int(kv.get("authkeys", 0) or 0),
                  "tmp_exec": lst("tmpx"), "top_attackers": top, "login_users": lst("users"), "login_sources": lst("lastip")}
    dv = (kv.get("drift", "") + "::::").split(":")
    out["drift"] = dict(zip(("sshd_config", "authorized_keys", "cron", "ufw_rules", "docker_compose"), dv[:5]))
    out["docker_images"] = dict(x.split("=", 1) for x in lst("dki") if "=" in x)
    out["cve_fixable"] = int(kv.get("cve", -1) or -1)
    # CVEs debsecan calls "fixed" for a package apt has NO update for -- a
    # vendor-repo build (Caddy from cloudsmith) that Debian's version table
    # cannot judge. Not a "fix available"; reported as a fact, not an alert.
    out["cve_no_update"] = int(kv.get("cvenoup", 0) or 0)
    out["cve_list"] = [{"cve": p.split()[0], "package": p.split()[1] if len(p.split()) > 1 else "", "urgency": p.split()[-1] if len(p.split()) > 2 else ""} for p in kv.get("cvel", "").split("~") if p.strip().startswith("CVE")]
    out["updates"] = {"pending": lst("updl"), "count": out["pending_updates"], "security": out["sec"]["security_updates"], "kernel": kv.get("kver"),
                      "kernel_latest": kv.get("kinst"), "os": (kv.get("os") or "").replace("_", " "), "last_apt": kv.get("lastapt") or None, "reboot_required": out["reboot_required"]}
    return out


def mac_updates():
    """macOS software updates available for this Mac (softwareupdate -l, slow, so the caller gates it)."""
    if not util.IS_MAC: return {"os": "linux sensor (patched by apt like the servers)", "available": [], "ts": util.now_iso()}
    try:
        v = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, timeout=10).stdout.strip()
        r = subprocess.run(["softwareupdate", "-l", "--no-scan"], capture_output=True, text=True, timeout=120)
        o = (r.stdout or "") + (r.stderr or "")
        items = [m.group(1).strip() for m in re.finditer(r"^\*\s*(?:Label:\s*)?(.+)$", o, re.M)]
        titles = [m.group(1).strip() for m in re.finditer(r"Title:\s*([^,]+),", o)]
        return {"os": v, "available": titles or items, "ts": util.now_iso(), "raw": o.strip()[-300:] if not (titles or items) else ""}
    except Exception as e:
        return {"error": str(e)[:120], "ts": util.now_iso()}
