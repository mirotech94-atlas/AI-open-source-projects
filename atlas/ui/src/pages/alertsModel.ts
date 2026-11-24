/**
 * Alerts grouped by where they come from, and the cards waiting on an
 * answer grouped by kind. Pure: no DOM, no fetch, the clock only when
 * passed.
 *
 * - An empty group has no health (null): it read and found nothing, which is
 *   neither Unknown nor OK. It is drawn as plain text.
 * - Rows from retired lanes are dropped and counted, never named.
 */
import { normaliseHealth, type ActivityRow, type Health, type Proposal } from "../api";
import type { FlowRow } from "./homeModel";
import { isHiddenModule, isRetiredText, visible } from "./retired";
import { left } from "./tasksModel";

export type AlertSource = "mail" | "calendar" | "video" | "sites" | "qa" | "ops";
export const ALERT_SOURCES: readonly AlertSource[] = [
  "mail", "calendar", "video", "sites", "qa", "ops"];

export function alertSource(module: string): AlertSource {
  switch (module) {
    case "mail": return "mail";
    case "calendar": return "calendar";
    case "academy":
    case "youtube": return "video";
    case "agency": return "sites";
    case "qa": return "qa";
    default: return "ops";
  }
}

export interface AlertItem {
  subject: string;
  module: string;
  at: string | null;
  health: Health;
  key: string;
}

export interface AlertGroup {
  source: AlertSource;
  count: number;
  /** Worst health in the group; null when the group is empty. */
  worst: Health | null;
  /** The newest three. */
  rows: AlertItem[];
}

const RANK: Record<Health, number> = { ok: 0, unknown: 1, degraded: 2, failing: 3 };

function time(at: string | null): number {
  const t = at ? Date.parse(at) : NaN;
  return Number.isNaN(t) ? -1e15 : t;
}

export function groupAlerts(
  rows: readonly ActivityRow[], brokenFlows: readonly FlowRow[],
): { groups: AlertGroup[]; hidden: number } {
  const seen = visible(rows, (r) => ({ module: r.module, text: [r.subject] }));
  const flows = visible(brokenFlows, (f) => ({ text: [f.title] }));
  const items: AlertItem[] = [
    ...seen.rows.map((r) => ({
      subject: r.subject, module: r.module, at: r.at,
      health: r.health ? normaliseHealth(r.health) : "unknown" as Health,
      key: `a-${r.id ?? r.at}-${r.subject}`,
    })),
    ...flows.rows.map((f) => ({
      subject: `Workflow ${f.title}: last run ${f.outcome}`, module: "workflows", at: f.at,
      health: (f.outcome === "failed" ? "failing" : "degraded") as Health, key: `w-${f.id}`,
    })),
  ];
  const groups = ALERT_SOURCES.map((source): AlertGroup => {
    const mine = items
      .filter((i) => alertSource(i.module) === source)
      .sort((a, b) => time(b.at) - time(a.at));
    const worst = mine.reduce<Health | null>(
      (acc, i) => (acc === null || RANK[i.health] > RANK[acc] ? i.health : acc), null);
    return { source, count: mine.length, worst, rows: mine.slice(0, 3) };
  });
  return { groups, hidden: seen.hidden + flows.hidden };
}

// --------------------------------------------------------------- decisions

export type DecisionKind =
  | "YouTube post" | "Mapfront draft" | "Sales email" | "Proposal" | "Invoice"
  | "SEO change" | "Research" | "Community post" | "Reddit post" | "Automation" | "other";
export const DECISION_KINDS: readonly DecisionKind[] = [
  "YouTube post", "Mapfront draft", "Sales email", "Proposal", "Invoice", "SEO change",
  "Research", "Community post", "Reddit post", "Automation", "other",
];

