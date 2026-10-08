"""Alert -> remedy: what Vault can do about one alert, and what it needs
from the owner when it cannot do it alone.

Every alert row now carries one of three answers (spec S7's three action
tiers are the axis this rides on):

- ``auto``     -- Vault can run the repair itself right now: the caller's
                  role allows it and the effective tier is 2, so pressing
                  **Fix** queues the real remediation.
- ``approval`` -- there is a repair, but it must be confirmed first (tier 3,
                  or the caller's role is below ``apply_fix``'s ``admin``).
                  The chat handoff mints the ordinary tier-3 proposal for it,
                  so a "yes" is still a real, single-use, token-bound yes.
- ``human``    -- nothing Vault can run repairs this. Then the answer says
                  what only a person can do and exactly what is needed. A
                  dead sensor is the archetype: every fix Vault applies
                  travels through the sensor, so the sensor is the one thing
                  it cannot fix from here.

**Where the mapping comes from.** Not a new catalogue: the sensor's own
``fix.FIX_ALERT_KEY`` already says which alert key each remediation clears
(``cleanup_disk`` -> ``disk:{h}``, ``restart_service`` -> ``svc:{h}:{a}``,
...). This module reads that relation backwards. The hub cannot import
``guardian_sensor`` (it is the sensor's package, installed on the sensor
host), so the two tables below are ported verbatim -- and
``tests/test_remedy.py`` parses ``sensor/guardian_sensor/fix.py`` and fails
the build if they ever drift, the same contract
``payloads/inbox.FIX_ACTIONS`` lives under.

**Why the host is parsed per template rather than with**
``inbox._entity_from_key``: that helper returns the third colon-segment, which
is the *service* in ``svc:{h}:{svc}`` and the *container* in
``docker:{h}:{c}``. Queueing ``restart_service`` against a host called
"nginx" is exactly the bug that hid behind the near-dead
``Alert.fix in FIX_ACTIONS`` gate (the sensor writes prose like
"auto-fixed: restart service" into that column, not an action name), so the
host and the argument are both read off the matching template here.
"""

from __future__ import annotations

import logging
import re

from . import confidence as conf_mod
from . import models as m, networks, platform_settings
# `chat.tiers` only, at module level, and deliberately: it reads `models`,
# `platform_settings` and `config` and nothing else, so it is a leaf.
#
# `chat.registry` and `chat.tools` are NOT imported here, and must not be.
# They close a cycle that runs back through this module:
#
#     remedy -> chat.registry -> chat.tools -> routers.write
#            -> payloads.jobs -> remedy
#
# `payloads/jobs.py` reads `remedy.FIX_LABELS`, which is defined below this
# import block -- so whenever anything imported `remedy` (or `chat.providers`,
# or `chat.rules`, or nine other modules) BEFORE the hub's own startup order
# had already pulled `payloads` in, `jobs` reached a half-built `remedy` and
# raised `cannot import name 'FIX_LABELS'`. The app never hit it because
# `app.py` imports the routers first; `guardian_hub.chat.providers` on its own,
# a one-off CLI script, or a test that imports a payload module cold all did.
#
# `remedy` is the lower-level module of the pair -- both `payloads` and `chat`
# depend on it -- so the edge to remove is this one, not theirs. The three
# names below are used in exactly one function (`decide_for`), so they are
# imported there. Do not lift them back up here: the cycle is still present in
# the other direction and this is the only thing holding it open.
from .chat.tiers import TIER3_FIXES, effective_tier, fix_args
from .schemas_dash import AlertRemedy, ConfidenceFactor
from .scheduler import SENSOR_SILENT_KEY

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# The ported halves of the sensor's fix catalogue (see the module docstring;
# `tests/test_remedy.py` asserts both against `sensor/guardian_sensor/fix.py`)
# --------------------------------------------------------------------------

#: `action -> the human label the sensor prints for it`, from
#: `FIX_ACTIONS[action][0]`. Used verbatim in the copy, so "Vault can free
#: disk space on prod" reads the same word the fix log will.
FIX_LABELS: dict[str, str] = {
    "enable_fail2ban": "enable fail2ban",
    "security_updates": "install pending security updates",
    "enable_unattended": "turn on automatic security updates",
    "kill_miner": "kill malware process",
    "harden_fail2ban": "harden fail2ban (3 tries, 24h ban, escalating)",
    "block_ip": "block attacker IP in the firewall",
    "block_subnet": "block the attacker's whole /24 network",
    "ban_long": "fail2ban ban for 30 days",
    "limit_ssh": "rate-limit SSH (6 connections / 30s per IP)",
    "restart_container": "restart container",
    "restart_service": "restart service",
    "cleanup_disk": "free disk space",
    "disable_ssh_password": "switch SSH to key-only login",
    "enable_ufw": "enable firewall for listening ports only",
    "reboot": "reboot host",
    "install_debsecan": "install vulnerability scanner (debsecan)",
    "full_upgrade": "full apt upgrade (all packages)",
    "security_headers": "add missing security headers (HSTS, X-Content-Type-Options, X-Frame-Options)",
}

