import argparse, json, sys
from .db import SessionLocal
from . import models as m, platform_settings, security, tenant_defaults
from .accounts import upsert_membership, upsert_user


def _money(cents) -> str:
    """Cents as dollars. Anything that is not an int came back from Stripe
    malformed (or absent), and printing it raw beats printing "$0.00"."""
    if not isinstance(cents, int):
        return "(none)" if cents is None else str(cents)
    whole, rem = divmod(cents, 100)
    return f"${whole}.{rem:02d}"


def _price_problems(price: dict, plan, interval: str) -> list[str]:
    """Every way one Stripe price disagrees with the catalogue -- all of them,
    not just the first.

    These are fixed by hand in the Stripe dashboard, so a check that stopped
    at the amount would send the owner back for a second trip when the
    currency was wrong too. Each problem names what Stripe holds and what
    `plans` expects, because the fix is a dashboard edit and he needs both
    numbers in front of him.
    """
    from . import plans
    out: list[str] = []
    got = price.get("unit_amount")
    if got != plan.cents(interval):
        out.append(f"amount is {_money(got)}, expected {_money(plan.cents(interval))}")
    currency = str(price.get("currency") or "")
    if currency != plans.CURRENCY:
        out.append(f"currency is {currency or '(none)'}, expected {plans.CURRENCY}")
    recurring = (price.get("recurring") or {}).get("interval")
    if recurring != interval:
        out.append(f"recurring interval is {recurring or '(one-off)'}, expected {interval}")
    return out


