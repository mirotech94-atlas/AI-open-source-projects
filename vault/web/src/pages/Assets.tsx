import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { DataNotice } from "../components/DataNotice";
import { Card, Drawer, Page, PageHeader, StatStrip, Tabs } from "../design";
import type { StatItem } from "../design";
import { HostsTable, SitesTable } from "../components/AssetTables";
import { DeviceDrawer } from "../components/DeviceDrawer";
import { DeviceGrid } from "../components/DeviceGrid";
import { DeviceSummaryTiles } from "../components/DeviceSummaryTiles";
import { HostCardView } from "../components/HostCardView";
import { NetworkChips } from "../components/NetworkChips";
import { TrendPanels } from "../components/TrendPanels";
import { NetworkSection } from "../components/NetworkSection";
import { ScreenTime } from "../components/ScreenTime";
import { SiteCardView } from "../components/SiteCardView";
import { VendorDonut } from "../components/VendorDonut";
import {
  devicesPayload,
  hostsPayload,
  overviewPayload,
  screenTimePayload,
  settingsPayload,
  sitesPayload,
} from "../lib/api";
import { useMe } from "../lib/auth";
import { useLiveFeed } from "../lib/live";
import { searchDevices, useSelectedNetwork } from "../lib/network";
import type { DeviceLens } from "../lib/network";
import type { DevicesPayload, HostsPayload, ScreenTimePayload, SitesPayload } from "../types";

const SUB_VIEWS = ["websites", "servers", "devices", "screen-time"] as const;
type SubView = (typeof SUB_VIEWS)[number];

function isSubView(v: string | undefined): v is SubView {
  return v !== undefined && (SUB_VIEWS as readonly string[]).includes(v);
}

const TAB_ITEMS = [
  // Four kinds of thing, named. There is no "Everything" view: it was a
  // fifth tab over the same four, so a client arriving here had to choose
  // between "Everything" and the things it was everything of.
  { value: "websites", label: "Websites" },
  { value: "servers", label: "Servers" },
  { value: "devices", label: "Devices" },
  { value: "screen-time", label: "Network presence" },
];

//: The live socket carries a frame the instant anything changes, so this is
//: only the backstop for when it is not connected -- but a frozen number is
//: worse than a slightly late one, and these are the figures a client reads
//: to decide nothing is wrong.
const GLANCE_POLL_MS = 30_000;

const DEFAULT_SITES: SitesPayload = { sites: [], charts: [] };
const DEFAULT_HOSTS: HostsPayload = { hosts: [] };
const DEFAULT_TILES = {
  new_devices: 0, trusted: 0, randomized: 0, down_mbps: null, up_mbps: null,
  latency_ms: null, dns_ms: null, dns_ok: null, wan_ip: "", wan_ips: [],
};
/** The four readings of "which devices", with why each one exists. */
const DEVICE_LENSES: { key: DeviceLens; label: string; note: string }[] = [
  { key: "all", label: "All", note: "Every device Vault has ever seen here" },
  { key: "online", label: "Online", note: "Answering right now" },
  { key: "away", label: "Not here", note: "Known, but not seen in the last sweep" },
  { key: "unidentified", label: "Unidentified", note: "Vault cannot say what this is — start here" },
];

/** Counted over the UNFILTERED payload, so the number beside a button says
 *  how many pressing it would show, not how many the current view holds. */
function countFor(payload: DevicesPayload, lens: DeviceLens): number {
  return searchDevices(payload, "", lens).total;
}

const DEFAULT_DEVICES: DevicesPayload = { groups: [], chips: [], total: 0, online: 0, unverified: 0, cameras: 0, cameras_online: 0, tiles: DEFAULT_TILES, networks: [], charts: [] };
const DEFAULT_SCREEN_TIME: ScreenTimePayload = { days: 0, scans: 0, devices: [], heatmap: [] };
// Transient fallback only: used for the brief window before `settingsPayload()`
// resolves (or if it errors). Once loaded, the tenant's own
// `thresholds.cert_days_warn` always wins — this constant never overrides it.
const DEFAULT_CERT_WARN_DAYS = 14;

/** Every field of a `SiteCard`/`HostCard` as a plain-text definition list —
 *  the "raw last facts" drawer for a site/host click. Never re-shapes a
 *  value: an array/object renders as JSON, so nothing here silently drops a
 *  field the hub adds later. */
