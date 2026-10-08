"""What a connection IS, in words an owner can read.

Live connections used to show `api.anthropic.com  tcp/443  3.8 MB` and leave
the reading to the reader. The owner's verdict was "I don't really
understand what I am seeing". This turns the three things a flow row carries
-- a hostname when the sensor saw one, an address, a port -- into two short
phrases: who the far end is ("Anthropic (AI service)") and what kind of
traffic it is ("Encrypted web").

**Best-effort, and it says so.** A hostname is matched by its registrable
suffix against a short list of services people actually recognise; a match
is `sure=True` only when the hostname itself says whose it is. An address
with no hostname is never "identified" -- it is shown as an address, because
guessing a company from an IP range is exactly the confident-and-wrong
answer this product exists not to give. Nothing here is fetched from outside
the hub: Vault has to keep working with the uplink down (CLAUDE.md, Design
assets), so the list is a table in this file.
"""
from __future__ import annotations

import ipaddress

#: `(suffix, name, what it is)`. Longest suffix wins, so `googlevideo.com`
#: (YouTube video) is checked before `google.com`. Keep entries to services a
#: home or small-office owner would recognise by name; an obscure CDN earns
#: its line only if it carries something they use.
_SERVICES: tuple[tuple[str, str, str], ...] = (
    ("anthropic.com", "Anthropic", "AI service"),
    ("claude.ai", "Anthropic", "AI service"),
    ("openai.com", "OpenAI", "AI service"),
    ("chatgpt.com", "OpenAI", "AI service"),
    ("icloud.com", "Apple iCloud", "cloud storage and sync"),
    ("icloud-content.com", "Apple iCloud", "cloud storage and sync"),
    ("apple.com", "Apple", "Apple services and updates"),
    ("mzstatic.com", "Apple", "App Store and media"),
    ("push.apple.com", "Apple", "push notifications"),
    ("googlevideo.com", "YouTube", "video"),
    ("youtube.com", "YouTube", "video"),
    ("ytimg.com", "YouTube", "video"),
    ("gstatic.com", "Google", "Google services"),
    ("googleapis.com", "Google", "Google services"),
    ("google.com", "Google", "Google services"),
    ("gmail.com", "Google", "email"),
    ("nflxvideo.net", "Netflix", "video"),
    ("netflix.com", "Netflix", "video"),
    ("spotify.com", "Spotify", "music"),
    ("scdn.co", "Spotify", "music"),
    ("slack.com", "Slack", "team chat"),
    ("slack-edge.com", "Slack", "team chat"),
    ("slack-imgs.com", "Slack", "team chat"),
    ("zoom.us", "Zoom", "video calls"),
    ("microsoft.com", "Microsoft", "Microsoft services and updates"),
    ("windowsupdate.com", "Microsoft", "Windows updates"),
    ("office.com", "Microsoft", "Office"),
    ("office365.com", "Microsoft", "Office"),
    ("live.com", "Microsoft", "Outlook and accounts"),
    ("teams.microsoft.com", "Microsoft Teams", "video calls and chat"),
    ("whatsapp.net", "WhatsApp", "messaging"),
    ("whatsapp.com", "WhatsApp", "messaging"),
    ("facebook.com", "Facebook", "social"),
    ("fbcdn.net", "Facebook", "social"),
    ("instagram.com", "Instagram", "social"),
    ("amazonaws.com", "Amazon Web Services", "cloud hosting"),
    ("amazon.com", "Amazon", "shopping and devices"),
    ("cloudflare-dns.com", "Cloudflare", "encrypted DNS"),
    ("cloudflareclient.com", "Cloudflare WARP", "VPN"),
    ("cloudflare.com", "Cloudflare", "web infrastructure"),
    ("github.com", "GitHub", "code hosting"),
    ("githubusercontent.com", "GitHub", "code hosting"),
    ("dropbox.com", "Dropbox", "cloud storage"),
    ("abuse.ch", "abuse.ch", "security blocklists"),
    ("guarder.io", "Vault", "this product"),
)

#: What a port means to someone who has never heard of ports. Keyed on
#: `(proto, port)`; `None` proto matches either.
_PORTS: dict[tuple[str | None, int], str] = {
    ("tcp", 443): "Encrypted web",
    ("udp", 443): "Encrypted web (fast)",
    ("tcp", 80): "Unencrypted web",
    ("udp", 53): "Name lookup (DNS)",
    ("tcp", 53): "Name lookup (DNS)",
    ("tcp", 853): "Encrypted name lookup",
    ("udp", 123): "Clock sync",
    ("tcp", 22): "Remote login (SSH)",
    ("udp", 5353): "Finding devices nearby",
    ("udp", 1900): "Finding devices nearby",
    ("udp", 3478): "Voice and video calls",
    ("udp", 3479): "Voice and video calls",
    ("tcp", 5223): "Apple notifications",
    ("tcp", 993): "Email (reading)",
    ("tcp", 587): "Email (sending)",
    ("tcp", 465): "Email (sending)",
    ("tcp", 8883): "Smart-home messaging",
    ("tcp", 1883): "Smart-home messaging",
    ("udp", 67): "Getting an address (DHCP)",
    ("udp", 68): "Getting an address (DHCP)",
    ("tcp", 445): "File sharing",
    ("tcp", 139): "File sharing",
    ("tcp", 62078): "iPhone sync",
    ("udp", 51820): "VPN (WireGuard)",
    ("udp", 41641): "VPN (Tailscale)",
}


def port_meaning(proto: str, port: int) -> str:
    """"Encrypted web", "Clock sync", ... -- or the bare port when unknown."""
    proto = (proto or "").lower()
    hit = _PORTS.get((proto, port)) or _PORTS.get((None, port))
    if hit:
        return hit
    return f"{proto.upper() or 'Port'} {port}" if port else (proto.upper() or "Other")


#: The ranges a home or office network actually uses. Not `ip.is_private`:
#: Python counts the RFC 5737 documentation blocks and other reserved space as
#: "private", which would describe an internet address as a device on the
#: owner's own network.
_LAN = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8",
    "224.0.0.0/4", "255.255.255.255/32", "fc00::/7", "fe80::/10", "ff00::/8", "::1/128"))


def is_local(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in _LAN if n.version == a.version)


def identify(host: str, ip: str) -> tuple[str, str, bool]:
    """`(name, what it is, sure)` for a far end.

    `host` is the TLS server name or resolved name when the sensor had one.
    With no hostname, an address is only ever described by where it is --
    "your network" or "the internet" -- never by whose it might be."""
    host = (host or "").lower().rstrip(".")
    if host:
        best = None
        for suffix, name, what in _SERVICES:
            if host == suffix or host.endswith("." + suffix):
                if best is None or len(suffix) > len(best[0]):
                    best = (suffix, name, what)
        if best:
            return best[1], best[2], True
        return host, "", False
    if is_local(ip):
        return ip, "a device on your network", False
    return ip, "an address on the internet", False
