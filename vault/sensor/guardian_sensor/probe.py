"""One-shot probe discipline: confirm before believing a bad reading, and
say what an error means in plain words.

A single dropped ping read as 20% loss; a single 502 became a CRIT "DOWN"
push; one slow speed test became "well below usual"; one missed ARP sweep
became "not on the LAN". Every one of those is a transient that clears on a
retry. `confirm` re-runs a probe once before its bad result is trusted -- a
blip clears, a real problem persists -- so an alert is only ever raised on a
reading that survived a second look.
"""
import re
import time


def confirm(run, bad, pause=2.0):
    """Run a probe; if `bad(result)`, wait `pause` seconds and run it again,
    returning the retry. A transient blip comes back clean; a real problem
    comes back bad, and that second reading is the one we report."""
    r = run()
    if not bad(r):
        return r
    time.sleep(pause)
    return run()

_RULES = [
    (r"could not get lock|unable to lock|dpkg.*lock|lock-frontend", "apt is busy (another update is running); try again in a few minutes"),
    (r"permission denied \(publickey", "SSH login refused (key not accepted)"),
    (r"host key verification failed", "SSH host key mismatch (the host's key changed)"),
    (r"connection refused|errno 111", "connection refused (nothing is listening)"),
    (r"timed? ?out|timeout|errno 110", "timed out (no answer in time)"),
    (r"name or service not known|nodename nor servname|could not resolve|does not resolve|getaddrinfo|temporary failure in name", "the name did not resolve (DNS)"),
    (r"no route to host|network is unreachable|errno 113|errno 101", "no route to the host (network path down)"),
    (r"\b502\b|bad gateway", "502 bad gateway (the site's backend did not answer)"),
    (r"\b503\b|service unavailable", "503 service unavailable (the site is overloaded or restarting)"),
    (r"\b504\b|gateway time-?out", "504 gateway timeout (the site's backend was too slow)"),
    (r"certificate verify failed|ssl: |tls", "TLS certificate problem"),
    (r"reset by peer|connection reset", "connection reset by the other side"),
    (r"permission denied", "permission denied"),
]
_NOISE = re.compile(r"<urlopen error |>|\[errno \d+\]|urlerror|httperror|\s+", re.I)


def humanize(text) -> str:
    """Turn a raw exception / stderr tail into one plain phrase a person can act
    on. Unknown errors are just de-noised and shortened, never hidden."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    for pat, phrase in _RULES:
        if re.search(pat, low):
            return phrase
    return _NOISE.sub(" ", raw).strip()[:160]
