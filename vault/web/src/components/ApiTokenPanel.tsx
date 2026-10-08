import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Card, Table, useToast } from "../design";
import type { Column } from "../design";
import { del, get, post } from "../lib/api";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { ago } from "../lib/format";
import { ADMIN_TOOLTIP, ConfirmDialog, RoleNotice } from "../pages/Settings/shared";

/** One row of `GET /api/v1/settings/api-tokens`.
 *
 *  Declared here rather than in `types.ts` on purpose, exactly as `Leads.tsx`
 *  does: an API token is not a dashboard payload, its hub schema lives in
 *  `schemas_api.py` (not `schemas_dash.py`), and `scripts/check_types.py`
 *  mirrors only the latter. Adding it to `types.ts` would fail that check, not
 *  satisfy it. */
export type ApiTokenRow = {
  id: string;
  name: string;
  prefix: string;
  scope: string;
  role: string;
  created_by: string;
  created: string | null;
  expires: string | null;
  last_used: string | null;
  last_used_ip: string;
  revoked: boolean;
};

export type ApiTokensOut = {
  tokens: ApiTokenRow[];
  /** The highest role this admin may hand to a new token. The hub clamps to it
   *  regardless; the form uses it to avoid offering a choice that would be
   *  silently lowered. */
  max_role: string;
  /** Where the API answers, e.g. `https://api.guarder.io`. Empty on a hub that
   *  has no public API hostname (a local dev box), in which case the links to
   *  the reference are left out rather than pointed somewhere dead. */
  base_url: string;
};

type Created = { token: ApiTokenRow; secret: string };

const ROLE_ORDER = ["viewer", "operator", "admin"] as const;

const LIFETIMES: { label: string; days: number | null }[] = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
  { label: "2 years", days: 730 },
  { label: "Never expires", days: null },
];

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function state(row: ApiTokenRow): { label: string; tone: "good" | "warn" | "alert" } {
  if (row.revoked) return { label: "Revoked", tone: "alert" };
  if (row.expires && new Date(row.expires).getTime() < Date.now()) {
    return { label: "Expired", tone: "warn" };
  }
  return { label: "Active", tone: "good" };
}

/** The one query for the token list.
 *
 *  Shared so the two entrances -- the Settings page and the Integrations tab's
 *  quiet section -- are one fetch and one cache under `["api-tokens"]`, not two
 *  that could disagree about how many keys exist. */
export function useApiTokens(enabled: boolean) {
  return useQuery({
    queryKey: ["api-tokens"],
    queryFn: () => get<ApiTokensOut>("/api/v1/settings/api-tokens"),
    enabled,
  });
}

/** How many of these tokens still open the door.
 *
 *  Revoked and expired rows stay in the table -- an admin matching a line in a
 *  log back to a name needs them -- but counting them would tell somebody they
 *  have access they do not have, which is the wrong direction for a number
 *  sitting next to the word "Active". */
export function liveTokenCount(tokens: ApiTokenRow[]): number {
  return tokens.filter((t) => state(t).label === "Active").length;
}

/** Create, list and revoke the credentials a customer's own code uses against
 *  the public API.
 *
 *  Admin only, here and at the hub. The gate below only makes the panel say
 *  something sensible instead of rendering an error panel; the hub's 403 is what
 *  actually protects the rows.
 *
 *  `chrome` is the only difference between the two places this appears. The
 *  page wraps each half in its own `Card`; the Integrations section is already
 *  inside a `<section>` with its own heading, so a second frame there would be
 *  a box inside a box. Everything that matters -- the show-once secret, the
 *  role clamp, the revoke confirmation -- is shared, because a second copy of a
 *  credential-minting flow is a second place to get those wrong. */
