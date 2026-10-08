# Myron Mekhael — open-source projects

Three projects, one repo. Each folder is self-contained and has its own README.

| Folder | What it is | Stack |
|---|---|---|
| [`vault/`](vault) | Network security monitoring for small sites: an on-site sensor scans the LAN, a hub explains problems in plain language and queues repairs, a dashboard shows them. | Python, FastAPI, SQLAlchemy, React, TypeScript |
| [`flycheap/`](flycheap) | Flight fare tracker with anomaly alerts and buy-or-wait verdicts. | Next.js, SQLite, TypeScript |
| [`atlas/`](atlas) | A personal operations agent: collectors only read, a single executor acts, and the LLM can only pick from a fixed, tiered catalog. | Python |

Hostnames, IP addresses and contact details in this repository are placeholders
(`example.com`, `203.0.113.x`). Copy each project's `.env.example` and fill in
your own values; no real credentials are committed.

## Notes on this public copy

- **Demo and test data are synthetic.** Vault's sample network captures were
  rewritten into a fictional dental practice (`vault/hub/guardian_hub/demoscrub.py`
  does the rewriting), so no real devices, addresses or people appear.
- **Run each project from its own folder** and follow its README. Vault's hub and
  sensor each use their own Python virtualenv (`pytest` from `vault/hub` and
  `vault/sensor`); the dashboard uses `npm test` in `vault/web`.
- The `git` history starts here; the projects were developed in private
  repositories first.

Licensed under the [MIT License](LICENSE).