const YOUTUBE_POSTS = new Set([
  "academy.publish_tutorial", "academy.autopublish_tutorial", "academy.relaunch_tutorial",
  "academy.add_to_series_playlist", "academy.pin_comment",
  "youtube.upload", "youtube.set_visibility", "youtube.draft_metadata",
  "academy.send_comment_reply",
]);
const BY_ACTION: Record<string, DecisionKind> = {
  "agency.send_outreach": "Sales email",
  "agency.send_agreement": "Proposal",
  "sales.draft_proposal": "Proposal",
  "bizops.send_invoice": "Invoice",
  "bizops.send_payment_reminder": "Invoice",
  "bizops.propose_automation": "Automation",
  "research.adopt_opportunity": "Research",
};
const BY_NAMESPACE: Record<string, DecisionKind> = {
  sales: "Sales email", seo: "SEO change", research: "Research", community: "Community post",
  reddit: "Reddit post", bizops: "Automation",
};

export function decisionKind(action: string): DecisionKind {
  if (YOUTUBE_POSTS.has(action)) return "YouTube post";
  const exact = BY_ACTION[action];
  if (exact) return exact;
  const ns = action.split(".")[0] ?? "";
  const byNs = BY_NAMESPACE[ns];
  if (byNs) return byNs;
  if (ns === "agency") return "Mapfront draft";
  return "other";
}

/** The card's owner tag, as the Slack header writes it: `Agent · Client`.
 *  Mirrors kernel/board.py (AGENT_OF_NAMESPACE, CLIENT_DEFAULT). null for a
 *  card no agency agent owns. */
const AGENT_OF: Record<string, string> = {
  sales: "Sales", academy: "Content", youtube: "Content", community: "Content",
  reddit: "Content", bizops: "BizOps", projects: "BizOps", seo: "SEO",
  research: "Research", qa: "QA", agency: "Websites",
};
const CLIENT_DEFAULT: Record<string, string> = {
  academy: "Atlas Academy", youtube: "Atlas Academy", community: "AMSM",
  reddit: "Atlas Agency",
};
const CLIENT_KEYS = ["client", "client_name", "business", "business_name", "lead_name"];

export function decisionTag(
  action: string, params?: Record<string, unknown> | null,
  evidence?: Record<string, unknown> | null,
): string | null {
  const ns = action.split(".")[0] ?? "";
  const agent = AGENT_OF[ns];
  if (!agent) return null;
  for (const src of [evidence, params]) {
    if (!src) continue;
    for (const k of CLIENT_KEYS) {
      const v = src[k];
      if (typeof v === "string" && v.trim()) return `${agent} · ${v.trim().slice(0, 40)}`;
    }
  }
  return `${agent} · ${CLIENT_DEFAULT[ns] ?? "Mapfront"}`;
}

export interface DecisionRow {
  id: number;
  action: string;
  kind: DecisionKind;
  tag: string | null;
  tier: number;
  expiresAt: string;
  expiresIn: string;
}

export type CardLike = Pick<Proposal, "id" | "action" | "tier" | "expires_at">
  & Partial<Pick<Proposal, "params" | "evidence">>;

/** The header's "need you": the cards the Decisions panel lists, so the two
 *  always agree. null when the cards were not read. */
export function waitingCount(cards: readonly CardLike[] | null, now: number): number | null {
  return cards === null ? null : pendingDecisions(cards, now).rows.length;
}

/** The server returns pending cards only. Sorted by expiry; a card past its
 *  time says "expired" (it will not run). */
export function pendingDecisions(
  cards: readonly CardLike[], now: number,
): { rows: DecisionRow[]; hidden: number } {
  const shown = cards.filter((c) => !isHiddenModule(c.action.split(".")[0]) && !isRetiredText(c.action));
  const rows = shown
    .map((c): DecisionRow => ({
      id: c.id, action: c.action, kind: decisionKind(c.action),
      tag: decisionTag(c.action, c.params, c.evidence), tier: c.tier,
      expiresAt: c.expires_at, expiresIn: left(c.expires_at, now),
    }))
    .sort((a, b) => time(a.expiresAt) - time(b.expiresAt));
  return { rows, hidden: cards.length - shown.length };
}
