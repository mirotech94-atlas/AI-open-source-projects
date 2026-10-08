import { NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Logo from "../Logo";
import { setActiveTenant } from "../api";
import { roleOf, useMe } from "../lib/auth";
import { jobsPayload, overviewPayload, platformPayload } from "../lib/api";
import { OwnerPalette } from "./OwnerPalette";
import { agoS } from "./net";
import "./owner.css";

/** The sections that exist. Nothing is listed here until it works — a nav
 *  entry that leads to a placeholder is worse than no entry, because it
 *  makes the portal feel broken rather than unfinished. */
/** The console's sections, in the order an operator works through them: what
 *  needs me, then the estate, then the queues people are waiting in, then how
 *  the platform itself is wired. `group` is not decoration -- it is what stops
 *  seven flat links reading as a pile. */
type Badge = "incidents" | "held" | "assets" | "sites" | "certs" | "open_requests" | "open_tickets";

/** A section, and the one number that means "there is work in here".
 *
 * Only four sections have one. A badge on every item is a badge on nothing --
 * the reason the count reads at all is that most of the rail is bare, so a
 * number appearing is itself the signal. Ledger and Fleet deliberately carry
 * none: "how many customers" is not work, and the Overview already says how
 * many sensors are quiet. */
/** The counts that mean "waiting on you". Everything else is inventory. */
const QUEUE_BADGES = new Set<Badge>(["incidents", "held", "open_requests", "open_tickets"]);

/** The reference's four groups -- Operations, Inventory, Insight, Account --
 *  with one of Vault's own between them. The reference is a console for
 *  ONE network; this one watches an estate of them, so the network screens
 *  read whichever customer the bar's picker has selected, and "Customers" is
 *  where the owner moves between them and runs the business. */
export const SECTIONS: { to: string; label: string; hint: string; group: string; badge?: Badge }[] = [
  { to: "/owner", label: "Overview", hint: "your network, right now", group: "Operations" },
  { to: "/owner/incidents", label: "Incidents", hint: "Open and recently resolved", group: "Operations", badge: "incidents" },
  { to: "/owner/maintenance", label: "Maintenance", hint: "Windows and held work", group: "Operations", badge: "held" },
  { to: "/owner/audit", label: "Audit log", hint: "Append-only record of every action", group: "Operations" },

  { to: "/owner/assets", label: "Assets", hint: "Devices, servers, sites and certificates", group: "Inventory", badge: "assets" },
  { to: "/owner/sites", label: "Websites & DNS", hint: "Endpoints, and the resolver", group: "Inventory", badge: "sites" },
  { to: "/owner/certificates", label: "Certificates", hint: "Every certificate Vault watches", group: "Inventory", badge: "certs" },

  { to: "/owner/reports", label: "Reports", hint: "Monthly summary and audit export", group: "Insight" },

  // Named for what is on the screen. "Ledger" and "Fleet" were the
  // console's own words and nobody else's; the owner asked what they were.
  { to: "/owner/ledger", label: "Customers", hint: "Every customer, and whether they are healthy", group: "Estate" },
  { to: "/owner/fleet", label: "Sensors", hint: "Every customer's sensor, and the ones gone quiet", group: "Estate" },
  { to: "/owner/requests", label: "Access requests", hint: "People asking for access", group: "Estate", badge: "open_requests" },
  { to: "/owner/tickets", label: "Support", hint: "Customer tickets, and your answers", group: "Estate", badge: "open_tickets" },

  { to: "/owner/settings", label: "Settings", hint: "How the platform behaves", group: "Account" },
  { to: "/owner/billing", label: "Billing", hint: "What recurs, and from whom", group: "Account" },
];

/**
 * The owner portal's frame.
 *
 * Deliberately not the tenant app's shell. The tenant app is about one
 * network and leads with alerts; this is about the business and leads with
 * customers, so it gets a narrower rail, a denser grid, and a header that
 * names the host you are on — the one piece of chrome that exists purely so
 * you never mistake this surface for a customer's own dashboard.
 */
/**
 * Jump straight to one customer, from anywhere in the console.
 *
 * Every route into an account today goes through a list: open the Ledger,
 * find the row, click it, click again. That is the right path when the
 * question is "who needs me" and the wrong one when the owner already knows
 * whose name they want -- which is the case every time a customer rings them,
 * and which is the case that has a person waiting on the line.
 *
 * It is a native `<select>` on purpose. A hand-rolled combobox would look more
 * like the search field in the design and would have to re-implement type-
 * ahead, keyboard semantics and the mobile picker to be usable; the platform
 * one already does all three, and this console is an instrument panel rather
 * than a showcase. If the customer list ever outgrows a picker, the honest
 * replacement is the command palette over assets, incidents and hostnames the
 * design asks for -- not a prettier dropdown.
 *
 * It NAVIGATES; it does not switch the session. `Ledger`'s "Open their
 * dashboard" button is the destructive one -- it re-points your whole session
 * at that tenant and you need the tenant switcher to get back. Two controls
 * that both say "go to this customer" and mean different things would be a
 * trap, so this one only ever moves the owner console's own route.
 */
function CustomerJump() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["platform"], queryFn: platformPayload, refetchInterval: 60_000 });
  const active = useMe().data?.active_tenant ?? null;
  const [switching, setSwitching] = useState(false);
  const rows = q.data?.tenants ?? [];

  if (rows.length === 0) return null;

  return (
    <label className="g-owner-jump">
      <span className="g-owner-jump-label">Customer</span>
      {/* It now SWITCHES the session's network rather than navigating to the
          account page: every Operations and Inventory screen reads the active
          tenant, so this is the one control that decides what all of them
          show. The cache is cleared on the way, because every payload in the
          app is tenant-scoped and a stale one would show the previous
          customer's numbers under this one's name. The route is left alone,
          so switching on Incidents keeps you on Incidents. */}
      <select
        className="g-owner-jump-select"
        value={active?.id ?? ""}
        disabled={switching}
        autoComplete="off"
        onChange={async (e) => {
          const id = e.target.value;
          // Only a choice a person made. Chrome restores a <select>'s last
          // value on navigation and fires change for it, and on this control
          // that meant the console re-pointing the session at a different
          // customer on every page load -- the hub log showed one
          // `POST /me/tenant` per screen during a plain walk through the
          // rail. A person's change arrives with the control focused; a
          // restoration does not, and a change that lands before `me` has
          // resolved cannot be compared to anything, so both are ignored.
          if (!active || document.activeElement !== e.target) return;
          if (!id || id === active.id) return;
          setSwitching(true);
          try {
            await setActiveTenant(id);
            qc.clear();
          } finally {
            setSwitching(false);
          }
        }}
      >
        {!active && <option value="">Pick a customer…</option>}
        {rows.map((r) => (
          <option key={r.id} value={r.id}>{r.name}</option>
        ))}
      </select>
    </label>
  );
}

