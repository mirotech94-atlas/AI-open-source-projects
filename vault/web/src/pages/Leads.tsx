import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, Drawer, Table, useToast } from "../design";
import type { Column } from "../design";
import { ApiError, get, post } from "../lib/api";
import { useMe } from "../lib/auth";

/** One row of `GET /api/v1/leads`.
 *
 *  Declared here rather than in `types.ts` on purpose: a lead is not a
 *  dashboard payload, its hub schema lives in `schemas.py` (not
 *  `schemas_dash.py`), and `scripts/check_types.py` mirrors only the latter. */
export type LeadRow = {
  id: string;
  name: string;
  email: string;
  company: string;
  network_size: string;
  note: string;
  ip: string;
  created: string;
  site_name: string;
  status: "pending" | "approved" | "rejected" | string;
  decided_at: string;
  decided_by: string;
  tenant_slug: string;
};

type LeadsOut = { leads: LeadRow[] };
type Decide = "approve" | "reject";
type ApproveForm = { tenant_name: string; slug: string; username: string; display_name: string };

const SIZE_LABEL: Record<string, string> = {
  home: "Home",
  "one-site": "One office or shop",
  "few-sites": "A handful of sites",
  msp: "Client sites (MSP)",
};

function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Same rule as the hub's `accounts.slugify`, so the dialog's default matches
 *  what the hub would derive if the field were left blank. */
export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "site";
}

function defaultsFor(lead: LeadRow): ApproveForm {
  const tenantName = lead.site_name || lead.company || `${lead.name}'s network`;
  return { tenant_name: tenantName, slug: slugify(tenantName), username: lead.email.toLowerCase(), display_name: lead.name };
}

function StatusCell({ r }: { r: LeadRow }) {
  if (r.status === "approved") {
    return (
      <span>
        <Badge tone="good">Approved</Badge>{" "}
        <span className="hint">by {r.decided_by} · {when(r.decided_at)} · </span>
        <code>{r.tenant_slug}</code>
      </span>
    );
  }
  if (r.status === "rejected") {
    return (
      <span>
        <Badge tone="plain">Rejected</Badge> <span className="hint">by {r.decided_by} · {when(r.decided_at)}</span>
      </span>
    );
  }
  return <Badge tone="warn">Pending</Badge>;
}

/** Access requests from the public site on guarder.io, newest first within
 *  pending/decided groups. Superadmin only, both here and at the hub
 *  (`require_superadmin` on `GET /api/v1/leads`) — a lead has no tenant and
 *  is the owner's sales pipeline, so a tenant admin must never see it. The
 *  gate below is only so the page says something sensible instead of
 *  rendering an error panel; the hub's 403 is the one that actually protects
 *  the rows.
 *
 *  Reachable at `/settings/leads`. `?lead=<id>&decide=approve|reject` is the
 *  link the owner gets in the notification email: it arms that lead's
 *  confirm drawer as soon as the list is in, without another click. */