#: `action -> what it costs to run it`, in the owner's language.
#:
#: Vault is asking permission to do something to a running network. The
#: whole job of an approval screen is to make the tradeoff legible in one
#: read, and neither of the two sentences we already had does that:
#: `FIX_LABELS` says WHAT ("restart service"), `_FIX_STEPS` says HOW (the
#: literal command). Between them sits the question the owner is actually
#: weighing -- what breaks, for how long, and for whom -- and until now they
#: had to know the answer already, which means the people most in need of the
#: sentence were the ones who could not supply it.
#:
#: Rules these sentences follow, because a hedge here is worse than silence:
#:
#: * Name the blast radius, not the risk in the abstract. "Drops live
#:   connections for a few seconds" is usable; "may cause disruption" is not.
#: * Name who else is affected when it is not only this host. Blocking a /24
#:   takes the attacker's neighbours with them, and that is the entire reason
#:   a human is being asked.
#: * Say when the honest answer is "nothing breaks". An owner who reads a
#:   warning on every action stops reading them, so the calm ones must be
#:   visibly calm.
#: * Cover the undo when it is the hard part -- HSTS is cached by browsers,
#:   and a key-only switch locks out anyone without a key already installed.
#:
#: Keyed by action, so it is one table beside the other two rather than a
#: sentence buried per call site. An action missing from here yields "" and
#: the UI simply prints nothing -- a missing impact line must never invent a
#: reassuring one.
FIX_IMPACT: dict[str, str] = {
    "enable_fail2ban":
        "Nothing goes down. Repeated failed logins from an address start getting "
        "banned — including yours, if someone in the office mistypes a password "
        "enough times.",
    "security_updates":
        "Each package is replaced and its service restarts as it goes, so expect a "
        "few seconds of interruption per service. Some updates only take effect "
        "after a reboot, which this does not do.",
    "enable_unattended":
        "Nothing changes right now. From here on, security updates install "
        "overnight on their own without asking you again.",
    "kill_miner":
        "The process ends immediately. If something else on the host started it, "
        "it can come back — worth watching this host afterwards rather than "
        "assuming it is settled.",
    "harden_fail2ban":
        "Nothing goes down, but bans get longer and start sooner: three failed "
        "tries and the address is out for 24 hours, escalating if it returns. A "
        "careless attempt from your own office locks that address out for a day.",
    "block_ip":
        "All traffic from that one address is dropped. If it turns out to be a "
        "customer, a VPN exit or your own remote office, they lose access until "
        "the rule comes off.",
    "block_subnet":
        "Drops 256 addresses, not one — a whole ISP or office range. The "
        "attacker's neighbours lose access with them, and some of them may be "
        "people you know.",
    "ban_long":
        "That address is locked out for 30 days. Nothing else is affected.",
    "limit_ssh":
        "SSH keeps working for people. Anything opening more than six connections "
        "in 30 seconds from one address gets throttled, which in practice means "
        "backup jobs, CI runners and file-sync tools that speak SSH.",
    "restart_container":
        "The container stops and starts again. Whatever it serves is down until it "
        "comes back — usually seconds — and any work it was part-way through is "
        "lost.",
    "restart_service":
        "Drops live connections to that service while it restarts: a few seconds "
        "for most, longer for anything slow to start. Everything else on the host "
        "keeps running.",
    "cleanup_disk":
        "Removes rotated logs, cached package archives and temporary files. "
        "Nothing stops running, but log history older than the rotation window is "
        "gone for good.",
    "disable_ssh_password":
        "Password logins over SSH stop working for everyone on this host. Anyone "
        "without a key already installed is locked out until someone adds one — "
        "check your own key works before approving this.",
    "enable_ufw":
        "The firewall comes up allowing only the ports something is listening on "
        "right now. A service that happens to be stopped, or a job that connects "
        "occasionally on another port, will be refused until its port is allowed.",
    "reboot":
        "The host goes down and comes back — everything on it is unavailable for "
        "the length of a boot. Anything not configured to start on boot stays "
        "down afterwards.",
    "install_debsecan":
        "Nothing stops. The host gains a scanner and starts reporting known "
        "vulnerabilities in the packages it already has installed.",
    "full_upgrade":
        "Every package with an update is replaced, not only the security ones. "
        "Services restart as it works through them, and a major version change can "
        "alter behaviour or overwrite configuration.",
    "security_headers":
        "Adds response headers and reloads the web server — a moment's "
        "interruption. HSTS is the one to think about: it tells browsers to refuse "
        "plain HTTP for this name for a long time, and that is hard to take back "
        "once visitors have cached it.",
}

#: `action -> the alert key template that action clears`, from the sensor's
#: `fix.FIX_ALERT_KEY`. `{h}` is the host, `{a}` the fix's argument.
FIX_ALERT_KEY: dict[str, str] = {
    "block_subnet": "sec:attacker:{h}:{a}",
    "ban_long": "sec:attacker:{h}:{a}",
    "limit_ssh": "bruteforce:{h}",
    "full_upgrade": "upd:pending:{h}",
    "disable_ssh_password": "sec:sshpw:{h}",
    "enable_ufw": "sec:ufw:{h}",
    "security_updates": "sec:secupd:{h}",
    "enable_unattended": "sec:unatt:{h}",
    "enable_fail2ban": "fail2ban:{h}",
    "block_ip": "sec:attacker:{h}:{a}",
    "restart_container": "docker:{h}:{a}",
    "restart_service": "svc:{h}:{a}",
    "cleanup_disk": "disk:{h}",
    "harden_fail2ban": "bruteforce:{h}",
}

#: Two key shapes the sensor repairs but does not list in `FIX_ALERT_KEY`,
#: each taken from the sensor's own recommendation for that key rather than
#: invented here: `alerts.py` recommends `{"action": "reboot"}` under key
#: `reboot:{n}`, and its `sec:cve:{n}` text says the upgrade clears them
#: ("the daily upgrade clears them"), which is `security_updates`.
EXTRA_ALERT_KEY: list[tuple[str, str]] = [
    ("reboot:{h}", "reboot"),
    ("sec:cve:{h}", "security_updates"),
]

#: Remediations whose command body takes a `%s` argument. Without one they
#: would run against nothing (`for p in ; do ...`), so a key that does not
#: carry the argument can never resolve to `auto`/`approval` for them.
ARG_REQUIRED = {"block_ip", "block_subnet", "ban_long", "restart_container",
                "restart_service", "kill_miner"}


def _compile(template: str) -> tuple[re.Pattern[str], bool]:
    """`disk:{h}` -> `^disk:(?P<host>[^:]+)$`. `{a}` is last in every
    template, so it may swallow colons (an attacker IPv6 literal, a
    container name with one); `{h}` never may."""
    out, has_arg = [], False
    for part in template.split(":"):
        if part == "{h}":
            out.append("(?P<host>[^:]+)")
        elif part == "{a}":
            out.append("(?P<arg>.+)")
            has_arg = True
        else:
            out.append(re.escape(part))
    return re.compile("^" + ":".join(out) + "$"), has_arg


def _by_template() -> list[tuple[re.Pattern[str], bool, str]]:
    """`(pattern, takes_arg, action)`, longest template first.

    Several actions can clear one key (`sec:attacker:{h}:{a}` is blocked by
    `block_ip`, `block_subnet` or `ban_long`; `bruteforce:{h}` by
    `harden_fail2ban` or `limit_ssh`). The least disruptive one wins --
    anything in `TIER3_FIXES` loses to anything that is not, then
    alphabetical for determinism -- so the row offers "block this IP", never
    "ban it for 30 days", and the harsher sibling stays a thing the owner
    asks for in chat. `fix.pick_attacker_method` is deliberately not called:
    it escalates on attack history this resolver does not read.
    """
    grouped: dict[str, list[str]] = {}
    for action, template in FIX_ALERT_KEY.items():
        grouped.setdefault(template, []).append(action)
    for template, action in EXTRA_ALERT_KEY:
        grouped.setdefault(template, []).append(action)

    rows = []
    for template, actions in grouped.items():
        action = sorted(actions, key=lambda a: (a in TIER3_FIXES, a))[0]
        pattern, has_arg = _compile(template)
        rows.append((template, pattern, has_arg, action))
    rows.sort(key=lambda r: -len(r[0]))
    return [(p, h, a) for _t, p, h, a in rows]


