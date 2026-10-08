import {DataNotice} from "../components/DataNotice";
import {DnsExceptions} from "../components/DnsExceptions";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, Drawer, Page, PageHeader, StatStrip, useToast } from "../design";
import type { StatItem } from "../design";
import { DnsInsights, DnsOddLookups } from "../components/DnsInsights";
import { DnsProfile } from "../components/DnsProfile";
import { DnsWizard } from "../components/DnsWizard";
import { EmptyState } from "../components/EmptyState";
import { QueryStream } from "../components/QueryStream";
import { RuleManager } from "../components/RuleManager";
import type { RuleKind } from "../components/RuleManager";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { dnsPayload, saveConfig, writeDnsRule } from "../lib/api";
import { useLiveFeed } from "../lib/live";
import { num, pct } from "../lib/format";
import type { DnsPayload, DnsStreamRow } from "../types";
import { sectionId, useSectionAnchor } from "../lib/section";

const EMPTY_DNS: DnsPayload = {
  status: {
    enabled: false,
    running: false,
    queries_24h: 0,
    blocked_24h: 0,
    block_pct: 0,
    cache: 0,
    upstream_ms_avg: null,
    total_blocked_domains: 0,
    family_filter: false,
    uptime_s: 0,
    ts: null,
    clients_using: 0,
    devices_total: 0,
    coverage_pct: 0,
  },
  top: [],
  blocked_top: [],
  clients: [],
  rules: { allow: [], block: [] },
  lists: [],
  beaconing: [],
  suspicious: [],
  wizard: { resolver_ip: "", router_hint: "", working: false, clients_using: 0, checked_at: null },
};

const BUFFER_CAP = 1000;
// Rule/list writes reach the sensor's DNS config the same way a fix does —
// gated admin on the hub, not operator (same bar as `Security.tsx`'s
// `canFix` and `Updates.tsx`'s reboot planner).
const ADMIN_TOOLTIP = "admin role required";

/** Normalises one `dns` live frame's loosely-typed `data` into a
 *  `DnsStreamRow`. The frame is a `Record<string, unknown>` (see
 *  `LiveEvent`), so a field the hub omitted falls back to an empty/neutral
 *  value rather than the row being dropped — the stream is best-effort by
 *  design, never a strict parse that can throw mid-frame. */
function toStreamRow(data: Record<string, unknown>): DnsStreamRow {
  return {
    ts: typeof data.ts === "string" ? data.ts : new Date().toISOString(),
    mac: typeof data.mac === "string" ? data.mac : null,
    client: typeof data.client === "string" ? data.client : "",
    name: typeof data.name === "string" ? data.name : "",
    qtype: typeof data.qtype === "string" ? data.qtype : "A",
    blocked: data.blocked === true,
    list: typeof data.list === "string" ? data.list : null,
    ms: typeof data.ms === "number" ? data.ms : 0,
    device: typeof data.device === "string" ? data.device : "",
    // The hub's live `dns` frame builder does not populate `network`
    // yet (task-51 report §11.2) — the field exists on the contract, so
    // it is spelled out as null rather than left off the row.
    network: null,
  };
}