function factRows(record: Record<string, unknown>): { key: string; value: string }[] {
  return Object.entries(record).map(([key, value]) => ({
    key,
    value:
      value === null
        ? "—"
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value),
  }));
}

export function Assets() {
  const params = useParams<{ view?: string; mac?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const live = useLiveFeed();
  const meQuery = useMe();
  const tenantId = meQuery.data?.active_tenant?.id ?? null;
  const [network, setNetwork] = useSelectedNetwork(tenantId);

  // Devices is the landing: it is what a client means by "my network".
  const view: SubView = params.mac !== undefined ? "devices" : isSubView(params.view) ? params.view : "devices";

  // One selection per network (keyed by network id, `""` for the ungrouped
  // view): a tenant filtering the Lab down to cameras shouldn't have that
  // filter follow them into the Office Wi-Fi section below it.
  const [selectedKinds, setSelectedKinds] = useState<Record<string, string | null>>({});

  function kindOf(key: string): string | null {
    return selectedKinds[key] ?? null;
  }

  function selectKind(key: string, kind: string | null) {
    setSelectedKinds((prev) => ({ ...prev, [key]: kind }));
  }

  const [days, setDays] = useState(7);
  // (The map/table toggle is gone -- cards are the only device view.)
  // Kept: the search, which narrows what those cards show.
  //
  // the week they were actually on.
  // The reference console's device search box: name, IP, MAC, vendor, label.
  const [search, setSearch] = useState("");
  // Item 16: which devices the grid is showing. "Unidentified" is here
  // because on a security product it is the most important cut of the list:
  // a device Vault cannot name is one that got on without anyone
  // deciding it should.
  const [lens, setLens] = useState<DeviceLens>("all");
  const [factsFor, setFactsFor] = useState<{ title: string; record: Record<string, unknown> } | null>(null);

  const sitesQuery = useQuery({ queryKey: ["sites"], queryFn: sitesPayload, enabled: view === "websites", refetchInterval: GLANCE_POLL_MS, refetchIntervalInBackground: false });
  const hostsQuery = useQuery({ queryKey: ["hosts"], queryFn: hostsPayload, enabled: view === "servers", refetchInterval: GLANCE_POLL_MS, refetchIntervalInBackground: false });
  const devicesQuery = useQuery({
    queryKey: ["devices", network],
    queryFn: () => devicesPayload(network),
    enabled: view === "devices" || params.mac !== undefined,
    refetchInterval: GLANCE_POLL_MS,
    refetchIntervalInBackground: false,
  });
  const screenTimeQuery = useQuery({
    queryKey: ["screenTime", days, network],
    queryFn: () => screenTimePayload(days, network),
    enabled: view === "screen-time",
    refetchInterval: GLANCE_POLL_MS,
    refetchIntervalInBackground: false,
  });
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: settingsPayload,
    enabled: view === "websites",
  });
  // Same `["overview"]` cache key `Overview.tsx`/`Topology.tsx` use, for the
  // camera-census gaps (Task 83) the device grid's unconfirmed-camera slots
  // read — never fetched twice for the same tenant.
  const overviewQuery = useQuery({
    queryKey: ["overview"],
    queryFn: overviewPayload,
    enabled: view === "devices",
  });
  const cameraGaps = overviewQuery.data?.cameras.gaps ?? [];

  useEffect(() => {
    return live.on("device", () => {
      void queryClient.invalidateQueries({ queryKey: ["devices"] });
    });
  }, [live, queryClient]);

  // Item 13: a finished sweep is exactly when a website's latency, a
  // certificate's remaining days, a server's package count or a device's
  // last-seen all move at once -- and nothing on this page was listening
  // for it. Sites and servers had neither a live handler nor a poll, so
  // their "at a glance" figures were whatever they had been when the tab
  // was opened, however long ago that was.
  useEffect(() => {
    return live.on("scan", () => {
      for (const key of ["sites", "hosts", "devices", "screenTime", "overview"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    });
  }, [live, queryClient]);

  // Item 14: Network presence opens a device WITHOUT leaving Network
  // presence. Routing to /assets/devices/<mac> also switches the sub-view
  // (see `view` above, which reads `params.mac`), so a client who clicked a
  // row to read its screen time was thrown onto the Devices tab and had to
  // navigate back. A drawer is an overlay; it should not be a destination.
  const [inlineMac, setInlineMac] = useState<string | null>(null);
  const mac = params.mac ?? inlineMac;

  function changeView(next: string) {
    navigate(`/assets/${next}`);
  }

  const sites = sitesQuery.data ?? DEFAULT_SITES;
  const hosts = hostsQuery.data ?? DEFAULT_HOSTS;
  const devices = devicesQuery.data ?? DEFAULT_DEVICES;
  const screenTime = screenTimeQuery.data ?? DEFAULT_SCREEN_TIME;
  const certWarnDays = settingsQuery.data?.thresholds.cert_days_warn ?? DEFAULT_CERT_WARN_DAYS;

  // "All networks" shows one section per network; a chip selection narrows it
  // to that one (the query itself is already scoped, this just keeps the page
  // honest if a stale payload is still in cache).
  // Every grid below reads the searched copy; the tiles keep the unfiltered
  // one, because they describe the network, not the query.
  const shown = searchDevices(devices, search, lens);
  const sections = network === null
    ? devices.networks
    : devices.networks.filter((n) => n.id === network);
  const allDevices = devices.groups.flatMap((g) => g.devices);
  const unattributed = shown.groups.flatMap((g) => g.devices).filter((d) => d.network === null);
  const unattributedGroups = shown.groups
    .map((g) => ({ ...g, devices: g.devices.filter((d) => d.network === null) }))
    .filter((g) => g.devices.length > 0);

  // Each view waits on its own query: sites and hosts arrive independently
  // own and the tiles/table simply fill in, rather than one slow fetch
  // holding the whole page on "Loading".
  const activeQuery = view === "websites" ? sitesQuery : view === "servers" ? hostsQuery : view === "devices" ? devicesQuery : screenTimeQuery;
  if (!activeQuery.data) return <Card title="Assets" actions={<Tabs items={TAB_ITEMS} value={view} onChange={changeView} label="Asset type" />}><p role="status">{activeQuery.isError ? "Assets could not be loaded." : "Loading assets…"}</p>{activeQuery.isError && <button onClick={()=>void activeQuery.refetch()}>Retry</button>}</Card>;

  const assetStats: StatItem[] = [
    { label: "Devices", value: String(devices.total), delta: `${devices.online} online`, deltaTone: "good" },
    { label: "Servers", value: String(hosts.hosts.length) },
    { label: "Websites", value: String(sites.sites.length) },
    { label: "Unverified", value: String(devices.unverified), deltaTone: devices.unverified > 0 ? "warn" : undefined },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Network · every way in"
        title="Assets"
        sub={view === "screen-time" ? `Estimated presence over ${days} days, for the selected network scope.` : view === "devices" ? "Everything on this network an attacker could reach, and what Vault knows about each one. A device it cannot identify is the one to look at first." : "Configured targets across this organization, with their latest reported observations."}
        extra={<Tabs items={TAB_ITEMS} value={view} onChange={changeView} label="Asset type" />}
      />
      <StatStrip items={assetStats} />
    <Card title={TAB_ITEMS.find((t) => t.value === view)?.label ?? "Assets"}>
      <DataNotice query={activeQuery} />
      {view === "websites" && (
        <>
          {sites.sites.length === 0 ? (
            <p className="g-asset-empty">No sites are being watched yet.</p>
          ) : (
            <>
              {/* An inventory is read by scanning columns: uptime and cert
                  expiry side by side across every site is the table's job,
                  so it leads rather than hiding under the cards. */}
              <SitesTable sites={sites.sites} certWarnDays={certWarnDays} />
              {/* The cards carry what the table cannot — the header pills and
                  the latency shape — as a visual glance underneath it, one
                  click from the same raw-facts drawer. */}
              <details className="g-asset-table-details" open>
                <summary>At a glance</summary>
                <div className="g-assets-grid">
                  {sites.sites.map((site) => (
                    <SiteCardView
                      key={site.name}
                      site={site}
                      certWarnDays={certWarnDays}
                      onOpen={() => setFactsFor({ title: site.name, record: site })}
                    />
                  ))}
                </div>
              </details>
            </>
          )}
          {/* Latency, certificate days left and availability per site — the
              reference's three panels under the table, over the hub's own
              7-day window. */}
          <TrendPanels charts={sites.charts} label="How each site has been doing" />
        </>
      )}

      {view === "servers" && (
        <>
          {hosts.hosts.length === 0 ? (
            <p className="g-asset-empty">No hosts are being watched yet.</p>
          ) : (
            <>
              {/* Load, disk and patch state read across every host at once —
                  the table leads for the same reason the websites tab's
                  does. */}
              <HostsTable hosts={hosts.hosts} />
              <details className="g-asset-table-details" open>
                <summary>At a glance</summary>
                <div className="g-assets-grid">
                  {hosts.hosts.map((host) => (
                    <HostCardView
                      key={host.name}
                      host={host}
                      onOpen={() => setFactsFor({ title: host.name, record: host })}
                    />
                  ))}
                </div>
              </details>
            </>
          )}
        </>
      )}

      {view === "devices" && (
        <>
          <NetworkChips networks={devices.networks} value={network} onChange={setNetwork} />
          {/* Below the chooser on purpose: figures that change above the
              control that changed them read as if they had led it. */}
          <DeviceSummaryTiles payload={devices} />
          <div className="g-device-tools">
            <label className="g-device-search">
              <span>Search devices</span>
              <input
                type="search"
                value={search}
                placeholder="name, IP, MAC or vendor"
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <div className="g-device-lens" role="group" aria-label="Show which devices">
              {DEVICE_LENSES.map((l) => (
                <button
                  key={l.key}
                  type="button"
                  aria-pressed={lens === l.key}
                  title={l.note}
                  onClick={() => setLens(l.key)}
                >
                  {l.label} · {countFor(devices, l.key)}
                </button>
              ))}
            </div>
          </div>
          {/* Cards only: a device is a thing with a name, an owner and a
              picture of what it is, and a row of cells says none of that. */}
          {sections.length === 0 ? (
            // No networks reported at all (a tenant whose hub predates them,
            // or a scoped read that came back empty): the flat grid, exactly
            // as it was before networks existed.
            <DeviceGrid
              payload={shown}
              selectedKind={kindOf("")}
              onSelectKind={(kind) => selectKind("", kind)}
              onOpen={(m) => navigate(`/assets/devices/${encodeURIComponent(m)}`)}
            />
          ) : (
            <div className="g-network-sections">
              {sections.map((net) => (
                <NetworkSection
                  key={net.id}
                  network={net}
                  payload={shown}
                  selectedKind={kindOf(net.id)}
                  onSelectKind={(kind) => selectKind(net.id, kind)}
                  onOpen={(m) => navigate(`/assets/devices/${encodeURIComponent(m)}`)}
                  cameraGaps={cameraGaps}
                  onOpenGap={(gap) =>
                    setFactsFor({
                      title: "Camera, never listed",
                      record: {
                        recorder: gap.name,
                        network: net.name,
                        expected_channels: gap.expected,
                        confirmed: gap.known,
                        reason: gap.reason,
                      },
                    })
                  }
                />
              ))}
              {unattributed.length > 0 && (
                <section className="g-network-section" data-testid="network-section-unassigned">
                  <div className="g-network-section-head">
                    <h3>Not on a known network</h3>
                    <span className="g-network-section-counts">{unattributed.length} devices</span>
                  </div>
                  <DeviceGrid
                    payload={{ ...shown, groups: unattributedGroups, chips: [] }}
                    selectedKind={kindOf("")}
                    onSelectKind={(kind) => selectKind("", kind)}
                    onOpen={(m) => navigate(`/assets/devices/${encodeURIComponent(m)}`)}
                  />
                </section>
              )}
            </div>
          )}
          <section className="g-vendor-breakdown">
            {/* Item 16: "who makes your devices" is a part-to-whole
                question, and a row of bars racing rightwards answered a
                different one ("which vendor is biggest"). */}
            <h3>What this network is made of</h3>
            <VendorDonut devices={allDevices} />
          </section>
          {/* Devices online, joins and leaves, and online-vs-known — the
              reference's three panels beside the vendor bars, over the hub's
              own 7-day window and scoped by the same network chooser. */}
          <TrendPanels charts={devices.charts} label="How the network has been moving" />
        </>
      )}

      {view === "screen-time" && (
        <ScreenTime
          payload={screenTime}
          days={days}
          onDaysChange={setDays}
          onOpenDevice={setInlineMac}
        />
      )}

      <DeviceDrawer
        mac={mac}
        devices={allDevices}
        network={network}
        onClose={() => {
          // Close the way it was opened: an inline drawer just closes, a
          // routed one returns to the list it came from.
          if (inlineMac !== null) setInlineMac(null);
          else navigate("/assets/devices");
        }}
      />

      <Drawer open={factsFor !== null} onClose={() => setFactsFor(null)} title={factsFor?.title ?? ""}>
        <dl className="g-facts">
          {factsFor &&
            factRows(factsFor.record).map((row) => (
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

export default Assets;