_PATTERNS = _by_template()


def fix_for_key(key: str) -> tuple[str, str, str] | None:
    """`(action, host, arg)` the sensor's catalogue says repairs `key`, or
    None when no remediation matches it."""
    for pattern, takes_arg, action in _PATTERNS:
        match = pattern.match(key or "")
        if not match:
            continue
        arg = match.group("arg") if takes_arg else ""
        if action in ARG_REQUIRED and not arg:
            return None
        return action, match.group("host"), arg
    return None


def alerts_for_fix(db, tenant, action: str, host: str, arg: str | None) -> list["m.Alert"]:
    """The tenant's OPEN alerts this exact repair would answer.

    `fix_for_key` reads the sensor's catalogue backwards for one key; this
    reads it backwards for one repair, over every open alert. It exists as a
    shared function because two callers need the same answer and a second
    definition of "which alert does this repair answer" would drift silently:

    * `routers/sensor._settle_fixed_alerts` resolves these alerts the moment
      the sensor reports the fix done, and
    * `routers/write.queue_fix` needs the alert key to record the decision
      against, because `confidence.assess` without one loses its `held` and
      `undone` factors -- the two that answer "did Vault's own repair stop
      this alert" and "has a person overruled it".

    This codebase documents that hazard twice already, and for the same
    reason: the fix tables in this module are ported from
    `sensor/guardian_sensor/fix.py` and checked against it by
    `tests/test_remedy.py`, and `payloads/history.py` is the only place a
    trend is computed for the three surfaces that draw one. Two answers to
    the same question, with only one of them under test, is how the untested
    one starts lying.

    Rows, not keys, because `_settle_fixed_alerts` needs the whole alert: it
    reads `key`, `sev` and `msg` and hands the key to `write.apply_alert_op`.

    **The argument match is deliberately asymmetric**, carried over verbatim
    from the caller it was lifted from. A template with no argument
    (`sec:secupd:{h}`) matches whatever `arg` the repair carries, including
    none; a template that does take one (`svc:{h}:{a}`, `docker:{h}:{a}`)
    must match it exactly, so restarting `nginx` never closes the alert about
    `postgres` on the same host. `None` and `""` are different arguments
    here, which is why `arg` is typed as optional rather than coerced -- the
    queue body stores whatever the caller sent.
    """
    out = []
    for a in db.query(m.Alert).filter(m.Alert.tenant_id == tenant.id,
                                      m.Alert.status == "open").all():
        found = fix_for_key(networks.canonical_lan_key(a.key or ""))
        if not found or found[0] != action or found[1] != host:
            continue
        if found[2] and found[2] != arg:
            continue
        out.append(a)
    return out


# --------------------------------------------------------------------------
# Alerts with no automatic remedy: what only a person can do
# --------------------------------------------------------------------------

