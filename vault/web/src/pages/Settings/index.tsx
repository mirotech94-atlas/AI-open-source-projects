import { useEffect, useRef } from "react";
import { sensorIsLive } from "../../lib/sensorLive";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Card, Page, PageHeader, StatStrip } from "../../design";
import { useLiveFeed } from "../../lib/live";
import type { StatItem } from "../../design";
import { settingsPayload } from "../../lib/api";
import { atLeast, roleOf, useMe } from "../../lib/auth";
import type { SettingsPayload } from "../../types";
import { Profile } from "./Profile";
import { Users } from "./Users";
import { Targets } from "./Targets";
import { AutoFix } from "./AutoFix";
import { Thresholds } from "./Thresholds";
import { Appearance } from "./Appearance";
import { Sensors } from "./Sensors";
import { Tenants } from "./Tenants";
import { AuditLog } from "./AuditLog";
import { Networks } from "./Networks";
import { Recorders } from "./Recorders";
import { Billing } from "./Billing";

const SECTIONS = [
  "profile",
  "users",
  "targets",
  "networks",
  "recorders",
  "autofix",
  "thresholds",
  "appearance",
  "sensors",
  "audit",
] as const;
type Section = (typeof SECTIONS)[number] | "tenants" | "billing";

function isSection(v: string | undefined): v is Section {
  return v !== undefined && ((SECTIONS as readonly string[]).includes(v) || v === "tenants" || v === "billing");
}

const LABELS: Record<Section, string> = {
  profile: "Profile",
  users: "Users & roles",
  tenants: "Tenants",
  targets: "Sites & targets",
  networks: "Networks",
  // Task 73: the NVRs/DVRs whose cameras only exist behind them.
  recorders: "Camera recorders",
  sensors: "Sensors",
  autofix: "Auto-fix policy",
  thresholds: "Alert thresholds",
  appearance: "Appearance",
  audit: "Audit log",
  // Billing is admin-only, not superadmin-only: a tenant admin is who buys,
  // changes and cancels their own plan. Gating it to the product owner would
  // make it a page only he could ever act on.
  billing: "Billing",
};

/** Config answers "what are my settings?", and the eleven sections below
 *  used to sit in one flat row of tabs in table order — Networks next to
 *  Recorders next to Thresholds, because that is the order the backing
 *  tables happen to live in, not because a person thinks of them together.
 *  Grouped instead by what someone is actually trying to change: who is
 *  let in, what Vault watches, how it behaves on its own, and the
 *  records it keeps. "Integrations" is a real route of its own (`saveConfig`
 *  for ntfy/Slack/webhooks lives there), so it is a plain link out of this
 *  list rather than a fifth group with one item in it. */
const NAV_GROUPS: { label: string; items: Section[] }[] = [
  { label: "Who has access", items: ["profile", "users", "tenants"] },
  { label: "What Vault watches", items: ["targets", "networks", "recorders", "sensors"] },
  { label: "How Vault behaves", items: ["autofix", "thresholds", "appearance"] },
  { label: "Records and billing", items: ["audit", "billing"] },
];

const DEFAULT_SETTINGS: SettingsPayload = {
  suggested_sites: [],
  profile: { owner: "", email: "", address: "", company: "" },
  users: [],
  sites: [],
  hosts: [],
  // The sensor's own defaults (`guardian_sensor/config.py`), so the summary
  // tiles never claim "up to 0/day" while settings are still loading.
  autofix: { enabled: true, max_per_day: 30, attacker_method: "auto", maintenance_window: { start: "02:00", end: "05:00" }, cooldowns: {} },
  thresholds: {},
  appearance: { theme: "system", accent: "blue" },
  sensors: [],
  tenants: [],
};

/** The grouped section nav. Same ARIA contract as the shared `Tabs`
 *  component (one `role="tablist"`, roving-tabindex `role="tab"` buttons,
 *  Left/Right/Home/End to move) built locally rather than reused, because
 *  `Tabs` has no notion of a group and this is the one nav in the app where
 *  a flat row would put "Sensors" and "Audit log" next to each other for no
 *  reason a person would recognise. Each group is a `role="presentation"`
 *  wrapper (the ARIA spec's own answer for grouping owned elements without
 *  claiming a role of its own — assistive tech and axe's `aria-required-
 *  children` check both see straight through it to the `tab` buttons), and
 *  its heading is `aria-hidden`: a sighted-layout aid, not a second level of
 *  the tab structure. */
