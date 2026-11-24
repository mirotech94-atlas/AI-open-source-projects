/**
 * Pure logic behind the ECC Channel tab (`/api/church/overview`): the
 * per-platform calendar, its gaps, the YouTube budget, the Facebook window
 * warnings, the content bank and the log -- plain functions over the rows
 * the server already returned, so none of it needs a DOM to test
 * (`tasksModel.ts` is the pattern). Every time is shown in New York time,
 * the time the channel posts by, and every number keeps the time it was
 * read: nothing here estimates or fills in a missing figure.
 */

export type Platform = "youtube" | "facebook";
export const PLATFORMS: readonly Platform[] = ["youtube", "facebook"];
export const PLATFORM_LABEL: Record<Platform, string> = {
  youtube: "YouTube", facebook: "Facebook",
};

export type PostStatus =
  | "planned" | "rendered" | "awaiting_review" | "scheduled" | "published"
  | "failed" | "deleted";

export interface ChurchPostBrief {
  id: number;
  title: string;
  status: PostStatus | string;
  url: string | null;
  language: string;
  reviewed: boolean;
  reviewed_at: string | null;
}

export interface ChurchPost extends ChurchPostBrief {
  platform: Platform;
  schedule_index: number;
  file: string;
  publish_at: string | null;
  video_id: string | null;
  series: string;
  scheduled_at: string | null;
  updated_at: string | null;
}

export interface CoverageSlot {
  day: string;
  slot: string;
  state: "covered" | "pending" | "gap";
  post: ChurchPostBrief | null;
}

export interface ChurchIdea {
  id: number;
  code: number;
  title: string;
  series: string;
  category: string;
  format: string;
  language: string;
  status: string;
  notes: string;
}

export interface ChurchEvent {
  id: number;
  at: string;
  kind: string;
  message: string;
}

export interface ChurchFeast {
  day: string;
  name: string;
  name_ar: string;
  major: boolean;
}

export interface ChurchOverview {
  served_at: string;
  as_of: string;
  last_sync: string | null;
  configured: { youtube: boolean; facebook: boolean; progress_token: boolean };
  brand: {
    name: string; name_ar: string; address: string; hours: string; phone: string;
    youtube: string; facebook_page: string;
  };
  posts: ChurchPost[];
  coverage: Record<Platform, CoverageSlot[]>;
  gaps_7d: Record<Platform, number>;
  gap_days_30d: Record<Platform, string[]>;
  youtube_budget: { used: number; limit: number };
  facebook_window: {
    window_days: number;
    due: Array<ChurchPostBrief & { publish_at: string }>;
    beyond: Array<ChurchPostBrief & { publish_at: string; schedule_from: string }>;
  };
  review: Array<ChurchPostBrief & { platform: Platform; publish_at: string | null;
                                    urgent: boolean }>;
  contact: Array<ChurchPostBrief & { platform: Platform; missing: string[] }>;
  feasts: ChurchFeast[];
  ideas: ChurchIdea[];
  launch: Array<{ day: number; date: string | null; kind: string; language: string;
                  item: string }>;
  events: ChurchEvent[];
}

const NY = "America/New_York";

/** "Thu 16 Oct, 9:00 AM" in New York time; "no time" for a missing one. */
export function nyWhen(iso: string | null): string {
  if (!iso) return "no time";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "no time";
  return d.toLocaleString("en-US", {
    timeZone: NY, weekday: "short", day: "numeric", month: "short",
    hour: "numeric", minute: "2-digit",
  });
}

/** "Thu 16 Oct" for a plain YYYY-MM-DD day (no time zone shift). */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  const at = new Date(Date.UTC(y, m - 1, d, 12));
  return at.toLocaleDateString("en-US", {
    timeZone: "UTC", weekday: "short", day: "numeric", month: "short",
  });
}