#: `key prefix -> (what Vault cannot do, what it needs from the owner)`,
#: longest prefix wins. Every prefix here is a real key shape the sensor or
#: the hub raises (`sensor/guardian_sensor/alerts.py`, `scheduler.py`); the
#: fallback at the end of `_human` covers anything new, so a fresh alert key
#: still explains itself instead of going silent.
HUMAN_REMEDIES: list[tuple[str, str, str]] = [
    ("home:rogue-dhcp",
     "a second device is handing out addresses on your network, and I have no login to your router",
     "unplug or reconfigure the extra router/AP handing out DHCP (its address is in the evidence), then tell me and I will re-check"),
    ("home:evil-twin",
     "another access point is broadcasting your Wi-Fi name, which I can see but cannot switch off",
     "check for an unexpected AP or repeater in range and turn it off; if it is not yours, change your Wi-Fi password from the router's admin page"),
    ("home:wifi:weak",
     "your Wi-Fi encryption is set on the router, which I have no credentials for",
     "set the Wi-Fi security to WPA2 or WPA3 in the router's admin page and re-join your devices"),
    ("home:route", "your router's routing table is not something I can change from here",
     "check the router for an unexpected static route or VPN, then tell me and I will re-check"),
    ("home:gw", "your gateway is router-side configuration I cannot change",
     "confirm the gateway is your own router, and if its MAC changed unexpectedly, power-cycle it and tell me"),
    ("home:exposed", "the ports open to the internet are forwarded by your router, and I cannot log into it",
     "remove the port forward (or UPnP mapping) in the router's admin page; if a host of mine should be firewalled instead, say so and I can enable its firewall"),
    ("adv:", "this is a router setting, and I have no login to your router",
     "make the change in the router's admin page, then tell me and I will re-check"),
    # Task 73: cameras behind an NVR/DVR. Every one of these is a human errand
    # by construction -- the recorder is somebody else's box with its own
    # console, and Vault holds no remediation that reaches inside it. The
    # `nvr:http-off` copy is the one that must be exactly right: it is the
    # state a stock Reolink NVR ships in, and it is why an owner with twelve
    # cameras sees one. It names the setting.
    ("nvr:http-off",
     "the recorder's HTTP interface appears to be switched off; its HTTP port refused the connection "
     "and only its proprietary client port is answering, and I will not speak an undocumented protocol",
     "on the recorder, turn its HTTP port back on: Settings -> Network -> Advanced -> Port Settings, and "
     "enable the HTTP port (80) or HTTPS (443); in the Reolink app the same switches are under Device "
     "Settings -> Advanced -> Port Settings on newer firmware. Then tell me and I will read it again on "
     "the next scan. A view-only account with permission to see channel status is all I need"),
    ("nvr:auth",
     "the recorder rejected the username or password stored for it, and I will not guess at credentials",
     "check the recorder's login and update it under Settings -> Recorders. A view-only account with "
     "permission to see channel status is enough; I never need an administrator on your recorder"),
    ("nvr:down",
     "the recorder itself did not answer, and every camera behind it goes through it, so there is nothing "
     "at my end to repair",
     "check the recorder is powered on and on the network at the address recorded for it; if you moved it, "
     "update the address under Settings -> Recorders and I will re-read it"),
    ("nvr:setup",
     "this recorder is registered but not yet readable: either it has no login stored, its address is "
     "outside the networks configured here, or its make has no adapter yet",
     "add the recorder's login (or correct its address) under Settings -> Recorders; if the make is one I "
     "cannot read yet, that one is on me, not on you"),
    ("nvr:cam-offline",
     "a camera behind your recorder has stopped answering, and it is on the recorder's own PoE network "
     "where I have nothing to power-cycle",
     "check that camera's cable, its PoE port on the recorder and its power supply; if you unplugged it "
     "deliberately, tell me and I will stop expecting it"),
    ("nvr:cam-gone",
     "the recorder no longer lists that channel at all, and only you know whether you removed the camera",
     "confirm whether you removed that camera; if you did not, re-seat it on the recorder's PoE port and "
     "I will pick it up again on the next scan"),
    ("nvr:cam-new",
     "a camera appeared behind your recorder; whether it is meant to be there is your call",
     "confirm the new camera is yours and I will keep it in the inventory"),
    ("nvr:count",
     "the recorder lists a different number of cameras from the number you told me to expect, and only "
     "you know which figure is right",
     "either re-seat the missing camera on the recorder, or update the expected count under "
     "Settings -> Recorders"),
    ("capture:unavailable",
     "traffic capture needs the tcpdump package on the sensor box, which I cannot install from here",
     "run `sudo apt-get install -y tcpdump` on the sensor, then tell me and I will re-check"),
    ("capture:silent",
     "the mirror port has stopped delivering packets, which is a switch setting I have no login for",
     "check the switch's mirror/SPAN configuration still points the uplink at the sensor's port, then tell me and I will re-check"),
    ("lan:new", "a device I have never seen joined the network; only you know whether it is yours",
     "tell me whether you recognise it and I will trust it (or quarantine it) for you"),
    ("lan:unverified", "some devices have never been confirmed as yours, which only you can say",
     "name the ones you recognise and I will mark them trusted"),
    ("lan:rotated", "a device changed its MAC address, which is the device's own privacy setting",
     "confirm it is the phone or laptop you think it is, and I will keep it trusted"),
    ("lan:unusual-hour", "a device was active at an unusual hour; whether that is normal is your call",
     "tell me if that was you, and I will stop flagging that pattern"),
    ("lan:flapping", "a device keeps dropping off the Wi-Fi, which is a signal or power problem at the device",
     "check that device's Wi-Fi signal, battery saver or power supply; move it closer to the AP if it is far"),
    ("lan:offline", "a device I usually see has gone missing; only you know if it was taken away",
     "tell me whether it is meant to be off, and I will stop expecting it"),
    ("lan:port", "the open ports belong to that device's own software, not to anything I administer",
     "if you do not need file sharing or remote desktop on it, turn that off on the device; say the word and I can quarantine it instead"),
    ("cert:", "the TLS certificate is issued by your certificate authority, which I do not hold keys for",
     "renew the certificate (certbot or your host's panel); I will confirm the new expiry on the next scan"),
    ("domain:", "domain registration and DNS records live with your registrar, which I have no account on",
     "renew or fix it in the registrar's control panel and I will re-check"),
    ("email:", "email DNS records live at your DNS provider, which I have no account on",
     "add or correct the SPF/DMARC record at your DNS provider; I will verify it on the next scan"),
    ("dns:filter-down", "the DNS filter runs as its own service on the sensor machine",
     "start the guardian-dns service on the sensor machine, or ask me to restart the service if that host has SSH configured here"),
    ("dns:system", "your devices' DNS servers are handed out by the router",
     "point the router's DHCP DNS at the filter (or back at a working resolver) in its admin page"),
    ("dns:all", "the resolver your network uses is not one I can restart from here",
     "confirm your internet connection is up, then check the DNS server the router hands out"),
    ("dns:slow", "resolver speed is your ISP's or your router's, not something I can tune",
     "try a different upstream resolver in the router (or the DNS filter) and I will measure it again"),
    ("dns:beacon", "a device is contacting one domain on a fixed rhythm; deciding it is malicious needs your context",
     "tell me which app on that device you expect to be talking, and I can block the domain if it is not yours"),
    ("dns:dga", "the name pattern looks machine-generated, but only you know the software on that device",
     "tell me what runs on that device; I can block the domain on your say-so"),
    ("dns:volume", "I can see how many lookups a device made, not what was inside them, so only you can "
     "judge whether the volume is expected",
     "tell me what that device normally does (a backup, a sync, a media box); if it should not be this "
     "busy, unplug it and tell me and I will keep watching for it to come back"),
    ("wan:", "your internet path is your ISP's, and I have nothing to fix at either end",
     "power-cycle the router/modem, and if it persists, raise it with your ISP; I keep measuring so you have the record"),
    ("speed:slow", "line speed is your ISP's, not something I can change",
     "I can re-run the speed test right now to see whether it has recovered; if it stays low, check "
     "nothing large is uploading, then raise the measurements with your ISP"),
    ("ssh:", "I reach that host over SSH, and SSH is exactly what is not answering, so I cannot repair it from here",
     "check the machine is powered on and on the network, then that its sshd is running; once I can reach it I can fix the rest"),
    ("sec:panel:", "an admin panel is reachable from the internet through your own hosting/router setup",
     "put the panel behind a login or a firewall rule (or remove the port forward); I can enable a host's firewall if you want that instead"),
    ("sec:preload:", "a rootkit technique on that host needs a human to inspect it; I will not guess at removing it",
     "treat the host as compromised: inspect /etc/ld.so.preload, and plan to rebuild it. I can pull diagnostics for you first"),
    ("sec:authkeys:", "SSH keys were added on that host, and only you know which of them should be there",
     "review the authorized_keys entries and tell me which to remove; I will not delete a key on a guess"),
    ("sec:users:", "new user accounts on that host are only suspicious if you did not create them",
     "confirm which accounts are yours; if any is not, treat the host as compromised and I will help you gather evidence"),
    ("sec:cron:", "a scheduled job changed on that host; whether that was you is your call",
     "confirm the cron change was yours, or tell me it was not and I will pull the details"),
    ("sec:drift:", "a tracked file changed on that host; only you know whether the change was intended",
     "confirm the change was yours; if not, say so and I will collect the evidence"),
    ("sec:login:", "a successful login happened from an address I cannot vouch for",
     "confirm it was you (or your VPN); if not, change that account's credentials and I can block the address"),
    ("sec:lanlogin:", "a device on your own network signed in somewhere it had never signed in before, "
     "and only you know every reason a device on your network might reach that host",
     "confirm what that device is and whether it should be able to reach that host; if not, change the "
     "credentials it used and tell me so I can help you check for anything else it touched"),
    ("sec:tmpexec:", "something is executing from a temporary directory, which needs a person to judge",
     "tell me what should be running there; I can pull the process list for you"),
    ("sec:miner:", "the sensor kills miners as it finds them, and I will not run a kill list I had to guess at",
     "tell me the exact process name still running and I will kill it on that host"),
    ("sec:env-", "the file permissions and age of your environment file are yours to manage",
     "tighten the file's permissions (or rotate the secrets in it) on the machine that holds it"),
    ("mem:", "memory pressure is the workload's, and restarting the wrong thing would cause an outage",
     "tell me which service may be restarted and I will do it, or I can show you what is using the memory first"),
    ("load:", "sustained load is the workload's, and I will not restart something on a guess",
     "tell me which service may be restarted, or ask me for the top processes on that host"),
    ("hb:", "a job that should check in with me has not, and it runs outside my reach",
     "check that the job still runs on its schedule; if it was retired, tell me and I will stop expecting it"),
    ("check:", "a check you configured is failing at the far end, which I only measure",
     "fix or retire the target; if the check itself is wrong, an admin can change it in Settings"),
    ("rebooted:", "the host already restarted, so there is nothing to repair, only to confirm",
     "confirm the restart was expected; if it was not, ask me and I will show what happened around it"),
    ("fix:cap", "I stopped applying fixes because this site's daily auto-fix cap was reached",
     "raise the cap under Settings, or tell me which fix to run now and I will run that one"),
    ("tailscale:", "the VPN client runs on that machine's own service manager",
     "start the VPN client on that machine (or tell me it is meant to be off)"),
    ("ts:peer:", "a VPN peer is unreachable, which is that peer's own machine",
     "check that peer is powered on and connected; I will confirm when it comes back"),
]


