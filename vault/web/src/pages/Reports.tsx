import {DataNotice} from "../components/DataNotice";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, Drawer, Page, PageHeader, StatStrip, useToast } from "../design";
import type { PageAction, StatItem } from "../design";
import { ReportBuilder } from "../components/ReportBuilder";
import type { BuildRequest } from "../components/ReportBuilder";
import { ReportLibrary } from "../components/ReportLibrary";
import { ScheduleEditor } from "../components/ScheduleEditor";
import { ShareDialog } from "../components/ShareDialog";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { ValueStrip } from "../components/ValueStrip";
import { ExportMenu } from "../components/ExportMenu";
import { buildReport, deleteReport, reportsPayload, scheduleReports, shareReport } from "../lib/api";
import type { ReportCard, ReportsPayload } from "../types";
import { sectionId, useSectionAnchor } from "../lib/section";

const EMPTY_REPORTS: ReportsPayload = {
  library: [],
  presets: [],
  sections: [],
  schedule: { weekly: false, monthly: false, preset: "full", ntfy: false, hour: 8 },
  value: { days: 30, stats: [], work: [] },
};

// Task 38 gates `build` and `share` at operator, `schedule` and `delete`
// at admin — the UI mirrors that exactly rather than a stricter reading
// (e.g. "share is admin too"): hiding a control the hub would accept
// would just push the user to hit the API directly.
const OPERATOR_TOOLTIP = "operator role required";
const ADMIN_TOOLTIP = "admin role required";

