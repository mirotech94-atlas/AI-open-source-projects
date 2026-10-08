# Guardian sensor

The sensor is the piece that actually looks at a network. It runs on a machine
*inside* that network (ARP, ping and Bonjour need L2 adjacency), sweeps every
network it is configured for, checks the sites/hosts it is given, and pushes one
scan per run to the hub.

Stdlib Python only — no third-party runtime dependencies.

## Install

```bash
python3 -m venv ~/.guardian/venv
~/.guardian/venv/bin/pip install /path/to/guardian/sensor
~/.guardian/venv/bin/guardian-sensor enrol <enrol code> --hub https://<your-hub>/
~/.guardian/venv/bin/guardian-sensor install --launchd     # or --systemd on Linux
```

The enrol code comes from the hub: **Settings → Networks → Copy enrol command**
(one per network) or `guardian enrol-code <tenant-slug>` on the hub host.

Everything the sensor keeps lives under `~/.guardian/`:

| path | what |
|---|---|
| `config.json` | the config below (yours; the hub can also push it) |
| `.env` | credentials — hub token, `NTFY_TOPIC`. Mode `600`, never committed |
| `state.json` / `last.json` | baseline + last scan, per network |
| `history.jsonl`, `events.jsonl`, `remediation.jsonl` | rolling local history |
| `logs/` | run logs |

## Watching more than one network

`config.networks` is the list of networks this sensor watches. Each entry gets
its own baseline, known-devices map, presence state, DNS attribution and Wi-Fi
guard, and its LAN alert keys are namespaced with the network's slug
(`lan:<slug>:<sub>:<rest>` — e.g. `lan:lab:new:aa:bb:cc:dd:ee:ff`), so the same
laptop seen on the Wi-Fi and on the lab VLAN is two devices, not one flapping
one.

```jsonc
{
  "networks": [
    {
      "name": "Home network",          // required; the hub derives the slug from it
      "kind": "wifi",                  // wifi | wired | lab | vlan | guest
      "subnet_prefix": "192.168.1",    // first three octets, no trailing dot
      "expected_gateway": "192.168.1.1",
      "enabled": true,
      "interface": null,               // optional: pin the sweep to one interface
      "watch_ips": {"192.168.1.50": "NAS"}
    },
    {
      "name": "Lab",
      "kind": "lab",
      "subnet_prefix": "10.10.0",
      "expected_gateway": "10.10.0.1"
    }
  ]
}
```

Rules worth knowing before you edit this by hand:

- **The first entry is the primary network.** It is the attribution fallback for
  anything the hub cannot place, and it is the one network the DNS setup wizard
  and the rogue-DHCP/evil-twin checks run against. It cannot be deleted from the
  hub UI — make another network primary first.
- **`lan` and `networks` are the same thing for one network.** A config with no
  `networks` key still works: the legacy `lan` block is promoted into a
  one-entry list, and `lan` keeps pointing at the primary's
  `subnet_prefix`/`expected_gateway`/`enabled`. Conversely, when you do write
  `networks`, the primary's values are mirrored back onto `lan`, so there is
  never a second, disagreeing copy. Do not maintain both by hand — edit
  `networks` and let `lan` follow.
- **`slug` is the hub's.** You may leave it out; the hub writes it and freezes
  it at creation, so renaming a network never orphans its history.
- **A network the sensor cannot reach is reported, not skipped.** If no local
  interface sits on `10.10.0.0/24`, that entry comes back as
  `status: "unreachable"` with a reason, and the hub shows *"needs a sensor on
  this network"* rather than an empty scan that would read as "everything left".
  That is the signal to install a second sensor on that network.
- **`watch_ips` on the primary is `lan.watch_ips`**; a secondary network's watch
  list is its own entry's.
- **The hub can push this whole file.** Adding or editing a network in
  Settings → Networks emits a whole-config edit that the sensor applies on its
  next poll, so hand-editing is the fallback, not the normal path. Restart after
  a hand edit: `launchctl kickstart -k gui/$(id -u)/com.guardian.sensor`.

## Running by hand

```bash
~/.guardian/venv/bin/guardian-sensor run --once     # one scan, prints the summary
~/.guardian/venv/bin/guardian-sensor run --once --no-lan   # skip the network sweeps
~/.guardian/venv/bin/guardian-sensor selftest       # local self-checks
~/.guardian/venv/bin/guardian-sensor dns            # run the DNS filter in the foreground
```

To prove notifications work end to end, use the hub's **Settings → Integrations
→ Test** button — delivery for every non-local channel is the hub's job, not the
sensor's.

`run --once` prints one line per network — its name, live device count, alerts
raised — and whether the push to the hub succeeded.
