import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, Drawer, Page, PageHeader, StatStrip } from "../design";
import type { StatItem } from "../design";
import { DeviceDrawer } from "../components/DeviceDrawer";
import { LinkIssueDrawer } from "../components/LinkIssueDrawer";
import { EmptyState } from "../components/EmptyState";
import { NetworkChips } from "../components/NetworkChips";
import { NetworkMap } from "../components/NetworkMap";
import type { GapSlotKey } from "../components/NetworkMap";
import { TrafficSection } from "../components/TrafficSection";
import { RouteCheck } from "../components/RouteCheck";
import { devicesPayload, overviewPayload, securityPayload, topologyPayload, trafficOverview } from "../lib/api";
import { useMe } from "../lib/auth";
import { useLiveFeed } from "../lib/live";
import { useSelectedNetwork } from "../lib/network";
import type { CameraCensus, RecorderGap, TopoNode, TopologyPayload } from "../types";

const EMPTY_TOPOLOGY: TopologyPayload = { nodes: [], links: [], clusters: [], networks: [] };
const EMPTY_CAMERAS: CameraCensus = { known: 0, online: 0, expected: 0, exposed: false, exposed_ports: [], gaps: [] };

/** Every field of a non-device node (host, site, router/extender the hub
 *  synthesized with no matching `Device` row) as a plain-text definition
 *  list — same "raw facts" drawer `Assets` uses for a site/host click, so a
 *  node without a `mac` still opens to something instead of doing nothing. */
function factRows(node: TopoNode): { key: string; value: string }[] {
  return Object.entries(node).map(([key, value]) => ({
    key,
    value: value === null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value),
  }));
}

/** The explanation drawer for one unconfirmed camera slot — never a device,
 *  so it gets the gap's own words (`RecorderGap.reason`, the same sentence
 *  `CameraCensusPanel` shows on Overview) rather than a device fact list. */
function gapFacts(gap: RecorderGap): { key: string; value: string }[] {
  return [
    { key: "recorder", value: gap.name },
    { key: "expected channels", value: String(gap.expected) },
    { key: "confirmed", value: String(gap.known) },
    { key: "reason", value: gap.reason },
  ];
}

