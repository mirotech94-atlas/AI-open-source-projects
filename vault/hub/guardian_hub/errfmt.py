"""Plain-words error text for anything shown to a person (Slack, the inbox,
a fix report). A raw `curl: (7) Failed to connect…` or a dpkg lock message is
data for a log, not a sentence for the owner."""
import re

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