/** The reference's 24h / 7d / 30d switch. It scopes the Overview's activity
 *  and self-healing chart, and nothing else claims to follow it -- a range
 *  control that some figures obey and others quietly ignore is worse than
 *  none. The value rides in the URL so the page and the bar share one
 *  reading and a link carries it. */
export const RANGES = ["24h", "7d", "30d"] as const;
export type Range = (typeof RANGES)[number];
export function useRange(): [Range, (r: Range) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("range");
  const range: Range = raw === "7d" || raw === "30d" ? raw : "24h";
  return [range, (r) => {
    const next = new URLSearchParams(params);
    if (r === "24h") next.delete("range"); else next.set("range", r);
    setParams(next, { replace: true });
  }];
}

function RangeSwitch() {
  const [range, setRange] = useRange();
  return (
    <div className="g-owner-range" role="group" aria-label="Range">
      {RANGES.map((r) => (
        <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>{r}</button>
      ))}
    </div>
  );
}

/** Two letters for the avatar box: the initials of a two-word name, or the
 *  first two letters of a one-word one. Purely a mark; the name is beside it. */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const s = parts.length >= 2 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
  return s.toUpperCase();
}

/** What the bar says you are looking at.
 *
 * Derived from the route rather than pushed up by each page, so a page cannot
 * forget to set it and no two pages can disagree about their own name. The
 * account route is the one that is not static: its title is the customer, read
 * from the payload the shell already holds for the picker.
 *
 * The reference puts the title in the bar because the bar is the only thing on
 * screen that is always there -- a title that scrolls away with the content is
 * not a label for the surface, it is the first line of it.
 */
function useHead(rows: { id: string; name: string; slug: string }[], tenant: string) {
  const { pathname } = useLocation();
  if (pathname.startsWith("/owner/accounts/")) {
    const row = rows.find((r) => r.id === pathname.split("/")[3]);
    return { group: "Estate", title: row?.name ?? "Customer", subtitle: row?.slug ?? "" };
  }
  // Longest match wins, so /owner/ledger does not resolve to /owner.
  const hit = [...SECTIONS].sort((a, b) => b.to.length - a.to.length)
    .find((s) => pathname === s.to || pathname.startsWith(s.to + "/"));
  // The network screens say WHOSE network, the reference's "Sensor-01 · your
  // network, right now" -- here the customer's name, since that is the fact
  // an owner with many of them has to be sure of.
  const network = hit && ["Operations", "Inventory", "Insight"].includes(hit.group);
  const sub = network && tenant ? `${tenant} · ${hit!.hint}` : hit?.hint ?? "";
  return { group: hit?.group ?? "Operations", title: hit?.label ?? "Overview", subtitle: sub };
}

