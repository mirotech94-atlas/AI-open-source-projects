import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { generateEnrolCode } from "../api";
import { EmptyState } from "../components/EmptyState";
import {
  createNetwork, devicesPayload, networksPayload, onboardingState, saveOnboarding,
  saveSettingsSection, updateDevice,
} from "../lib/api";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { useLiveFeed } from "../lib/live";
import { installCommand, PLATFORM_LABEL, PLATFORM_SERVICE, PLATFORMS } from "../lib/install";
import type { Platform } from "../lib/install";
import type { DeviceCard } from "../types";

type Step = "name" | "networks" | "sensor" | "waiting" | "label" | "done";

// Step 2 ("networks") comes BEFORE the install step on purpose: the enrol
// command the wizard then shows is only useful once the hub knows which
// networks exist, so the sensor's first push has something to attribute rows
// to. A step name persisted by an older build ("sensor", "waiting", "label")
// is still in this list, so a half-finished wizard resumes rather than
// restarting.
const STEP_ORDER: Step[] = ["name", "networks", "sensor", "waiting", "label", "done"];

/** One row of the "which networks?" step. `id` is set for a network the hub
 *  already knows about (the primary it created from the tenant's config) —
 *  those are left alone, only the rows the operator added are written. */
type NetworkDraft = {
  id: string | null;
  name: string;
  subnet_prefix: string;
  expected_gateway: string;
  // Never auto-detected (spec §5): a wrong guess here would show a customer
  // their neighbour's traffic, so the wizard asks and defaults to the answer
  // that assumes nothing rather than inferring one.
  traffic_mode: "mirror" | "router";
};

const BLANK_NETWORK: NetworkDraft = {
  id: null,
  name: "Home network",
  subnet_prefix: "192.168.1",
  expected_gateway: "192.168.1.1",
  traffic_mode: "router",
};

// The `localStorage` step cursor that used to live here is gone. It could only
// ever describe one network at one stage, and a copy of the answer is a copy
// that can go stale against the rows it copied. `SetupWizard` derives the step
// from `GET /api/v1/onboarding` instead.

function minutesLeft(expires: string): number {
  const ms = new Date(expires).getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / 60_000));
}

/** Leaving the wizard: record it, then go.
 *
 *  Both halves matter, and a fire-and-forget POST gets both wrong. The save is
 *  AWAITED because `Shell` re-reads `/api/v1/onboarding` the moment it mounts on
 *  `/overview`, and a read that beats the write sees `show: true` and bounces
 *  the user straight back into the wizard. The hub's fresh answer is then
 *  written into the query cache, because `Shell` and this page share the
 *  `["onboarding"]` key at `staleTime: Infinity` — a stale cached `show: true`
 *  would bounce them back just as surely as the race would.
 *
 *  A failed save still leaves: being trapped in the wizard by an offline hub is
 *  worse than seeing it again on the next load.
 */
function useLeaveWizard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return async function leave(body: { completed?: boolean; skipped?: boolean }) {
    try {
      const state = await saveOnboarding(body);
      queryClient.setQueryData(["onboarding"], state);
    } catch {
      // Best-effort; see above.
    }
    navigate("/overview", { replace: true });
  };
}

/** The INSTALLING walkthrough (`OnboardingState.path === "setup"`): name the
 *  site, declare the networks, install the sensor, wait for its first scan,
 *  label whatever it found unverified. This is the path for a site where
 *  nothing has ever reported — a newcomer joining a site that already works
 *  gets `TourWizard` below instead, which asks them to install nothing.
 *
 *  Deliberately chrome-free — no `<Shell>` sidebar/top bar — and calls
 *  `useLiveFeed()` itself rather than `Shell`'s `useLive()`, since `/welcome`
 *  sits outside `<Shell>`'s route tree in `App.tsx` and a fresh tenant reaches
 *  this page before there is anything else worth showing. */