function SettingsNav({
  groups,
  value,
  onChange,
}: {
  groups: { label: string; items: Section[] }[];
  value: Section;
  onChange: (next: Section) => void;
}) {
  const flat = groups.flatMap((g) => g.items);
  const listRef = useRef<HTMLDivElement | null>(null);

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const keys = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(e.key)) return;
    const i = flat.indexOf(value);
    if (i < 0) return;
    e.preventDefault();
    let next = i;
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") next = (i - 1 + flat.length) % flat.length;
    if (e.key === "ArrowDown" || e.key === "ArrowRight") next = (i + 1) % flat.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = flat.length - 1;
    onChange(flat[next]);
    listRef.current?.querySelectorAll<HTMLButtonElement>("button.g-settings-nav-tab")[next]?.focus();
  }

  return (
    <div className="g-settings-nav-rail">
      <div
        ref={listRef}
        role="tablist"
        aria-label="Settings section"
        aria-orientation="vertical"
        className="g-settings-nav"
        onKeyDown={onKeyDown}
      >
        {groups.map((group) => (
          // `role="presentation"` so this grouping wrapper (and its heading,
          // separately `aria-hidden`) is transparent to the tablist's owned-
          // element check — the buttons inside are still the tablist's direct
          // semantic children, just visually clustered.
          <div className="g-settings-nav-group" role="presentation" key={group.label}>
            <p className="g-settings-nav-group-label" aria-hidden="true">
              {group.label}
            </p>
            {group.items.map((item) => {
              const selected = item === value;
              return (
                <button
                  key={item}
                  type="button"
                  role="tab"
                  className="g-settings-nav-tab"
                  aria-selected={selected}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => onChange(item)}
                >
                  {LABELS[item]}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {/* A real page, not a section state — kept out of the tablist
          entirely (a link has no business as a tablist's owned element)
          rather than reusing the "integrations" trick the old flat Tabs
          row depended on. */}
      <div className="g-settings-nav-group g-settings-nav-elsewhere">
        <p className="g-settings-nav-group-label" aria-hidden="true">
          Elsewhere
        </p>
        <Link to="/settings/integrations" className="g-settings-nav-tab g-settings-nav-link">
          Integrations
        </Link>
      </div>
    </div>
  );
}

export function Settings() {
  const params = useParams<{ section?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const isSuperadmin = meQuery.data?.user.is_superadmin ?? false;
  const tenantId = meQuery.data?.active_tenant?.id ?? null;

  const section: Section = isSection(params.section) ? params.section : "profile";

  // Item 49: a sweep enrols sensors and discovers networks, so the counts
  // on this page move without anyone touching the form. It had no poll and
  // no live handler, so they moved only on a reload.
  const live = useLiveFeed();
  useEffect(() => {
    return live.on("scan", () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    });
  }, [live, queryClient]);

  const settingsQuery = useQuery({ queryKey: ["settings"], queryFn: settingsPayload });
  const settings = settingsQuery.data ?? DEFAULT_SETTINGS;

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
  }

  function changeSection(next: Section) {
    navigate(`/settings/${next}`);
  }

  const groups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((item) => item !== "tenants" || isSuperadmin),
  }));
  const canReadAudit = atLeast(role, "admin");

  const sensorsOnline = settings.sensors.filter(sensorIsLive).length;
  const cfgStats: StatItem[] = [
    { label: "Users", value: String(settings.users.length) },
    { label: "Targets", value: String(settings.sites.length + settings.hosts.length), delta: `${settings.sites.length} sites · ${settings.hosts.length} hosts` },
    { label: "Auto-fix", value: settings.autofix.enabled ? "On" : "Off", delta: settings.autofix.enabled ? `up to ${settings.autofix.max_per_day}/day` : undefined, deltaTone: settings.autofix.enabled ? "good" : "warn" },
    { label: "Sensors", value: String(settings.sensors.length), delta: `${sensorsOnline} online`, deltaTone: sensorsOnline === settings.sensors.length ? "good" : "warn" },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Account · how Vault is set up"
        title="Settings"
        sub="Grouped by what you are changing, not by which table it lives in."
      />
      <StatStrip items={cfgStats} />
    <div className="g-settings">
      <div className="g-settings-layout">
        <SettingsNav groups={groups} value={section} onChange={changeSection} />
        <Card title={LABELS[section]} className="g-settings-panel">
          <div className="g-settings-body">
            {/* Gated on the real payload being PRESENT, not on "not loading".
                `isLoading` is false after a request FAILS too, and then every
                form below mounted on `DEFAULT_SETTINGS` -- whose auto-fix cap
                was 0. On 2026-09-22, during a slow-hub moment, the owner saved
                Auto-fix with every field equal to that placeholder, and the
                sensor has refused to repair anything unattended since: a cap
                of 0 is "the cap is already reached". A form must never be
                able to save values it did not load. */}
            {!settingsQuery.data ? (
              settingsQuery.isError ? (
                <p className="hint" role="alert">
                  Could not load settings, so nothing here can be changed yet.{" "}
                  <button type="button" onClick={() => void settingsQuery.refetch()}>Try again</button>
                </p>
              ) : (
                <p className="hint">Loading settings…</p>
              )
            ) : (
              <>
                {/* Every section below forks `settings` into local editable state
                 *  once, on mount (an admin typing a threshold can't have that
                 *  overwritten by a background refetch) — so it must not mount
                 *  until the real payload is in, or it would fork the transient
                 *  `DEFAULT_SETTINGS` fallback and never see the real values. */}
                {section === "profile" && <Profile settings={settings} role={role} onInvalidate={invalidate} />}
                {section === "users" && <Users settings={settings} role={role} onInvalidate={invalidate} />}
                {section === "targets" && <Targets settings={settings} role={role} onInvalidate={invalidate} />}
                {section === "networks" && <Networks settings={settings} role={role} />}
                {section === "recorders" && <Recorders role={role} />}
                {section === "autofix" && <AutoFix settings={settings} role={role} onInvalidate={invalidate} />}
                {section === "thresholds" && <Thresholds settings={settings} role={role} onInvalidate={invalidate} />}
                {section === "appearance" && <Appearance settings={settings} role={role} onInvalidate={invalidate} />}
                {section === "sensors" && (
                  <Sensors settings={settings} role={role} onInvalidate={invalidate} tenantId={tenantId} />
                )}
                {section === "audit" && <AuditLog role={role} canRead={canReadAudit} />}
                {section === "tenants" && isSuperadmin && <Tenants settings={settings} onInvalidate={invalidate} />}
                {section === "tenants" && !isSuperadmin && (
                  <p className="g-settings-role-notice">Tenants is a superadmin-only area.</p>
                )}
                {section === "billing" && <Billing role={role} />}
              </>
            )}
          </div>
          <p className="g-settings-integrations-link">
            Looking for ntfy / Slack / Teams / webhooks? That is its own page —{" "}
            <Link to="/settings/integrations">Integrations</Link>.
          </p>
        </Card>
      </div>
    </div>
    </Page>
  );
}

export default Settings;
