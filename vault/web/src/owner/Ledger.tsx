import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { setActiveTenant } from "../api";
import { Badge, Drawer, Table } from "../design";
import type { Column, SortState } from "../design";
import { TrendCell } from "./TrendCell";
import { accountPayload, platformPayload } from "../lib/api";
import type { PlatformTenantRow } from "../types";

/** What is set up on the account, under the figures: sensors, people,
 *  where alerts go. Read on open. Every question a customer asks turns out
 *  to be one of these, so the drawer answers them before the call. */
function Setup({ tid }: { tid: string }) {
  const q = useQuery({ queryKey: ["account", tid], queryFn: () => accountPayload(tid) });
  if (q.isPending) return <p className="g-own-quiet">Reading the account…</p>;
  const a = q.data;
  if (!a) return null;
  return (
    <dl className="g-facts g-own-setup">
      <div className="g-facts-row"><dt>Sensors</dt><dd>{a.sensors.length === 0 ? "none enrolled" : a.sensors.map((s) => `${s.name} · ${s.status}`).join(", ")}</dd></div>
      <div className="g-facts-row"><dt>Networks</dt><dd>{a.networks.length === 0 ? "none named" : a.networks.map((n) => `${n.name} (${n.devices})`).join(", ")}</dd></div>
      <div className="g-facts-row"><dt>People</dt><dd>{a.users.map((u) => `${u.display_name || u.username} · ${u.role}`).join(", ") || "—"}</dd></div>
      <div className="g-facts-row"><dt>Alerts go to</dt><dd>{a.integrations.length === 0 ? "nowhere wired" : a.integrations.map((i) => i.kind).join(", ")}</dd></div>
      <div className="g-facts-row"><dt>Billing</dt><dd>{a.billing ? `${a.billing.plan_label} · ${a.billing.status}` : "no subscription"}</dd></div>
    </dl>
  );
}

/** "4m", "3h", "6d" — the same shorthand the app's own header uses for the
 *  last scan, so the two surfaces read the same way. */
function ago(iso: string): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "never";
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

/** What the tenant is paying, said the way the owner thinks about it. */
function planLabel(row: PlatformTenantRow): string {
  if (!row.plan) return "no subscription";
  if (row.plan === "trial") return row.plan_status === "paused" ? "trial ended" : "trial";
  return row.plan.replace(/_/g, " ");
}

/** Whether this customer needs the owner today. One predicate, read by both
 *  the opening sentence and the row's own left edge, so the count can never
 *  disagree with the rows it is counting. */
function needsYou(r: PlatformTenantRow): boolean {
  return (
    !r.ingesting ||
    (r.trial_days_left !== null && r.trial_days_left <= 14) ||
    (r.sensors_total > 0 && r.sensors_online < r.sensors_total) ||
    r.open_crit > 0
  );
}

