import {DataNotice} from "../components/DataNotice";
import { lazy, Suspense, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, Page, PageHeader, StatStrip, Verdict, useToast } from "../design";
import type { PageAction, SkeletonTone as Tone, StatItem } from "../design";
import ProtectionPanel from "../components/ProtectionPanel";
import { AttackerMethod } from "../components/AttackerMethod";
import { CheckDetailDrawer } from "../components/CheckDetailDrawer";
import { CheckList } from "../components/CheckList";
import { EmailAuth, ExternalPorts, FixLog, RogueChecks } from "../components/SecurityPanels";
import { FirewallProposals } from "../components/FirewallProposals";
import { Domains, ExtraChecks, Heartbeats } from "../components/WatchList";
import { EmailCheck } from "../components/EmailCheck";
import { ExposureTiles } from "../components/ExposureTiles";
import { NetworkChips } from "../components/NetworkChips";
import { ScorecardGrid } from "../components/ScorecardGrid";
import { VulnScanPanel } from "../components/VulnScanPanel";
import { WifiGuardPanel } from "../components/WifiGuardPanel";
import { ConfirmDialog } from "./Settings/shared";
import { atLeast, roleOf, useMe } from "../lib/auth";
import type { Ack, CheckRef, FixOutcome } from "../lib/api";
import { checkHandoff, queueStatus, requestFix, requestFixOrConfirm, requestRun, securityPayload, updatesPayload,
  setAttackerMethod, threatDetail } from "../lib/api";
import { useLiveFeed } from "../lib/live";
import { useSelectedNetwork } from "../lib/network";
import { useChatHandoff } from "../shell/chat/handoff";
import type { Attacker, CheckDetail, Deduction, FixConfirmation, Scorecard, SecurityCheck, SecurityPayload,
  WifiGuard } from "../types";
import { sectionId, useSectionAnchor } from "../lib/section";

const EMPTY_SECURITY: SecurityPayload = {
  posture_coverage: { scored: 0, total: 0, unscored: [] },
  scorecards: [],
  wifi: {
    verdict: "",
    state: "unknown",
    ssid: "",
    security: "",
    channel: null,
    phy: "",
    signal: null,
    evil_twin: false,
    twin_channels: [],
    rogue_dhcp: false,
    dhcp_server: "",
    expected_gateway: "",
    default_gateway: "",
    gateway_mac: "",
    unknown_devices: 0,
    rows: [],
    network: null,
  },
  checks: [],
  attackers: [],
  exposure: [],
  attacker_method: "",
  methods: [],
  networks: [],
  wifis: [],
  extra_checks: [],
  heartbeats: [],
  domains: [],
  email: [],
  ext_ports: [],
  fix_log: [],
  protection: { defences: [], defences_host: "", blocked: [], blocked_total: 0, recent: [], filtering: false, days: 7, threats: [] },
};

const ADMIN_TOOLTIP = "admin role required";

// `AttackMap` pulls in the bundled 110m world atlas (`assets/world-110m.ts`,
// ~436KB) purely to draw country outlines — the heaviest single import on
// this page by far. Lazy-loading it puts the atlas in its own chunk so every
// other route stops paying for it in the main bundle.
const AttackMap = lazy(() => import("../components/AttackMap"));

/** Polls `GET /api/v1/queue/{id}` every 5s until a queued fix reaches a
 *  terminal state, then reports it and stops — same pattern as Inbox's
 *  recommendation-apply flow (Task 25). */
function QueuePoller({ id, onSettled }: { id: string; onSettled: (status: "done" | "failed" | "missed" | "cancelled") => void }) {
  const query = useQuery({
    queryKey: ["queue", id],
    queryFn: () => queueStatus(id),
    refetchInterval: 5_000,
  });
  useEffect(() => {
    if (query.data?.status === "done" || query.data?.status === "failed" || query.data?.status === "missed" || query.data?.status === "cancelled") {
      onSettled(query.data.status);
    }
  }, [query.data?.status, onSettled]);
  return null;
}

