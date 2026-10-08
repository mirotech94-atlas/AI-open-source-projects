// Settings → Networks: the table of every network on this tenant, plus the
// add / edit / delete flows.
//
// Reads are viewer-level (`GET /api/v1/networks`); every write is admin-only
// on the hub, so every control here is disabled below admin with the same
// visible `RoleNotice` the rest of Settings uses — nothing is hidden.
//
// Two hub rules the UI has to make legible rather than discover through a 422:
//  * the primary network cannot be deleted (task 51 §4), so its Delete is
//    disabled with the reason on the button;
//  * a delete that would strand devices needs somewhere to move them, so
//    Delete always asks which network to move them to (`?reassign=`).
//
// `slug` and `is_primary` are read-only on the hub — a rename changes `name`
// only, because the slug is baked into every `lan:<slug>:…` alert key that
// network has ever opened.

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Table, useToast } from "../../design";
import type { Column } from "../../design";
import { generateEnrolCode } from "../../api";
import { createNetwork, deleteNetwork, networksPayload, updateNetwork } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import { installCommand, PLATFORM_SERVICE, PLATFORMS } from "../../lib/install";
import type { Platform } from "../../lib/install";
import { NeedsSensorPill } from "../../components/NetworkBadge";
import type { Network, SettingsPayload } from "../../types";
import { ADMIN_TOOLTIP, RoleNotice } from "./shared";

export type NetworksProps = {
  settings: SettingsPayload;
  role: Role | null;
};

// The hub's own `networks.KINDS` (task 51 §3). `home` is in the set because
// the 0007 migration labels every pre-existing primary that way and it must
// stay PATCH-able.
const KINDS = ["wifi", "wired", "lab", "vlan", "guest", "home"] as const;

/** `networks.SUBNET_RE` on the hub: three dotted octets, no host part. */
const SUBNET_RE = /^(\d{1,3}\.){2}\d{1,3}$/;

type Draft = {
  name: string;
  kind: string;
  subnet_prefix: string;
  expected_gateway: string;
  ssid: string;
  colour: string;
};

const BLANK: Draft = { name: "", kind: "wifi", subnet_prefix: "", expected_gateway: "", ssid: "", colour: "" };

function draftOf(network: Network): Draft {
  return {
    name: network.name,
    kind: network.kind,
    subnet_prefix: network.subnet_prefix,
    expected_gateway: network.expected_gateway,
    ssid: network.ssid,
    colour: network.colour,
  };
}

/** The same checks the hub runs, run early so a typo is caught inline instead
 *  of coming back as a 422 detail string. Never a *replacement* for the hub's
 *  validation — just the fast half of it. */
function validate(draft: Draft): string | null {
  if (draft.name.trim() === "") return "A network needs a name.";
  if (!(KINDS as readonly string[]).includes(draft.kind)) return "Pick a network kind.";
  if (draft.subnet_prefix !== "" && !SUBNET_RE.test(draft.subnet_prefix)) {
    return "Subnet looks like 192.168.1 — three octets, no host part.";
  }
  if (
    draft.expected_gateway !== "" &&
    draft.subnet_prefix !== "" &&
    !draft.expected_gateway.startsWith(`${draft.subnet_prefix}.`)
  ) {
    return `The gateway has to be inside ${draft.subnet_prefix}.`;
  }
  return null;
}

