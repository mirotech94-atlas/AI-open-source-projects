import {DataNotice} from "../components/DataNotice";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, Page, PageHeader, StatStrip, useToast } from "../design";
import type { StatItem } from "../design";
import { CveTable } from "../components/CveTable";
import { PatchCalendar } from "../components/PatchCalendar";
import { PendingPackages, RestartNeeded, SensorHostUpdates } from "../components/PendingPackages";
import { RebootPlanner } from "../components/RebootPlanner";
import { UpdateHosts } from "../components/UpdateHosts";
import { UpgradeProgressPanel } from "../components/UpgradeProgressPanel";
import { ConfirmDialog } from "./Settings/shared";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { planReboot, requestFix, requestFixOrConfirm, updatesPayload } from "../lib/api";
import { useLiveFeed } from "../lib/live";
import type { FixConfirmation, RebootWindow, UpdateHost, UpdatesPayload } from "../types";
import { sectionId, useSectionAnchor } from "../lib/section";

const EMPTY_UPDATES: UpdatesPayload = {
  calendar: [],
  hosts: [],
  cves: [],
  progress: [],
  posture: { pending: 0, security_pending: 0, needs_reboot: 0, servers_checked: 0, servers_unchecked: 0, unmeasured: 0 },
  reboot_plan: { hosts: [], windows: [] },
  mac: { os: "", available: [], checked: "", error: "", reported: false },
};

// The reboot planner reboots a host, and so do the apt row actions — the hub
// gates all of them admin (`POST /api/v1/fix`, see `Security.tsx`'s `canFix`),
// not operator.
const ADMIN_TOOLTIP = "admin role required";