#: `HUMAN_REMEDIES` longest-prefix-first, computed once.
_HUMAN_BY_LENGTH = sorted(HUMAN_REMEDIES, key=lambda r: -len(r[0]))


def _sensor_line(db, tenant_id: str) -> tuple[str, str]:
    """`(machine, command)` for the sensor-silence copy: the stalest sensor
    on this site by `last_seen`, and the one command that tells the owner
    whether its process is alive on that platform."""
    sensors = db.query(m.Sensor).filter(m.Sensor.tenant_id == tenant_id,
                                        m.Sensor.status != "revoked").all()
    if not sensors:
        return "the sensor machine", "systemctl status guardian-sensor"
    stalest = sorted(sensors, key=lambda s: (s.last_seen is not None, s.last_seen))[0]
    name = stalest.name or "the sensor machine"
    if (stalest.platform or "").lower().startswith(("darwin", "mac")):
        return name, "launchctl list | grep com.guardian.sensor"
    return name, "systemctl status guardian-sensor"


def _sensor_silent(db, tenant, alert: "m.Alert") -> AlertRemedy:
    """The one alert Vault provably cannot fix: its own eyes. Every
    remediation it applies travels through the sensor, so a silent sensor is
    a human errand -- and the copy has to say so instead of leaving an
    acknowledged CRIT sitting there looking handled."""
    machine, command = _sensor_line(db, tenant.id)
    ev = alert.evidence or {}
    age = ev.get("age_min")
    last = ev.get("last_scan_ts") or "unknown"
    aged = f"{age} min ago" if age else "some time ago"
    summary = ("I cannot repair my own sensor from here; every fix I apply travels through it, "
               f"and {machine} is the part that has gone quiet.")
    need = (f"On {machine}, run `{command}`. If it is not running, start it; if it is running, it "
            f"cannot reach the hub, so check that machine's network. Paste me what you see and I "
            f"will take it from there. If the machine is off or off the network, it has to come "
            f"back online first.")
    opening = (f"{alert.msg}\n\n{summary} Its last scan reached me at {last} ({aged}), so either the "
               f"sensor process stopped, its machine is off, or it cannot reach the hub.\n\n"
               f"What I need from you: {need}")
    return AlertRemedy(kind="human", summary=summary, need=need, opening=opening)


def _sentence(text: str) -> str:
    """The `need` strings below are written as clause fragments so they read
    naturally after "what I need from you:", but they are also rendered on
    their own (the inbox row's second line, the chat `Next:` row) -- so they
    are capitalised and stopped here rather than in three places of copy."""
    if not text:
        return text
    out = text[0].upper() + text[1:]
    return out if out[-1] in ".!?" else out + "."


def _human(db, tenant, alert: "m.Alert") -> AlertRemedy:
    # LAN keys are namespaced per network (`lan:<slug>:new:<mac>`), so the
    # prefixes below are tested against the canonical shape -- otherwise
    # every `lan:` prefix here would miss on a real row.
    key = networks.canonical_lan_key(alert.key or "")
    if key == SENSOR_SILENT_KEY:
        return _sensor_silent(db, tenant, alert)
    for prefix, cannot, need in _HUMAN_BY_LENGTH:
        if key.startswith(prefix):
            summary = f"I cannot fix this one myself: {cannot}."
            need = _sentence(need)
            opening = f"{alert.msg}\n\n{summary}\n\nWhat I need from you: {need}"
            return AlertRemedy(kind="human", summary=summary, need=need, opening=opening)

    summary = ("I have no remediation of my own for this one: none of the fixes I can run "
               "addresses it.")
    need = ("Tell me what you want done about it and I will say whether I can do it; I can also "
            "pull diagnostics from the hosts and devices involved.")
    opening = (f"{alert.msg}\n\n{summary}\n\nWhat I need from you: {need}")
    return AlertRemedy(kind="human", summary=summary, need=need, opening=opening)


# --------------------------------------------------------------------------
# The resolver
# --------------------------------------------------------------------------

#: `security_headers`'s `arg` header keys -> the name a non-technical reader
#: recognises, for `fix_steps()` below.
_HEADER_NAMES = {
    "hsts": "HSTS (Strict-Transport-Security)",
    "nosniff": "X-Content-Type-Options: nosniff",
    "frame": "X-Frame-Options: SAMEORIGIN",
}

#: `action -> the exact steps sentence for `fix_steps()`, keyed the same way
#: `FIX_LABELS` is. Every disruptive (`chat.tiers.TIER3_FIXES`) action gets
#: one -- the owner asked to see "which host, which file, the specific
#: change, and what I will do if the check fails" before saying yes, not
#: only the action's name, and this is that sentence for every fix that
#: needs a "yes" rather than only the newest one.
_FIX_STEPS: dict[str, str] = {
    "security_headers": (
        "This is disruptive: it edits a live web server. On {host}, I will find the one nginx "
        "server block serving {domain}, back up its config file, add {headers} to that block, then "
        "run `nginx -t` against the whole config. If that test fails, I restore the backup "
        "byte-for-byte and tell you it failed -- I will not reload an untested config. Only if it "
        "passes do I reload nginx and confirm the headers are there."
    ),
    "reboot": (
        "This is disruptive and cannot be undone from here: on {host}, I will run `systemctl "
        "reboot`, a hard restart with no rollback; if a service does not come back up cleanly "
        "afterward, I will show you what failed."
    ),
    "full_upgrade": (
        "This is disruptive and cannot be undone from here: on {host}, I will run `apt-get "
        "dist-upgrade` for every package with a pending update. apt does not roll back a partial "
        "upgrade on its own; if packages still need a reboot afterward I will say so rather than "
        "reboot on my own."
    ),
    "disable_ssh_password": (
        "This is disruptive and hard to undo remotely: on {host}, I will write an sshd config file "
        "that turns off password login and root password login, test it with `sshd -t`, and only "
        "reload sshd if that test passes. If the test fails, sshd keeps running on its current "
        "config, unchanged."
    ),
    "block_subnet": (
        "This is disruptive: on {host}, I will add one firewall rule (`ufw insert 1 deny from "
        "{arg} to any`) blocking the attacker's whole /24, which is 256 addresses, not just the "
        "one that attacked."
    ),
    "ban_long": (
        "This is disruptive: on {host}, I will set fail2ban's ban time for {arg} to 30 days."
    ),
    "limit_ssh": (
        "This is disruptive: on {host}, I will rate-limit SSH with ufw to 6 new connections per "
        "30 seconds per address."
    ),
}