export function ApiTokenPanel({ chrome = "cards" }: { chrome?: "cards" | "bare" }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canWrite = atLeast(role, "admin");

  const tokensQuery = useApiTokens(canWrite);

  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [newRole, setNewRole] = useState("viewer");
  const [days, setDays] = useState<number | null>(365);
  // The secret, held in component state for exactly as long as this panel is
  // open. It is never written to storage of any kind: the hub keeps only a hash,
  // so this is the one and only copy, and a copy left in localStorage would
  // outlive the reason for having it.
  const [created, setCreated] = useState<Created | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<ApiTokenRow | null>(null);

  const createMutation = useMutation({
    mutationFn: () =>
      post<Created>("/api/v1/settings/api-tokens", { name, scope, role: newRole, days }),
    onSuccess: (result) => {
      setCreated(result);
      setCopied(false);
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => del(`/api/v1/settings/api-tokens/${encodeURIComponent(id)}`),
    onSuccess: () => {
      setRevokeTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
      toast.show("Token revoked. Anything using it stops working immediately.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  async function copySecret() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.secret);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be denied; the secret stays selectable as text.
    }
  }

  if (meQuery.isLoading) {
    return chrome === "bare" ? (
      <p className="hint">Loading…</p>
    ) : (
      <Card title="API tokens">
        <p className="hint">Loading…</p>
      </Card>
    );
  }

  if (!canWrite) {
    return chrome === "bare" ? (
      <RoleNotice role={role} required="admin" />
    ) : (
      <Card title="API tokens" subtitle="Credentials for the Vault API.">
        <RoleNotice role={role} required="admin" />
      </Card>
    );
  }

  const data = tokensQuery.data;
  const tokens = data?.tokens ?? [];
  const baseUrl = data?.base_url ?? "";
  const maxRole = data?.max_role ?? "viewer";
  // The hub clamps a token's role to the creator's own regardless of what is
  // asked for, so offering a higher one would only produce a silent surprise.
  // An unrecognised `max_role` (or the moment before the payload lands) yields
  // viewer alone — the safe end of the ladder, never the permissive one.
  const maxIndex = ROLE_ORDER.findIndex((r) => r === maxRole);
  const allowedRoles = ROLE_ORDER.slice(0, maxIndex < 0 ? 1 : maxIndex + 1);
  const live = liveTokenCount(tokens);

  const columns: Column<ApiTokenRow>[] = [
    { key: "name", header: "Name" },
    {
      key: "prefix",
      header: "Token",
      width: 150,
      // The prefix is the whole point of storing one: it is what an admin
      // matches a line in someone's log back to a row here.
      render: (r) => <code>{r.prefix}…</code>,
    },
    {
      key: "scope",
      header: "Access",
      width: 150,
      render: (r) => (
        <span>
          {r.scope === "write" ? "Read and write" : "Read only"} · {r.role}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: 100,
      render: (r) => {
        const s = state(r);
        return <Badge tone={s.tone}>{s.label}</Badge>;
      },
    },
    {
      key: "last_used",
      header: "Last used",
      width: 150,
      render: (r) =>
        r.last_used ? (
          <span title={r.last_used_ip ? `from ${r.last_used_ip}` : undefined}>
            {ago(r.last_used)}
          </span>
        ) : (
          "Never"
        ),
    },
    { key: "expires", header: "Expires", width: 120, render: (r) => when(r.expires) },
    { key: "created_by", header: "Created by", width: 130 },
    {
      key: "revoke",
      header: "",
      align: "right",
      render: (r) =>
        r.revoked ? null : (
          <button
            type="button"
            className="g-danger"
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onClick={() => setRevokeTarget(r)}
          >
            Revoke
          </button>
        ),
    },
  ];

  const form = (
    <>
      <form
        className="g-api-tokens-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) createMutation.mutate();
        }}
      >
        <label>
          <span>Name</span>
          <input
            type="text"
            value={name}
            maxLength={64}
            placeholder="What will use this token?"
            onChange={(e) => setName(e.target.value)}
          />
        </label>

        <label>
          <span>Access</span>
          <select value={scope} onChange={(e) => setScope(e.target.value as "read" | "write")}>
            <option value="read">Read only</option>
            <option value="write">Read and write</option>
          </select>
        </label>

        <label>
          <span>Role</span>
          <select value={newRole} onChange={(e) => setNewRole(e.target.value)}>
            {allowedRoles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>Lifetime</span>
          <select
            value={days === null ? "never" : String(days)}
            onChange={(e) => setDays(e.target.value === "never" ? null : Number(e.target.value))}
          >
            {LIFETIMES.map((l) => (
              <option key={l.label} value={l.days === null ? "never" : String(l.days)}>
                {l.label}
              </option>
            ))}
          </select>
        </label>

        <button type="submit" disabled={!name.trim() || createMutation.isPending}>
          Create token
        </button>
      </form>

      <p className="hint">
        A write token can acknowledge, snooze and resolve alerts, label and trust devices, and ask
        for a scan. It can never reboot a server, run a repair, change your DNS lists or touch your
        account settings — those stay here, with a person.
        {baseUrl ? (
          <>
            {" "}
            <a href={`${baseUrl}/v1/docs`} target="_blank" rel="noreferrer">
              Read the API reference
            </a>
            .
          </>
        ) : null}
      </p>

      {created && (
        <div className="g-api-tokens-secret" role="status">
          <p>
            <strong>Copy this now.</strong> Vault stores only a hash of it, so this is the one
            time it can be shown. If you lose it, revoke the token and create another.
          </p>
          <code>{created.secret}</code>
          <div className="g-api-tokens-secret-actions">
            <button type="button" onClick={copySecret}>
              {copied ? "Copied" : "Copy token"}
            </button>
            <button type="button" onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
          <p className="hint">
            Send it as a header: <code>Authorization: Bearer {created.token.prefix}…</code>
          </p>
        </div>
      )}
    </>
  );

  const table = (
    <Table
      columns={columns}
      rows={tokens}
      rowKey={(r) => r.id}
      dense
      empty={tokensQuery.isLoading ? "Loading…" : "No API tokens yet. Create one above."}
    />
  );

  const dialog = (
    <ConfirmDialog
      open={revokeTarget !== null}
      title="Revoke this token?"
      body={`Anything using ${revokeTarget?.name ?? "this token"} stops working immediately. This cannot be undone — create a new token instead.`}
      confirmLabel="Revoke"
      onCancel={() => setRevokeTarget(null)}
      onConfirm={() => {
        if (revokeTarget) revokeMutation.mutate(revokeTarget.id);
      }}
    />
  );

  if (chrome === "bare") {
    return (
      <div className="g-api-tokens g-api-tokens-bare">
        {form}
        {table}
        {dialog}
      </div>
    );
  }

  return (
    <div className="g-api-tokens">
      <Card
        title="API tokens"
        subtitle={
          baseUrl
            ? `Credentials your own code uses against ${baseUrl}. ${live} active.`
            : `Credentials your own code uses against the Vault API. ${live} active.`
        }
      >
        {form}
      </Card>

      <Card title="Existing tokens">{table}</Card>

      {dialog}
    </div>
  );
}

export default ApiTokenPanel;
