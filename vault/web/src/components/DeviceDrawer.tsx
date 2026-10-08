import {DeviceImportance} from "./DeviceImportance";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Drawer, Table, Tabs, Timeline, useToast } from "../design";
import type { Column } from "../design";
import { NetworkBadge } from "./NetworkBadge";
import { TrafficBlock } from "./TrafficBlock";
import { atLeast, roleOf, useMe } from "../lib/auth";
import type { Ack } from "../lib/api";
import { deviceDetail, deviceTraffic, forgetDevice, updateDevice } from "../lib/api";
import { ago } from "../lib/format";
import type { DeviceCard, DeviceDetail as DeviceDetailPayload, DevicesPayload, DeviceTraffic as DeviceTrafficPayload, PortRow } from "../types";

export type DeviceDrawerProps = {
  /** `null` closes the drawer. */
  mac: string | null;
  /** Every device the Devices sub-view already has loaded — the merge
   *  picker's candidate list. Never re-fetched just for the drawer. */
  devices: DeviceCard[];
  /** The selected network id, or `null` for "All networks". Scopes both the
   *  detail read and every write to one network's row for this MAC — without
   *  it the hub acts on the newest-`last_seen` row, which is the wrong one
   *  the moment a MAC is live on two networks (task 51 §4). */
  network?: string | null;
  onClose: () => void;
};

type DrawerTab = "timeline" | "familiar" | "ports" | "presence" | "dns";

const TABS: { value: DrawerTab; label: string }[] = [
  { value: "timeline", label: "Timeline" },
  { value: "familiar", label: "Is it mine?" },
  { value: "ports", label: "Ports" },
  { value: "presence", label: "Presence" },
  { value: "dns", label: "DNS" },
];

const PORT_COLUMNS: Column<PortRow>[] = [
  { key: "port", header: "Port", align: "right" },
  { key: "service", header: "Service" },
  { key: "since", header: "Since", render: (row) => ago(row.since) },
  { key: "risky", header: "Risky", render: (row) => (row.risky ? "yes" : "no") },
];

const OPERATOR_TOOLTIP = "operator role required";

/** Applies `patch` to the one device in `payload` with matching `mac`,
 *  leaving every other group/device untouched. Used to keep the Devices
 *  grid's list query in step with an optimistic drawer edit without a
 *  refetch. */
function patchDeviceInList(payload: DevicesPayload, mac: string, patch: Partial<DeviceCard>): DevicesPayload {
  return {
    ...payload,
    groups: payload.groups.map((g) => ({
      ...g,
      devices: g.devices.map((d) => (d.mac === mac ? { ...d, ...patch } : d)),
    })),
  };
}

/** Drops the device with `mac` from every group — the optimistic side of
 *  Forget, which deletes the device server-side rather than patching it. */
function removeDeviceFromList(payload: DevicesPayload, mac: string): DevicesPayload {
  return {
    ...payload,
    groups: payload.groups.map((g) => ({ ...g, devices: g.devices.filter((d) => d.mac !== mac) })),
  };
}

export function DeviceDrawer({ mac, devices, network = null, onClose }: DeviceDrawerProps) {
  const detailQuery = useQuery({
    queryKey: ["deviceDetail", mac, network],
    queryFn: () => deviceDetail(mac as string, network),
    enabled: mac !== null,
  });

  const trafficQuery = useQuery({
    queryKey: ["deviceTraffic", mac, network],
    queryFn: () => deviceTraffic(mac as string, 24, network),
    enabled: mac !== null,
  });

  const detail = detailQuery.data;

  if (mac === null) {
    return <Drawer open={false} onClose={onClose} title="" />;
  }

  const title = detail ? detail.device.label || detail.device.name || detail.device.mac : mac;

  return (
    <Drawer open onClose={onClose} title={title} subtitle={detail?.device.mac}>
      {!detail ? (
        <p className="hint">Loading…</p>
      ) : (
        // Keyed on `mac`: every draft (label edit, active tab, merge pick,
        // forget confirmation) is local UI state that must start fresh for a
        // newly opened device rather than carry over from the last one — a
        // remount does that for free, with no reset effect needed.
        <DeviceDrawerBody
          key={mac}
          mac={mac}
          network={network}
          detail={detail}
          traffic={trafficQuery.data}
          otherDevices={devices.filter((d) => d.mac !== mac)}
          onClose={onClose}
        />
      )}
    </Drawer>
  );
}

