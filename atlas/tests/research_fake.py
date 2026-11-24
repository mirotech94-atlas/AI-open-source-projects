"""An in-memory research store shared by the research tests (no network, no
database). Every function `atlas.modules.research.store` exposes."""
from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

FIELDS = ("channels", "offer", "pricing", "positioning", "cadence", "strengths",
          "weaknesses")


class Fake:
    def __init__(self) -> None:
        self.comps: dict[int, dict[str, Any]] = {}
        self.history: dict[int, dict[str, Any]] = {}
        self.watch: dict[int, dict[str, Any]] = {}
        self.changes: dict[int, dict[str, Any]] = {}
        self.prices_: dict[int, dict[str, Any]] = {}
        self.opps: dict[int, dict[str, Any]] = {}
        self.reports: dict[int, dict[str, Any]] = {}
        self.client_reports: dict[int, dict[str, Any]] = {}
        self.cards: list[tuple[str, dict[str, Any], str]] = []

    # competitors
    async def add_competitor(self, lane, name, homepage, client_lead_id,
                             youtube_channel_id):
        for c in self.comps.values():
            if (c["lane"], c["client_lead_id"], c["name"].lower()) == (
                    lane, client_lead_id, name.lower()):
                return None
        cid = len(self.comps) + 1
        self.comps[cid] = {"id": cid, "lane": lane, "name": name, "homepage": homepage,
                           "client_lead_id": client_lead_id,
                           "youtube_channel_id": youtube_channel_id,
                           "status": "active", "updated_at": None,
                           **{f: [] for f in FIELDS}}
        return cid

    async def archive_competitor(self, cid):
        self.comps[int(cid)]["status"] = "archived"

    async def competitor(self, cid):
        c = self.comps.get(int(cid))
        return copy.deepcopy(c) if c else None

    async def competitors(self, lane=None, client_lead_id=None):
        return [copy.deepcopy(c) for c in self.comps.values()
                if c["status"] == "active" and (lane is None or c["lane"] == lane)
                and (client_lead_id is None or c["client_lead_id"] == client_lead_id)]

    async def set_fields(self, cid, fields):
        self.comps[int(cid)].update(copy.deepcopy(fields))
        self.comps[int(cid)]["updated_at"] = datetime.now(UTC)

    async def add_history(self, cid, field, old, new, source):
        hid = len(self.history) + 1
        self.history[hid] = {"id": hid, "competitor_id": cid, "field": field,
                             "old": copy.deepcopy(old), "new": copy.deepcopy(new),
                             "source": source, "status": "active",
                             "at": datetime.now(UTC)}
        return hid

    async def history_rows(self, ids):
        return [copy.deepcopy(self.history[int(i)]) for i in ids]

    async def withdraw_history(self, ids):
        for i in ids:
            self.history[int(i)]["status"] = "withdrawn"

    # watches
    async def add_watch(self, url, label, competitor_id):
        if any(w["url"] == url for w in self.watch.values()):
            return None
        wid = len(self.watch) + 1
        self.watch[wid] = {"id": wid, "url": url, "label": label,
                           "competitor_id": competitor_id, "status": "active",
                           "last_sha": None, "last_text": None, "last_read_at": None,
                           "fail_streak": 0, "last_error": None}
        return wid

    async def archive_watch(self, wid):
        self.watch[int(wid)]["status"] = "archived"

    async def watches(self, limit):
        return [dict(w) for w in self.watch.values() if w["status"] == "active"][:limit]

    async def watch_ok(self, wid, sha, text, at):
        self.watch[int(wid)].update(last_sha=sha, last_text=text, last_read_at=at,
                                    fail_streak=0, last_error=None)

    async def watch_failed(self, wid, error):
        w = self.watch[int(wid)]
        w["fail_streak"] += 1
        w["last_error"] = error
        return w["fail_streak"]

    async def insert_change(self, watch_id, old_sha, new_sha, excerpt, source_url,
                            fetched_at):
        i = len(self.changes) + 1
        self.changes[i] = {"id": i, "watch_id": watch_id, "old_sha": old_sha,
                           "new_sha": new_sha, "excerpt": excerpt,
                           "source_url": source_url, "fetched_at": fetched_at,
                           "status": "active", "label": "", "at": fetched_at}
        return i

    async def insert_price(self, competitor_id, watch_id, amount, currency, period,
                           label, source_url, fetched_at):
        i = len(self.prices_) + 1
        self.prices_[i] = {"id": i, "competitor_id": competitor_id,
                           "watch_id": watch_id, "amount": amount,
                           "currency": currency, "period": period, "label": label,
                           "source_url": source_url, "fetched_at": fetched_at,
                           "status": "active", "name": "", "lane": "mapfront"}
        return i

    async def withdraw(self, table, ids):
        rows = {"research_changes": self.changes, "research_prices": self.prices_}[table]
        for i in ids:
            rows[int(i)]["status"] = "withdrawn"

    async def prices(self, lane=None, client_lead_id=None):
        return [dict(p) for p in self.prices_.values() if p["status"] == "active"]

    async def changes_since(self, days):
        return [dict(c) for c in self.changes.values() if c["status"] == "active"]

    async def watch_stats(self):
        ws = [w for w in self.watch.values() if w["status"] == "active"]
        reads = [w["last_read_at"] for w in ws if w["last_read_at"]]
        return {"active": len(ws), "failing": sum(w["fail_streak"] >= 3 for w in ws),
                "last_read": max(reads) if reads else None}

    # opportunities
    async def insert_opportunity(self, title, detail, source_urls):
        i = len(self.opps) + 1
        self.opps[i] = {"id": i, "title": title, "detail": detail,
                        "source_urls": list(source_urls), "status": "proposed",
                        "task_id": None}
        return i

    async def opportunity(self, oid):
        o = self.opps.get(int(oid))
        return dict(o) if o else None

    async def set_opportunity(self, oid, status, task_id=None):
        self.opps[int(oid)]["status"] = status
        if task_id is not None:
            self.opps[int(oid)]["task_id"] = task_id

    async def opportunities(self, status):
        return [dict(o) for o in self.opps.values() if o["status"] == status]

    # reports
    async def insert_report(self, kind, title, body, sections):
        i = len(self.reports) + 1
        self.reports[i] = {"id": i, "kind": kind, "title": title, "body": body,
                           "sections": sections}
        return i

    async def withdraw_report(self, rid, kind):
        if self.reports[int(rid)]["kind"] == kind:
            self.reports[int(rid)]["kind"] = "withdrawn"

    async def insert_client_report(self, lead_id, to, subject, body):
        i = len(self.client_reports) + 1
        self.client_reports[i] = {"id": i, "client_lead_id": lead_id, "to_address": to,
                                  "subject": subject, "body": body, "status": "drafted"}
        return i

    async def client_report(self, rid):
        r = self.client_reports.get(int(rid))
        return dict(r) if r else None

    async def set_client_report(self, rid, status):
        self.client_reports[int(rid)]["status"] = status

    async def raise_card(self, action, params, reason, client, text, dedup):
        self.cards.append((action, params, f"{reason}\n{text}"))
        return True


def install(monkeypatch) -> Fake:
    from atlas.modules.research import store
    f = Fake()
    for name in dir(Fake):
        if not name.startswith("_"):
            monkeypatch.setattr(store, name, getattr(f, name))
    return f