def fix_steps(action: str, host: str, arg: str = "") -> str:
    """Exactly what Vault will do for `action` on `host` -- the host,
    the file or setting it touches, the specific change, and what happens
    if a safety check fails -- so a tier-3 proposal shows the owner the
    steps he asked for ("give me your steps... if I say yes, then do it")
    rather than only the action's name. Read by the tier-3 proposal's three
    front doors: `resolve_action()` below, `routers/write.fix_confirm`, and
    `chat/registry._proposal_parts` (apply_fix's own confirmation).

    Falls back to the plain label for a tier-2 action or one with no
    bespoke steps written yet -- never a blank line.
    """
    if action == "security_headers":
        domain, _, hdr_csv = (arg or "").partition("|")
        names = [_HEADER_NAMES.get(k, k) for k in hdr_csv.split(",") if k.strip()]
        return _FIX_STEPS["security_headers"].format(
            host=host, domain=domain or "this site", headers=", ".join(names) or "the missing headers")
    template = _FIX_STEPS.get(action)
    if template:
        return template.format(host=host, arg=arg or "")
    return f"On {host}, I will run: {FIX_LABELS.get(action, action.replace('_', ' '))}."


def cause_key(alert_key: str) -> str:
    """The shared cause behind an alert key, coarser than the key itself:
    two SPF/DMARC findings on two different domains (`email:spf:{d1}`,
    `email:dmarc:{d2}`) both resolve to the `"email:"` prefix from
    `HUMAN_REMEDIES`, and a cron-drift alert on two hosts
    (`sec:cron:{h1}`, `sec:cron:{h2}`) both resolve to `"sec:cron:"` --
    exactly the two roll-ups the inbox triage (task 83, pattern A3) groups
    on. An alert with a real remedy groups by the action that clears it
    instead (`fix:block_ip`), so two hosts under attack from the pattern
    Vault would fix the same way still collapse to one row.

    This reads the same two tables `resolve()` already reads for its
    human/auto split, so a cause key can never disagree with what the row
    itself says Vault can do about it.
    """
    key = networks.canonical_lan_key(alert_key or "")
    found = fix_for_key(key)
    if found is not None:
        return f"fix:{found[0]}"
    for prefix, _cannot, _need in _HUMAN_BY_LENGTH:
        if key.startswith(prefix):
            return prefix
    return key


def _factor_weight(factors: list, name: str) -> int:
    """The weight of the one factor named `name`.

    `confidence.assess` always returns all six factors (`Factor`'s own
    docstring: "a factor that found nothing is still returned"), so this
    never falls through -- but it exists as a function at all so the three
    places `_apply_confidence` reads a factor's sign (`held`, `undone`,
    `sensor_fresh`) do not each spell out their own
    `next(f for f in ... if f.name == ...)`, which is exactly the kind of
    duplication that drifts (see the module docstring's fix-table ledger, and
    `payloads/history.py`'s "one place a trend is computed").

    There is no fourth. It used to say four, and `resolve_action` rather than
    `_apply_confidence`, and both halves were stale: the fourth value `gate`
    needs is a COUNT, not a sign, and it is now read off `conf.confirmed`,
    which `confidence.assess` fills from the single `confirmed_repairs` call
    it already makes.
    """
    return next(f.weight for f in factors if f.name == name)


def _confidence_why(conf: "conf_mod.Confidence") -> str:
    """A sentence to show even when `gate` had nothing to say.

    `gate` returns `""` on its most common paths, including the ordinary
    case where it looked at the score and agreed with the tier rules --
    which is nearly every alert. Treating that silence as "no explanation
    available" would mean the confidence UI has nothing to print on nearly
    every row it appears on, even though `assess` always has an answer for
    "why this score": the factors it computed. So when `gate` is silent,
    this composes the sentence from the two or three factors that moved the
    score the most (by absolute weight) instead of leaving the field empty --
    the customer is owed a reason either way, and "the rubric agreed with
    the rules" is not one they can read off a blank string.
    """
    ranked = sorted(conf.factors, key=lambda f: abs(f.weight), reverse=True)
    return " ".join(f.why for f in ranked[:3] if f.why)


def _handoff(msg: str, rem: AlertRemedy) -> str:
    """Vault's opening chat message for a remedy that needs the owner.

    ONE definition of the paragraph order, because there are two callers --
    the capability-gap `human` outcome and every non-`auto` decision below --
    and they were previously two copies of the same f-string. They had already
    started to diverge on nothing worse than whitespace, and the next thing
    added to the message would have landed in one of them.

    The order is the order a person decides in, and it is not alphabetical or
    historical:

    1. `msg` -- the alert in the sensor's own words, so the reader knows which
       finding this is about before being asked anything.
    2. `summary` -- what Vault can do.
    3. **What it will cost** -- the blast radius. A proposal that names the
       command but not what it interrupts is asking someone to authorise
       something they have not been told.
    4. **How sure I am** -- the rubric's sentence. It goes here, after the
       cost and before the ask, because it qualifies the offer rather than
       answering the question: a reader weighing "should I say yes" wants the
       hedge while they are still weighing, not after they have been asked.
    5. **What I need from you** -- last on purpose. It is the call to action,
       and anything printed below it reads as an afterthought to the thing the
       message exists to get.

    Every section but the first two is dropped entirely when it is empty
    rather than printed with nothing after the colon. That is the same rule
    `impact` already had and it matters most for the cost line: a heading with
    a blank body reads as "no cost", which is the one meaning Vault must
    never accidentally assert.
    """
    parts = [msg, rem.summary]
    if rem.impact:
        parts.append(f"What it will cost: {rem.impact}")
    if rem.confidence_why:
        parts.append(f"How sure I am: {rem.confidence_why}")
    if rem.need:
        parts.append(f"What I need from you: {rem.need}")
    return "\n\n".join(parts)


