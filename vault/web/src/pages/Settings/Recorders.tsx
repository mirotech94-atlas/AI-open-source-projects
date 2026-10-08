// Settings → Recorders: the NVRs and DVRs Vault asks for the cameras
// behind them.
//
// Why this tab exists at all: a house with ten or twelve cameras shows ONE
// camera on the LAN, because the cameras hang off the recorder's own PoE
// ports and never answer a network sweep. Registering the recorder here is
// what makes the other eleven appear — each one becomes an ordinary device on
// a network of its own, with the recorder as its gateway.
//
// Reads are viewer-level; every write is admin-only on the hub, so every
// control is disabled below admin with the same visible `RoleNotice` the rest
// of Settings uses — nothing is hidden.
//
// The rule this file exists to hold on the browser side: **the password goes
// up and never comes down.** A row carries `has_credentials`, never the value,
// so the password field always starts empty and an empty field on save means
// "leave the stored one alone" — never "clear it". Clearing is its own
// deliberate button, because those two intentions look identical in a form and
// silently wiping a credential would leave the recorder unreadable with no
// explanation.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Table, useToast } from "../../design";
import type { Column } from "../../design";
import { createRecorder, deleteRecorder, recordersPayload, updateRecorder } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import type { RecorderRow } from "../../types";
import { ADMIN_TOOLTIP, ConfirmDialog, RoleNotice } from "./shared";

export type RecordersProps = { role: Role | null };

const IPV4_RE = /^(\d{1,3}\.){3}\d{1,3}$/;

type Draft = {
  name: string;
  vendor: string;
  host: string;
  port: string;
  scheme: string;
  username: string;
  password: string;
  expected_channels: string;
};

const BLANK: Draft = {
  name: "",
  vendor: "reolink",
  host: "",
  port: "80",
  scheme: "http",
  username: "",
  password: "",
  expected_channels: "",
};

function draftOf(rec: RecorderRow): Draft {
  return {
    name: rec.name,
    vendor: rec.vendor,
    host: rec.host,
    port: String(rec.port),
    scheme: rec.scheme,
    username: rec.username,
    // Never pre-filled: the hub does not return it, and a masked placeholder
    // in a real password input is how a "save" ends up posting asterisks.
    password: "",
    expected_channels: rec.expected_channels === 0 ? "" : String(rec.expected_channels),
  };
}

/** The same checks the hub runs, run early so a typo is caught inline instead
 *  of coming back as a 422 detail string. Never a *replacement* for the hub's
 *  validation — just the fast half of it. */