export function Ledger() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [opening, setOpening] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["platform"], queryFn: platformPayload, refetchInterval: 60_000 });
  const [sort, setSort] = useState<SortState>({ key: "name", dir: "asc" });
  const [open, setOpen] = useState<PlatformTenantRow | null>(null);

  const rows = useMemo(() => {
    const list = [...(query.data?.tenants ?? [])];
    const { key, dir } = sort;
    list.sort((a, b) => {
      const av = a[key as keyof PlatformTenantRow];
      const bv = b[key as keyof PlatformTenantRow];
      const n = typeof av === "number" && typeof bv === "number"
        ? av - bv
        : String(av ?? "").localeCompare(String(bv ?? ""));
      return dir === "asc" ? n : -n;
    });
    return list;
  }, [query.data, sort]);

  const columns: Column<PlatformTenantRow>[] = [
    {
      key: "name",
      header: "Customer",
      render: (r) => (
        <span className="g-led-name">
          <strong>{r.name}</strong>
          <span className="g-led-slug">{r.slug}</span>
        </span>
      ),
    },
    {
      key: "plan",
      header: "Plan",
      render: (r) => (
        <span className="g-led-plan">
          {planLabel(r)}
          {!r.ingesting && <Badge tone="warn">not collecting</Badge>}
        </span>
      ),
    },
    {
      key: "sensors_online",
      header: "Sensors",
      align: "right",
      render: (r) => (
        <span className={`mono${r.sensors_total > 0 && r.sensors_online < r.sensors_total ? " g-led-short" : ""}`}>
          {r.sensors_online}/{r.sensors_total}
        </span>
      ),
    },
    { key: "last_scan", header: "Last scan", align: "right", render: (r) => <span className="mono">{ago(r.last_scan)}</span> },
    { key: "devices", header: "Devices", align: "right", render: (r) => <span className="mono">{r.devices}</span> },
    {
      key: "open_crit",
      header: "Open alerts",
      align: "right",
      render: (r) =>
        r.open_crit + r.open_warn === 0 ? (
          <span className="g-led-quiet mono">0</span>
        ) : (
          <span className="g-led-alerts mono">
            {r.open_crit > 0 && <span className="is-crit">{r.open_crit} crit</span>}
            {r.open_warn > 0 && <span className="is-warn">{r.open_warn} warn</span>}
          </span>
        ),
    },
    {
      // Not sortable in any meaningful way, so it carries no sort key that
      // would promise one: `trend` matches no field on the row, and the Table
      // sorts on `row[key]`. The column is read across, next to the open-alert
      // count it gives context to.
      key: "trend",
      header: "30 days",
      align: "left",
      width: 110,
      render: (r) => <TrendCell tenantId={r.id} />,
    },
    {
      key: "trial_days_left",
      header: "Trial",
      align: "right",
      render: (r) =>
        r.trial_days_left === null ? (
          <span className="g-led-quiet">—</span>
        ) : (
          <span className={`mono${r.trial_days_left <= 14 ? " g-led-soon" : ""}`}>
            {r.trial_days_left}d left
          </span>
        ),
    },
  ];

  const t = query.data?.totals;
  // Customers that need attention, plus people waiting on access. Tickets are
  // not counted here: the ledger payload does not carry them, and a number
  // that quietly omits a category is worse than one that does not claim it.
  const work = rows.filter(needsYou).length + (t?.open_requests ?? 0);

  return (
    <div className="g-led">
      <header className="g-led-head">
        {/* The sentence counts the work and then says what it is. A person
            opening this at 8am wants the answer before the table, and on a
            good morning the answer is that there is nothing to do -- which is
            worth saying plainly rather than showing as an empty state. */}
        {/* Not a page title -- the bar carries that now. This is the
            answer to "is anything wrong", which is why it is the biggest
            thing on the screen and why it stays. */}
        <h2 className={`g-led-headline${work === 0 ? " is-quiet" : ""}`}>
          {work === 0 ? (
            "Nothing needs you."
          ) : (
            <>
              <span className="g-led-count">{work}</span>
              {work === 1 ? " thing needs you." : " things need you."}
            </>
          )}
        </h2>
        {/* The status line, not a row of stat cards: the same shorthand the
            tenant app's header uses, so a number means the same thing on
            both surfaces. */}
        {t && (
          <p className="g-led-status" role="status">
            <span><b>{t.tenants}</b> customers</span>
            <span><b>{t.sensors_online}</b>/{t.sensors_total} sensors reporting</span>
            {t.paused > 0 && <span className="is-warn"><b>{t.paused}</b> paused</span>}
            {t.trials_ending > 0 && <span className="is-warn"><b>{t.trials_ending}</b> trials ending</span>}
            {t.open_requests > 0 && <span className="is-warn"><b>{t.open_requests}</b> requests waiting</span>}
            {t.open_crit > 0 && <span className="is-crit"><b>{t.open_crit}</b> critical</span>}
          </p>
        )}
      </header>

      <section aria-label="Customers" className="g-own-section">
        <h2>Customers <span className="g-own-meta">{rows.length} {rows.length === 1 ? "customer" : "customers"}</span></h2>
      {query.isError ? (
        <p className="g-led-empty">
          The ledger could not be loaded. Your session may have expired — reload to sign in again.
        </p>
      ) : (
        <Table
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          rowClassName={(r) => (needsYou(r) ? "needs-you" : undefined)}
          sort={sort}
          onSort={setSort}
          dense
          onRowClick={(r) => setOpen(r)}
          selected={open?.id ?? null}
          empty={query.isPending ? "Loading the ledger…" : "No customers yet. Approved access requests appear here."}
        />
      )}
      </section>

      <Drawer
        open={open !== null}
        onClose={() => setOpen(null)}
        title={open?.name ?? ""}
        subtitle={open?.slug || undefined}
      >
        {open && (
          <p className="g-own-crumb">
            {/* The drawer answers "how is this customer". Their own setup --
                networks, sensors, people, where their alerts go -- is the next
                question and a different screen. */}
            <Link to={`/owner/accounts/${open.id}`}>See this account’s own setup</Link>
          </p>
        )}
        {open && (
          <dl className="g-facts">
            {[
              ["Plan", planLabel(open)],
              ["Status", open.plan_status || "—"],
              ["Collecting data", open.ingesting ? "yes" : "no — paused"],
              ["Trial ends", open.trial_ends ? new Date(open.trial_ends).toLocaleDateString() : "—"],
              ["Sensors", `${open.sensors_online} of ${open.sensors_total} reporting`],
              ["Last scan", ago(open.last_scan)],
              ["Devices seen", String(open.devices)],
              ["People with access", String(open.users)],
              ["Open alerts", `${open.open_crit} critical, ${open.open_warn} warning`],
              ["Customer since", open.created ? new Date(open.created).toLocaleDateString() : "—"],
            ].map(([k, v]) => (
              <div key={k} className="g-facts-row">
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        )}
        {open && <Setup tid={open.id} />}
        {open && (
          <>
            {/* Point this console at them: every Operations and Inventory
                screen reads the session's network, so this is the fastest
                way from "how is this customer" to "what is on their network". */}
            <button
              type="button"
              className="g-own-btn g-led-open"
              disabled={opening !== null}
              onClick={async () => {
                setOpening(open.id);
                try {
                  await setActiveTenant(open.id);
                  qc.clear();
                  navigate("/owner");
                } finally {
                  setOpening(null);
                }
              }}
            >
              Show {open.name}'s network here
            </button>
            {/* A superadmin already holds the admin role on every tenant
                (`deps.memberships`), so this grants nothing new — it saves
                the trip through the sidebar's tenant switcher.

                It stays on THIS hostname rather than sending you to
                my.guarder.io: the session cookie is host-only (no `domain=`
                on `set_cookie`), so a cross-host link would land the owner
                signed out and asking for a code. The bundle serves the whole
                app on both names, so the customer's dashboard renders here. */}
            <button
              type="button"
              className="g-btn-primary g-led-open"
              disabled={opening !== null}
              onClick={async () => {
                setOpening(open.id);
                try {
                  await setActiveTenant(open.id);
                  // Every payload in the app is tenant-scoped, so a stale
                  // cache after a switch would show the previous customer's
                  // numbers under this one's name.
                  qc.clear();
                  navigate("/overview");
                } finally {
                  setOpening(null);
                }
              }}
            >
              {opening === open.id ? "Opening…" : `Open ${open.name}'s dashboard`}
            </button>
            <p className="g-led-open-note">
              Switches your session to this customer. Come back with the tenant switcher.
            </p>
          </>
        )}
      </Drawer>
    </div>
  );
}

export default Ledger;