export function OwnerShell() {
  const me = useMe().data;
  const owner = me?.user?.is_superadmin === true;
  // One read for the whole frame: the picker's list, the nav badges and the
  // account title all come from it. `useQuery` dedupes it with the page's own
  // call under the same key, so this costs no extra request.
  const platform = useQuery({
    queryKey: ["platform"], queryFn: platformPayload, refetchInterval: 60_000,
    enabled: owner,
  });
  const totals = platform.data?.totals;
  const active = me?.active_tenant ?? null;
  // The selected customer's own readings: the sensor chip and the Operations
  // and Inventory badges come from here. Same keys the pages use, so these
  // are cache hits rather than second requests.
  const overview = useQuery({
    queryKey: ["overview"], queryFn: overviewPayload, refetchInterval: 60_000,
    enabled: owner && active !== null,
  });
  const jobs = useQuery({
    queryKey: ["jobs"], queryFn: jobsPayload, refetchInterval: 60_000,
    enabled: owner && active !== null,
  });
  const ov = overview.data;
  const held = (jobs.data?.jobs ?? []).filter((j) => j.status === "queued" || j.status === "waiting").length;
  const badges: Record<Badge, number> = {
    incidents: ov?.alerts.open ?? 0,
    held,
    assets: ov?.assets.total ?? 0,
    sites: ov?.assets.sites ?? 0,
    certs: ov?.coverage.find((r) => r.label === "Certificates")?.total ?? 0,
    open_requests: totals?.open_requests ?? 0,
    open_tickets: totals?.open_tickets ?? 0,
  };
  const [paletteOpen, setPaletteOpen] = useState(false);
  const head = useHead(platform.data?.tenants ?? [], active?.name ?? "");
  const onOverview = useLocation().pathname === "/owner";

  if (me && !owner) {
    // The API refuses this person anyway; saying so plainly beats a page of
    // failed requests. `roleOf` is read so the message can name what they
    // *are*, rather than only what they are not.
    return (
      <div className="g-owner-refused">
        <h1>This is the owner console.</h1>
        <p>
          Your account is signed in as {me.user.username} ({roleOf(me) ?? "no role"}), which does
          not administer the platform. Your own dashboard is at{" "}
          <a href="https://my.guarder.io">my.guarder.io</a>.
        </p>
      </div>
    );
  }

  return (
    <div className="g-owner">
      <header className="g-owner-bar">
        <span className="g-owner-head">
          <span className="g-owner-eyebrow" aria-hidden="true">{head.group}</span>
          <h1 className="g-owner-title">{head.title}</h1>
          {head.subtitle !== "" && <span className="g-owner-sub">{head.subtitle}</span>}
        </span>
        <button
          type="button"
          className="g-owner-find"
          onClick={() => setPaletteOpen(true)}
          aria-label="Find a customer or a screen"
        >
          {/* The reference's search field: a ring glyph, placeholder-weight
              text, the shortcut at the far end. It is a button that opens
              the palette, drawn as the field the palette is. */}
          <svg className="g-owner-find-glyph" viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
            <circle cx="5" cy="5" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M7.8 7.8 L11 11" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <span className="g-owner-find-text">Find a customer or a screen</span>
          {/* The shortcut printed on the control that runs it. A palette
              nobody can discover is a palette nobody uses. */}
          <kbd className="g-owner-kbd">⌘K</kbd>
        </button>
        <CustomerJump />
        {onOverview && <RangeSwitch />}
        {/* Whether Vault is repairing on its own, in the bar on every
            screen, as the reference has it. Green is the normal state and
            says so quietly; off is amber, because it is work waiting -- an
            estate nobody is repairing -- and it links to the switch. */}
        {platform.data && (
          <NavLink
            to="/owner/settings"
            className={`g-owner-heal${platform.data.auto_heal ? "" : " is-off"}`}
            aria-label={platform.data.auto_heal ? "Automatic repair is on" : "Automatic repair is off"}
          >
            <span className="g-owner-heal-dot" aria-hidden="true" />
            {platform.data.auto_heal ? "Auto-heal on" : "Auto-heal off"}
          </NavLink>
        )}
      </header>

      {/* Vault is watching and repairing nothing. Deliberately in the
          frame rather than on the settings page: settings is the one screen
          where the owner already knows. */}
      {platform.data && platform.data.auto_heal === false && (
        <div className="g-owner-paused" role="status">
          <span>
            Automatic repair is off. Vault is still watching every customer, but
            nothing is repaired until you approve it.
          </span>
          <NavLink to="/owner/settings">Turn it back on</NavLink>
        </div>
      )}

      <OwnerPalette
        open={paletteOpen}
        onOpen={() => setPaletteOpen(true)}
        onClose={() => setPaletteOpen(false)}
        rows={platform.data?.tenants ?? []}
        sections={SECTIONS}
      />

      <div className="g-owner-body">
        <nav className="g-owner-rail" aria-label="Owner console">
          {/* The mark sits at the top of the rail, not in the bar. The bar's
              job is to name the page you are on; leading it with the product
              name puts the least changing thing in the most prominent slot.
              The hostname chip comes with it, because "which surface am I on"
              is the same kind of standing fact. */}
          <div className="g-owner-railhead">
            <span className="g-owner-brand">
              <Logo size={22} />
              <span className="g-owner-wordmark">Vault</span>
            </span>
            <span className="g-owner-host">{window.location.hostname}</span>
            {/* The reference's sensor chip, translated. It watches one sensor
                and says "Online · 41 days"; this console watches an estate, so
                the standing fact is how much of that estate is reporting. Same
                job: one glance that says the thing underneath you is alive.
                Amber the moment any sensor has gone quiet, because a fleet
                that is 5/6 reporting is a customer with no monitoring. */}
            {/* Boxed, per the reference: it is a readout about the thing
                underneath you, not another line of the brand block, and the
                border is what separates the two. */}
            {/* The reference's sensor chip, literally: the selected customer's
                sensor, "SENSOR-01 / Online · 41 days". Falls back to the
                estate's count when no customer is selected. */}
            {ov?.sensor ? (
              <span className={`g-owner-pulse${ov.sensor.status === "active" && (ov.sensor.age_s ?? Infinity) < 900 ? "" : " is-quiet"}`}>
                <span className="g-owner-pulse-top">
                  <span className="g-owner-pulse-dot" aria-hidden="true" />
                  <span className="g-owner-pulse-text">{ov.sensor.name || "sensor"}</span>
                </span>
                <span className="g-owner-pulse-sub">
                  {ov.sensor.status === "active" && (ov.sensor.age_s ?? Infinity) < 900
                    ? `Online · ${ov.health.run_seconds ? agoS(ov.health.run_seconds) : "reporting"}`
                    : `Silent · last seen ${agoS(ov.sensor.age_s)}`}
                </span>
              </span>
            ) : totals && totals.sensors_total > 0 ? (
              <span className={`g-owner-pulse${totals.sensors_online < totals.sensors_total ? " is-quiet" : ""}`}>
                <span className="g-owner-pulse-top">
                  <span className="g-owner-pulse-dot" aria-hidden="true" />
                  <span className="g-owner-pulse-text">
                    {totals.sensors_online}/{totals.sensors_total} sensors
                  </span>
                </span>
                <span className="g-owner-pulse-sub">
                  {totals.sensors_online < totals.sensors_total
                    ? `${totals.sensors_total - totals.sensors_online} gone quiet`
                    : "all reporting"}
                </span>
              </span>
            ) : null}
          </div>
          {SECTIONS.map((s, i) => (
            <div key={s.to} className="g-owner-navitem">
              {/* Drawn once, by the first link carrying the heading, rather
                  than by grouping the array -- so adding a section stays one
                  line in SECTIONS and nothing else. */}
              {(i === 0 || SECTIONS[i - 1].group !== s.group) && (
                <span className="g-owner-navgroup">{s.group}</span>
              )}
            <NavLink
              to={s.to}
              end={s.to === "/owner"}
              className={({ isActive }) => `g-owner-navlink${isActive ? " is-active" : ""}`}
            >
              <span className="g-owner-navrow">
                <span className="g-owner-navlabel">{s.label}</span>
                {/* Only when there is something in it. A zero badge is a badge
                    saying "nothing", which is the state the rail should be
                    silent about. */}
                {s.badge && badges[s.badge] > 0 && (
                  <span className={`g-owner-navbadge${QUEUE_BADGES.has(s.badge) ? " is-waiting" : ""}`}>
                    {badges[s.badge]}
                  </span>
                )}
              </span>
            </NavLink>
            </div>
          ))}
          {/* Who is signed in, at the foot of the rail where the reference
              keeps it: standing fact, not chrome, so it leaves the bar to the
              page's own name and controls. */}
          {me?.user && (
            <div className="g-owner-railfoot">
              <span className="g-owner-avatar" aria-hidden="true">
                {initials(me.user.display_name || me.user.username)}
              </span>
              <span className="g-owner-railwho">
                <span className="g-owner-railname">{me.user.display_name || me.user.username}</span>
                <span className="g-owner-railsub">{me.user.username}</span>
              </span>
            </div>
          )}
        </nav>
        <main className="g-owner-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default OwnerShell;