function validate(draft: Draft): string | null {
  if (draft.host.trim() === "") return "A recorder needs an address on your network.";
  if (!IPV4_RE.test(draft.host.trim())) return "The address looks like 192.168.1.182 — an IPv4 address on your own network.";
  const port = Number(draft.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return "The port has to be between 1 and 65535.";
  if (draft.expected_channels !== "") {
    const n = Number(draft.expected_channels);
    if (!Number.isInteger(n) || n < 0 || n > 256) return "Expected cameras has to be a whole number, 0–256.";
  }
  return null;
}

function statusLabel(rec: RecorderRow): string {
  if (rec.status === "ok") return `${rec.channels_online}/${rec.channel_count} cameras answering`;
  if (rec.status === "pending") return "not read yet";
  return "cannot be read";
}

export function Recorders({ role }: RecordersProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const canWrite = atLeast(role, "admin");

  const query = useQuery({ queryKey: ["recorders"], queryFn: recordersPayload });
  const recorders = query.data?.recorders ?? [];
  const vendors = query.data?.vendors ?? [];

  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<RecorderRow | null>(null);

  async function cancel() {
    await queryClient.cancelQueries({ queryKey: ["recorders"] });
  }

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["recorders"], refetchType: "all" });
    // Registering a recorder creates a network and (on the next scan) a dozen
    // devices, so every count on every other scoped tab moves.
    void queryClient.invalidateQueries({ queryKey: ["networks"] });
    void queryClient.invalidateQueries({ queryKey: ["devices"] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
  }

  function closeForm() {
    setAdding(false);
    setEditing(null);
    setDraft(BLANK);
    setError(null);
  }

  function bodyOf(d: Draft, isEdit: boolean): Record<string, unknown> {
    const body: Record<string, unknown> = {
      name: d.name.trim(),
      vendor: d.vendor,
      host: d.host.trim(),
      port: Number(d.port),
      scheme: d.scheme,
      username: d.username.trim(),
      expected_channels: d.expected_channels === "" ? 0 : Number(d.expected_channels),
    };
    // An empty password on an EDIT means "leave the stored one alone", so the
    // field is omitted entirely rather than posted as "".
    if (!isEdit || d.password !== "") body.password = d.password;
    return body;
  }

  const createMutation = useMutation({
    mutationFn: (d: Draft) => createRecorder(bodyOf(d, false)),
    onMutate: cancel,
    onSuccess: async () => {
      closeForm();
      await refresh();
      toast.show("Recorder added. Vault reads it on the next scan.", "good");
    },
    onError: (err: Error) => setError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, d }: { id: string; d: Draft }) => updateRecorder(id, bodyOf(d, true)),
    onMutate: cancel,
    onSuccess: async () => {
      closeForm();
      await refresh();
      toast.show("Recorder saved.", "good");
    },
    onError: (err: Error) => setError(err.message),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updateRecorder(id, { enabled }),
    onMutate: cancel,
    onSuccess: refresh,
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const clearCredsMutation = useMutation({
    mutationFn: (id: string) => updateRecorder(id, { password: "" }),
    onMutate: cancel,
    onSuccess: async () => {
      await refresh();
      toast.show("Stored password cleared.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteRecorder(id),
    onMutate: cancel,
    onSuccess: async () => {
      setDeleting(null);
      await refresh();
      toast.show("Recorder removed. Its cameras stay in your history.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const problem = validate(draft);
    if (problem !== null) {
      setError(problem);
      return;
    }
    setError(null);
    if (editing !== null) updateMutation.mutate({ id: editing, d: draft });
    else createMutation.mutate(draft);
  }

  const columns: Column<RecorderRow>[] = [
    {
      key: "name",
      header: "Recorder",
      render: (r) => (
        <span className="g-network-name">
          {r.name}
          {!r.supported && (
            <span className="g-network-section-counts"> {r.vendor_label} — no adapter yet</span>
          )}
        </span>
      ),
    },
    { key: "host", header: "Address", render: (r) => `${r.host}:${r.port}` },
    { key: "vendor_label", header: "Make" },
    {
      key: "channel_count",
      header: "Cameras",
      align: "right",
      render: (r) => (r.status === "ok" ? `${r.channels_online}/${r.channel_count}` : "—"),
    },
    {
      key: "status",
      header: "Status",
      render: (r) => (
        <span className="g-network-status" data-testid={`status-${r.id}`}>
          {statusLabel(r)}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (r) => (
        <span className="g-network-row-actions">
          <button
            type="button"
            data-testid={`edit-${r.id}`}
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onClick={() => {
              setAdding(false);
              setEditing(r.id);
              setDraft(draftOf(r));
              setError(null);
            }}
          >
            Edit
          </button>
          <button
            type="button"
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onClick={() => toggleMutation.mutate({ id: r.id, enabled: !r.enabled })}
          >
            {r.enabled ? "Pause" : "Resume"}
          </button>
          {r.has_credentials && (
            <button
              type="button"
              data-testid={`clear-creds-${r.id}`}
              disabled={!canWrite}
              title={canWrite ? undefined : ADMIN_TOOLTIP}
              onClick={() => clearCredsMutation.mutate(r.id)}
            >
              Clear password
            </button>
          )}
          <button
            type="button"
            className="g-danger"
            data-testid={`delete-${r.id}`}
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onClick={() => setDeleting(r)}
          >
            Remove
          </button>
        </span>
      ),
    },
  ];

  const problems = recorders.filter((r) => r.status === "error" && r.problem !== "");

  return (
    <div className="g-settings-networks">
      {!canWrite && <RoleNotice role={role} required="admin" />}

      <p className="hint">
        Cameras plugged into an NVR or DVR sit on the recorder’s own network, behind it — a scan of
        your Wi-Fi only ever finds the recorder itself. Register the recorder here and Vault asks
        it which cameras are behind it, then watches each one like any other device. Vault reads
        recorders over their documented HTTP interface, so that interface has to be switched on, and
        a view-only account is all it needs.
      </p>

      {/* The diagnosis is on the page, not only inside an alert. An owner who
          registered his recorder and saw nothing appear has to be told why,
          right here, with the setting named — a silent zero is the wrong
          answer for a house with twelve cameras. */}
      {problems.map((r) => (
        <div className="g-settings-role-notice" key={r.id} data-testid={`problem-${r.id}`}>
          <strong>{r.name}: </strong>
          {r.problem}
          {r.owner_action !== "" && (
            <>
              {" "}
              <em>What to do: {r.owner_action}</em>
            </>
          )}
        </div>
      ))}

      {query.isLoading ? (
        <p className="hint">Loading recorders…</p>
      ) : (
        <Table
          columns={columns}
          rows={recorders}
          rowKey={(r) => r.id}
          empty="No recorders yet. If your cameras are plugged into an NVR or DVR, add it here."
        />
      )}

      {(adding || editing !== null) && (
        <form className="g-network-form" onSubmit={submit}>
          <label>
            Name
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="Garage NVR"
            />
          </label>
          <label>
            Make
            <select value={draft.vendor} onChange={(e) => setDraft({ ...draft, vendor: e.target.value })}>
              {vendors.map((v) => (
                <option key={v.key} value={v.key}>
                  {v.label}
                  {v.supported ? "" : " (no adapter yet)"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Address on your network
            <input
              value={draft.host}
              onChange={(e) => setDraft({ ...draft, host: e.target.value })}
              placeholder="192.168.1.182"
            />
          </label>
          <label>
            Scheme
            <select value={draft.scheme} onChange={(e) => setDraft({ ...draft, scheme: e.target.value })}>
              <option value="http">http</option>
              <option value="https">https</option>
            </select>
          </label>
          <label>
            Port
            <input value={draft.port} onChange={(e) => setDraft({ ...draft, port: e.target.value })} />
          </label>
          <label>
            Username
            <input
              value={draft.username}
              onChange={(e) => setDraft({ ...draft, username: e.target.value })}
              autoComplete="off"
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={draft.password}
              onChange={(e) => setDraft({ ...draft, password: e.target.value })}
              autoComplete="new-password"
              placeholder={editing !== null ? "leave blank to keep the stored one" : ""}
            />
          </label>
          <label>
            Cameras you expect (optional)
            <input
              value={draft.expected_channels}
              onChange={(e) => setDraft({ ...draft, expected_channels: e.target.value })}
              placeholder="12"
            />
          </label>
          <p className="hint">
            Vault tells you if the recorder ever lists a different number than this.
          </p>
          {error !== null && <p className="g-settings-role-notice">{error}</p>}
          <div className="g-settings-sensors-actions">
            <button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
              {editing !== null ? "Save recorder" : "Add recorder"}
            </button>
            <button type="button" onClick={closeForm}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {!adding && editing === null && (
        <div className="g-settings-sensors-actions">
        <button
          type="button"
          data-testid="add-recorder"
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onClick={() => {
            setAdding(true);
            setDraft(BLANK);
            setError(null);
          }}
        >
          Add a recorder
        </button>
        </div>
      )}

      <ConfirmDialog
        open={deleting !== null}
        title="Remove this recorder?"
        body={
          <>
            Vault stops asking {deleting?.name ?? "it"} which cameras are behind it. The cameras
            already found stay in your history — removing them is a separate, deliberate step under
            Networks.
          </>
        }
        confirmLabel="Remove recorder"
        onConfirm={() => deleting !== null && deleteMutation.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

export default Recorders;