def _apply_confidence(base: AlertRemedy, db, tenant, *, action: str, host: str, tier: int,
                      msg_key: str, role: str) -> None:
    """Attach a confidence score and sentence to `base`, in place, and let
    the rubric adjust `base.kind` within the bounds `confidence.gate`
    enforces.

    No label is threaded through to `assess`, deliberately. Its factor
    questions name neither the action nor the alert -- they say "this" and
    "this repair", because the row that opens the drawer has already named
    both one line above. An earlier draft passed `FIX_LABELS`'s wording down
    for the questions to interpolate, which produced "Has Vault run install
    pending security updates on records successfully before?": a verb phrase
    dropped into a verb slot. See `confidence.assess`'s own docstring.

    Called for every outcome `resolve_action` can produce, including
    `human`: a score is still informative there (the owner reading "Vault
    has never repaired this host this way" is not told anything false by
    also being told how sure it is), and `gate` already refuses to move a
    `human` decision on its own (`if kind == "human": return kind, ""`), so
    running it costs nothing and duplicating this block per call site would
    be exactly the kind of drift this module elsewhere goes out of its way
    to avoid.

    It only ever reads the decision the caller already reached --
    `confidence.gate` may always add caution (demote `auto` -> `approval` on
    a low score), and may only remove it on demonstrated history, never on a
    score alone (see `gate`'s own docstring for the bar it has to clear, which
    is listed there rather than counted here -- a count of its conditions in
    this sentence was already wrong by one within the round that added
    `caller_may_run` to them).

    A rubric must never cost a repair, so a bug here has to fail into the
    decision exactly as it was, not into an exception that stops the repair
    from ever being offered. Read-path callers depend on that --
    `payloads/incidents.py` calls `resolve()` once per alert on every
    dashboard load, so an exception here would blank the whole page, not
    just the score. Swallowing it silently would just move the failure from
    "loud and visible" to "the dashboard quietly stopped explaining itself,"
    so it is logged at warning level instead of passed over.
    """
    try:
        conf = conf_mod.assess(db, tenant, alert_key=msg_key, host=host, action=action, tier=tier)
        base.confidence = conf.score
        # The score and its working are set together, from the one object
        # `assess` just returned, and in ONE assignment rather than appended
        # to in a loop. Both halves matter for the fail-open below: this
        # function must leave the decision exactly as the tier rules made it
        # if anything raises, and a list built up incrementally is a list that
        # can be left half-filled -- six factors is a complete answer and two
        # is a misleading one, because the drawer's whole promise is that the
        # owner sees everything that was considered, including what was
        # considered and found absent.
        #
        # No `why`: see `schemas_dash.ConfidenceFactor` for why it is dropped
        # rather than carried (`_confidence_why` below already prints the
        # three loudest ones as the row's sentence, and both surfaces appear
        # on the same screen).
        base.confidence_factors = [
            ConfidenceFactor(name=f.name, question=f.question, answer=f.answer,
                             weight=f.weight)
            for f in conf.factors
        ]

        # `gate` takes its history inputs as plain booleans/an int, not a db
        # handle, so it stays a pure function `test_confidence_gate.py` can
        # drive with no database at all (see `gate`'s own docstring). `assess`
        # has already read everything it needs to answer them, so these are
        # read straight off the factors it already computed rather than a
        # second `m.Fix`/`m.Edit` query -- otherwise "has this been undone"
        # (or "did the alert recur", or "is the sensor fresh") would have two
        # definitions with only one of them under test, which is the same
        # drift this codebase already guards against twice: the fix-tier
        # tables ported from `fix.FIX_ALERT_KEY` and checked against it by
        # `tests/test_remedy.py`, and `payloads/history.py` being the one
        # place a trend is computed for every surface that draws one.
        # The count `gate` needs is the one exception, because a weight's sign
        # cannot carry an integer -- `verified` is +15/-10/0, which says "at
        # least three" and not how many, while `gate`'s promotion sentence
        # prints the number to the customer. It is read off `conf.confirmed`,
        # which `assess` filled from the one `confirmed_repairs` call it
        # already made for the `verified` factor. It used to call
        # `confirmed_repairs` again here: the same query, for the same host
        # and action, twice per alert, on every dashboard load.
        recurred_since = _factor_weight(conf.factors, "held") < 0
        ever_undone = _factor_weight(conf.factors, "undone") < 0
        sensor_fresh = _factor_weight(conf.factors, "sensor_fresh") > 0
        # `caller_may_run` is `role == "admin"` and nothing subtler, because
        # that is the same test the `role != "admin"` branch below uses to
        # reach `kind="approval"` in the first place. The two must agree by
        # construction: `gate` may decide to skip a CONFIRMATION, and the only
        # way it can tell a confirmation from a closed door is to be told
        # whether this caller could have run the repair at all. `apply_fix`'s
        # `min_role` is the real authority (`TOOLS["apply_fix"].min_role`, and
        # every write route re-checks it), so a rubric that promoted here
        # would produce a row claiming Vault is acting while every actual
        # execution path still refused -- see `gate`'s docstring for what that
        # looked like on a real operator's dashboard.
        caller_may_run = role == "admin"

        kind, why = conf_mod.gate(
            base.kind, conf, action=action, tier=tier,
            verified_successes=conf.confirmed, recurred_since=recurred_since,
            ever_undone=ever_undone, sensor_fresh=sensor_fresh,
            auto_heal=platform_settings.auto_heal(), caller_may_run=caller_may_run)
        # `gate` returns "" on its most common paths, including the ordinary
        # case where it agrees with the caller's own decision -- which is
        # nearly every alert. `_confidence_why` explains why that silence
        # still needs a sentence rather than an empty field.
        base.confidence_why = why or _confidence_why(conf)
        if kind != base.kind:
            base.kind = kind
            base.can_run_now = (kind == "auto")
    except Exception:
        log.warning(
            "confidence scoring failed for tenant=%s alert_key=%r action=%s host=%s",
            tenant.id, msg_key, action, host, exc_info=True)