def _billing_check() -> int:
    """Prove this hub's Stripe account carries a usable price for every plan it
    offers to sell. Returns a process exit status: 0 only if it does.

    Why this command exists at all: Vault resolves prices by lookup key at
    checkout time (`plans.Plan.lookup_key` -> `stripe_api.price_for_lookup_key`,
    called from `routers/billing.py`), never from a price id pasted into the
    environment. That is what lets the owner edit the catalogue in the Stripe
    dashboard without a redeploy, and it is also why a price he never created
    -- or created at the wrong amount, currency or interval -- is completely
    invisible from inside Vault until a real customer reaches the hosted
    checkout and is told no. Nothing in this codebase can create those prices;
    they are dashboard objects. So this does the one thing code can do: read
    them back and hold them against `plans`.
    """
    from . import plans, stripe_api
    from .config import settings

    # Said first, because it decides what "configured" even means here. On a
    # `trial_only` hub the paid catalogue still exists in `plans.py` but is
    # never offered: `routers/billing.py` refuses checkout before it reaches a
    # price. Verifying the prices anyway is worth doing -- switching the mode
    # is a setting, not a deploy, and the prices are the part that cannot be
    # switched on in a second -- but a hub deliberately selling only the free
    # trial is not misconfigured, and must not be reported as though it were.
    mode = "free trial only" if platform_settings.trial_only() else "paid plans offered"
    print(f"Billing mode: {platform_settings.billing_mode()} ({mode}).")
    if platform_settings.trial_only():
        print("Checkout refuses on this hub whatever Stripe holds, so nothing below "
              "is in use yet. It is what has to be true before "
              "GUARDIAN_BILLING_MODE=plans.\n")

    if not settings.billing_configured():
        # Not a traceback: "billing was never set up" is the expected state of
        # a self-hosted hub, and the answer the owner needs is the name of the
        # variable, not a stack.
        print("Billing is not configured on this hub: there is no Stripe secret key, "
              "so no price can be looked up.")
        print("Set GUARDIAN_STRIPE_SECRET_KEY (Stripe dashboard > Developers > "
              "API keys > Secret key) and run this again.")
        # Nothing is wrong with a trial-only hub that has no Stripe account at
        # all -- that is the shipped default. Only a hub that says it offers
        # paid plans and cannot reach Stripe is actually broken.
        return 0 if platform_settings.trial_only() else 1

    # `PAID_PLANS` is `PLANS` minus the trial, and the trial's absence here is
    # deliberate rather than an omission to be tidied up later. The trial is
    # free, takes no card and creates no Stripe object at all: it is granted
    # by `billing.start_trial` writing a local Subscription row. There is no
    # `guardian_trial_month` price in Stripe and there must never be one, so
    # checking for it would report a permanent failure that no dashboard edit
    # could ever clear.
    rows: list[tuple[str, str, str, str]] = []
    failures = 0
    probe_price = ""   # the first price that checks out, for the session probe below
    for plan in plans.PAID_PLANS:
        for interval in plans.INTERVALS:
            key = plan.lookup_key(interval)
            expected = f"{_money(plan.cents(interval))}/{interval}"
            try:
                price = stripe_api.price_for_lookup_key(key)
                problems = _price_problems(price, plan, interval)
                if not problems and not probe_price:
                    probe_price = str(price.get("id") or "")
            except stripe_api.StripeError as exc:
                # Caught per row, not per run: a missing price on the second
                # plan must not hide the state of every row after it, or the
                # owner fixes one price per re-run instead of all at once.
                problems = [str(exc)]
            if problems:
                failures += 1
            rows.append((key, expected, "yes" if plan.per_site else "no",
                         "OK" if not problems else "FAIL: " + "; ".join(problems)))

    head = ("lookup key", "expected", "per-site", "result")
    # Sized from the rows rather than from a constant, so adding a plan with a
    # longer id cannot quietly break the alignment.
    wk = max(len(head[0]), *(len(r[0]) for r in rows))
    wa = max(len(head[1]), *(len(r[1]) for r in rows))
    wp = len(head[2])
    print(f"{head[0]:<{wk}}  {head[1]:<{wa}}  {head[2]:<{wp}}  {head[3]}")
    for key, expected, per_site, result in rows:
        print(f"{key:<{wk}}  {expected:<{wa}}  {per_site:<{wp}}  {result}")

    if failures:
        print(f"\n{failures} of {len(rows)} prices need fixing in the Stripe dashboard.")
    else:
        print(f"\nAll {len(rows)} prices are present and correct.")
    print("A per-site plan is charged by quantity: checkout passes the site count as "
          "the line item quantity, so those prices must be created per-unit in "
          "Stripe, not as a flat amount.")

    # The prices being right is not the same as a customer being able to pay.
    # 2026-09-21: every row above read OK, and every Upgrade click was a 502 --
    # the account had accepted Managed Payments and refused any Checkout
    # Session under the API version the hub then pinned. This check verified
    # the catalogue and never asked the owner's actual question. So it asks
    # it: make a real session against the account, then expire it at once so
    # nothing is left waiting to be paid. A write on the live account, but an
    # inert one -- a session charges nothing until a customer completes it.
    if failures:
        print("Checkout session: skipped -- fix the prices above first, then run again.")
    elif not probe_price:
        print("Checkout session: skipped -- no usable price to probe with.")
    else:
        base = settings.base_url.rstrip("/")
        try:
            session = stripe_api.create_checkout_session(
                price_id=probe_price, quantity=1, tenant_id="billing-check",
                customer_id="", customer_email="",
                success_url=f"{base}/settings/billing?probe=done",
                cancel_url=f"{base}/settings/billing?probe=cancelled",
                probe=True)
            stripe_api.expire_checkout_session(str(session.get("id") or ""))
            print("Checkout session: OK -- created against this account under "
                  f"API version {stripe_api.CHECKOUT_API_VERSION}, then expired.")
        except stripe_api.StripeError as exc:
            failures += 1
            print(f"Checkout session: FAIL -- {exc}")
            print("A customer pressing Upgrade gets this same answer. If it names an API "
                  "version, the account has Managed Payments accepted and "
                  "GUARDIAN_STRIPE_MANAGED_PAYMENTS must be true on this hub.")

    # Reported, but deliberately NOT part of the exit status. The prices are
    # what this command verifies; a script asking "can a customer pay?" should
    # not be answered "no" because of a separate variable. The gap is real all
    # the same: with no signing secret `stripe_api.verify_signature` rejects
    # every webhook (it must -- that route is unauthenticated), so a
    # subscription that completes checkout never becomes active in Vault.
    if settings.stripe_webhook_secret.strip():
        print("Webhook signing secret: set.")
    else:
        print("Webhook signing secret: NOT SET -- checkout will succeed and the "
              "subscription will never become active here, because every Stripe "
              "webhook is rejected unsigned. Set GUARDIAN_STRIPE_WEBHOOK_SECRET "
              "(Stripe dashboard > Developers > Webhooks > your endpoint > "
              "Signing secret).")
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="guardian"); sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    t = sub.add_parser("tenant"); ts = t.add_subparsers(dest="tcmd", required=True)
    ta = ts.add_parser("add"); ta.add_argument("slug"); ta.add_argument("name")
    ts.add_parser("list")
    tad = ts.add_parser("apply-defaults")  # shared policy -> an existing tenant, on purpose
    tad.add_argument("slug"); tad.add_argument("--dry-run", action="store_true")
    tp = ts.add_parser("purge-orphans")   # rows whose tenant no longer exists
    tp.add_argument("--apply", action="store_true")   # without it, reports and changes nothing
    tmd = ts.add_parser("migrate-dns-upstreams")  # the old plain-Cloudflare default -> []
    tmd.add_argument("--slug")            # omit for every tenant
    tmd.add_argument("--apply", action="store_true")   # without it, reports and changes nothing
    u = sub.add_parser("user"); us = u.add_subparsers(dest="ucmd", required=True)
    ua = us.add_parser("add"); ua.add_argument("username"); ua.add_argument("email"); ua.add_argument("--tenant"); ua.add_argument("--role", default="admin"); ua.add_argument("--superadmin", action="store_true"); ua.add_argument("--name", default="")
    e = sub.add_parser("enrol-code"); e.add_argument("slug")
    sub.add_parser("selftest")            # Task 12
    sub.add_parser("billing-check")        # is Stripe carrying a price for every paid plan?
    imp = sub.add_parser("import-nexus"); imp.add_argument("src"); imp.add_argument("--tenant-map", default="myron=home,demo=demo")  # Task 11
    sd = sub.add_parser("seed-demo")      # Task 17
    sd.add_argument("--slug", default="demo"); sd.add_argument("--days", type=int, default=30); sd.add_argument("--reset", action="store_true")
    sd.add_argument("--force", action="store_true")   # required to --reset a non-"demo" slug -- guards against wiping a real tenant by typo
    pr = sub.add_parser("prune")          # Task 55
    pr.add_argument("--slug"); pr.add_argument("--dry-run", action="store_true")
    dr = sub.add_parser("demo-refresh")   # Task 64: run scheduler.demo_refresh_job's work once, by hand
    dr.add_argument("--slug", default="demo"); dr.add_argument("--days", type=int, default=30)
    ld = sub.add_parser("lead"); lds = ld.add_subparsers(dest="lcmd", required=True)
    lds.add_parser("list")
    la = lds.add_parser("approve"); la.add_argument("lead_id")
    a = ap.parse_args(argv)
    if a.cmd == "migrate":
        from alembic.config import Config; from alembic import command
        import pathlib; command.upgrade(Config(str(pathlib.Path(__file__).resolve().parent.parent / "alembic.ini")), "head")
        # Data fixes that are not schema changes and so do not belong in a
        # revision, but must reach a deployed database. Idempotent, and each
        # prints only when it actually changed something.
        from .importer import relabel_imported_sites
        _db = SessionLocal()
        try:
            n = relabel_imported_sites(_db)
            if n:
                print(f"relabelled {n} stale site label(s)")
        finally:
            _db.close()
        return
    if a.cmd == "billing-check":
        # Answered before the database is opened, like `migrate`: this command
        # reads Stripe and nothing else, and it should still answer on a hub
        # whose database is unreachable -- "can customers pay?" is exactly the
        # question you ask while something else is broken.
        sys.exit(_billing_check())
    db = SessionLocal()
    if a.cmd == "tenant" and a.tcmd == "add":
        db.add(m.Tenant(slug=a.slug, name=a.name,
                        config=tenant_defaults.new_tenant_config())); db.commit(); print("ok")
    elif a.cmd == "tenant" and a.tcmd == "apply-defaults":
        # Deliberate opt-in for an *existing* tenant. Nothing backfills
        # automatically: `demo` carries the legacy bare threshold names on
        # purpose and the public demo must not shift under the owner's feet
        # (CLAUDE.md's demo rules), so an owner asks for this per tenant.
        t = db.query(m.Tenant).filter_by(slug=a.slug).first() or sys.exit(f"no such tenant: {a.slug}")
        merged, added, kept = tenant_defaults.apply_defaults(t.config)
        # Printed before the additions, because this is the one part of the
        # output that is not simply "a missing key gets filled in": the
        # canonical name wins over the legacy one in `assets.py`, so these
        # lines are a live threshold changing value, not a gap being closed.
        for bare, was, canonical, now in tenant_defaults.legacy_conflicts(t.config):
            print(f"! {canonical}={now} supersedes {bare}={was} -- "
                  f"the effective threshold changes from {was} to {now}")
        for path in added:
            print(f"+ {path}")
        for path in kept:
            print(f"= {path} (kept)")
        if not added:
            print(f"{a.slug}: already has every policy key")
        elif a.dry_run:
            print(f"{a.slug}: would add {len(added)} key(s), keep {len(kept)} -- nothing changed")
        else:
            t.config = merged      # whole-dict assignment: the JSON column does not track in-place edits
            db.commit()
            print(f"{a.slug}: added {len(added)} key(s), kept {len(kept)}")
    elif a.cmd == "tenant" and a.tcmd == "purge-orphans":
        # Tenant deletion used to leave every scoped row behind (see
        # accounts.purge_tenant). This clears what the old code stranded.
        # Reports by default; only --apply writes.
        from .accounts import TENANT_SCOPED_TABLES, purge_tenant
        from sqlalchemy import text as _text
        live = {t.id for t in db.query(m.Tenant)}
        found: dict[str, int] = {}
        for table in TENANT_SCOPED_TABLES:
            for tid, n in db.execute(_text(
                    f"SELECT tenant_id, COUNT(*) FROM {table} GROUP BY tenant_id")):
                if tid not in live:
                    found[tid] = found.get(tid, 0) + n
        if not found:
            print("no orphaned rows")
        for tid, n in sorted(found.items(), key=lambda kv: -kv[1]):
            if not a.apply:
                print(f"would purge {n} row(s) owned by missing tenant {tid}")
                continue
            counts = purge_tenant(db, tid)
            db.commit()
            print(f"purged {sum(counts.values())} row(s) owned by missing tenant {tid}")
            for table, c in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"  {table}: {c}")
        if found and not a.apply:
            print("nothing changed -- re-run with --apply")
    elif a.cmd == "tenant" and a.tcmd == "migrate-dns-upstreams":
        # Part 3 of the security-resolver fix (CLAUDE.md's "sensor never
        # reads the database" cuts the other way here too: a sensor only
        # ever gets what the hub pushes it, so fixing the code defaults in
        # sensor/config.py and hub/tenant_defaults.py changes nothing for a
        # tenant whose `Tenant.config` already has the old value written
        # into it). Same shape as purge-orphans just above: reports by
        # default, `--apply` writes, because rewriting a tenant's stored DNS
        # config is exactly the kind of automatic change that must not
        # happen by accident on a shared production database.
        if a.slug:
            tenants = [db.query(m.Tenant).filter_by(slug=a.slug).first()
                       or sys.exit(f"no such tenant: {a.slug}")]
        else:
            tenants = db.query(m.Tenant).order_by(m.Tenant.slug).all()
        migrated = 0
        for t in tenants:
            before = ((t.config or {}).get("dns") or {}).get("upstreams")
            new_config, status = tenant_defaults.dns_upstreams_migration(t.config)
            after = ((new_config or {}).get("dns") or {}).get("upstreams")
            print(f"{t.slug}: {status} (before={before!r} after={after!r})")
            if status.startswith("migrated"):
                migrated += 1
                if a.apply:
                    t.config = new_config   # whole-dict assignment: see apply-defaults above
        if a.apply:
            db.commit()
            print(f"{migrated} tenant(s) migrated")
        else:
            print(f"{migrated} tenant(s) would be migrated -- re-run with --apply")
    elif a.cmd == "tenant":
        for t in db.query(m.Tenant): print(t.slug, t.name, t.id)
    elif a.cmd == "user":
        usr = upsert_user(db, a.username, a.email, a.name, superadmin=a.superadmin)
        if a.tenant:
            t = db.query(m.Tenant).filter_by(slug=a.tenant).first() or sys.exit("no such tenant")
            upsert_membership(db, usr, t, a.role)
        db.commit(); print("ok")
    elif a.cmd == "enrol-code":
        import datetime as dt
        t = db.query(m.Tenant).filter_by(slug=a.slug).first() or sys.exit("no such tenant")
        s = m.Sensor(tenant_id=t.id, enrol_code=security.new_enrol_code(), enrol_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15)); db.add(s); db.commit(); print(s.enrol_code)
    elif a.cmd == "selftest":
        from .selftest import run; sys.exit(run())
    elif a.cmd == "import-nexus":
        from .importer import import_nexus; import_nexus(a.src, dict(p.split("=") for p in a.tenant_map.split(","))); print("imported")
    elif a.cmd == "seed-demo":
        from .seed import reset_tenant, seed_demo
        if a.reset:
            if a.slug != "demo" and not a.force:
                sys.exit(f"refusing to --reset tenant '{a.slug}': not 'demo' -- pass --force to wipe it anyway")
            t = db.query(m.Tenant).filter_by(slug=a.slug).first()
            if t:
                reset_tenant(db, t.id)
        out = seed_demo(db, slug=a.slug, days=a.days)
        print(json.dumps(out))
    elif a.cmd == "demo-refresh":
        from .seed import refresh_demo
        out = refresh_demo(db, slug=a.slug, days=a.days)
        if out is None:
            print(json.dumps({"skipped": True, "reason": f"no tenant with slug '{a.slug}'"}))
        else:
            print(json.dumps(out))
    elif a.cmd == "prune":
        from . import retention
        try:
            if a.slug:
                t = db.query(m.Tenant).filter_by(slug=a.slug).first() or sys.exit(f"no such tenant: {a.slug}")
                results = {t.slug: retention.prune_tenant(db, t, dry_run=a.dry_run)}
            else:
                results = retention.prune_all(db, dry_run=a.dry_run)
        except SystemExit:
            raise
        except Exception as e:
            print(f"prune failed: {e}", file=sys.stderr)
            sys.exit(1)
        for slug, counts in results.items():
            cols = "  ".join(f"{k}={v}" for k, v in counts.items())
            print(f"{slug}  {cols}")
    elif a.cmd == "lead":
        from .accounts import provision_from_lead, slugify
        from . import mailer
        if a.lcmd == "list":
            for r in db.query(m.Lead).order_by(m.Lead.created.desc()):
                print(r.id, r.status, r.email, r.site_name or "-")
        else:
            lead = db.get(m.Lead, a.lead_id)
            if lead is None:
                raise SystemExit(f"no lead {a.lead_id}")
            if lead.status != "pending":
                raise SystemExit(f"lead is {lead.status}")
            tenant_name = lead.site_name or lead.company or f"{lead.name}'s network"
            tenant, user, existed = provision_from_lead(
                db, lead, tenant_name=tenant_name, slug=slugify(tenant_name),
                username=lead.email, display_name=lead.name, by="cli")
            db.commit()
            mailer.send_account_ready(lead.email, user.username, tenant.name, user_existed=existed)
            print(f"approved: tenant {tenant.slug} · user {user.username}")
