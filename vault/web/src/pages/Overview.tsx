import {DataNotice} from "../components/DataNotice";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Area, Bars, Card, Checks, Feature, Page, PageHeader, Records, StatStrip, Verdict, useToast } from "../design";
import { VerdictHero } from "../components/VerdictHero";
import type { CheckRow, PageAction, SkeletonTone as Tone, StatItem } from "../design";
import { NeedsYou } from "../components/NeedsYou";
import type { Ack } from "../lib/api";
import { alertOp, livePayload, myTickets, overviewPayload, requestFixOrConfirm, runAction, vulnScans } from "../lib/api";
import { useLiveFeed } from "../lib/live";
import type { NeedsYouItem, NextAction, TimelineItem } from "../types";

const LIVE_FRAME_KINDS = ["scan", "alert", "device", "fix"];
const TIMELINE_CAP = 60;

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Epoch ms for a timeline item's `ts`, or `null` for an unparseable one — a
 *  live frame or a hub row that fails to parse never wins a compare, it just
 *  never gets dropped by age. */
function tsMs(ts: string): number | null {
  const ms = new Date(ts).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** A stable identity for a timeline row, used to drop a live frame that the
 *  refetched `payload.timeline` already carries (the same event, reported
 *  twice: once live, once through the next poll). */
function timelineKey(item: TimelineItem): string {
  return `${item.ts}|${item.kind}|${item.entity}`;
}

/** Reconciles live-inserted frames against the freshly fetched payload:
 *  - drop any live frame the payload already carries (same ts/kind/entity)
 *  - drop any live frame no newer than the payload's newest row — the hub's
 *    own timeline has since caught up to it, so keeping it would risk a
 *    duplicate the key check misses (a live frame's `kind`/`ts` need not be
 *    byte-identical to how the hub renders the same event on refetch). */
function reconcileLiveFrames(liveFrames: TimelineItem[], payloadTimeline: TimelineItem[]): TimelineItem[] {
  const newestPayloadMs = payloadTimeline.length > 0 ? tsMs(payloadTimeline[0].ts) : null;
  const payloadKeys = new Set(payloadTimeline.map(timelineKey));
  return liveFrames.filter((frame) => {
    if (payloadKeys.has(timelineKey(frame))) return false;
    const frameMs = tsMs(frame.ts);
    if (newestPayloadMs !== null && frameMs !== null && frameMs <= newestPayloadMs) return false;
    return true;
  });
}

/** Turns one WS frame into a `TimelineItem` shaped the same way the hub's own
 *  timeline rows are, so a live insert and a fetched row render identically. */
function frameToTimelineItem(type: string, ts: string, data: Record<string, unknown>): TimelineItem {
  const sev = data.sev === "CRIT" || data.sev === "WARN" || data.sev === "INFO" ? data.sev : null;
  const title = str(data.msg) || str(data.title) || `${type[0]?.toUpperCase()}${type.slice(1)}`;
  return {
    ts,
    kind: type,
    title,
    detail: str(data.detail),
    sev,
    entity: str(data.entity) || str(data.key),
    href: null,
  };
}

export function Overview() {
  const toast = useToast();
  const live = useLiveFeed();

  const overviewQuery = useQuery({
    queryKey: ["overview"],
    queryFn: overviewPayload,
    refetchInterval: 60_000,
  });

  // Same queryKey `Shell` polls at `LIVE_POLL_MS` (4s) -- React Query
  // dedupes the two subscribers onto one shared fetch cycle at the shorter
  // of the two intervals, so this just has to not be looser than that.
  // Kept in step with it rather than left at its own number so the two
  // never quietly drift apart.
  useQuery({
    queryKey: ["live"],
    queryFn: livePayload,
    refetchInterval: 4_000,
    // Polling pauses on a hidden tab by itself (React Query's default
    // refetchIntervalInBackground is false). Gating `enabled` on visibility
    // did more than pause: it blocked the FIRST fetch too, so a page opened in
    // a background tab sat on "Loading" until someone looked at it -- which
    // reads as a dead tab, not a paused one.
    refetchIntervalInBackground: false,
  });

  const [liveFrames, setLiveFrames] = useState<TimelineItem[]>([]);

  useEffect(() => {
    const offs = LIVE_FRAME_KINDS.map((kind) =>
      live.on(kind, (event) => {
        const item = frameToTimelineItem(event.type, event.ts, event.data);
        setLiveFrames((prev) => [item, ...prev].slice(0, TIMELINE_CAP));
      }),
    );
    return () => offs.forEach((off) => off());
  }, [live]);

  // Approve is NOT a one-click repair. An `approval` remedy is precisely the
  // case the hub mints a tier-3 proposal for, and `requestFixOrConfirm`
  // returns that confirmation instead of running. Only a second, explicit
  // press sends the single-use token back. The codebase already carries a
  // scar from "a plain button that rebooted a host on one click"; this queue
  // is the most tempting place to repeat it.
  const [pending, setPending] = useState<{ key: string; summary: string } | null>(null);

  const approve = useMutation({
    mutationFn: async (item: NeedsYouItem) => {
      const out = await requestFixOrConfirm(item.host, item.action, null);
      return { item, out };
    },
    onSuccess: ({ item, out }) => {
      if (out.confirmation) {
        setPending({ key: item.key, summary: out.confirmation.summary ?? "This needs a confirmation." });
        toast.show(out.confirmation.summary ?? "Press Approve again to confirm.", "plain");
        return;
      }
      setPending(null);
      toast.show("Approved — Vault is running it.", "good");
      void overviewQuery.refetch();
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const notNow = useMutation({
    mutationFn: (item: NeedsYouItem) => alertOp(item.key, "snooze"),
    onSuccess: () => {
      toast.show("Snoozed. It comes back if it is still true.", "plain");
      void overviewQuery.refetch();
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  // Item 06: two questions an owner asks on the front page that used to
  // need another tab -- what the last vulnerability scan found, and whether
  // anyone is waiting on a reply.
  const vulnQuery = useQuery({ queryKey: ["vuln-scans"], queryFn: vulnScans });
  const ticketQuery = useQuery({ queryKey: ["my-tickets"], queryFn: myTickets });

  const runHeroAction = useMutation({
    mutationFn: (action: NextAction) => runAction<Ack>(action),
    // "Done" is a claim, and it has to be earned. The hub answers
    // `status: "needs-you"` when the thing the owner accepted is not
    // something Vault can run, and saying Done to that is how an owner
    // walks away believing a CRIT was handled while nothing ran. The inbox
    // and the triage card were fixed for exactly this; this dispatch path is
    // structurally identical, so it gets the same rule before a producer ever
    // points a hero action at such a route.
    onSuccess: (ack) => {
      if ((ack as { status?: string } | null)?.status === "needs-you") {
        toast.show("Noted, but this one needs you. Open it in the inbox and I will explain.", "plain");
        return;
      }
      toast.show("Done", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const payload = overviewQuery.data;

  if (!payload) {
    return (
      <Page>
        <PageHeader eyebrow="Watch · your network, right now" title="Overview" />
        <p role="status" className="sk-empty">Loading…</p>
      </Page>
    );
  }

  const reconciledLive = reconcileLiveFrames(liveFrames, payload.timeline);
  const timeline = [...reconciledLive, ...payload.timeline].slice(0, TIMELINE_CAP);

  /* ---- the four-part skeleton (handoff §The design decision) ------------
   * header → verdict → stat strip → ONE feature block → records.
   * Every figure below is a payload field; nothing is derived twice. */
  const needs = payload.needs_you.length;
  const crit = payload.alerts.crit;
  const tone: Tone = crit > 0 ? "alert" : needs > 0 ? "warn" : "good";
  // "Nothing needs you" is a fact about a queue. "You are protected" is a
  // claim Vault cannot always back, so the good verdict never says it.
  const headline = crit > 0
    ? `${crit} ${crit === 1 ? "thing needs" : "things need"} a look now`
    : needs > 0 ? `${needs} ${needs === 1 ? "thing needs" : "things need"} a decision`
    : "Nothing needs you";
  const handled = payload.fixed_without_you.automatic;
  const body = crit > 0
    ? (payload.verdict || "Something on the network is critical. It is at the top of the Inbox.")
    : needs > 0 ? "Vault can run the repair, and is waiting for your yes. The queue is just below."
    : `Vault handled ${handled} ${handled === 1 ? "thing" : "things"} on its own in the last ${payload.fixed_without_you.days} days${payload.fixed_without_you.approved > 0 ? `, and ${payload.fixed_without_you.approved} with your approval` : ""}.`;

  const ago = (secs: number | null) => secs === null ? "—" : secs < 60 ? "just now" : secs < 3600 ? `${Math.round(secs / 60)} min` : `${Math.round(secs / 3600)} h`;
  // `Series.points` carries nulls for days with no sweep; the line is drawn
  // over the readings that exist rather than dropping to zero on a quiet day,
  // which would read as a crash rather than a gap.
  const lastVuln = vulnQuery.data?.scans?.[0] ?? null;
  const vulnFindings = lastVuln
    ? Object.values(lastVuln.summary ?? {}).reduce((n, v) => n + v, 0)
    : 0;

  const scoreReadings = payload.score_trend.points.filter((p) => p.value !== null);
  const scorePoints = scoreReadings.map((p) => p.value as number);
  const scoreFrom = scoreReadings[0]?.ts?.slice(0, 10) ?? "";
  const first = scorePoints[0];
  const last = scorePoints[scorePoints.length - 1];
  // Hourly means, so the ends are fractional: `84.3 - 57.1` prints
  // `27.200000000000003` unless it is rounded before it reaches the page.
  const scoreMove = first === undefined || last === undefined ? 0 : Math.round(last - first);
  const scoreNote = scoreMove === 0
    ? "steady over the window"
    : scoreMove > 0 ? `up ${scoreMove} points over the window` : `down ${-scoreMove} points over the window`;

  const stats: StatItem[] = [
    { label: "Devices watched", value: `${payload.devices.online} / ${payload.devices.total}`, delta: payload.devices.unverified > 0 ? `${payload.devices.unverified} unverified` : "all named", deltaTone: payload.devices.unverified > 0 ? "warn" : "good" },
    { label: "Blocked · 24h", value: payload.health.dns_blocked_24h.toLocaleString(), delta: payload.health.dns_filter_state || undefined, deltaTone: "plain" },
    { label: "Handled alone · 14d", value: handled.toLocaleString(), delta: payload.fixed_without_you.approved > 0 ? `+${payload.fixed_without_you.approved} approved` : undefined, deltaTone: "plain", spark: payload.fixed_without_you.daily.map((d) => d.automatic + d.approved) },
    { label: "Last check", value: ago(payload.age_s), delta: "ago", deltaTone: payload.age_s !== null && payload.age_s > 1800 ? "warn" : "plain" },
  ];

  //: Item 06: "make recent activity a smaller section". The whole record
//: lives on History; this is the glance.
const RECENT_ROWS = 8;

const QUESTION_TONE: Record<string, Tone> = { good: "good", ok: "good", warn: "warn", bad: "alert", alert: "alert", crit: "alert" };
  const checks: CheckRow[] = payload.questions.map((q) => ({
    label: q.question, state: QUESTION_TONE[q.state] === "good" ? "yes" : QUESTION_TONE[q.state] === "warn" ? "look" : QUESTION_TONE[q.state] === "alert" ? "no" : "—",
    tone: QUESTION_TONE[q.state] ?? "plain", sentence: q.answer,
  }));

  // No "Run a check" here: the top bar's "Scan now" queues the same scan on
  // every page and reports its live state (Queued… / Scanning… / finished),
  // which this button could not. Two controls for one action on one screen
  // is the duplication, not the second opinion.
  const actions: PageAction[] = [
    ...(payload.next_action ? [{ label: payload.next_action.label, onClick: () => runHeroAction.mutate(payload.next_action as NextAction), disabled: runHeroAction.isPending }] : []),
  ];

  return (
    <Page>
      <DataNotice query={overviewQuery} />
      <PageHeader eyebrow="Watch · your network, right now" title="Overview"
                  sub="What Vault is watching for you, and the one thing that needs you before anything else." actions={actions} />

      {/* The verdict and the score ring side by side: the sentence says
          whether anything needs you, the ring says how the network is doing
          -- 100 minus the named deductions, with its 7-day trend. */}
      <div className="sk-lead">
        <Verdict tone={tone} headline={headline} body={body} facts={[
          { value: String(payload.devices.online), label: "Devices online" },
          { value: String(payload.alerts.open), label: "Open alerts", tone: payload.alerts.open > 0 ? "warn" : "plain" },
          { value: payload.score === null ? "—" : `${payload.score}`, label: "Security score" },
        ]}>
          {/* What backs the sentence up: every day of the window, what
              Vault fixed on its own (solid) and with an approval (tint). */}
          {payload.fixed_without_you.automatic + payload.fixed_without_you.approved > 0 && (
            <span className="sk-note">Handled per day · last {payload.fixed_without_you.days} days · on its own, and with your approval</span>
          )}
          <Bars
            bars={payload.fixed_without_you.daily.map((d) => ({
              a: d.automatic, b: d.approved,
              title: `${d.day}: ${d.automatic} on its own, ${d.approved} approved`,
            }))}
            axis={[payload.fixed_without_you.daily[0]?.day ?? "", "today"]}
            empty={`Nothing has needed fixing in the last ${payload.fixed_without_you.days} days.`}
          />
        </Verdict>
        <Card title="Security score" subtitle="100 minus the named deductions · 7-day trend">
          <VerdictHero
            payload={payload}
            onAction={(action) => runHeroAction.mutate(action)}
            busy={runHeroAction.isPending}
          />
        </Card>
      </div>

      {/* The verdict's actionable detail: every Approve / Not now stays. When
          the queue is empty the verdict has already said so; nothing repeats it. */}
      {needs > 0 && (
        <NeedsYou items={payload.needs_you} fixed={payload.fixed_without_you}
                  onApprove={(it) => approve.mutate(it)} onDismiss={(it) => notNow.mutate(it)} pendingKey={pending?.key ?? null} />
      )}

      <StatStrip items={stats} />

      {/* The 30-day score line, which the console has always carried and
          which the recomposition dropped: the ring says where the score is
          now, and this says whether it has been going the right way. Only
          drawn once there are two readings to draw between -- a single dot
          is not a trend. */}
      {scorePoints.length > 1 && (
        <Feature title="Security score, 30 days" note={scoreNote}>
          <Area points={scorePoints} axis={[scoreFrom, "today"]} />
        </Feature>
      )}

      <Feature title="What Vault is doing for you" note="the five questions, answered from the last sweep">
        <Checks rows={checks} />
      </Feature>

      {/* Item 06: the last scan's result and the support queue, as two
          short strips rather than two more tabs to visit. Each renders
          only once its own read has landed -- neither blocks the page. */}
      <div className="g-ov-pair">
        <Feature title="Last vulnerability scan" note={lastVuln ? lastVuln.target : "none run yet"}>
          {lastVuln ? (
            <ul className="g-ov-mini">
              <li>
                <span className="g-ov-mini-n">{vulnFindings}</span>
                <span className="g-ov-mini-l">Findings</span>
              </li>
              <li>
                <span className="g-ov-mini-n">{lastVuln.score ?? "—"}</span>
                <span className="g-ov-mini-l">Score</span>
              </li>
              <li>
                <span className="g-ov-mini-n">{lastVuln.ports.length}</span>
                <span className="g-ov-mini-l">Open ports</span>
              </li>
              <li className="g-ov-mini-when">
                {lastVuln.finished_at
                  ? `finished ${new Date(lastVuln.finished_at).toLocaleString()}`
                  : `${lastVuln.status}…`}
              </li>
            </ul>
          ) : (
            <p className="sk-empty">
              No vulnerability scan has been run yet. Start one from Security to see
              what a target exposes.
            </p>
          )}
        </Feature>

        <Feature title="Support" note={ticketQuery.data ? `${ticketQuery.data.open_count} open` : ""}>
          {ticketQuery.data && ticketQuery.data.tickets.length > 0 ? (
            <ul className="g-ov-tickets">
              {ticketQuery.data.tickets.slice(0, 3).map((t) => (
                <li key={t.id}>
                  <a href={`/support/${encodeURIComponent(t.id)}`}>{t.subject}</a>
                  <span className="g-ov-ticket-state mono">{t.status}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="sk-empty">No open tickets. Vault is not waiting on anyone, and nobody is waiting on you.</p>
          )}
        </Feature>
      </div>

      <Records title="Recent activity" columns={[
        { key: "when", header: "When", width: "110px" }, { key: "what", header: "What happened" }, { key: "where", header: "Where", width: "180px" }, { key: "result", header: "Result", width: "120px", align: "right" },
      ]} rows={timeline.slice(0, RECENT_ROWS).map((t, i) => ({
        id: `${timelineKey(t)}-${i}`,
        cells: [<span key="w" className="mono">{new Date(t.ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>, t.href ? <a key="t" href={t.href}>{t.title}</a> : t.title, t.entity || "—", <span key="r" className="mono">{t.sev ? String(t.sev).toUpperCase() : t.kind}</span>],
      }))} empty="Nothing has happened yet. The first sweep fills this in." />
    </Page>
  );
}

export default Overview;