export function Security() {
  // `/<tab>/<section>` scrolls that panel into view — see lib/section.ts.
  useSectionAnchor();
    const toast = useToast();
  const queryClient = useQueryClient();
  const live = useLiveFeed();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  // Both the scorecard "Fix now" and the attack map's "Block this /24" apply
  // a real fix through the sensor's fix queue — the hub itself gates
  // `POST /api/v1/fix` at admin (see `hub/guardian_hub/routers/write.py`), so
  // an operator must see the same buttons disabled here, not just get a 403
  // on click. Same bar as the attacker-method select.
  const canFix = atLeast(role, "admin");
  const isAdmin = atLeast(role, "admin");
  // "Leave it" / "undo" is ack-shaped, not fix-shaped — gated the same as
  // acking an alert (`hub/guardian_hub/routers/write.py`'s `OPERATOR` dep on
  // `POST /alerts/{key}/{op}`), not at admin.
  const canDecide = atLeast(role, "operator");
  const canRun = atLeast(role, "operator");
  const openHandoff = useChatHandoff();

  const tenantId = meQuery.data?.active_tenant?.id ?? null;
  const [network, setNetwork] = useSelectedNetwork(tenantId);

  const securityQuery = useQuery({
    queryKey: ["security", network],
    queryFn: () => securityPayload(network),
  });
  const payload = securityQuery.data ?? EMPTY_SECURITY;

  // One panel per network. `wifis` is the full per-network list; `wifi` stays
  // the primary network's guard so a hub (or a fixture) that predates the
  // list still renders exactly one panel — task 51 §4.
  //
  // Nothing at all until the payload has actually arrived: `EMPTY_SECURITY`'s
  // `wifi` is an all-zero guard, and rendering it draws a panel that FAILS the
  // SSID/encryption check and claims "every device is recognized" — four
  // confident statements about a network we have not heard from yet. A
  // reassuring or alarming lie during load is worse than a blank card.
  const guards: WifiGuard[] =
    securityQuery.data === undefined
      ? []
      : payload.wifis.length > 0
        ? payload.wifis
        : [payload.wifi];

  // `NetworkHealth` carries no `is_primary` flag; the hub documents the list
  // as primary-first (task 51 §3 `health()`), which is the only handle the UI
  // has on "which network does a tenant-level LAN scorecard belong to".
  const primaryNetwork = payload.networks[0] ?? null;

  const [queueIds, setQueueIds] = useState<Record<string, { id: string; label: string }>>({}); // target -> queued fix
  // The disruptive-fix confirmation currently on screen. The hub minted it,
  // bound to this exact host + action + user, and it is single-use; the dialog
  // shows the hub's own sentence, never one written here.
  const [pendingFix, setPendingFix] = useState<
    { confirmation: FixConfirmation; label: string; target: string; arg: string | null } | null
  >(null);
  const [method, setMethod] = useState<string | null>(null);
  // The Wi-Fi guard / checklist / posture drill-down (owner request:
  // clicking a failing check like "evil twin" should open detail and offer
  // resolve or leave it). `null` closes the drawer.
  const [checkRef, setCheckRef] = useState<CheckRef | null>(null);

  // Which threat class has its drawer open, and that class's detail. Held here
  // rather than inside `ProtectionPanel` so that component stays
  // presentational -- `protection.test.tsx` renders it with no query client,
  // and keeping that true is worth a prop.
  //
  // `""` closes: `ThreatMatrix` reports the empty key on close, so there is one
  // way in and one way out rather than a separate handler to keep in step.
  const [openThreat, setOpenThreat] = useState<string>("");
  // Item 29: "am I patched" is a security question, and Security never
  // answered it -- the whole update picture lived one tab away.
  const patchQuery = useQuery({ queryKey: ["updates"], queryFn: updatesPayload });

  const threatQuery = useQuery({
    queryKey: ["threat-detail", openThreat],
    queryFn: () => threatDetail(openThreat),
    enabled: openThreat !== "",
  });

  useEffect(() => {
    return live.on("alert", () => {
      void queryClient.invalidateQueries({ queryKey: ["security"] });
    });
  }, [live, queryClient]);

  // The invalidations below stay on the bare `["security"]` prefix on
  // purpose: every network's copy of this page's data is stale after a write,
  // not just the one currently selected.

  // Cancel the page's own in-flight `["security"]` GET before writing, then
  // wait for the write to land before refetching — otherwise
  // `invalidateQueries` dedupes onto a fetch already in flight from
  // mount/poll and the write can be overwritten by data captured before it
  // landed.
  async function cancelSecurity() {
    await queryClient.cancelQueries({ queryKey: ["security"] });
  }

  async function invalidate() {
    await queryClient.invalidateQueries({ queryKey: ["security"], refetchType: "all" });
  }

  /** Reads a queue id out of an ack whose `queue_id` may come back as a
   *  string or a number, depending on which write route produced it. */
  function queueIdOf(ack: Ack): string | null {
    const queueId = ack.queue_id;
    return typeof queueId === "string" ? queueId : typeof queueId === "number" ? String(queueId) : null;
  }

  const fixMutation = useMutation({
    // The hub's `FixIn` is `{host, action, arg}` (`hub/guardian_hub/routers/
    // write.py`). A scorecard only ever carries a non-empty `fix` for a
    // `kind: "host"` card (`payloads/security.py` always sets `fix: ""` for
    // "site"/"lan" scorecards) — `target` is that host's name, so it is the
    // fix's `host` regardless of kind, and `fix` is the `action`.
    mutationFn: (scorecard: Scorecard) => requestFixOrConfirm(scorecard.target, scorecard.fix),
    onMutate: cancelSecurity,
    onSuccess: async (outcome: FixOutcome, scorecard) => {
      // A disruptive repair (`disable_ssh_password`, say) is refused by the hub
      // until this exact proposal is confirmed — see `pendingFix` below.
      if (outcome.confirmation !== null) {
        setPendingFix({ confirmation: outcome.confirmation, label: "Fix", target: scorecard.target,
                        arg: null });
        return;
      }
      const id = queueIdOf(outcome.ack);
      if (id) {
        setQueueIds((prev) => ({ ...prev, [scorecard.target]: { id, label: "Fix" } }));
        toast.show("Fix queued", "plain");
      } else {
        toast.show("Fix applied", "good");
        await invalidate();
      }
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  // The drawer's "Resolve" for a check Vault can repair itself
  // (`detail.remedy.action` non-empty) — same propose-and-confirm flow as
  // `fixMutation` above, generalised to any host/action rather than only a
  // scorecard's own single best fix.
  const checkFixMutation = useMutation({
    mutationFn: ({ host, action }: { host: string; action: string }) => requestFixOrConfirm(host, action),
    onMutate: cancelSecurity,
    onSuccess: async (outcome: FixOutcome, { host }) => {
      if (outcome.confirmation !== null) {
        setPendingFix({ confirmation: outcome.confirmation, label: "Resolve", target: host, arg: null });
        return;
      }
      const id = queueIdOf(outcome.ack);
      if (id) {
        setQueueIds((prev) => ({ ...prev, [host]: { id, label: "Resolve" } }));
        toast.show("Fix queued", "plain");
      } else {
        toast.show("Fix applied", "good");
        await invalidate();
      }
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  // The drawer's "Resolve" for a check Vault cannot repair itself — hand
  // off to chat with Vault's own explanation, exactly the way the
  // inbox's `offerRemedy` already does for an alert only a person can fix.
  const checkHandoffMutation = useMutation({
    mutationFn: (ref: CheckRef) => checkHandoff(ref),
    onSuccess: (handoff) => openHandoff(handoff),
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function resolveCheck(detail: CheckDetail, ref: CheckRef) {
    setCheckRef(null);
    if (detail.remedy.action) {
      checkFixMutation.mutate({ host: detail.remedy.host, action: detail.remedy.action });
    } else {
      checkHandoffMutation.mutate(ref);
    }
  }

  function openCheck(check: SecurityCheck, network?: string) {
    setCheckRef({ key: check.key, network, target: check.target });
  }

  function openDeduction(deduction: Deduction) {
    if (deduction.key) setCheckRef({ key: deduction.key });
  }

  // Same queue + poll flow as `fixMutation` above — this used to toast
  // "Subnet blocked" straight off the `POST /api/v1/fix` response, which
  // claimed success before the sensor had actually run the block. It also
  // used to post `{action, ip}`, missing the `host` the hub's `FixIn`
  // requires — `attacker.host` (`payloads/security.py`'s `attackers()`
  // always populates it) is the host whose subnet is being blocked; the ip
  // is the fix's `arg`.
  const blockMutation = useMutation({
    mutationFn: (attacker: Attacker) => requestFixOrConfirm(attacker.host, "block_subnet", attacker.ip),
    onMutate: cancelSecurity,
    onSuccess: async (outcome: FixOutcome, attacker) => {
      // Blocking a whole /24 is in the hub's disruptive set, so this always
      // comes back as a confirmation to agree to rather than a queued job.
      if (outcome.confirmation !== null) {
        setPendingFix({ confirmation: outcome.confirmation, label: "Block", target: attacker.ip,
                        arg: attacker.ip });
        return;
      }
      const id = queueIdOf(outcome.ack);
      if (id) {
        setQueueIds((prev) => ({ ...prev, [attacker.ip]: { id, label: "Block" } }));
        toast.show("Block queued", "plain");
      } else {
        toast.show("Subnet blocked", "good");
        await invalidate();
      }
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const methodMutation = useMutation({
    mutationFn: (next: string) => setAttackerMethod(next),
    onMutate: async (next: string) => {
      await cancelSecurity();
      setMethod(next);
    },
    onError: (err: Error) => {
      setMethod(null);
      toast.show(err.message, "alert");
    },
    onSettled: () => invalidate(),
  });

  function settleQueue(target: string, status: "done" | "failed" | "missed" | "cancelled") {
    const label = queueIds[target]?.label ?? "Fix";
    setQueueIds((prev) => {
      const next = { ...prev };
      delete next[target];
      return next;
    });
    toast.show(status === "done" ? `${label}: command completed. Check recovery in Activity.` : `${label}: ${status}`, status === "done" ? "plain" : "alert");
    invalidate();
  }

  const pendingTargets = new Set(Object.keys(queueIds));

  /** Spend the confirmation the reader just agreed to. Cancelling drops the
   *  token unspent — it expires on its own and nothing was queued. */
  async function confirmPendingFix() {
    const pending = pendingFix;
    setPendingFix(null);
    if (pending === null) return;
    // `arg` is carried from the call that minted the confirmation, never
    // re-derived: the token is bound to the exact (host, action, arg) triple,
    // so a guess that ever stopped matching would be refused rather than run.
    const { confirmation, label, target, arg } = pending;
    await cancelSecurity();
    try {
      const ack = await requestFix(confirmation.host, confirmation.action, arg, confirmation.token);
      const id = queueIdOf(ack);
      if (id) {
        setQueueIds((prev) => ({ ...prev, [target]: { id, label } }));
        toast.show(`${label} queued`, "plain");
      } else {
        toast.show(`${label} applied`, "good");
        await invalidate();
      }
    } catch (err) {
      toast.show((err as Error).message, "alert");
    }
  }

  if (securityQuery.isPending) return <p role="status">Loading security…</p>;
  if (securityQuery.isError && !securityQuery.data) return <div role="alert">Could not load security. <button onClick={() => void securityQuery.refetch()}>Retry</button></div>;
  const coverage = payload.posture_coverage;
  const postureSubtitle = coverage.total > 0
    ? `Networks and servers Vault scores, ${coverage.scored} of ${coverage.total} covered. Check freshness and network coverage separately below.`
    : "Networks and servers Vault scores. Check freshness and network coverage separately below.";
  const failing = payload.checks.filter((c) => c.passed === false && c.decision?.status !== "left");
  const left = payload.checks.filter((c) => c.passed === false && c.decision?.status === "left").length;
  const unverified = payload.checks.filter((c) => c.passed === null).length;
  const worstGrade = payload.scorecards.reduce<string | null>((w, s) => (w === null || s.grade > w ? s.grade : w), null);
  const secTone: Tone = failing.length > 0 ? (failing.length >= 3 ? "alert" : "warn") : "good";
  const secHeadline = failing.length > 0
    ? `${failing.length} check${failing.length === 1 ? "" : "s"} need${failing.length === 1 ? "s" : ""} a look`
    : "Nothing needs you";
  const secBody = failing.length > 0
    ? failing.slice(0, 3).map((c) => c.title).join(" · ")
    : left > 0
      ? `${left} finding${left === 1 ? "" : "s"} deliberately left as-is; everything else passes.`
      : "Every check Vault runs is passing.";
  const secStats: StatItem[] = [
    { label: `Threats blocked · ${payload.protection.days}d`, value: String(payload.protection.blocked_total), deltaTone: "good" },
    { label: "Open findings", value: String(failing.length), deltaTone: failing.length > 0 ? "warn" : undefined },
    { label: "Checks", value: String(payload.checks.length), delta: unverified > 0 ? `${unverified} unverified` : undefined, deltaTone: unverified > 0 ? "warn" : undefined },
    { label: "Worst grade", value: worstGrade ?? "—" },
  ];
  const secActions: PageAction[] = [
    { label: "Run a scan", primary: true, disabled: !canRun, title: canRun ? "Queue a scan of every network" : "operator role required",
      onClick: () => { void requestRun("scan", "").then(() => toast.show("Scan queued", "plain")).catch((e: Error) => toast.show(e.message, "alert")); } },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Network · what is being stopped"
        title="Security"
        sub="What Vault refused, what it checks, and what it does about each kind of attack."
        actions={secActions}
        extra={<NetworkChips networks={payload.networks} value={network} onChange={setNetwork} />}
      />
      <Verdict tone={secTone} headline={secHeadline} body={secBody} />
      <StatStrip items={secStats} />
    <div className="g-security">
      <DataNotice query={securityQuery} />

      {/* High on the page on purpose: "am I protected from viruses and the
          dark web" is the question people arrive at this tab asking, and it
          used to have no answer anywhere in the app. */}
      <Card
        id={sectionId("protection")}
        title="Threats and protection"
        subtitle="What Vault refused before it reached a device, whether this machine is defending itself, and what it does about each kind of attack."
      >
        <ProtectionPanel
          data={payload.protection}
          onOpenThreat={setOpenThreat}
          threat={threatQuery.data ?? null}
        />
      </Card>

      {/* Item 29: patch state belongs on the security page, counted across
          everything Vault can read -- and honest about what it cannot.
          A total that silently skipped the phones and cameras would imply
          an all-clear Vault has not earned. */}
      {patchQuery.data && (
        <Card id={sectionId("patching")} title="Patch state"
              subtitle="Updates waiting across everything Vault can read a package list from.">
          <ul className="g-sec-patch" aria-label="Patch state">
            <li>
              <span className="g-sec-patch-n g-tone-alert">{patchQuery.data.posture.security_pending}</span>
              <span className="g-sec-patch-l">Security updates waiting</span>
            </li>
            <li>
              <span className="g-sec-patch-n">{patchQuery.data.posture.pending}</span>
              <span className="g-sec-patch-l">Updates in total</span>
            </li>
            <li>
              <span className="g-sec-patch-n">{patchQuery.data.posture.needs_reboot}</span>
              <span className="g-sec-patch-l">Waiting on a restart</span>
            </li>
            <li>
              <span className="g-sec-patch-n">{patchQuery.data.posture.servers_checked}</span>
              <span className="g-sec-patch-l">Servers read</span>
            </li>
          </ul>
          <p className="hint">
            {patchQuery.data.posture.servers_unchecked > 0 && (
              <>
                <strong>{patchQuery.data.posture.servers_unchecked}</strong>{" "}
                {patchQuery.data.posture.servers_unchecked === 1 ? "server was" : "servers were"} not
                reached on the last sweep, so nothing above speaks for{" "}
                {patchQuery.data.posture.servers_unchecked === 1 ? "it" : "them"}.{" "}
              </>
            )}
            {patchQuery.data.posture.unmeasured > 0 && (
              <>
                A further <strong>{patchQuery.data.posture.unmeasured}</strong> devices — phones,
                cameras, anything without a package list Vault can read — are watched on the
                network but cannot be counted here. They are not included in the totals.
              </>
            )}
          </p>
        </Card>
      )}

      <Card id={sectionId("scorecards")} title="Security posture"
            subtitle={postureSubtitle}>
        {/* Item 05: a target Vault has stopped hearing from is left out
            of the scores on purpose -- a card stamped "verified now" for an
            asset nothing looked at is worse than no card. Leaving it out
            SILENTLY was the bug: the client counts the cards and assumes
            that is the estate. */}
        {coverage.unscored.length > 0 && (
          <p className="g-sec-posture-gap" role="status">
            <strong>{coverage.scored} of {coverage.total} scored.</strong>{" "}
            Vault has not checked {coverage.unscored.join(", ")} recently, so
            {coverage.unscored.length === 1 ? " it has" : " they have"} no score rather
            than a stale one. A machine that has gone quiet is worth asking about.
          </p>
        )}
        <ScorecardGrid
          scorecards={payload.scorecards}
          onFix={(scorecard) => fixMutation.mutate(scorecard)}
          onOpenDeduction={(deduction) => openDeduction(deduction)}
          pending={pendingTargets}
          canWrite={canFix}
          writeTooltip={ADMIN_TOOLTIP}
          lanNetwork={primaryNetwork}
        />
      </Card>

      {/* The manual scan: the scheduled sweep watches everything shallowly;
          this points at one target and looks hard, when the owner asks. */}
      <Card id={sectionId("vuln-scans")} title="Vulnerability scans" subtitle="On demand, one target at a time — ports, services, TLS, headers, and on servers what only root can see.">
        <VulnScanPanel canWrite={canFix} isAdmin={isAdmin} />
      </Card>

      {/* The checklist is the proof behind the posture card above it: every
          deduction there names a `key` that resolves to a row in here, so
          the two read as one argument (score, then the evidence for it)
          rather than two lists a reader has to reconcile themselves. */}
      <Card id={sectionId("checklist")} title="Checks">
        <CheckList checks={payload.checks} onOpen={(check) => openCheck(check)} />
      </Card>

      <Card id={sectionId("wifi-guard")} title="Wi-Fi guard">
        {guards.length === 0 ? (
          <p className="g-asset-empty">{securityQuery.isError ? "Wi-Fi guard is unavailable." : "Checking the network…"}</p>
        ) : (
          guards.map((guard, i) => (
            <WifiGuardPanel
              key={guard.network?.id ?? `wifi-${i}`}
              wifi={guard}
              onOpenCheck={(check) => openCheck(check, guard.network?.id)}
            />
          ))
        )}
      </Card>

      <Card
        title="Rogue network checks"
        subtitle="Who is handing out addresses, where traffic actually leaves, and the MAC answering as the router."
      >
        {guards.length === 0 ? (
          <p className="g-asset-empty">Waiting for the first reading from this network.</p>
        ) : (
          <RogueChecks wifi={guards[0]} />
        )}
      </Card>

      <Card id={sectionId("attack-map")} title="Attack map">
        <Suspense fallback={<p className="g-asset-empty">Loading attack map…</p>}>
          <AttackMap
            attackers={payload.attackers}
            onBlock={(attacker) => blockMutation.mutate(attacker)}
            canWrite={canFix}
            writeTooltip={ADMIN_TOOLTIP}
            pending={pendingTargets}
            attackerMethod={method ?? payload.attacker_method}
          />
        </Suspense>
      </Card>

      <Card title="Exposure">
        <ExposureTiles tiles={payload.exposure} />
      </Card>

      <Card id={sectionId("checklist-extra")} title="Custom checks"
            subtitle="The TCP, ping and DNS probes configured beyond the website checks.">
        <ExtraChecks checks={payload.extra_checks} />
      </Card>

      <Card title="Dead-man heartbeats"
            subtitle="Jobs that must call in on a cadence. Silence is the alert.">
        <Heartbeats heartbeats={payload.heartbeats} canReveal={canFix} />
      </Card>

      <Card title="Domain registrations">
        <Domains domains={payload.domains} />
      </Card>

      <Card
        title="Email spoofing protection"
        subtitle="SPF, DKIM and DMARC per monitored domain. Protection depends on whether the domain sends mail; see the records and recommendations below."
      >
        <EmailAuth rows={payload.email} />
        {/* Beside the domains Vault watches, because it answers the
            other half of the same question: those rows say whether YOUR
            mail can be forged, this says whether a letter you just received
            can be trusted. */}
        <EmailCheck />
      </Card>

      <Card
        title="Site hosts seen from the internet"
        subtitle="What answers from outside, probed from a server rather than from in here."
      >
        <ExternalPorts rows={payload.ext_ports} />
        {/* Item 21: directly under what is reachable from outside, because
            the rules are derived from exactly those findings and reading
            them apart would hide where they came from. */}
        <FirewallProposals />
      </Card>

      <Card title="Fix log" subtitle="Every repair Vault ran, automatic and asked for.">
        <FixLog rows={payload.fix_log} />
      </Card>

      <Card title="Attacker handling">
        <AttackerMethod
          value={method ?? payload.attacker_method}
          methods={payload.methods}
          onChange={(next) => methodMutation.mutate(next)}
          canWrite={canFix}
          writeTooltip={ADMIN_TOOLTIP}
        />
      </Card>

      <ConfirmDialog
        open={pendingFix !== null}
        title={pendingFix === null ? "" : `Run ${pendingFix.confirmation.action.replace(/_/g, " ")}?`}
        body={pendingFix?.confirmation.summary ?? ""}
        confirmLabel={pendingFix?.label ?? "Confirm"}
        onConfirm={() => void confirmPendingFix()}
        onCancel={() => setPendingFix(null)}
      />

      <CheckDetailDrawer
        checkRef={checkRef}
        onClose={() => setCheckRef(null)}
        canResolveFix={canFix}
        resolveFixTooltip={ADMIN_TOOLTIP}
        canDecide={canDecide}
        decideTooltip="operator role required"
        onResolveFix={resolveCheck}
        onResolveChat={resolveCheck}
      />

      {Object.entries(queueIds).map(([target, { id }]) => (
        <QueuePoller key={id} id={id} onSettled={(status) => settleQueue(target, status)} />
      ))}
    </div>
    </Page>
  );
}

export default Security;