def resolve_action(db, tenant, role: str, action: str, host: str, arg: str, msg: str,
                   msg_key: str = "") -> AlertRemedy:
    """What can be done about a repair the catalogue already names (`action`
    on `host`, with argument `arg`), by a caller with `role` -- the part of
    `resolve()` that no longer needs an `m.Alert` row: it takes the action
    and the sentence to open with (`msg`) as plain arguments, so a Wi-Fi
    guard row or a checklist row that carries no Alert of its own (see this
    module's docstring) can resolve exactly the same way an alert does.

    `msg_key` is `m.Alert.key` verbatim, not `networks.canonical_lan_key(key)`
    -- the two dialects are both real and `confidence.assess` explains at
    length which one it needs and why; pass the alert's own key raw and let
    `assess` do its own lookup. `resolve_check` has no alert at all and
    passes nothing, which is a real answer ("no evidence either way") rather
    than a lie about there being one.

    Read-only: it never queues, mints or writes anything.
    """
    # Imported here rather than at module scope -- see the note on the import
    # block at the top of this file for the cycle this breaks.
    from .chat.registry import TOOLS, capability_gap
    from .chat.tools import ToolContext

    tool = TOOLS["apply_fix"]
    args = fix_args(action=action, host=host, arg=arg)
    label = FIX_LABELS.get(action, action.replace("_", " "))
    # Read once, before the branches. Every outcome below wants it: the fix
    # Vault runs alone, the one it asks about, and the one it cannot reach
    # at all -- that last case is a person about to do this by hand, which is
    # the moment the cost matters most and the moment nothing else tells them.
    impact = FIX_IMPACT.get(action, "")
    # Computed before the reachability check below rather than after it, so
    # a `human` outcome (the host cannot be reached at all) can still carry a
    # tier for `_apply_confidence` to score against -- `effective_tier` is a
    # pure decision over the tenant's own fix history and settings, and does
    # not depend on `host` actually answering.
    tier = effective_tier(db, tenant, tool, args)

    ctx = ToolContext(db=db, tenant=tenant, user=None, role=role)  # type: ignore[arg-type]
    gap = capability_gap(ctx, "apply_fix", args)
    if gap:
        # The capability message already names precisely what is missing and
        # who can add it, in Vault's own voice -- reuse it rather than
        # writing a second, vaguer version of the same sentence.
        summary = f"I know the repair for this ({label} on {host}) but I cannot reach {host} to run it."
        need = _sentence(gap)
        human = AlertRemedy(kind="human", action=action, host=host, arg=arg, tool="apply_fix",
                            min_role=tool.min_role, summary=summary, need=need,
                            impact=impact)
        # `gate` refuses to move a `human` decision on its own -- see
        # `_apply_confidence` -- so this only ever adds the score and its
        # sentence, never changes what the owner is asked to do by hand.
        _apply_confidence(human, db, tenant, action=action, host=host, tier=tier,
                          msg_key=msg_key, role=role)
        # Composed AFTER scoring, not before, because `_confidence_why` is one
        # of the paragraphs. This used to be built above the `AlertRemedy` and
        # passed in as `opening=`, which read fine until the handoff had
        # something to say that scoring produces: the message would have gone
        # out without the sentence while the row beside it printed one. Nothing
        # between the two lines reads `opening`, so moving it down changes only
        # which facts it can include.
        human.opening = _handoff(msg, human)
        return human

    base = AlertRemedy(kind="approval", action=action, host=host, arg=arg, tier=tier,
                        tool="apply_fix", min_role=tool.min_role, impact=impact)

    if role != "admin":
        base.summary = f"I can repair this by running {label} on {host}, but not with your access."
        base.need = (f"An admin on this site has to run it. Ask me in chat and I will prepare it "
                     f"for them to confirm.")
    elif tier >= 3:
        base.summary = f"I can repair this by running {label} on {host}, but not without your go-ahead."
        # WHY it is waiting, not just that it is. When automatic repair is
        # paused platform-wide the honest reason is the pause -- telling an
        # owner that clearing a full disk is "disruptive or hard to undo" when
        # in fact they switched Vault off themselves is how a person
        # concludes the product is being precious and stops reading the line.
        if not platform_settings.auto_heal():
            base.need = (f"{fix_steps(action, host, arg)} Automatic repair is switched off for the "
                         f"whole platform, so I am asking before every fix, including the ones I "
                         f"would normally just do. Say yes to the exact proposal I show in chat and "
                         f"I will do exactly that.")
        else:
            base.need = (f"{fix_steps(action, host, arg)} Say yes to the exact proposal I show in chat "
                         f"and I will do exactly that -- it is disruptive or hard to undo, so I will not "
                         f"run it on an acknowledgement alone.")
    else:
        base.kind = "auto"
        base.can_run_now = True
        base.summary = f"I can fix this myself: {label} on {host}."
        base.need = ""

    # The rubric runs AFTER the tier rules and BEFORE the handoff message is
    # composed below, so a promotion or demotion here is reflected in the
    # `kind` the owner's message actually describes, rather than left to
    # contradict a decision the rubric just changed underneath it. See
    # `_apply_confidence` for what it may and may not change.
    _apply_confidence(base, db, tenant, action=action, host=host, tier=tier,
                      msg_key=msg_key, role=role)

    if base.kind != "auto":
        base.opening = _handoff(msg, base)
    return base


def resolve(db, tenant, alert: "m.Alert", role: str) -> AlertRemedy:
    """What can be done about `alert` by a caller with `role`.

    Read-only: it never queues, mints or writes anything. The inbox payload
    calls it per row to decide whether **Fix** may act immediately, and
    `routers/chat.py`'s handoff calls it before minting the tier-3 proposal
    for the approval case.
    """
    found = fix_for_key(networks.canonical_lan_key(alert.key or ""))
    if found is None:
        return _human(db, tenant, alert)
    action, host, arg = found
    # Raw `alert.key`, not the canonicalized form used two lines up to look
    # the fix template up -- `confidence.assess` documents at length why it
    # needs the namespaced key `m.Alert.key` is actually stored under.
    return resolve_action(db, tenant, role, action, host, arg, alert.msg, msg_key=alert.key or "")


def resolve_check(db, tenant, role: str, *, action: str, host: str, title: str, msg: str,
                  need: str, arg: str = "") -> AlertRemedy:
    """The Security-page counterpart to `resolve()` for a check that carries
    no `Alert` row: a Wi-Fi guard row, a checklist row, or a posture
    deduction (task: the "resolve or leave it" drill-down).

    `action`/`host` come from the payload builder's own knowledge of which
    fix clears which check (`_fix_action_for` in `payloads/security.py`) --
    when there is one, resolution is identical to `resolve_action()`. When
    there is none (every Wi-Fi guard check today: evil twin's `autofix` is
    `"none"`), this builds the same `kind="human"` shape `_human()` does for
    an alert, from the check-specific `need` copy the caller already wrote
    (`payloads/security._CHECK_COPY`) -- never a generic "ask Vault"
    paragraph.
    """
    if action and host:
        return resolve_action(db, tenant, role, action, host, arg, msg)
    summary = f'I have no repair I can run for "{title}" -- this one needs you.'
    need_s = _sentence(need)
    opening = f"{msg}\n\n{summary}\n\nWhat I need from you: {need_s}"
    return AlertRemedy(kind="human", summary=summary, need=need_s, opening=opening)