type DeviceDrawerBodyProps = {
  mac: string;
  network: string | null;
  detail: DeviceDetailPayload;
  /** `undefined` while the traffic fetch is still in flight -- rendered as
   *  nothing rather than a loading placeholder, since the rest of the drawer
   *  is already interactive by the time this settles. */
  traffic?: DeviceTrafficPayload;
  otherDevices: DeviceCard[];
  onClose: () => void;
};

function DeviceDrawerBody({ mac, network, detail, traffic, otherDevices, onClose }: DeviceDrawerBodyProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<DrawerTab>("timeline");
  const [labelDraft, setLabelDraft] = useState(detail.device.label);
  const [mergeTarget, setMergeTarget] = useState("");
  const [confirmForget, setConfirmForget] = useState(false);

  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canWrite = atLeast(role, "operator");
  const isAdmin = atLeast(role, "admin");

  const detailKey = ["deviceDetail", mac, network];
  const devicesKey = ["devices", network];

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: detailKey });
    void queryClient.invalidateQueries({ queryKey: devicesKey });
  }

  // Shared by every field-patch mutation below: snapshot both the device
  // detail and the Devices grid's list query, apply `patch` to each
  // optimistically, and hand back the snapshots for `onError` to restore.
  // `onSettled` still invalidates both afterward — the optimistic value is
  // what the viewer sees instantly, the invalidation is what makes the
  // server's actual state (audit-logged, hub-computed fields included) the
  // one that sticks.
  async function snapshotAndApply(patch: Partial<DeviceCard>) {
    await queryClient.cancelQueries({ queryKey: detailKey });
    await queryClient.cancelQueries({ queryKey: devicesKey });
    const previousDetail = queryClient.getQueryData<DeviceDetailPayload>(detailKey);
    const previousDevices = queryClient.getQueryData<DevicesPayload>(devicesKey);
    if (previousDetail) {
      queryClient.setQueryData<DeviceDetailPayload>(detailKey, {
        ...previousDetail,
        device: { ...previousDetail.device, ...patch },
      });
    }
    if (previousDevices) {
      queryClient.setQueryData<DevicesPayload>(devicesKey, patchDeviceInList(previousDevices, mac, patch));
    }
    return { previousDetail, previousDevices };
  }

  function rollback(context?: { previousDetail?: DeviceDetailPayload; previousDevices?: DevicesPayload }) {
    if (context?.previousDetail) queryClient.setQueryData(detailKey, context.previousDetail);
    if (context?.previousDevices) queryClient.setQueryData(devicesKey, context.previousDevices);
  }

  // Every field write (label, trusted, always_on, quarantined, parent —
  // merge and split alike) is the same hub route: `POST /api/v1/devices/
  // {mac}` with whatever subset of `DeviceUpdate` changed. One mutation, one
  // optimistic-update/rollback shape; `onSuccess` reads which key was
  // patched to decide the toast/close side effect per action.
  const patchMutation = useMutation({
    mutationFn: (patch: Ack) => updateDevice(mac, patch, network),
    onMutate: (patch: Ack) => snapshotAndApply(patch as Partial<DeviceCard>),
    onError: (err: Error, _patch, context) => {
      rollback(context);
      toast.show(err.message, "alert");
    },
    onSuccess: (_ack, patch) => {
      if ("quarantined" in patch) {
        toast.show(patch.quarantined ? "Device quarantined" : "Quarantine lifted", patch.quarantined ? "warn" : "good");
      } else if ("parent" in patch) {
        if (patch.parent) {
          toast.show("Merged", "good");
          onClose();
        } else {
          toast.show("Split from parent", "good");
        }
      }
    },
    onSettled: invalidate,
  });

  const forgetMutation = useMutation({
    mutationFn: () => forgetDevice(mac, network),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: devicesKey });
      const previousDevices = queryClient.getQueryData<DevicesPayload>(devicesKey);
      if (previousDevices) {
        queryClient.setQueryData<DevicesPayload>(devicesKey, removeDeviceFromList(previousDevices, mac));
      }
      return { previousDevices };
    },
    onError: (err: Error, _vars, context) => {
      if (context?.previousDevices) queryClient.setQueryData(devicesKey, context.previousDevices);
      toast.show(err.message, "alert");
    },
    onSuccess: () => {
      toast.show("Device forgotten", "good");
      onClose();
    },
    onSettled: invalidate,
  });

  function saveLabel() {
    const next = labelDraft.trim();
    if (next === detail.device.label) return;
    patchMutation.mutate({ label: next });
  }

  // What the customer SEES this device called, plus the address that makes
  // it identifiable -- stored on the ticket, because the row it names may be
  // forgotten by the time the operator reads the thread.
  const deviceTitle = [detail.device.label || detail.device.name || detail.device.mac,
                       detail.device.ip].filter(Boolean).join(" · ");

  const parentDevice = detail.device.parent
    ? otherDevices.find((d) => d.mac === detail.device.parent)
    : undefined;
  const parentLabel = parentDevice?.label || parentDevice?.name || detail.device.parent || "";

  return (
    <div className="g-device-drawer">
      {detail.device.network !== null && (
        <p className="g-device-drawer-network">
          On <NetworkBadge network={detail.device.network} />
        </p>
      )}

      {detail.device.kind === "camera" && (
        // Why Vault filed this as a camera, in the sensor's own words. A
        // camera the hub's text fallback classified carries no reasons, so it
        // says how it was recognised rather than showing an empty list.
        <p className="g-device-drawer-camera" data-testid="camera-reasons">
          <Badge tone="good">Camera</Badge>{" "}
          {detail.device.camera_reasons.length > 0
            ? detail.device.camera_reasons.join("; ")
            : "recognised from its name and maker"}
        </p>
      )}

      {(detail.risk_reasons?.length ?? 0) > 0 && (
        <section className="g-device-risk-why" aria-label="Why the risk score">
          <strong>Why risk {detail.device.risk}</strong>
          <ul>
            {detail.risk_reasons!.map((r) => (
              <li key={r.why}>
                <span className="num">+{r.points}</span> {r.why}
              </li>
            ))}
          </ul>
        </section>
      )}

      {traffic && <TrafficBlock data={traffic} />}

      <Tabs items={TABS} value={tab} onChange={(v) => setTab(v as DrawerTab)} label="Device detail" />

      <div className="g-device-drawer-controls">
        <label className="g-device-field">
          <span>Label</span>
          <input
            type="text"
            aria-label="Label"
            value={labelDraft}
            disabled={!canWrite}
            title={canWrite ? undefined : OPERATOR_TOOLTIP}
            onChange={(e) => setLabelDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                saveLabel();
              }
            }}
            onBlur={saveLabel}
          />
        </label>

        <DeviceImportance mac={detail.device.mac} network={network} canWrite={canWrite} />
        <div className="g-device-toggles">
          <button
            type="button"
            className={detail.device.trusted ? "g-toggle-on" : ""}
            aria-pressed={detail.device.trusted}
            disabled={!canWrite}
            title={canWrite ? undefined : OPERATOR_TOOLTIP}
            onClick={() => patchMutation.mutate({ trusted: !detail.device.trusted })}
          >
            Trusted
          </button>
          <button
            type="button"
            className={detail.device.always_on ? "g-toggle-on" : ""}
            aria-pressed={detail.device.always_on}
            disabled={!canWrite}
            title={canWrite ? undefined : OPERATOR_TOOLTIP}
            onClick={() => patchMutation.mutate({ always_on: !detail.device.always_on })}
          >
            Always-on
          </button>
          <button
            type="button"
            className={detail.device.quarantined ? "g-toggle-on" : ""}
            aria-pressed={detail.device.quarantined}
            disabled={!canWrite}
            title={canWrite ? undefined : OPERATOR_TOOLTIP}
            onClick={() => patchMutation.mutate({ quarantined: !detail.device.quarantined })}
          >
            Quarantine
          </button>
        </div>

        {detail.device.parent ? (
          <div className="g-device-merged">
            <span>Merged into {parentLabel}</span>
            <button
              type="button"
              disabled={!canWrite}
              title={canWrite ? undefined : OPERATOR_TOOLTIP}
              onClick={() => patchMutation.mutate({ parent: null })}
            >
              Split
            </button>
          </div>
        ) : (
          <div className="g-device-merge">
            <label>
              <span>Merge into</span>
              <select
                aria-label="Merge into"
                value={mergeTarget}
                disabled={!canWrite}
                title={canWrite ? undefined : OPERATOR_TOOLTIP}
                onChange={(e) => setMergeTarget(e.target.value)}
              >
                <option value="">Choose a parent device…</option>
                {otherDevices.map((d) => (
                  <option key={d.mac} value={d.mac}>
                    {d.label || d.name || d.mac}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={!canWrite || mergeTarget === ""}
              title={canWrite ? undefined : OPERATOR_TOOLTIP}
              onClick={() => patchMutation.mutate({ parent: mergeTarget })}
            >
              Merge
            </button>
          </div>
        )}

        {/* Item 46: the ticket desk is only useful where the problem is.
            This carries the device with it, so the operator opens a thread
            that already names what the customer was looking at instead of
            asking them to describe it. */}
        <div className="g-device-ask">
          <Link
            className="g-btn-ghost"
            to={`/support?about_kind=device&about_id=${encodeURIComponent(mac)}`
              + `&about_label=${encodeURIComponent(deviceTitle)}&about_category=device`}
          >
            Ask Vault about this device
          </Link>
        </div>

        {isAdmin && (
          <div className="g-device-forget">
            {confirmForget ? (
              <>
                <span>Forget this device permanently?</span>
                <button type="button" onClick={() => forgetMutation.mutate()}>
                  Confirm forget
                </button>
                <button type="button" onClick={() => setConfirmForget(false)}>
                  Cancel
                </button>
              </>
            ) : (
              <button type="button" className="g-danger" onClick={() => setConfirmForget(true)}>
                Forget device
              </button>
            )}
          </div>
        )}
      </div>

      {tab === "timeline" && <Timeline items={detail.timeline} />}

      {tab === "ports" && (
        <Table columns={PORT_COLUMNS} rows={detail.ports} rowKey={(row) => `${row.port}`} />
      )}

      {tab === "familiar" && <FamiliarTab detail={detail} />}
      {tab === "presence" && <PresenceTab detail={detail} />}

      {tab === "dns" && <DnsTab detail={detail} />}
    </div>
  );
}

/** "Is this device one of ours?" -- Vault's own history for the device,
 *  assembled into a verdict with the facts behind it. Evidence, never
 *  certainty: every reason is a line the owner can disagree with. */
function FamiliarTab({ detail }: { detail: DeviceDetailPayload }) {
  const f = detail.familiarity;
  const tone = f.verdict === "resident" ? "good" : f.verdict === "regular" ? "plain" : f.verdict === "visitor" ? "warn" : "plain";
  const verdictLabel =
    f.verdict === "resident" ? "Lives here" : f.verdict === "regular" ? "Regular" : f.verdict === "visitor" ? "Visitor" : "No history yet";
  return (
    <div className="g-device-familiar" data-testid="device-familiar">
      <p className="g-familiar-verdict">
        <Badge tone={tone}>{verdictLabel}</Badge> <span>{f.summary}</span>
      </p>
      <ul className="g-familiar-reasons">
        {f.reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      <dl className="g-facts">
        <dt>Seen on</dt>
        <dd>
          {f.days_seen} of the last {f.lookback_days} days
        </dd>
        <dt>Overnight</dt>
        <dd>{Math.round(f.overnight_share * 100)}% of sightings</dd>
        <dt>First / last seen</dt>
        <dd>
          {f.first_seen || "—"} / {f.last_seen || "—"}
        </dd>
        <dt>Joins / leaves (30d)</dt>
        <dd>
          {f.joins} / {f.leaves}
        </dd>
        {f.platform_hint && (
          <>
            <dt>Looks like</dt>
            <dd>{f.platform_hint} device (from DNS)</dd>
          </>
        )}
        {f.name_siblings.length > 0 && (
          <>
            <dt>Same name under</dt>
            <dd>
              {f.name_siblings.map((s) => (
                <span key={s.mac}>
                  {s.mac} {s.trusted ? <Badge tone="good">trusted</Badge> : <Badge tone="plain">untrusted</Badge>}{" "}
                </span>
              ))}
            </dd>
          </>
        )}
        {f.other_networks.length > 0 && (
          <>
            <dt>Also seen on</dt>
            <dd>{f.other_networks.join(", ")}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

function PresenceTab({ detail }: { detail: DeviceDetailPayload }) {
  const { presence } = detail;
  return (
    <dl className="g-facts">
      <dt>Online, 24h</dt>
      <dd>{Math.round(presence.online_pct_24h)}%</dd>
      <dt>Online, 7d</dt>
      <dd>{Math.round(presence.online_pct_7d)}%</dd>
      <dt>Typical hours</dt>
      <dd>{presence.typical_hours.join(", ") || "—"}</dd>
    </dl>
  );
}

function DnsTab({ detail }: { detail: DeviceDetailPayload }) {
  const { dns } = detail;
  return (
    <div className="g-device-dns">
      <dl className="g-facts">
        <dt>Queries, 24h</dt>
        <dd>{dns.queries_24h}</dd>
        <dt>Blocked, 24h</dt>
        <dd>{dns.blocked_24h}</dd>
      </dl>
      <div className="g-device-dns-lists">
        <div>
          <h3>Top domains</h3>
          <ul>
            {dns.top.map((n) => (
              <li key={n.name}>
                {n.name} · {n.count}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Top blocked</h3>
          <ul>
            {dns.blocked_top.map((n) => (
              <li key={n.name}>
                <Badge tone="warn">{n.name}</Badge> · {n.count}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

export default DeviceDrawer;