export function Dns() {
  // `/<tab>/<section>` scrolls that panel into view — see lib/section.ts.
  useSectionAnchor();
    const toast = useToast();
  const queryClient = useQueryClient();
  const live = useLiveFeed({ subscribe: ["dns"] });
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canWrite = atLeast(role, "admin");

  const [buffer, setBuffer] = useState<DnsStreamRow[]>([]);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("");
  const [whyRow, setWhyRow] = useState<DnsStreamRow | null>(null);
  const [profileMac, setProfileMac] = useState<string | null>(null);

  // The live check re-polls this same payload every 10s while the tab is
  // open, per spec — there is no separate wizard-status route.
  const dnsQuery = useQuery({ queryKey: ["dns"], queryFn: () => dnsPayload(24), refetchInterval: 10_000 });
  const payload = dnsQuery.data ?? EMPTY_DNS;

  useEffect(() => {
    return live.on("dns", (event) => {
      setBuffer((prev) => {
        const next = [...prev, toStreamRow(event.data)];
        return next.length > BUFFER_CAP ? next.slice(next.length - BUFFER_CAP) : next;
      });
    });
  }, [live]);

  // Cancel the page's own in-flight `["dns"]` GET before writing, then wait
  // for the write to land before refetching — otherwise `invalidateQueries`
  // dedupes onto a fetch already in flight from mount/poll and the write can
  // be overwritten by data captured before it landed.
  async function cancelDns() {
    await queryClient.cancelQueries({ queryKey: ["dns"] });
  }

  async function invalidate() {
    await queryClient.invalidateQueries({ queryKey: ["dns"], refetchType: "all" });
  }

  const ruleMutation = useMutation({
    mutationFn: ({ kind, domain, op }: { kind: RuleKind; domain: string; op: "add" | "remove" }) =>
      writeDnsRule(kind, domain, op),
    onMutate: cancelDns,
    onSuccess: invalidate,
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const listMutation = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      saveConfig({ dns: { lists: { [name]: enabled } } }),
    onMutate: cancelDns,
    onSuccess: invalidate,
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const profileRow = buffer.find((r) => r.mac === profileMac) ?? null;

  // Item 36: why this name was blocked or kept, built only from what
  // Vault actually knows -- which blocklist matched, what that list is
  // for, and whether one of the client's own rules decided it. No guess.
  const whyList = whyRow?.list
    ? payload.lists.find((l) => l.name === whyRow.list) ?? null
    : null;
  const whyOwnBlock = whyRow ? payload.rules.block.includes(whyRow.name) : false;
  const whyOwnAllow = whyRow ? payload.rules.allow.includes(whyRow.name) : false;

  // With filtering off, the query/block counters below and the live stream
  // are all zero by definition — that reads as "broken", not "off". A
  // one-line banner above them names the actual state and links straight to
  // the setup wizard card further down the same tab, rather than the whole
  // page pretending nothing is different.
  function focusWizard() {
    // The card is rendered with `id={sectionId("setup")}` -- i.e. `g-sec-setup`.
    // This looked for "dns-wizard", an id nothing has ever had, and the `?.`
    // swallowed the miss: the button scrolled nowhere and reported nothing.
    // Deriving the id from the same helper that writes it is the fix; a
    // literal here can go stale again the moment the section is renamed.
    const section = document.getElementById(sectionId("setup"));
    if (!section) return;
    section.scrollIntoView({ behavior: "smooth", block: "start" });
    // Land the keyboard there too, so the button works for someone who is
    // not using a mouse -- scrolling alone leaves focus on the dead button.
    section.querySelector<HTMLElement>("button, input, select, a[href]")?.focus({ preventScroll: true });
  }

  if (dnsQuery.isPending) return <p role="status">Loading DNS history…</p>;
  if (dnsQuery.isError && !dnsQuery.data) return <div role="alert">Could not load DNS history. <button onClick={()=>void dnsQuery.refetch()}>Retry</button></div>;
  // Item 35: while the resolver is not running there is nothing to count,
  // and a row of zeros is the wrong way to say that -- "0 blocked" reads as
  // "nothing bad happened", which is the opposite of the truth when nothing
  // is being inspected at all. An em dash says "no answer"; a zero is an
  // answer. The banner above already explains how to turn it on.
  const filtering = payload.status.enabled && payload.status.running;
  const off = { value: "—", delta: "filtering is off", deltaTone: "warn" as const };
  const dnsStats: StatItem[] = filtering
    ? [
        { label: "Queries · 24h", value: num(payload.status.queries_24h) },
        { label: "Blocked · 24h", value: num(payload.status.blocked_24h), delta: pct(payload.status.block_pct), deltaTone: "good" },
        { label: "Devices using it", value: String(payload.status.clients_using) },
        { label: "Upstream latency", value: payload.status.upstream_ms_avg === null ? "—" : `${Math.round(payload.status.upstream_ms_avg)} ms` },
      ]
    : [
        { label: "Queries · 24h", ...off },
        { label: "Blocked · 24h", ...off },
        { label: "Devices using it", ...off },
        { label: "Upstream latency", ...off },
      ];
  return (
    <Page>
      <PageHeader
        eyebrow="Network · what devices look up"
        title="DNS filter"
        sub={payload.status.enabled ? "Vault's own resolver: query volume, blocking and setup." : "Filtering is off — turn it on from the setup wizard below."}
      />
      <StatStrip items={dnsStats} />
    <div className="g-dns">
      <DataNotice query={dnsQuery} />
      {!payload.status.enabled && (
        <EmptyState
          title="DNS filtering is off."
          body="Turn it on from the setup wizard to start blocking ads and trackers and see real query volume here."
          action={{ label: "Go to the setup wizard", onClick: focusWizard }}
        />
      )}

      <Card title="DNS" subtitle="Vault's own resolver: query volume, blocking and setup.">
        <dl className="g-dns-status">
          <dt>Queries, 24h</dt>
          <dd>{num(payload.status.queries_24h)}</dd>
          <dt>Blocked, 24h</dt>
          <dd>{num(payload.status.blocked_24h)}</dd>
          <dt>Block rate</dt>
          <dd>{pct(payload.status.block_pct)}</dd>
          <dt>Blocklist domains</dt>
          <dd>{num(payload.status.total_blocked_domains)}</dd>
        </dl>
        {/* Every figure above comes from the resolver's own logs, so it
            describes only the devices that ask Vault. On most networks
            that is a minority, and the device doing the lookups you would
            most want to see is the one most likely to be using its own
            resolver — where it shows up as perfect silence. Saying what
            fraction is covered is the difference between a fact about the
            network and a fact about four devices. */}
        {/* Withheld until the payload is actually in. `EMPTY_DNS` stands in
            while the query is still running, and its zeroes would make this
            line say "no devices known on this network yet" -- a claim about
            the network, asserted before anything has been read. Same mistake
            the uplink blackout guard in `sensor/alerts.py` exists to avoid:
            do not report a state nothing has observed. Absent for a moment
            is honest; wrong for a moment is not. */}
        {dnsQuery.data && (
          <p className="hint" data-testid="dns-coverage">
            {payload.status.devices_total === 0
              ? "No devices known on this network yet, so these figures describe nothing."
              : `Based on ${payload.status.clients_using} of ${payload.status.devices_total} ` +
                `devices (${payload.status.coverage_pct}% coverage). Devices using their own ` +
                `resolver, or encrypted DNS, do not appear here at all.`}
          </p>
        )}
      </Card>

      <Card title="Where the lookups go" subtitle="The last 24 hours of resolver traffic.">
        <DnsInsights top={payload.top} blockedTop={payload.blocked_top} clients={payload.clients} />
      </Card>

      <Card
        title="Beaconing & suspicious lookups"
        subtitle="A device contacting one domain on a fixed timer, or a random-looking name."
      >
        <DnsOddLookups payload={payload} />
      </Card>

      {/* The per-device profile opens as a drawer from a client row in this
          stream, so `/dns/profiles` anchors here — on the picker — rather
          than on a panel that only exists once a device is chosen. */}
      <div id={sectionId("profiles")}>
        <Card id={sectionId("live")} title="Live query stream">
        <QueryStream
          rows={buffer}
          paused={paused}
          onPause={setPaused}
          filter={filter}
          onFilter={setFilter}
          onSelectClient={(row) => {
            if (row.mac) setProfileMac(row.mac);
          }}
          onSelectName={setWhyRow}
          />
        </Card>
      </div>

      <Card id={sectionId("lists")} title="Block / allow list">
        <DnsExceptions canWrite={canWrite} />
        <RuleManager
          rules={payload.rules}
          lists={payload.lists}
          onAdd={(kind, domain) => ruleMutation.mutate({ kind, domain, op: "add" })}
          onRemove={(kind, domain) => ruleMutation.mutate({ kind, domain, op: "remove" })}
          onToggleList={(name, enabled) => listMutation.mutate({ name, enabled })}
          canWrite={canWrite}
          writeTooltip={ADMIN_TOOLTIP}
        />
      </Card>

      <Card id={sectionId("setup")} title="Setup wizard">
        <DnsWizard wizard={payload.wizard} />
      </Card>

      <Drawer
        open={whyRow !== null}
        onClose={() => setWhyRow(null)}
        title={whyRow ? whyRow.name : ""}
        subtitle={whyRow ? (whyRow.blocked ? "Blocked" : "Allowed") : undefined}
      >
        {whyRow && (
          <dl className="g-dns-why">
            <dt>What happened</dt>
            <dd>
              {whyRow.blocked
                ? whyOwnBlock
                  ? "You added this name to your own block list, so Vault refused it before the device could reach it."
                  : whyList
                    ? `Vault refused it before the device could reach it. It appears on the ${whyList.name} list, which covers ${whyList.category}.`
                    : whyRow.list
                      ? `Vault refused it before the device could reach it, because the ${whyRow.list} list carries it.`
                      : "Vault refused it before the device could reach it."
                : whyOwnAllow
                  ? "You added this name to your own allow list, so Vault answered it even if a blocklist carries it."
                  : "No blocklist Vault loads carries this name, so it was answered normally."}
            </dd>
            <dt>Who asked</dt>
            <dd>
              {whyRow.device || whyRow.client}
              {whyRow.qtype ? ` · ${whyRow.qtype} record` : ""}
              {` · answered in ${whyRow.ms} ms`}
            </dd>
            <dt>What you can do</dt>
            <dd>
              {whyRow.blocked
                ? "If this name is one you need, add it to the allow list below and Vault will answer it from now on."
                : "If this name should never be reachable, add it to the block list below and Vault will refuse it from now on."}
            </dd>
          </dl>
        )}
      </Drawer>

      <Drawer
        open={profileMac !== null}
        onClose={() => setProfileMac(null)}
        title={profileRow?.device || profileRow?.client || profileMac || "Device"}
      >
        {profileMac && <DnsProfile mac={profileMac} />}
      </Drawer>
    </div>
    </Page>
  );
}

export default Dns;