export default function Leads() {
  const meQuery = useMe();
  const isSuperadmin = meQuery.data?.user.is_superadmin ?? false;
  const queryClient = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();

  const leadsQuery = useQuery({
    queryKey: ["leads"],
    queryFn: () => get<LeadsOut>("/api/v1/leads"),
    enabled: isSuperadmin,
  });

  // The confirm drawer: which lead, which decision, and the editable fields.
  const [target, setTarget] = useState<{ lead: LeadRow; decide: Decide } | null>(null);
  const [form, setForm] = useState<ApproveForm>({ tenant_name: "", slug: "", username: "", display_name: "" });

  function open(lead: LeadRow, decide: Decide) {
    setForm(defaultsFor(lead));
    setTarget({ lead, decide });
  }
  function close() {
    setTarget(null);
    // Clear only the two params this page owns — a future param riding
    // alongside `?lead=&decide=` (or one another page set) must survive.
    if (params.get("lead") || params.get("decide")) {
      const next = new URLSearchParams(params);
      next.delete("lead");
      next.delete("decide");
      setParams(next, { replace: true });
    }
  }

  // `?lead=<id>&decide=approve|reject` is the link in the owner's email: arm
  // that lead's confirm once the list is in. Only for a still-pending lead.
  const leads = leadsQuery.data?.leads ?? [];
  const armId = params.get("lead");
  const armDecide = params.get("decide");
  useEffect(() => {
    if (!armId || target !== null) return;
    const lead = leads.find((l) => l.id === armId);
    if (lead && lead.status === "pending" && (armDecide === "approve" || armDecide === "reject")) open(lead, armDecide);
  }, [armId, armDecide, leads, target]);

  const decision = useMutation({
    mutationFn: ({ lead, decide }: { lead: LeadRow; decide: Decide }) =>
      decide === "approve"
        ? post<LeadRow>(`/api/v1/leads/${encodeURIComponent(lead.id)}/approve`, form)
        : post<LeadRow>(`/api/v1/leads/${encodeURIComponent(lead.id)}/reject`, {}),
    onSuccess: async (row, { decide }) => {
      toast.show(decide === "approve" ? `Account created: ${row.tenant_slug}` : "Request rejected", "plain");
      await queryClient.invalidateQueries({ queryKey: ["leads"] });
      close();
    },
    onError: (err: Error) => {
      toast.show(err instanceof ApiError ? err.message : "That did not go through.", "alert");
      if (err instanceof ApiError && err.status === 409) {
        // Someone else (or another tab) already decided this lead -- refetch
        // so the page shows what actually happened instead of a stale row.
        queryClient.invalidateQueries({ queryKey: ["leads"] });
      }
    },
  });

  const pending = useMemo(() => leads.filter((l) => l.status === "pending").length, [leads]);

  // The filter row. `null` is everything, which is the default: a page that
  // opened on "pending" would hide the decided rows the owner comes back to
  // check ("did I approve them?"), and the pending ones already sort first.
  const [show, setShow] = useState<string | null>(null);
  const counts = useMemo(() => {
    const c: Record<string, number> = { pending: 0, approved: 0, rejected: 0 };
    for (const l of leads) c[l.status] = (c[l.status] ?? 0) + 1;
    return c;
  }, [leads]);
  const filters: { value: string | null; label: string; warn?: boolean }[] = [
    { value: null, label: `All · ${leads.length}` },
    { value: "pending", label: `Pending · ${counts.pending}`, warn: counts.pending > 0 },
    { value: "approved", label: `Approved · ${counts.approved}` },
    { value: "rejected", label: `Rejected · ${counts.rejected}` },
  ];

  const columns: Column<LeadRow>[] = [
    { key: "created", header: "When", width: 170, render: (r) => when(r.created) },
    { key: "status", header: "Status", render: (r) => <StatusCell r={r} /> },
    { key: "name", header: "Name" },
    { key: "email", header: "Email", render: (r) => <a href={`mailto:${r.email}`}>{r.email}</a> },
    { key: "site_name", header: "Site", render: (r) => r.site_name || "—" },
    { key: "company", header: "Company", render: (r) => r.company || "—" },
    { key: "network_size", header: "Looking after", render: (r) => SIZE_LABEL[r.network_size] ?? (r.network_size || "—") },
    { key: "note", header: "Note", render: (r) => <span title={r.note}>{r.note || "—"}</span> },
    { key: "ip", header: "From", width: 130, render: (r) => <code>{r.ip || "—"}</code> },
    {
      key: "id",
      header: "",
      render: (r) =>
        r.status === "pending" ? (
          <span className="g-lead-actions">
            <button type="button" className="primary" onClick={() => open(r, "approve")}>Approve</button>
            <button type="button" onClick={() => open(r, "reject")}>Reject</button>
          </span>
        ) : null,
    },
  ];

  if (meQuery.isLoading) {
    return (
      <Card title="Access requests">
        <p className="hint">Loading…</p>
      </Card>
    );
  }
  if (!isSuperadmin) {
    return (
      <Card title="Access requests">
        <p className="hint">This page is for the Vault account owner.</p>
      </Card>
    );
  }

  // Pending first, then newest first within each group.
  const rows = [...leads]
    .filter((l) => show === null || l.status === show)
    .sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending"));
  const approving = target?.decide === "approve";

  return (
    <>
      <Card
        title="Access requests"
        subtitle={leadsQuery.isLoading ? "Loading…" : "Approving creates the account and emails them a sign-in link."}
        actions={!leadsQuery.isLoading && <span className="g-own-meta">{pending} pending · {leads.length} total</span>}
      >
        {/* One text node per chip, on purpose: a chip that rendered "Pending"
            in its own element would be a third match for the status badge's
            text, and a count of pending rows is a thing tests -- and people
            scanning the page -- take from the badges. */}
        <div className="g-own-toolbar" role="group" aria-label="Show">
          {filters.map((f) => (
            <button
              key={f.label}
              type="button"
              className={`g-own-fchip${f.warn ? " is-warn" : ""}`}
              aria-pressed={show === f.value}
              onClick={() => setShow(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <Table
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          dense
          // Marked, not styled: the owner console draws the edge that says
          // this row is waiting on a decision. The tenant app leaves it plain.
          rowClassName={(r) => (r.status === "pending" ? "is-pending" : undefined)}
          empty={show === null ? "No access requests yet." : `Nothing ${show}.`}
        />
      </Card>

      <Drawer
        open={target !== null}
        onClose={close}
        title={target ? (approving ? `Approve ${target.lead.name || target.lead.email}` : `Reject ${target.lead.name || target.lead.email}`) : ""}
        subtitle={target ? target.lead.email : undefined}
      >
        {target && (
          <form
            className="g-lead-form"
            onSubmit={(e) => {
              e.preventDefault();
              decision.mutate(target);
            }}
          >
            {approving ? (
              <>
                <p className="hint">Creates a tenant on the 90-day trial with this person as its admin, then emails them a sign-in link.</p>
                <label htmlFor="lead-site">Site name</label>
                <input id="lead-site" value={form.tenant_name}
                       onChange={(e) => setForm({ ...form, tenant_name: e.target.value, slug: slugify(e.target.value) })} />
                <label htmlFor="lead-slug">Slug</label>
                <input id="lead-slug" className="mono" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} />
                <label htmlFor="lead-username">Username</label>
                <input id="lead-username" className="mono" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
                <label htmlFor="lead-display">Display name</label>
                <input id="lead-display" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
              </>
            ) : (
              <p className="hint">Marks the request rejected and sends them a short, fixed note. Nothing is created.</p>
            )}
            <div className="g-lead-form-actions">
              <button type="submit" className={approving ? "primary" : "g-danger"} disabled={decision.isPending}>
                {decision.isPending ? "Working…" : approving ? "Create account" : "Reject request"}
              </button>
              <button type="button" onClick={close}>Cancel</button>
            </div>
          </form>
        )}
      </Drawer>
    </>
  );
}