export function Updates() {
  // `/<tab>/<section>` scrolls that panel into view — see lib/section.ts.
  useSectionAnchor();
    const queryClient = useQueryClient();
  const live = useLiveFeed();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canWrite = atLeast(role, "admin");

  const updatesQuery = useQuery({ queryKey: ["updates"], queryFn: updatesPayload });
  const payload = updatesQuery.data ?? EMPTY_UPDATES;

  // A queued fix (a scorecard "Fix now" on the Security tab, or anything
  // else that patches a host) can change the pending/CVE counts here too —
  // refetch this payload when one lands, same as Security does for `alert`.
  useEffect(() => {
    // A sweep is when apt state is re-read; a "fix" frame only covers the
    // repairs Vault itself ran.
    const offScan = live.on("scan", () => {
      void queryClient.invalidateQueries({ queryKey: ["updates"] });
    });
    const offFix = live.on("fix", () => {
      // Cancel the page's own in-flight `["updates"]` GET before refetching
      // — otherwise `invalidateQueries` dedupes onto a fetch already in
      // flight from mount/poll and the fix's effect can be overwritten by
      // data captured before it landed.
      void (async () => {
        await queryClient.cancelQueries({ queryKey: ["updates"] });
        await queryClient.invalidateQueries({ queryKey: ["updates"], refetchType: "all" });
      })();
    });
    return () => { offScan(); offFix(); };
  }, [live, queryClient]);

  const toast = useToast();
  // Hosts with a repair in flight, so a button cannot be pressed twice while
  // the sensor is still running the first one.
  const [busy, setBusy] = useState<string[]>([]);
  // The disruptive-fix confirmation currently on screen. The hub minted it,
  // bound to this exact host + action + user, and it is single-use — the
  // dialog shows the hub's own sentence, never one written here.
  const [pending, setPending] = useState<FixConfirmation | null>(null);

  async function refetchUpdates() {
    await queryClient.cancelQueries({ queryKey: ["updates"] });
    await queryClient.invalidateQueries({ queryKey: ["updates"], refetchType: "all" });
  }

  /** A row action. A reversible repair runs on this press; for a disruptive one
   *  the hub refuses to queue anything and hands back the confirmation to show
   *  — which of the two it is, is the hub's decision, not a list kept here. */
  async function startFix(host: UpdateHost, action: string) {
    setBusy((prev) => [...prev, host.name]);
    try {
      const outcome = await requestFixOrConfirm(host.name, action);
      if (outcome.confirmation !== null) {
        setPending(outcome.confirmation);
        return;
      }
      toast.show(`Queued ${action.replace(/_/g, " ")} on ${host.name}`, "plain");
      await refetchUpdates();
    } catch (err) {
      toast.show((err as Error).message, "alert");
    } finally {
      setBusy((prev) => prev.filter((n) => n !== host.name));
    }
  }

  async function confirmFix() {
    const confirmation = pending;
    setPending(null);
    if (confirmation === null) return;
    setBusy((prev) => [...prev, confirmation.host]);
    try {
      await requestFix(confirmation.host, confirmation.action, null, confirmation.token);
      toast.show(`Full upgrade queued on ${confirmation.host}`, "plain");
      await refetchUpdates();
    } catch (err) {
      toast.show((err as Error).message, "alert");
    } finally {
      setBusy((prev) => prev.filter((n) => n !== confirmation.host));
    }
  }

  const rebootMutation = useMutation({
    mutationFn: ({ host, window }: { host: string; window: RebootWindow }) => planReboot(host, window),
  });

  async function schedule(host: string, window: RebootWindow) {
    return rebootMutation.mutateAsync({ host, window });
  }

  if (updatesQuery.isPending) return <p role="status">Loading updates…</p>;
  if (updatesQuery.isError && !updatesQuery.data) return <div role="alert">Could not load updates. <button onClick={() => void updatesQuery.refetch()}>Retry</button></div>;
  const pendingTotal = payload.hosts.reduce((n, h) => n + h.pending, 0);
  const securityTotal = payload.hosts.reduce((n, h) => n + h.security_pending, 0);
  const rebootHosts = payload.hosts.filter((h) => h.reboot_required).length;
  const patched30 = payload.calendar.reduce((n, d) => n + d.patches, 0);
  const updStats: StatItem[] = [
    { label: "Pending", value: String(pendingTotal), deltaTone: pendingTotal > 0 ? "warn" : undefined },
    { label: "Security", value: String(securityTotal), deltaTone: securityTotal > 0 ? "alert" : undefined },
    { label: "Needs reboot", value: String(rebootHosts), delta: rebootHosts === 1 ? "host" : "hosts", deltaTone: rebootHosts > 0 ? "warn" : undefined },
    { label: "Patches on the calendar", value: String(patched30), deltaTone: "good" },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Network · unpatched software"
        title="Updates"
        sub="Unpatched software is the most-used way into a network. What is waiting to be installed, what needs a restart before it counts, and when patches landed."
      />
      <StatStrip items={updStats} />
    <div className="g-updates">
      <DataNotice query={updatesQuery} />
      {/* The most useful fact on this tab, said first and on its own: which
          hosts have a fix sitting installed but not yet active because
          nothing has restarted them. Everything below is when the rest of
          the queue lands and how urgent it is — this is the one line that
          answers "when will it hurt" directly. */}
      <Card title="Needs a restart" subtitle="The installed kernel update takes effect after this host restarts.">
        <RestartNeeded hosts={payload.hosts} />
      </Card>

      <Card
        title="Servers"
        subtitle="Reported package state by host. Automatic updates depend on the effective repair policy."
      >
        <UpdateHosts
          hosts={payload.hosts}
          onFix={(host, action) => void startFix(host, action)}
          canWrite={canWrite}
          writeTooltip={ADMIN_TOOLTIP}
          busy={busy}
        />
      </Card>

      <ConfirmDialog
        open={pending !== null}
        title="Run a full upgrade?"
        body={pending?.summary ?? ""}
        confirmLabel="Upgrade all"
        onConfirm={() => void confirmFix()}
        onCancel={() => setPending(null)}
      />

      <Card id={sectionId("calendar")} title="Patch calendar" subtitle="Patches applied and reboots taken, per host.">
        <PatchCalendar calendar={payload.calendar} />
      </Card>

      <Card id={sectionId("cves")} title="Known vulnerabilities"
            subtitle={
              payload.posture.unmeasured > 0
                ? `Read from the ${payload.posture.servers_checked} server${payload.posture.servers_checked === 1 ? "" : "s"} Vault can log into. The ${payload.posture.unmeasured} other devices on the network publish no package list, so no vulnerability feed covers them here.`
                : `Read from the ${payload.posture.servers_checked} server${payload.posture.servers_checked === 1 ? "" : "s"} Vault can log into.`
            }>
        {payload.hosts.filter(h=>h.pending===0 && payload.cves.some(c=>c.host===h.name)).map(h=><p key={h.name}><strong>{h.name}:</strong> CVE findings remain, but the last package check reported no pending packages{h.last_apt ? ` (${h.last_apt})` : ""}. {h.reboot_required ? "A restart is required. " : ""}A package fix may not yet be available in the configured repositories, or these observations may differ in age. Refresh the scan and inspect the package evidence before treating this as resolved.</p>)}
        <CveTable cves={payload.cves} />
      </Card>

      <Card id={sectionId("reboot")} title="Reboot planner">
        <RebootPlanner
          plan={payload.reboot_plan}
          onSchedule={schedule}
          canWrite={canWrite}
          writeTooltip={ADMIN_TOOLTIP}
        />
      </Card>

      <div className="g-updates-pair">
        <Card title="This machine" subtitle="The updates waiting on the machine Vault's sensor runs on.">
          <SensorHostUpdates mac={payload.mac} />
        </Card>
        <Card title="Pending packages by host" subtitle="What apt is holding back, named.">
          <PendingPackages hosts={payload.hosts} />
        </Card>
      </div>

      <Card title="Upgrade progress">
        <UpgradeProgressPanel progress={payload.progress} />
      </Card>
    </div>
    </Page>
  );
}

export default Updates;