export function Networks({ settings, role }: NetworksProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const canWrite = atLeast(role, "admin");

  const query = useQuery({ queryKey: ["networks"], queryFn: networksPayload });
  const networks = query.data?.networks ?? [];

  const [adding, setAdding] = useState(false);
  // `?add=1` opens the form straight away. The Overview's Networks card links
  // here with it, so "add a network" is one click from where the networks are
  // being read rather than a hunt through Settings.
  //
  // Armed once: the ref is what stops the form reopening every render, and
  // stops it fighting the user if they close it while the parameter is still
  // in the URL. The parameter is stripped as soon as it is honoured, so a
  // refresh or a back-navigation does not re-open it either.
  const [params, setParams] = useSearchParams();
  const armed = useRef(false);
  useEffect(() => {
    if (armed.current || params.get("add") !== "1") return;
    armed.current = true;
    if (!canWrite) return;          // a viewer following the link gets the list, not a form
    setAdding(true);
    const next = new URLSearchParams(params);
    next.delete("add");
    setParams(next, { replace: true });
  }, [params, setParams, canWrite]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Network | null>(null);
  const [reassignTo, setReassignTo] = useState("");
  const [enrolFor, setEnrolFor] = useState<{ network: Network; code: string } | null>(null);
  const [platform, setPlatform] = useState<Platform>("macos");
  const [copied, setCopied] = useState(false);

  // Cancel the page's own in-flight read before a write, then refetch after it
  // lands — the pattern every other page here uses, so an `invalidateQueries`
  // can't dedupe onto a fetch that started before the write.
  async function cancel() {
    await queryClient.cancelQueries({ queryKey: ["networks"] });
  }

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["networks"], refetchType: "all" });
    // A network change moves device counts and the health strip on every other
    // scoped tab too.
    void queryClient.invalidateQueries({ queryKey: ["devices"] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
  }

  function closeForm() {
    setAdding(false);
    setEditing(null);
    setDraft(BLANK);
    setError(null);
  }

  const createMutation = useMutation({
    mutationFn: (body: Draft) => createNetwork({ ...body, name: body.name.trim() }),
    onMutate: cancel,
    onSuccess: async () => {
      closeForm();
      await refresh();
      toast.show("Network added.", "good");
    },
    onError: (err: Error) => setError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Draft }) =>
      updateNetwork(id, { ...body, name: body.name.trim() }),
    onMutate: cancel,
    onSuccess: async () => {
      closeForm();
      await refresh();
      toast.show("Network saved.", "good");
    },
    onError: (err: Error) => setError(err.message),
  });

  const sensorMutation = useMutation({
    mutationFn: ({ id, sensorId }: { id: string; sensorId: string }) =>
      updateNetwork(id, { sensor_id: sensorId === "" ? null : sensorId }),
    onMutate: cancel,
    onSuccess: async () => {
      await refresh();
      toast.show("Sensor assigned.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const trafficMutation = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: string }) =>
      updateNetwork(id, { traffic_mode: mode }),
    onMutate: cancel,
    onSuccess: async (_out, { mode }) => {
      await refresh();
      toast.show(
        mode === "none"
          ? "Vault will stop watching traffic here."
          : "Vault will watch traffic here once the sensor has a capture interface.",
        mode === "none" ? "plain" : "good",
      );
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const deleteMutation = useMutation({
    mutationFn: ({ id, reassign }: { id: string; reassign: string }) =>
      deleteNetwork(id, reassign === "" ? null : reassign),
    onMutate: cancel,
    onSuccess: async () => {
      setDeleting(null);
      setReassignTo("");
      await refresh();
      toast.show("Network deleted.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const enrolMutation = useMutation({
    mutationFn: generateEnrolCode,
    onSuccess: (result, network: Network) => {
      setEnrolFor({ network, code: result.code });
      setCopied(false);
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
    if (editing !== null) updateMutation.mutate({ id: editing, body: draft });
    else createMutation.mutate(draft);
  }

  async function copyCommand(command: string) {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be denied; the command stays visible as text.
    }
  }

  const columns: Column<Network>[] = [
    {
      key: "name",
      header: "Name",
      render: (n) => (
        <span className="g-network-name">
          {n.name}
          {n.is_primary && <span className="g-network-section-counts"> primary</span>}
        </span>
      ),
    },
    { key: "kind", header: "Kind" },
    { key: "subnet_prefix", header: "Subnet", render: (n) => n.subnet_prefix || "—" },
    { key: "expected_gateway", header: "Gateway", render: (n) => n.expected_gateway || "—" },
    {
      key: "sensor_id",
      header: "Sensor",
      render: (n) => (
        <label className="g-network-sensor">
          <select
            aria-label={`Sensor for ${n.name}`}
            value={n.sensor_id ?? ""}
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onChange={(e) => sensorMutation.mutate({ id: n.id, sensorId: e.target.value })}
          >
            <option value="">No sensor</option>
            {settings.sensors.map((sensor) => (
              <option key={sensor.id} value={sensor.id}>
                {sensor.name}
              </option>
            ))}
          </select>
        </label>
      ),
    },
    {
      key: "traffic_mode",
      header: "Traffic",
      // Item 20, on Miro's own ruling: traffic capture is OPT-IN PER
      // NETWORK, never attached to every network Vault happens to find.
      // The control lives here rather than on a global settings page for
      // exactly that reason — you are turning it on for one named network
      // you own, and the row you are turning it on for is in front of you.
      render: (n) => (
        <label className="g-network-sensor">
          <select
            aria-label={`Watch traffic on ${n.name}`}
            value={n.traffic_mode ?? "none"}
            disabled={!canWrite}
            title={canWrite
              ? "Vault reads packet headers on the interface the sensor is given — never message bodies — and only on the networks you switch on here."
              : ADMIN_TOOLTIP}
            onChange={(e) => trafficMutation.mutate({ id: n.id, mode: e.target.value })}
          >
            {/* Short labels: this table already overflows its column at
                nine fields, and the full sentence lives in the title. */}
            <option value="none">Not watched</option>
            <option value="mirror">Mirror port</option>
            <option value="router">Router counts</option>
            <option value="host">This sensor only</option>
          </select>
        </label>
      ),
    },
    { key: "devices", header: "Devices", align: "right", render: (n) => `${n.online}/${n.devices}` },
    {
      key: "status",
      header: "Status",
      render: (n) => (
        <span className="g-network-status">
          {n.status}
          <NeedsSensorPill status={n.status} />
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (n) => (
        <span className="g-network-row-actions">
          <button
            type="button"
            data-testid={`edit-${n.slug}`}
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onClick={() => {
              setAdding(false);
              setEditing(n.id);
              setDraft(draftOf(n));
              setError(null);
            }}
          >
            Edit
          </button>
          <button
            type="button"
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onClick={() => enrolMutation.mutate(n)}
          >
            Copy enrol command
          </button>
          {/* The primary network is undeletable on the hub. The reason is
              VISIBLE text beside the disabled button, not a tooltip: a
              disabled control with a hover-only explanation says nothing at
              all on a touch screen or to a keyboard user, which is exactly
              where "why can't I delete this?" gets asked. The `title` stays
              as well, for the mouse. */}
          {n.is_primary && (
            <span className="g-network-primary-note" data-testid={`delete-note-${n.slug}`}>
              Primary network — it can’t be deleted. Make another network primary first, or edit this
              one instead.
            </span>
          )}
          <button
            type="button"
            className="g-danger"
            data-testid={`delete-${n.slug}`}
            disabled={!canWrite || n.is_primary}
            title={
              n.is_primary
                ? "The primary network can't be deleted."
                : canWrite
                  ? undefined
                  : ADMIN_TOOLTIP
            }
            onClick={() => {
              setDeleting(n);
              setReassignTo(networks.find((other) => other.id !== n.id)?.id ?? "");
            }}
          >
            Delete
          </button>
        </span>
      ),
    },
  ];

  const command = enrolFor === null ? "" : installCommand(enrolFor.code, window.location.origin);

  return (
    <div className="g-settings-networks">
      {!canWrite && <RoleNotice role={role} required="admin" />}

      <p className="hint">
        Every network needs a sensor that can reach it — a sensor only ever sees the network it is
        installed on.
      </p>

      {query.isLoading ? (
        <p className="hint">Loading networks…</p>
      ) : (
        <Table
          columns={columns}
          rows={networks}
          rowKey={(n) => n.id}
          empty="No networks yet. Add the first one below."
        />
      )}

      <div className="g-settings-sensors-actions">
        <button
          type="button"
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onClick={() => {
            setEditing(null);
            setDraft(BLANK);
            setError(null);
            setAdding(true);
          }}
        >
          Add network
        </button>
      </div>

      {(adding || editing !== null) && (
        <form className="g-network-form" onSubmit={submit}>
          <label>
            <span>Name</span>
            <input
              value={draft.name}
              autoFocus
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            />
          </label>
          <label>
            <span>Kind</span>
            <select value={draft.kind} onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value }))}>
              {KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {kind}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Subnet</span>
            <input
              value={draft.subnet_prefix}
              placeholder="192.168.1"
              onChange={(e) => setDraft((d) => ({ ...d, subnet_prefix: e.target.value }))}
            />
          </label>
          <label>
            <span>Gateway</span>
            <input
              value={draft.expected_gateway}
              placeholder="192.168.1.1"
              onChange={(e) => setDraft((d) => ({ ...d, expected_gateway: e.target.value }))}
            />
          </label>
          <label>
            <span>SSID</span>
            <input
              value={draft.ssid}
              onChange={(e) => setDraft((d) => ({ ...d, ssid: e.target.value }))}
            />
          </label>
          <div className="g-network-form-actions">
            <button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
              Save
            </button>
            <button type="button" onClick={closeForm}>
              Cancel
            </button>
          </div>
          {error !== null && (
            <p className="g-field-error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}

      {deleting !== null && (
        <div className="g-network-form" role="group" aria-label={`Delete ${deleting.name}`}>
          <p>
            Deleting <strong>{deleting.name}</strong>. Its devices, alerts and history have to go
            somewhere.
          </p>
          <label>
            <span>Move its devices to</span>
            <select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)}>
              {networks
                .filter((n) => n.id !== deleting.id)
                .map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
            </select>
          </label>
          <div className="g-network-form-actions">
            <button
              type="button"
              className="g-danger"
              disabled={reassignTo === "" || deleteMutation.isPending}
              onClick={() => deleteMutation.mutate({ id: deleting.id, reassign: reassignTo })}
            >
              Delete network
            </button>
            <button
              type="button"
              onClick={() => {
                setDeleting(null);
                setReassignTo("");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {enrolFor !== null && (
        <div className="g-network-enrol">
          <h3>Install a sensor for {enrolFor.network.name}</h3>
          {/* The hub's `POST /api/v1/sensors/enrol-code` takes no network, so
              the code itself is not bound to one: run it on a host that sits
              on this network, then pick the sensor in this network's row
              above. That assignment is what binds the two. */}
          <div className="g-onboarding-os-picker" role="group" aria-label="Sensor platform">
            {PLATFORMS.map((p) => (
              <button
                key={p.value}
                type="button"
                aria-pressed={platform === p.value}
                onClick={() => setPlatform(p.value)}
              >
                {p.label}
              </button>
            ))}
          </div>
          <code>{command}</code>
          <p className="hint">
            Run this on a host that can see {enrolFor.network.subnet_prefix || "this network"}, then
            pick it as {enrolFor.network.name}&apos;s sensor above. The installer sets up{" "}
            {PLATFORM_SERVICE[platform]}.
          </p>
          <div className="g-network-form-actions">
            <button type="button" onClick={() => copyCommand(command)}>
              {copied ? "Copied" : "Copy command"}
            </button>
            <button type="button" onClick={() => setEnrolFor(null)}>
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default Networks;