export function Reports() {
  // `/<tab>/<section>` scrolls that panel into view — see lib/section.ts.
  useSectionAnchor();
    const toast = useToast();
  const queryClient = useQueryClient();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canBuild = atLeast(role, "operator");
  const canShare = atLeast(role, "operator");
  const canSchedule = atLeast(role, "admin");
  const canDelete = atLeast(role, "admin");

  const [shareCard, setShareCard] = useState<ReportCard | null>(null);

  const reportsQuery = useQuery({ queryKey: ["reports"], queryFn: reportsPayload });
  const payload = reportsQuery.data ?? EMPTY_REPORTS;

  const buildMutation = useMutation({
    mutationFn: (req: BuildRequest) => buildReport(req.days, req.preset, req.sections, req.title),
    onSuccess: (card) => {
      // Prepend directly into the cache — no refetch, per the brief.
      queryClient.setQueryData<ReportsPayload>(["reports"], (prev) =>
        prev ? { ...prev, library: [card, ...prev.library] } : prev,
      );
      toast.show(`"${card.title || card.preset}" built`, "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const deleteMutation = useMutation({
    mutationFn: (card: ReportCard) => deleteReport(card.id),
    onSuccess: (_ack, card) => {
      queryClient.setQueryData<ReportsPayload>(["reports"], (prev) =>
        prev ? { ...prev, library: prev.library.filter((c) => c.id !== card.id) } : prev,
      );
      toast.show("Report deleted", "plain");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const scheduleMutation = useMutation({
    mutationFn: scheduleReports,
    onSuccess: () => toast.show("Schedule saved", "good"),
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function handleShare(hours: number, push = false) {
    if (!shareCard) return Promise.reject(new Error("no report selected"));
    return shareReport(shareCard.id, hours, push).then((result) => {
      // Keep the library card's own share fields in sync so a re-opened
      // dialog (or a future card render) reflects the freshest link.
      queryClient.setQueryData<ReportsPayload>(["reports"], (prev) =>
        prev
          ? {
              ...prev,
              library: prev.library.map((c) =>
                c.id === shareCard.id ? { ...c, share_url: result.share_url } : c,
              ),
            }
          : prev,
      );
      return result;
    });
  }

  // The report library's "Build your first report" empty-state action: jump
  // to the builder above and hand it keyboard focus, rather than just
  // scrolling — a keyboard-only user gets exactly the same shortcut a mouse
  // user gets from clicking into the form.
  function focusBuilder() {
    const section = document.getElementById(sectionId("builder"));
    if (!section) return;
    section.scrollIntoView({ behavior: "smooth", block: "start" });
    section.querySelector<HTMLElement>("select, input, textarea, button")?.focus();
  }

  if (reportsQuery.isPending) return <p role="status">Loading reports…</p>;
  if (reportsQuery.isError && !reportsQuery.data) return <div role="alert">Could not load reports. <button onClick={() => void reportsQuery.refetch()}>Retry</button></div>;
  const scheduled = (payload.schedule.weekly ? 1 : 0) + (payload.schedule.monthly ? 1 : 0);
  const shared = payload.library.filter((c) => c.share_url).length;
  const repStats: StatItem[] = [
    { label: "Reports built", value: String(payload.library.length) },
    { label: "Scheduled", value: String(scheduled), delta: scheduled > 0 ? `at ${String(payload.schedule.hour).padStart(2, "0")}:00` : undefined },
    { label: "Shared links", value: String(shared) },
    { label: "Presets", value: String(payload.presets.length) },
  ];
  const repActions: PageAction[] = [
    { label: "Build", primary: true, onClick: focusBuilder },
    { label: "Library", href: `#${sectionId("library")}` },
    { label: "Schedule", href: `#${sectionId("schedule")}` },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Record · what Vault did, on paper"
        title="Reports"
        sub="Build one now, keep the library, or let a schedule build them for you."
        actions={repActions}
      />
      <StatStrip items={repStats} />
    <div className="g-reports">
      <DataNotice query={reportsQuery} />
      {/* Above the builder on purpose: the answer to "what am I getting for
          this" should not be behind a form. */}
      <Card
        id={sectionId("value")}
        title="What Vault did for you"
        subtitle={`Last ${payload.value.days} days. Every number here counts rows you can find in Activity.`}
      >
        <ValueStrip data={payload.value} />
      </Card>
      <Card
        id={sectionId("builder")}
        title="Build a report"
        subtitle="Pick a preset, tick sections, and preview it live before building."
      >
        <ReportBuilder
          defaultPreset={role === "viewer" ? "executive" : role === "operator" ? "ops" : "full"}
          presets={payload.presets}
          sections={payload.sections}
          onBuild={(req) => buildMutation.mutate(req)}
          building={buildMutation.isPending}
          canBuild={canBuild}
          buildTooltip={OPERATOR_TOOLTIP}
        />
      </Card>

      <Card id={sectionId("library")} title="Report library"
            subtitle="Every report built so far, newest first. Open one to read or print it; the list itself exports."
            actions={<ExportMenu path="/api/v1/export/reports" query={{ days: 365 }} label="Export the list" />}>
        <ReportLibrary
          library={payload.library}
          onOpen={(card) => window.open(card.url, "_blank", "noopener")}
          onShare={(card) => setShareCard(card)}
          onDelete={(card) => deleteMutation.mutate(card)}
          canShare={canShare}
          shareTooltip={OPERATOR_TOOLTIP}
          canDelete={canDelete}
          deleteTooltip={ADMIN_TOOLTIP}
          onBuildFirst={focusBuilder}
        />
      </Card>

      <Card id={sectionId("schedule")} title="Scheduled reports" subtitle="Auto-build and push a link on a cadence.">
        <ScheduleEditor
          schedule={payload.schedule}
          presets={payload.presets}
          onSave={(schedule) => scheduleMutation.mutate(schedule)}
          canWrite={canSchedule}
          writeTooltip={ADMIN_TOOLTIP}
          saving={scheduleMutation.isPending}
        />
      </Card>

      <Drawer open={shareCard !== null} onClose={() => setShareCard(null)} title="Share report">
        {shareCard && <ShareDialog card={shareCard} onShare={handleShare} />}
      </Drawer>
    </div>
    </Page>
  );
}

export default Reports;