export function Topology() {
  const queryClient = useQueryClient();
  const live = useLiveFeed();
  const meQuery = useMe();
  const tenantId = meQuery.data?.active_tenant?.id ?? null;
  const [network, setNetwork] = useSelectedNetwork(tenantId);
  const [showWholeMap, setShowWholeMap] = useState(false);
  const [selected, setSelected] = useState<TopoNode | null>(null);
  const [selectedGap, setSelectedGap] = useState<{ gap: RecorderGap; key: GapSlotKey } | null>(null);
  // The node whose coloured link was clicked -- its open alerts, with Fix.
  const [issueNode, setIssueNode] = useState<TopoNode | null>(null);

  // The map's organizing idea is fixed — internet, edge, one boundary per
  // network — so `cluster_by` is always "network" here. There is no chooser
  // for it: unlike the retired force graph, this layout does not have a
  // "by kind" or "by area" reading of the same data.
  const topologyQuery = useQuery({
    queryKey: ["topology", "network", network],
    queryFn: () => topologyPayload("network", network),
  });
  const topology = topologyQuery.data ?? EMPTY_TOPOLOGY;

  // The "who is talking to whom" panel, scoped to the same network selection
  // as the map. Always a 1-hour window here -- Topology is "what is
  // happening now", not a history view.
  const trafficQuery = useQuery({
    queryKey: ["traffic", network],
    queryFn: () => trafficOverview(1, network),
  });

  // Overview's camera census (Task 83) is the only source of `RecorderGap`
  // rows — same `["overview"]` query key `Overview.tsx` uses, so the map
  // reuses its cache instead of issuing a second read.
  const overviewQuery = useQuery({ queryKey: ["overview"], queryFn: overviewPayload });
  // The route-check facts come from the same payload the Security tab grades
  // its Wi-Fi guard from, so the two can never disagree.
  const securityQuery = useQuery({ queryKey: ["security", network], queryFn: () => securityPayload(network) });
  const guard = (() => {
    const sec = securityQuery.data;
    if (!sec) return null;
    if (network) return sec.wifis.find((w) => w.network?.id === network) ?? null;
    return sec.wifi ?? null;
  })();
  const cameraGaps = overviewQuery.data?.cameras.gaps ?? EMPTY_CAMERAS.gaps;

  // The device drawer's "merge with…" picker needs the real device list —
  // without it the picker renders empty even though devices exist. Reuse the
  // same `devices` query Assets.tsx keys off of so a merge from here shares
  // cache with the Assets tab. Only fetch once the drawer is actually open
  // (a node is selected) — the map itself never needs this list.
  const devicesQuery = useQuery({
    queryKey: ["devices", network],
    queryFn: () => devicesPayload(network),
  });
  const allDevices = useMemo(
    () => (devicesQuery.data?.groups ?? []).flatMap((g) => g.devices),
    [devicesQuery.data],
  );

  useEffect(() => {
    // Item 49: a finished sweep redraws the whole map -- links, gaps and
    // the network summaries -- and only a "device" frame was listened for,
    // so a map left open aged silently.
    const offScan = live.on("scan", () => {
      for (const key of ["topology", "overview", "security", "devices"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    });
    const offDevice = live.on("device", () => {
      void queryClient.invalidateQueries({ queryKey: ["topology", "network", network] });
    });
    return () => { offScan(); offDevice(); };
  }, [live, queryClient, network]);

  function onSelectNode(node: TopoNode) {
    setSelectedGap(null);
    setIssueNode(null);
    setSelected(node);
  }

  function onSelectLink(node: TopoNode) {
    setSelectedGap(null);
    setSelected(null);
    setIssueNode(node);
  }

  function onSelectGap(gap: RecorderGap, key: GapSlotKey) {
    setSelected(null);
    setSelectedGap({ gap, key });
  }

  const deviceMac = selected?.mac ?? null;

  if (topologyQuery.isPending) return <p role="status">Loading topology…</p>;
  if (topologyQuery.isError && !topologyQuery.data) return <div role="alert">Could not load topology. <button onClick={() => void topologyQuery.refetch()}>Retry</button></div>;
  const gateways = topology.nodes.filter((n) => n.kind === "router" || n.kind === "gateway").length;
  const pulsing = topology.links.filter((l) => l.pulse).length;
  const topoStats: StatItem[] = [
    { label: "Networks", value: String(topology.networks.length) },
    { label: "Gateways", value: String(gateways) },
    { label: "Devices on the map", value: String(topology.nodes.length) },
    { label: "Links", value: String(topology.links.length), delta: pulsing > 0 ? `${pulsing} active` : undefined, deltaTone: "good" },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Network · how an attacker would move"
        title="How your network is put together"
        sub="Every device, what it connects through, and where the boundary between your network and the internet actually falls."
        extra={<NetworkChips networks={topology.networks} value={network} onChange={setNetwork} />}
      />
      <StatStrip items={topoStats} />
    <Card title="Map">
      {/* Gated on `isSuccess`, not just `topology.nodes.length`, so the
          loading state still mounts the map exactly as before — swapping in
          `EmptyState` before the fetch even settles would just show it as a
          false negative on every load. */}
      {!network && topology.networks.length > 1 && !showWholeMap ? (
        <section aria-label="Network summaries" className="g-network-summaries">
          <p>Choose a network to inspect its devices at a readable size.</p>
          {topology.networks.map(n => <button key={n.id} onClick={() => setNetwork(n.id)}>
            <strong>{n.name}</strong><span>{n.online} of {n.devices} last reported online</span><span className={n.sensor_ok ? "g-tone-good" : "g-tone-warn"}>{n.sensor_ok ? "Sensor reporting" : "Coverage needs attention"}</span>
          </button>)}
          <button onClick={() => setShowWholeMap(true)}>Show the full organization map</button>
        </section>
      ) : topologyQuery.isSuccess && topology.nodes.length === 0 ? (
        <EmptyState
          title="No network map yet."
          body="The map builds itself from the sensor's first scan — install it and come back."
          action={{ label: "Install the sensor", href: "/welcome" }}
        />
      ) : (
        <div className="g-topo-body">
          <NetworkMap
            topology={topology}
            cameraGaps={cameraGaps}
            selectedNodeId={selected?.id ?? null}
            selectedGapKey={selectedGap?.key ?? null}
            onSelectNode={onSelectNode}
            onSelectLink={onSelectLink}
            onSelectGap={onSelectGap}
          />
          {/* v2 sets the route check beside the map: the picture of the
              network, and whether its way out is still the one expected. */}
          <RouteCheck guard={guard} />
        </div>
      )}

      {trafficQuery.data && <TrafficSection data={trafficQuery.data} />}

      <DeviceDrawer mac={deviceMac} devices={allDevices} network={network} onClose={() => setSelected(null)} />

      <LinkIssueDrawer node={issueNode} onClose={() => setIssueNode(null)} onOpenDetails={onSelectNode} />

      <Drawer
        open={selected !== null && selected.mac === null}
        onClose={() => setSelected(null)}
        title={selected?.label ?? ""}
        subtitle={selected?.ip || undefined}
      >
        <dl className="g-facts">
          {selected &&
            factRows(selected).map((row) => (
              <div key={row.key} className="g-facts-row">
                <dt>{row.key}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
        </dl>
      </Drawer>

      <Drawer
        open={selectedGap !== null}
        onClose={() => setSelectedGap(null)}
        title="Camera, never listed"
        subtitle={selectedGap ? `behind ${selectedGap.gap.name}` : undefined}
      >
        <p className="hint">
          Vault only sees what the recorder tells it. This slot is inferred from the recorder's
          own expected-channel count, not observed — that is why it is drawn hollow.
        </p>
        <dl className="g-facts">
          {selectedGap &&
            gapFacts(selectedGap.gap).map((row) => (
              <div key={row.key} className="g-facts-row">
                <dt>{row.key}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
        </dl>
      </Drawer>
    </Card>
    </Page>
  );
}

export default Topology;