function SetupWizard() {
  const leave = useLeaveWizard();
  // `leave` routes to the overview when it finishes. Setting up DNS means
  // going somewhere else instead, so this runs after it and wins.
  const navigate = useNavigate();
  const live = useLiveFeed();
  const meQuery = useMe();
  const tenantId = meQuery.data?.active_tenant?.id ?? null;
  // `POST /api/v1/networks` is admin-only on the hub. A non-admin first-run
  // still walks the wizard — it just cannot write the networks, and is told
  // so instead of being handed a 403 on "Next".
  const canWriteNetworks = atLeast(roleOf(meQuery.data), "admin");

  // What the hub says is outstanding. This is the authority for which screen
  // the install half of the wizard shows -- see `stepFor` below.
  const stateQuery = useQuery({
    queryKey: ["onboarding", "state"],
    queryFn: onboardingState,
    retry: false,
  });

  const [step, setStep] = useState<Step>("name");
  const [siteName, setSiteName] = useState("");
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [showTroubleshoot, setShowTroubleshoot] = useState(false);
  const [networkDrafts, setNetworkDrafts] = useState<NetworkDraft[] | null>(null);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const resumedRef = useRef(false);

  // Resume once the active tenant id is known — never before, or a resumed
  // step from a previous tenant could flash for a tick under the new one.
  // `tenantId` only ever becomes non-null once, on `useMe()`'s first
  // successful fetch, so this genuinely synchronizes with that external
  // (network-backed) value rather than deriving something render already
  // has — the case oxlint's rule means to catch.
  //
  // WHY THIS IS NOT A STORED CURSOR ANY MORE
  //
  // It used to resume from a step name in `localStorage`, backed by a copy on
  // the hub. That works for one network and one sensor, and it cannot describe
  // what a practice with three LANs behind one WAN actually has: two sensors
  // enrolled and one not, one reporting and one silent.
  //
  // Worse than incomplete, a cursor can CONTRADICT the rows -- saying "install
  // a sensor" while two are already pushing scans -- and a customer told to do
  // something they have already done calls support.
  //
  // So the hub answers what is outstanding and this maps that answer onto a
  // screen. The install half (name/networks/sensor/waiting) is derived, because
  // it is the half that can disagree. The post-scan tail (label/dns/done) stays
  // local: once a scan has landed those steps are genuinely linear and carry no
  // tenant state to be wrong about.
  const derived = stateQuery.data?.next ?? null;
  const siteNamed = stateQuery.data?.site_named ?? false;
  const stepFor = useMemo<Step | null>(() => {
    if (!stateQuery.data) return null;
    if (!derived) return null;                       // nothing outstanding
    if (derived.action === "declare_network") return siteNamed ? "networks" : "name";
    if (derived.action === "enrol_sensor") return "sensor";
    if (derived.action === "await_first_scan") return "waiting";
    // `offer_dns` means every network has reported, so the scan the customer
    // was waiting for has landed: they belong on the post-scan screen. Landing
    // them back on "name" because the tail is local was the hole in deriving
    // only part of the flow.
    return "label";
  }, [stateQuery.data, derived, siteNamed]);

  useEffect(() => {
    if (resumedRef.current || tenantId === null || stepFor === null) return;
    resumedRef.current = true;
    // oxlint-disable-next-line react/set-state-in-effect
    setStep(stepFor);
  }, [tenantId, stepFor]);

  /** The network whose first scan just landed, for the post-scan summary. The
   *  first that has reported -- a tenant part-way through its second network
   *  still has a first one worth summarising. */
  const summary = useMemo(
    () => (stateQuery.data?.networks ?? []).find((n) => n.first_scan_at) ?? null,
    [stateQuery.data],
  );

  /** The network the derived step is about, for copy that has to name it. */
  const activeNetwork = useMemo(
    () => (stateQuery.data?.networks ?? []).find((n) => n.id === derived?.network_id) ?? null,
    [stateQuery.data, derived],
  );

  // The step is written both places on purpose. `localStorage` is what makes a
  // reload resume before any network round-trip; the hub copy is what makes it
  // resume on a different machine. The hub is the authority on *completion*
  // (see `lib/onboarding.ts`), and a failed step save is not worth telling
  // anyone about — the wizard still works, it just resumes a step earlier.
  useEffect(() => {
    if (tenantId === null) return;
    // No `localStorage` write any more: a local copy can only ever go stale
    // against the rows, and being stale is precisely the failure this step
    // selection was rebuilt to remove. The hub save stays -- it is the resume
    // hint for the tour path and costs nothing if it fails.
    void saveOnboarding({ step }).catch(() => {});
  }, [tenantId, step]);

  // Minted when step 2 (sensor/platform) mounts, not on step 1 — naming the
  // site shouldn't burn a 15-minute-TTL code the admin hasn't reached the OS
  // picker for yet. `enabled` only gates the *first* fetch; `staleTime:
  // Infinity` means the result then rides along for the rest of the wizard
  // (steps 3/4) without a second mint even though `enabled` goes false again
  // once `step` moves past "sensor".
  const enrolQuery = useQuery({
    queryKey: ["onboarding", "enrolCode"],
    queryFn: generateEnrolCode,
    enabled: step === "sensor",
    staleTime: Infinity,
    retry: false,
  });

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  void now; // re-renders the countdown every second; the value itself is read fresh below.

  const nameMutation = useMutation({
    // `ProfileSettings` (`types.ts`) has no dedicated "site name" field —
    // `company` is the one field of that shape a report cover already
    // prints and `settingsPayload().profile.company` reads back, so reusing
    // it is what makes step 1 actually resumable across a reload rather
    // than a purely cosmetic local echo. See the task report for the same
    // note.
    mutationFn: (name: string) => saveSettingsSection("profile", { company: name }),
    onSuccess: () => setStep("networks"),
  });

  // What the hub already knows about this tenant's networks — the primary it
  // built from the config `lan` block, if there is one. That is as close to a
  // "detected subnet" as the browser can get before a sensor has ever
  // reported, so it is what the first row is pre-filled with.
  const detectedQuery = useQuery({
    queryKey: ["onboarding", "networks"],
    queryFn: networksPayload,
    enabled: step === "networks",
    staleTime: Infinity,
    retry: false,
  });

  const drafts: NetworkDraft[] =
    networkDrafts ??
    (detectedQuery.data && detectedQuery.data.networks.length > 0
      ? detectedQuery.data.networks.map((n) => ({
          id: n.id,
          name: n.name,
          subnet_prefix: n.subnet_prefix,
          expected_gateway: n.expected_gateway,
          // Already-set-up rows (`id !== null`) are display-only here --
          // `submitNetworks` never writes them back -- so this only needs to
          // be a legal value, not a faithful read of "none".
          traffic_mode: n.traffic_mode === "mirror" ? "mirror" : "router",
        }))
      : [BLANK_NETWORK]);

  function editDraft(index: number, patch: Partial<NetworkDraft>) {
    setNetworkDrafts(drafts.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  const networksMutation = useMutation({
    mutationFn: async (rows: NetworkDraft[]) => {
      // One POST per network the operator added; the hub's own primary is
      // left as it is (renaming it belongs in Settings → Networks, which can
      // explain that the slug is frozen).
      for (const row of rows) {
        await createNetwork({
          name: row.name.trim(),
          kind: "wifi",
          subnet_prefix: row.subnet_prefix.trim(),
          expected_gateway: row.expected_gateway.trim(),
          ssid: "",
          colour: "",
          traffic_mode: row.traffic_mode,
        });
      }
    },
    onSuccess: () => {
      setNetworkError(null);
      setStep("sensor");
    },
    onError: (err: Error) => setNetworkError(err.message),
  });

  function submitNetworks() {
    const fresh = drafts.filter((row) => row.id === null && row.name.trim() !== "");
    if (!canWriteNetworks || fresh.length === 0) {
      setStep("sensor");
      return;
    }
    networksMutation.mutate(fresh);
  }

  function submitName(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = siteName.trim();
    if (!trimmed) return;
    nameMutation.mutate(trimmed);
  }

  // Advance out of "waiting" the moment the sensor's first scan lands, live —
  // no polling needed, the whole point of this step is the WS frame. The
  // `setStep` runs inside the live feed's own callback, not synchronously in
  // the effect body — textbook "synchronize with an external system", the
  // case the rule's own help text carves out.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => {
    if (step !== "waiting") return;
    return live.on("scan", () => setStep("label"));
  }, [step, live]);

  const devicesQuery = useQuery({
    queryKey: ["onboarding", "devices"],
    queryFn: () => devicesPayload(),
    enabled: step === "label",
  });

  const unverified = useMemo(() => {
    const groups = devicesQuery.data?.groups ?? [];
    // Unverified first. Those are the devices where a name from a person adds
    // something `kind_of` could not infer; one already reading as "Reolink
    // camera" needs no help, so it should not be at the top of the list.
    const all = groups.flatMap((g) => g.devices);
    return [...all.filter((d) => d.unverified), ...all.filter((d) => !d.unverified)];
  }, [devicesQuery.data]);

  const trustMutation = useMutation({
    mutationFn: (mac: string) => updateDevice(mac, { trusted: true }),
    onSuccess: () => devicesQuery.refetch(),
  });

  const [labelDraft, setLabelDraft] = useState<Record<string, string>>({});
  const labelMutation = useMutation({
    mutationFn: ({ mac, label }: { mac: string; label: string }) => updateDevice(mac, { label }),
    onSuccess: () => devicesQuery.refetch(),
  });

  function finish() {
    setStep("done");
    void leave({ completed: true });
  }

  // "Skip for now" — available on every step: someone who doesn't want to
  // finish right now (or ever) isn't stuck here. Records the skip against THIS
  // user on THIS tenant, which is what stops `Shell`'s gate sending them back
  // to `/welcome`; a colleague on the same site is unaffected, and "Re-run
  // setup" in Settings → Sensors clears it.
  function skip() {
    void leave({ skipped: true });
  }

  return (
    <div className="g-onboarding">
      <a className="g-skip-link" href="#g-onboarding-step">
        Skip to wizard content
      </a>

      <header className="g-onboarding-head">
        <p className="g-onboarding-brand">Vault</p>
        <ol className="g-onboarding-steps" aria-label="Setup progress">
          {(["name", "networks", "sensor", "waiting", "label"] as Step[]).map((s, i) => (
            <li
              key={s}
              className={`g-onboarding-step-dot${step === s ? " g-onboarding-step-dot-active" : ""}${
                STEP_ORDER.indexOf(step) > i ? " g-onboarding-step-dot-done" : ""
              }`}
              aria-current={step === s ? "step" : undefined}
            >
              {i + 1}
            </li>
          ))}
        </ol>
        {step !== "done" && (
          <button type="button" className="link-button g-onboarding-skip" onClick={skip}>
            Skip for now
          </button>
        )}
      </header>

      <main id="g-onboarding-step" className="g-onboarding-card" tabIndex={-1}>
        {step === "name" && (
          <form onSubmit={submitName}>
            <h1>Let's set up Vault</h1>
            <label htmlFor="onboarding-site-name">What should we call your site?</label>
            <input
              id="onboarding-site-name"
              value={siteName}
              autoFocus
              onChange={(e) => setSiteName(e.target.value)}
              placeholder="Home"
            />
            <button type="submit" disabled={!siteName.trim() || nameMutation.isPending}>
              Next
            </button>
          </form>
        )}

        {step === "networks" && (
          <div>
            <h1>Which networks should Vault watch?</h1>
            <p>
              Each network needs a sensor that can reach it — a sensor only ever sees the network it
              is installed on. You can add more later in Settings → Networks.
            </p>

            <div className="g-onboarding-networks">
              {drafts.map((row, i) => (
                <div key={row.id ?? `new-${i}`} className="g-onboarding-network-row">
                  <label>
                    <span>Network name</span>
                    <input
                      value={row.name}
                      onChange={(e) => editDraft(i, { name: e.target.value })}
                    />
                  </label>
                  <label>
                    <span>Subnet</span>
                    <input
                      value={row.subnet_prefix}
                      placeholder="192.168.1"
                      onChange={(e) => editDraft(i, { subnet_prefix: e.target.value })}
                    />
                  </label>
                  <label>
                    <span>Gateway</span>
                    <input
                      value={row.expected_gateway}
                      placeholder="192.168.1.1"
                      onChange={(e) => editDraft(i, { expected_gateway: e.target.value })}
                    />
                  </label>
                  <label>
                    <span>How does Vault see this network's traffic?</span>
                    <select
                      value={row.traffic_mode}
                      onChange={(e) => editDraft(i, { traffic_mode: e.target.value as "mirror" | "router" })}
                    >
                      <option value="router">Not sure — skip for now</option>
                      <option value="mirror">A mirror/SPAN port on the switch feeds the sensor</option>
                    </select>
                  </label>
                  <p className="hint">
                    With a mirror port Vault can say what each device talks to. Without one it can only see how much
                    each device moves, if the router reports it. You can change this later in Settings → Networks.
                  </p>
                  {row.id !== null && <span className="hint">already set up</span>}
                </div>
              ))}
            </div>

            <button
              type="button"
              className="link-button"
              onClick={() => setNetworkDrafts([...drafts, { ...BLANK_NETWORK, name: "", subnet_prefix: "", expected_gateway: "" }])}
            >
              Add another network
            </button>

            {!canWriteNetworks && (
              <p className="hint">
                Only an admin can add networks — ask one to set them up in Settings → Networks.
              </p>
            )}

            {networkError !== null && (
              <p className="g-field-error" role="alert">
                {networkError}
              </p>
            )}

            <button type="button" disabled={networksMutation.isPending} onClick={submitNetworks}>
              Next
            </button>
          </div>
        )}

        {step === "sensor" && (
          <div>
            <h1>Install the sensor</h1>
            <p>Pick where it'll run, then copy the one-line install command.</p>
            <div className="g-onboarding-os-picker" role="group" aria-label="Sensor platform">
              {PLATFORMS.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  aria-pressed={platform === p.value}
                  onClick={() => setPlatform(p.value)}
                >
                  {PLATFORM_LABEL[p.value]}
                </button>
              ))}
            </div>

            {platform &&
              (enrolQuery.data ? (
                <div className="g-onboarding-command">
                  <pre>
                    <code>{installCommand(enrolQuery.data.code, window.location.origin)}</code>
                  </pre>
                  <p className="hint">
                    Code expires in {minutesLeft(enrolQuery.data.expires)} min. On{" "}
                    {PLATFORM_LABEL[platform]} the installer sets up {PLATFORM_SERVICE[platform]}.
                  </p>
                </div>
              ) : enrolQuery.isError ? (
                <EmptyState
                  title="Couldn't mint an enrol code."
                  body="Try again from Settings → Sensors once you're past this wizard."
                />
              ) : (
                <p className="hint">Minting your enrol code…</p>
              ))}

            <button type="button" disabled={!platform} onClick={() => setStep("waiting")}>
              Next
            </button>
          </div>
        )}

        {step === "waiting" && (
          <div>
            <h1>Waiting for the first scan…</h1>
            {/* "Never spoke" and "spoke, then stopped" need opposite advice, and
                the hub tells them apart. Sending somebody to reinstall a sensor
                that is sitting there correctly installed and unplugged is the
                most annoying thing this screen could do. */}
            {activeNetwork?.sensor === "quiet" ? (
              <div className="g-onboarding-spinner" role="status" aria-live="polite">
                <span>
                  The sensor on {activeNetwork.name} reported once and has stopped answering.
                  It is installed — something is keeping it from reaching us, so check the
                  machine is on and has internet before installing anything again.
                </span>
              </div>
            ) : (
              <div className="g-onboarding-spinner" role="status" aria-live="polite">
                <span className="g-spinner" aria-hidden="true" />
                <span>Listening for the sensor's first report — this updates the moment it arrives.</span>
              </div>
            )}
            {/* The blank minute, spent saying what is happening.
              *
              * This was a spinner and a sentence. A first scan takes about a
              * minute, and that minute is the only moment in the whole product
              * where the customer is waiting with nothing to read -- so it is
              * the cheapest place to explain what Vault is for. Written as
              * what the sensor is doing right now rather than a feature list,
              * because it IS happening: every one of these is part of the scan
              * they are waiting on. */}
            <ul className="g-onboarding-doing">
              <li>Finding everything on the network and working out what each device is.</li>
              <li>Checking whether anything answers from the internet that should not.</li>
              <li>Looking for the weaknesses attackers actually use, not a generic list.</li>
              <li>Noting what is normal here, so we can tell you when it changes.</li>
            </ul>
            <button type="button" className="link-button" onClick={() => setShowTroubleshoot((v) => !v)}>
              Still nothing?
            </button>
            {showTroubleshoot && (
              <ul className="g-onboarding-troubleshoot">
                <li>Check the sensor host has outbound internet access.</li>
                <li>Confirm the enrol code hadn't already expired when you ran the command.</li>
                <li>A container needs `--network host` (or an equivalent bridge) to see the LAN.</li>
                <li>Re-run Settings → Sensors → Generate enrol code for a fresh one.</li>
              </ul>
            )}
          </div>
        )}

        {step === "label" && (
          <div>
            <h1>Here is what we found</h1>
            {/* The summary leads, and the list follows.
              *
              * This step used to open with "Label unknown devices" and a form
              * per device, and it could not be left until the list was walked.
              * That is the step people abandon -- and none of it is required,
              * because the sensor's baseline already treats everything present
              * at the first scan as the network as it stands, so nothing here
              * is being flagged at anybody.
              *
              * What the customer is actually doing at this moment is looking at
              * their own network for the first time. The shape of it is the
              * thing to lead with. Naming stays, underneath, because this is
              * also the moment they are best able to say which laptop is
              * whose -- and a name they type is a human warrant, which is
              * exactly the thing a machine may not write for them. */}
            {summary && (
              <p className="g-onboarding-summary">
                {summary.devices} device{summary.devices === 1 ? "" : "s"} on {summary.name}
                {Object.keys(summary.by_group).length > 0 && ": "}
                {Object.entries(summary.by_group)
                  .sort((a, b) => b[1] - a[1])
                  .map(([group, n]) => `${n} ${group.toLowerCase()}`)
                  .join(", ")}
              </p>
            )}
            <p>Name anything you want to recognise later. You can skip all of this.</p>

            {devicesQuery.isLoading ? (
              <p className="hint">Loading the device list…</p>
            ) : unverified.length === 0 ? (
              <EmptyState
                title="Nothing unverified."
                body="Every device the first scan found is already trusted or recognized."
              />
            ) : (
              <ul className="g-onboarding-devices">
                {unverified.map((d: DeviceCard) => (
                  <li key={d.mac} className="g-onboarding-device-row">
                    <span className="g-onboarding-device-name">{d.name || d.vendor || d.mac}</span>
                    <input
                      aria-label={`Label for ${d.name || d.mac}`}
                      placeholder="Label"
                      value={labelDraft[d.mac] ?? d.label}
                      onChange={(e) => setLabelDraft((prev) => ({ ...prev, [d.mac]: e.target.value }))}
                    />
                    <button
                      type="button"
                      onClick={() => labelMutation.mutate({ mac: d.mac, label: labelDraft[d.mac] ?? d.label })}
                    >
                      Save label
                    </button>
                    <button type="button" onClick={() => trustMutation.mutate(d.mac)}>
                      Trust
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {/* Two ways out, and Done is one of them.
              *
              * DNS filtering is a headline reason to buy Vault and the only
              * part of setup that needs a password this customer may not have:
              * somebody has to point the router at the resolver. Making it a
              * required step would hard-stop most practices on day one, and
              * leaving it off this screen entirely means most never find it.
              *
              * So it is offered here, next to the exit, and skipping costs
              * nothing. `dns` is the hub's live signal -- traffic actually
              * flowing through the resolver, not a button someone pressed --
              * so once it works this offer stops appearing on its own. */}
            <div className="g-onboarding-finish">
              <button type="button" onClick={() => finish()}>
                Done
              </button>
              {summary && summary.dns !== "working" && (
                <button
                  type="button"
                  className="link-button"
                  onClick={async () => {
                    await leave({ completed: true });
                    navigate("/dns", { replace: true });
                  }}
                >
                  Set up DNS filtering
                </button>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}


// --------------------------------------------------------------------------
// The ORIENTING walkthrough
// --------------------------------------------------------------------------

type TourStep = "welcome" | "where" | "help";

const TOUR_ORDER: TourStep[] = ["welcome", "where", "help"];

/** Three short screens for someone joining a site that already works
 *  (`OnboardingState.path === "tour"`).
 *
 *  It is a different walkthrough, not a shortened one. The installing path
 *  exists to get a sensor reporting; there is already a sensor reporting here,
 *  and asking a newcomer to enrol a second one — or to rename the site, or to
 *  re-label devices a colleague already labelled — would be worse than showing
 *  them nothing. So this path writes nothing at all. It says what Vault is
 *  watching, where the answers live, and how to ask for more; every step is a
 *  real link into the app, and the reader can leave at any point.
 */
function TourWizard({ siteName }: { siteName: string }) {
  const [step, setStep] = useState<TourStep>("welcome");
  const leave = useLeaveWizard();

  function finish() {
    void leave({ completed: true });
  }

  function skip() {
    void leave({ skipped: true });
  }

  useEffect(() => {
    void saveOnboarding({ step }).catch(() => {});
  }, [step]);

  return (
    <div className="g-onboarding">
      <a className="g-skip-link" href="#g-onboarding-step">
        Skip to wizard content
      </a>

      <header className="g-onboarding-head">
        <p className="g-onboarding-brand">Vault</p>
        <ol className="g-onboarding-steps" aria-label="Setup progress">
          {TOUR_ORDER.map((s, i) => (
            <li
              key={s}
              className={`g-onboarding-step-dot${step === s ? " g-onboarding-step-dot-active" : ""}${
                TOUR_ORDER.indexOf(step) > i ? " g-onboarding-step-dot-done" : ""
              }`}
              aria-current={step === s ? "step" : undefined}
            >
              {i + 1}
            </li>
          ))}
        </ol>
        <button type="button" className="link-button g-onboarding-skip" onClick={skip}>
          Skip for now
        </button>
      </header>

      <main id="g-onboarding-step" className="g-onboarding-card" tabIndex={-1}>
        {step === "welcome" && (
          <div>
            <h1>Welcome to {siteName || "Vault"}</h1>
            <p>
              This site is already set up and reporting — you have nothing to install. Vault
              watches the websites, servers, devices and connection here, and tells you when
              something changes for the worse.
            </p>
            <p>
              It watches from the inside: a sensor already runs on this network and reports what it
              can see. You will see the same picture your colleagues do.
            </p>
            <button type="button" onClick={() => setStep("where")}>
              Next
            </button>
          </div>
        )}

        {step === "where" && (
          <div>
            <h1>Where the answers live</h1>
            <ul className="g-onboarding-troubleshoot">
              <li>
                <Link to="/overview">Overview</Link> — one screen for “is everything fine?”, with
                the score, what changed today, and the next thing worth doing.
              </li>
              <li>
                <Link to="/inbox?filter=alerts">Inbox</Link> — anything that needs a person.
                Vault says what is wrong and what it can do about it.
              </li>
              <li>
                <Link to="/assets/devices">Assets</Link> — every website, server and device it knows
                about, and what each one looks like right now.
              </li>
              <li>
                <Link to="/reports/builder">Reports</Link> — a shareable write-up of any period,
                built on demand or on a schedule.
              </li>
            </ul>
            <p>
              Press ⌘K anywhere to jump to a device, a site or a guide without leaving the keyboard.
            </p>
            <button type="button" onClick={() => setStep("help")}>
              Next
            </button>
          </div>
        )}

        {step === "help" && (
          <div>
            <h1>When you need a hand</h1>
            <p>
              The <Link to="/help">Help section</Link> has short guides to the things people ask
              about most — what the severities mean, how to read the score, where notifications go.
              Each one ends with a way to ask Vault about this site specifically.
            </p>
            <p>
              Vault's chat sits in the bottom-right corner of every page. Ask it plainly: “is
              everything up?”, “any new devices?”, “which alerts need me?”. It answers from the
              latest scan.
            </p>
            <button type="button" onClick={() => finish()}>
              Done
            </button>
          </div>
        )}
      </main>
    </div>
  );
}

/**
 * The first-run wizard. Which walkthrough a person gets is the hub's decision,
 * per (user, tenant): `setup` for a site where nothing has ever reported,
 * `tour` for someone joining a site that already works. See
 * `hub/guardian_hub/onboarding.py` and `shell/Shell.tsx`'s gate.
 *
 * On a hub that cannot answer, the installing path is the safe default: it is
 * the one that can still be *completed* offline-ish (every step tells you what
 * to do next), and it is what this page did before the tour existed.
 */
export function Onboarding() {
  const stateQuery = useQuery({
    queryKey: ["onboarding"],
    queryFn: onboardingState,
    staleTime: Infinity,
    retry: false,
  });
  const meQuery = useMe();
  const siteName = meQuery.data?.active_tenant?.name ?? "";

  if (stateQuery.isLoading) {
    return (
      <div className="g-onboarding">
        <p className="hint" role="status">
          Getting things ready…
        </p>
      </div>
    );
  }
  if (stateQuery.data?.path === "tour") return <TourWizard siteName={siteName} />;
  return <SetupWizard />;
}

export default Onboarding;