/** "9:00 AM" / "6:00 PM" from a slot's "09:00" / "18:00". */
export function slotLabel(slot: string): string {
  const [h, m] = slot.split(":").map(Number);
  if (h === undefined || m === undefined || Number.isNaN(h)) return slot;
  const hour = h % 12 === 0 ? 12 : h % 12;
  return `${hour}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

export type Tone = "done" | "ready" | "review" | "planned" | "bad" | "gap";

/** The chip each status wears. A gap is its own tone, never "planned". */
export function statusTone(status: string | null): Tone {
  switch (status) {
    case "scheduled":
    case "published":
      return "done";
    case "rendered":
      return "ready";
    case "awaiting_review":
      return "review";
    case "planned":
      return "planned";
    case "failed":
    case "deleted":
      return "bad";
    default:
      return "gap";
  }
}

export function statusLabel(status: string | null): string {
  if (!status) return "no post";
  return status === "awaiting_review" ? "awaiting review" : status;
}

export interface CalendarRow {
  key: string;
  when: string;
  title: string;
  status: string | null;
  tone: Tone;
  url: string | null;
  reviewed: boolean | null;
  gap: boolean;
}

/** The next seven days slot by slot -- every empty slot a highlighted gap
 *  row -- then every later post on the platform, soonest first. */
export function calendarRows(o: ChurchOverview, platform: Platform,
                             now: Date): CalendarRow[] {
  const slots = o.coverage[platform] ?? [];
  const rows: CalendarRow[] = slots.map((s) => ({
    key: `${s.day}-${s.slot}`,
    when: `${dayLabel(s.day)}, ${slotLabel(s.slot)}`,
    title: s.post?.title ?? "No post in this slot",
    status: s.post?.status ?? null,
    tone: s.post ? statusTone(s.post.status) : "gap",
    url: s.post?.url ?? null,
    reviewed: s.post ? s.post.reviewed : null,
    gap: s.post === null,
  }));
  const last = slots.length ? slots[slots.length - 1]!.day : null;
  const later = o.posts
    .filter((p) => p.platform === platform && p.publish_at)
    .filter((p) => {
      const at = new Date(p.publish_at!);
      if (at.getTime() < now.getTime()) return false;
      return last === null || nyDay(p.publish_at!) > last;
    })
    .sort((a, b) => a.publish_at!.localeCompare(b.publish_at!));
  for (const p of later) {
    rows.push({
      key: `post-${p.id}`, when: nyWhen(p.publish_at), title: p.title,
      status: p.status, tone: statusTone(p.status), url: p.url,
      reviewed: p.reviewed, gap: false,
    });
  }
  return rows;
}

/** The New York calendar date (YYYY-MM-DD) of an instant. */
export function nyDay(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: NY, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(iso));
  return parts;
}

export function budgetLine(b: ChurchOverview["youtube_budget"]): string {
  const left = Math.max(0, b.limit - b.used);
  return `${b.used} of ${b.limit} uploads used today (New York), ${left} left`;
}

/** One warning per reel beyond the window, with the day it can be scheduled;
 *  and one per reel inside it that is still not scheduled. */
export function windowWarnings(w: ChurchOverview["facebook_window"]): string[] {
  const later = w.beyond.map((p) =>
    `${p.title} (${nyWhen(p.publish_at)}) is more than ${w.window_days} days out: ` +
    `schedule it on or after ${dayLabel(p.schedule_from)}.`);
  const due = w.due.map((p) =>
    `${p.title} (${nyWhen(p.publish_at)}) is inside the window and not scheduled yet.`);
  return [...due, ...later];
}

export function gapSummary(o: ChurchOverview, platform: Platform): string {
  const days = o.gap_days_30d[platform] ?? [];
  if (days.length === 0) return "every day of the next 30 has a post";
  const shown = days.slice(0, 5).map(dayLabel).join(", ");
  return `${days.length} day(s) of the next 30 with no post: ${shown}` +
    (days.length > 5 ? "…" : "");
}

export const IDEA_ORDER: readonly string[] = [
  "idea", "planned", "in_production", "scheduled", "published", "dropped",
];

/** Ideas counted by status, in a fixed order, unknown statuses appended. */
export function ideaCounts(ideas: readonly ChurchIdea[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const i of ideas) counts.set(i.status, (counts.get(i.status) ?? 0) + 1);
  const known = IDEA_ORDER.filter((s) => counts.has(s));
  const extra = [...counts.keys()].filter((s) => !IDEA_ORDER.includes(s)).sort();
  return [...known, ...extra].map((s) => [s, counts.get(s) ?? 0]);
}

/** "synced 2 h ago" -- or the plain truth that it never was. */
export function syncLine(lastSync: string | null, now: Date): string {
  if (!lastSync) return "never synced from the Mac (run atlas_sync.py)";
  const mins = Math.round((now.getTime() - new Date(lastSync).getTime()) / 60000);
  if (mins < 60) return `synced ${Math.max(0, mins)} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `synced ${hours} h ago` : `synced ${Math.round(hours / 24)} days ago`;
}
