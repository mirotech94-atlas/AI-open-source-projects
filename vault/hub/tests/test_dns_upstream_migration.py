"""Task: `dnsfilter.DEFAULT_UPSTREAMS` (the security resolver) only ever
takes over when `dns.upstreams` is empty -- but every tenant created before
this fix has the old plain-Cloudflare pair written into `Tenant.config` by
`tenant_defaults.POLICY`'s former default, and that stored value is what the
sensor actually reads. Fixing the code defaults does nothing for a tenant
that already exists; `guardian tenant migrate-dns-upstreams` is the one-time
cleanup, and this pins its rule: rewrite the exact old pair, in that order,
to `[]`, and leave anything an operator might have chosen alone.
"""

from guardian_hub import cli, models as m, tenant_defaults
from guardian_hub.db import SessionLocal

OLD_PAIR = [
    "https://cloudflare-dns.com/dns-query",
    "https://dns.quad9.net/dns-query",
]


def _tenant(slug, upstreams):
    db = SessionLocal()
    db.add(m.Tenant(slug=slug, name=slug.title(),
                     config={"dns": {"enabled": True, "upstreams": upstreams}}))
    db.commit()
    return slug


# --- the pure function -------------------------------------------------

def test_the_exact_old_pair_is_rewritten_to_empty():
    new_config, status = tenant_defaults.dns_upstreams_migration({"dns": {"upstreams": list(OLD_PAIR)}})
    assert new_config["dns"]["upstreams"] == []
    assert status.startswith("migrated")


def test_a_reordered_pair_is_left_alone():
    # Somebody (or a config round-trip) could produce the same two entries
    # in the other order. That is not proof it is the shipped default --
    # only the literal, in-order pair is.
    reordered = list(reversed(OLD_PAIR))
    new_config, status = tenant_defaults.dns_upstreams_migration({"dns": {"upstreams": reordered}})
    assert new_config["dns"]["upstreams"] == reordered
    assert status.startswith("skipped")


def test_a_single_custom_resolver_is_left_alone():
    custom = ["https://my.dns/dns-query"]
    new_config, status = tenant_defaults.dns_upstreams_migration({"dns": {"upstreams": custom}})
    assert new_config["dns"]["upstreams"] == custom
    assert status.startswith("skipped")


def test_an_already_empty_list_is_left_alone():
    new_config, status = tenant_defaults.dns_upstreams_migration({"dns": {"upstreams": []}})
    assert new_config["dns"]["upstreams"] == []
    assert status == "skipped: already empty"


def test_a_config_with_no_dns_block_is_left_alone():
    new_config, status = tenant_defaults.dns_upstreams_migration({"thresholds": {}})
    assert new_config == {"thresholds": {}}
    assert status.startswith("skipped")


# --- the CLI, against a real tenant row ---------------------------------

def test_cli_migrates_the_exact_match(client, capsys):
    slug = _tenant("acme", list(OLD_PAIR))
    cli.main(["tenant", "migrate-dns-upstreams", "--slug", slug, "--apply"])
    out = capsys.readouterr().out
    assert "migrated" in out
    cfg = SessionLocal().query(m.Tenant).filter_by(slug=slug).first().config
    assert cfg["dns"]["upstreams"] == []


def test_cli_leaves_a_custom_resolver_untouched(client, capsys):
    slug = _tenant("bespoke", ["https://my.dns/dns-query"])
    cli.main(["tenant", "migrate-dns-upstreams", "--slug", slug, "--apply"])
    out = capsys.readouterr().out
    assert "skipped" in out
    cfg = SessionLocal().query(m.Tenant).filter_by(slug=slug).first().config
    assert cfg["dns"]["upstreams"] == ["https://my.dns/dns-query"]


def test_cli_leaves_an_already_empty_list_untouched(client, capsys):
    slug = _tenant("clean", [])
    cli.main(["tenant", "migrate-dns-upstreams", "--slug", slug, "--apply"])
    out = capsys.readouterr().out
    assert "skipped" in out
    cfg = SessionLocal().query(m.Tenant).filter_by(slug=slug).first().config
    assert cfg["dns"]["upstreams"] == []


def test_dry_run_is_the_default_and_writes_nothing(client, capsys):
    slug = _tenant("wouldbe", list(OLD_PAIR))
    cli.main(["tenant", "migrate-dns-upstreams", "--slug", slug])
    out = capsys.readouterr().out
    assert "would" in out or "re-run with --apply" in out
    cfg = SessionLocal().query(m.Tenant).filter_by(slug=slug).first().config
    assert cfg["dns"]["upstreams"] == list(OLD_PAIR), "dry-run must not write"
