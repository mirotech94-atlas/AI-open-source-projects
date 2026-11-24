/**
 * The Cameras tab's pure model. Twelve cards always (a failed read is twelve
 * UNKNOWN cards, never an empty page); a frame older than its own max age
 * plus a margin, or any missing read time, is UNKNOWN, never OK; every
 * figure shown carries the time it was read (invariants 6 and 7).
 */
import { normaliseHealth, type Health } from "../api";

export type CameraMode = "home" | "away";
export const MODES: readonly CameraMode[] = ["home", "away"];

/** One plain line under each mode button saying what it does. */
export const MODE_HELP: Record<CameraMode, string> = {
  home: "Normal: tells you about a person I don't know or a vehicle. Animals and plain motion are only recorded.",
  away: "Serious: a person I don't know or a vehicle is urgent and I call if you don't answer. Animals, plain motion and known people are a normal alert.",
};

/** An old stored mode ("night") reads as home. */
export function normaliseMode(value: unknown): CameraMode {
  return value === "away" ? "away" : "home";
}

export type CameraHealth = "ok" | "failing" | "unknown";

/** Human number 1..12 -> name, from the NVR's channel list. */
export const CAMERA_NAMES: readonly string[] = [
  "Left Siding", "Right Pool", "Top Main Street", "Left Pool", "1st Driveway",
  "2nd Left Siding", "Main Door", "Right Siding", "2nd Driveway", "3rd Driveway",
  "2nd Main Door", "Garage",
];

/** Slack on top of `snapshot_max_age_s` before a frame stops counting. */
export const FRAME_MARGIN_S = 15;

export const OVERVIEW_REFRESH_MS = 10_000;
export const LARGE_REFRESH_MS = 1_000;

export interface CameraCard {
  channel: number;
  number: number;
  name: string;
  model: string | null;
  online: boolean | null;
  health: CameraHealth;
  reason: string | null;
  last_motion_at: string | null;
  last_motion_kind: string | null;
  frame_at: string | null;
  read_at: string | null;
  snapshot_url: string;
  snapshot_max_age_s: number;
}

export interface NvrRead {
  health: CameraHealth;
  reason: string | null;
  read_at: string | null;
}

export interface CamerasOverview {
  served_at: string;
  mode: CameraMode;
  nvr: NvrRead;
  cameras: CameraCard[];
}

export type EventKind = "person" | "vehicle" | "animal" | "motion";

/** 'household: <name>' | 'household' | 'stranger' | 'unverified' | 'unlabelled'. */
export type EventLabel = string;

export interface CameraEvent {
  id: number;
  at: string;
  channel: number;
  number: number;
  name: string;
  kind: EventKind;
  label: EventLabel;
  cluster_id: string | null;
  alert_level: number;
  read_at: string;
  quiet_note?: string | null;
  snapshot_url: string | null;
}

export interface CameraEvents {
  served_at: string;
  events: CameraEvent[];
}

export interface CameraClip {
  at: string;
  channel: number;
  number: number;
  name: string;
  size: number;
  read_at: string;
  url: string;
}

/** One event that was rated an alert (level 2+): what a WhatsApp heads-up is about. */
export interface CameraAlert {
  id: number;
  at: string;
  channel: number;
  number: number;
  name: string;
  kind: EventKind;
  alert_level: number;
  read_at: string;
  snapshot_url: string | null;
  clip_url: string | null;
}

export interface CameraAlerts {
  served_at: string;
  alerts: CameraAlert[];
}

/** "11:07:00 · Camera 3 Top Main Street · car". Level and links are drawn beside it. */
export function alertLine(a: CameraAlert): string {
  return `${formatClock(a.at)} \u00B7 Camera ${a.number} ${a.name} \u00B7 ${kindText(a.kind)}`;
}

export interface CameraClips {
  served_at: string;
  clips: CameraClip[];
}

/** "09:05:30 · Camera 7 Main Door · 0.6 MB". */
export function clipLine(c: CameraClip): string {
  const mb = Math.max(0.1, Math.round(c.size / 104857.6) / 10);
  return `${formatClock(c.at)} · Camera ${c.number} ${c.name} · ${mb.toFixed(1)} MB`;
}

const WORD: Record<Health, string> = {
  ok: "OK", degraded: "Degraded", failing: "Failing", unknown: "Unknown",
};

function ms(value: string | null | undefined): number | null {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? null : t;
}

/** Local time as HH:MM:SS; "not read" when there is no usable time. */
export function formatClock(value: string | null | undefined): string {
  const t = ms(value);
  if (t === null) return "not read";
  const d = new Date(t);
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** "read HH:MM:SS", or "not read" -- never a bare figure without a time. */
export function readLabel(value: string | null | undefined): string {
  return ms(value) === null ? "not read" : `read ${formatClock(value)}`;
}

function asCameraHealth(h: string | null | undefined): CameraHealth {
  const n = normaliseHealth(h);
  return n === "ok" ? "ok" : n === "failing" ? "failing" : "unknown";
}

function unknownCard(number: number): CameraCard {
  return {
    channel: number - 1, number, name: CAMERA_NAMES[number - 1] ?? `Camera ${number}`,
    model: null, online: null, health: "unknown", reason: "not read",
    last_motion_at: null, last_motion_kind: null, frame_at: null, read_at: null,
    snapshot_url: `/api/cameras/${number - 1}/snap`, snapshot_max_age_s: 30,
  };
}

/** Always twelve cards in order 1..12. A camera the server did not return is
 *  an UNKNOWN card; a failed overview is twelve of them. */
export function twelveCards(overview: CamerasOverview | null): CameraCard[] {
  const by = new Map<number, CameraCard>();
  for (const c of overview?.cameras ?? []) {
    if (c.number >= 1 && c.number <= 12 && !by.has(c.number)) by.set(c.number, c);
  }
  return Array.from({ length: 12 }, (_, i) => by.get(i + 1) ?? unknownCard(i + 1));
}

/** The server's health, except that a frame older than its max age plus the
 *  margin, or any missing/unparseable read time, is UNKNOWN. A camera the
 *  NVR reports offline (read, but with no frame) stays FAILING. */
export function cardHealth(card: CameraCard, now: Date): CameraHealth {
  const frame = ms(card.frame_at);
  const read = ms(card.read_at);
  if (read === null) return "unknown";
  if (asCameraHealth(card.health) === "failing") return "failing";
  if (frame === null) return "unknown";
  const limit = (card.snapshot_max_age_s + FRAME_MARGIN_S) * 1000;
  if (now.getTime() - frame > limit) return "unknown";
  return asCameraHealth(card.health);
}

/** The card's class list. UNKNOWN is hatched and dashed and never shares the
 *  OK treatment. */
export function cardStyleClass(h: CameraHealth): string {
  if (h === "ok") return "cam-ok";
  if (h === "failing") return "cam-failing";
  return "cam-unknown hatch-edge";
}

export function healthWord(h: CameraHealth): string {
  return WORD[h];
}

export function motionLine(card: CameraCard): string {
  if (ms(card.last_motion_at) === null) return "no motion read";
  return `${card.last_motion_kind ?? "motion"} at ${formatClock(card.last_motion_at)}`;
}

export function onlineLine(card: CameraCard): string {
  return card.online === null ? "online: unknown" : card.online ? "online" : "offline";
}

export const KIND_ICON: Record<EventKind, string> = {
  person: "\u{1F6B6}", vehicle: "\u{1F697}", animal: "\u{1F43E}", motion: "\u3030",
};

/** "icon word" for what moved: person, car, animal or motion. */
export function kindText(kind: string): string {
  const word = kind === "vehicle" ? "car" : kind;
  const icon = KIND_ICON[kind as EventKind];
  return icon ? `${icon} ${word}` : word;
}

export function eventLine(e: CameraEvent): string {
  return `${formatClock(e.at)} · Camera ${e.number} ${e.name} · ${kindText(e.kind)} · ${eventLabelText(e.label)}`;
}

/** The label as the server sent it; anything missing reads "unlabelled". */
export function eventLabelText(label: string | null | undefined): string {
  const t = (label ?? "").trim();
  return t === "" ? "unlabelled" : t;
}

export interface AlertBadge { text: string; tone: "none" | "low" | "mid" | "serious" }

/** Alert level 0-3 -> badge. 3 is serious; a value that is not 0-3 is not
 *  drawn as a level at all ("level unknown"), never as level 0. */
export function alertBadge(level: number | null | undefined): AlertBadge {
  if (typeof level !== "number" || !Number.isInteger(level) || level < 0 || level > 3) {
    return { text: "level unknown", tone: "none" };
  }
  if (level === 3) return { text: "serious (3)", tone: "serious" };
  if (level === 2) return { text: "alert 2", tone: "mid" };
  if (level === 1) return { text: "alert 1", tone: "low" };
  return { text: "no alert", tone: "none" };
}

export type ChipState = "ready" | "unknown";
export interface PartState { state: ChipState; reason: string | null }
export type CardState = "pending" | "approved" | "declined" | "expired";
export interface PeopleSlot { slot: number; name: string | null; clusters: number }
export interface PeopleCluster {
  id: string;
  status: "unknown" | "household";
  slot: number | null;
  sightings: number;
  first_seen: string;
  last_seen: string;
  event_id: number | null;
  snapshot_url: string | null;
  channel_name: string | null;
  card_id: number | null;
  card_state: CardState | null;
}
export interface People {
  served_at: string;
  read_at: string;
  engine: PartState;
  key: PartState;
  slots: PeopleSlot[];
  clusters: PeopleCluster[];
}

export const SLOT_COUNT = 5;
export const NAME_MAX = 40;
export const FORGET_WORD = "FORGET";
/** Where the Approve/Decline buttons live (the Agency tab's approvals board). */
export const APPROVALS_HREF = "/agency";

export interface ChipView { label: string; text: string; unknown: boolean }
export interface SlotView { slot: number; text: string; name: string | null; clusters: number }
export interface ClusterView {
  id: string; status: string; sightings: number; first: string; last: string;
  household: boolean; slot: number | null; thumb: string | null; where: string;
  note: string | null; cardLink: boolean;
}
export interface PeopleView {
  /** false = the read failed or was untrusted: show UNKNOWN, never an empty list. */
  readable: boolean;
  readLine: string;
  chips: ChipView[];
  slots: SlotView[];
  clusters: ClusterView[];
  empty: boolean;
}

function chip(label: string, p: PartState | null | undefined): ChipView {
  if (!p || p.state !== "ready") {
    const why = p?.reason ? ` · ${p.reason}` : "";
    return { label, text: `${label} unknown${why}`, unknown: true };
  }
  return { label, text: `${label} ready`, unknown: false };
}

export function slotText(slot: number, name: string | null): string {
  const n = (name ?? "").trim();
  return n === "" ? `Slot ${slot} - name not set yet` : `Slot ${slot} - ${n}`;
}

/** The note under a cluster; names are never invented, only echoed. */
export function clusterNote(c: Pick<PeopleCluster, "status" | "card_state">): string | null {
  if (c.status !== "unknown") return null;
  switch (c.card_state) {
    case "pending": return "Decide this card on the Approvals board (or from WhatsApp)";
    case "expired": return "expired - nobody was marked known";
    case "approved": return "card approved";
    case "declined": return "card declined";
    default: return "no approval card";
  }
}

/** `null` (a failed or 503 read) is an UNKNOWN panel with five unnamed-looking
 *  slots left empty: nothing is shown as "no people". */
export function peopleView(p: People | null): PeopleView {
  if (!p || formatClock(p.read_at) === "not read") {
    return {
      readable: false, readLine: "People not read",
      chips: [chip("Face engine", null), chip("Key", null)],
      slots: [], clusters: [], empty: false,
    };
  }
  const by = new Map<number, PeopleSlot>();
  for (const s of p.slots ?? []) if (s.slot >= 1 && s.slot <= SLOT_COUNT) by.set(s.slot, s);
  const slots = Array.from({ length: SLOT_COUNT }, (_, i): SlotView => {
    const s = by.get(i + 1);
    const name = s?.name && s.name.trim() !== "" ? s.name : null;
    return { slot: i + 1, text: slotText(i + 1, name), name, clusters: s?.clusters ?? 0 };
  });
  const clusters = (p.clusters ?? []).map((c): ClusterView => ({
    id: c.id, status: c.status, sightings: c.sightings,
    first: formatClock(c.first_seen), last: formatClock(c.last_seen),
    household: c.status === "household", slot: c.slot, thumb: c.snapshot_url,
    where: c.channel_name ?? "camera not recorded", note: clusterNote(c),
    cardLink: c.status === "unknown" && c.card_state === "pending",
  }));
  return {
    readable: true, readLine: readLabel(p.read_at),
    chips: [chip("Face engine", p.engine), chip("Key", p.key)],
    slots, clusters, empty: clusters.length === 0,
  };
}

/** Name to send: trimmed, 1-40 chars, else null. Never defaulted. */
export function validName(raw: string): string | null {
  const t = raw.trim();
  return t.length >= 1 && t.length <= NAME_MAX ? t : null;
}

export function forgetReady(typed: string): boolean {
  return typed === FORGET_WORD;
}

/** Header line: NVR health and when it was read. No read time = Unknown. */
export function nvrLine(nvr: NvrRead | null): string {
  if (!nvr || ms(nvr.read_at) === null) return "NVR Unknown · not read";
  return `NVR ${WORD[asCameraHealth(nvr.health)]} · read ${formatClock(nvr.read_at)}`;
}

export function nvrHealth(nvr: NvrRead | null): CameraHealth {
  return !nvr || ms(nvr.read_at) === null ? "unknown" : asCameraHealth(nvr.health);
}

/** Cache-busting snapshot URL; `view=large` for the big view. */
export function snapUrl(base: string, t: number, large = false): string {
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}${large ? "view=large&" : ""}t=${t}`;
}

// ---- coverage: recorder against ATLAS, and each camera's alert rules ------------------

/** Seconds between coverage reads: it reads the NVR 36 times, so it is slow on purpose. */
export const COVERAGE_REFRESH_MS = 60_000;

export interface RuleInfo {
  label: string;
  subject: "person" | "vehicle";
  test: "anywhere" | "zone" | "cross" | "dwell" | "near" | "tag";
  direction: "enter" | "leave" | "both" | null;
  needs_check: boolean;
  zone: number[][] | null;
  line: { a: number[]; b: number[]; inside: number[] } | null;
  near: number | null;
  health: string;
  reason: string | null;
}

export interface CoverageSideRead {
  health: string;
  reason: string | null;
  read_at: string | null;
}

export interface CoverageRow {
  channel: number;
  number: number;
  name: string;
  recorder: CoverageSideRead & {
    motion_on?: boolean | null; ai_supported?: boolean | null; sensitivity?: number | null;
  };
  atlas: CoverageSideRead & { running?: boolean };
  busy_frames: number;
  rules: RuleInfo[];
  suggestion: string | null;
}

export interface Coverage {
  served_at: string;
  read_at: string;
  atlas_detection: { enabled: boolean; interval_s: number };
  rules_note: string;
  cameras: CoverageRow[];
}

function unknownCoverageRow(number: number): CoverageRow {
  const none: CoverageSideRead = { health: "unknown", reason: "not read", read_at: null };
  return {
    channel: number - 1, number, name: CAMERA_NAMES[number - 1] ?? `Camera ${number}`,
    recorder: { ...none }, atlas: { ...none }, busy_frames: 0, rules: [], suggestion: null,
  };
}

/** Always twelve rows 1..12; a failed read is twelve UNKNOWN rows. */
export function twelveCoverage(cov: Coverage | null): CoverageRow[] {
  const by = new Map<number, CoverageRow>();
  for (const r of cov?.cameras ?? []) {
    if (r.number >= 1 && r.number <= 12 && !by.has(r.number)) by.set(r.number, r);
  }
  return Array.from({ length: 12 }, (_, i) => by.get(i + 1) ?? unknownCoverageRow(i + 1));
}

export interface SideView { health: CameraHealth; text: string }

/** OK only with a usable read time; anything else is UNKNOWN with its reason. */
export function coverageSide(side: CoverageSideRead): SideView {
  const read = ms(side.read_at);
  const health: CameraHealth = read === null ? "unknown" : asCameraHealth(side.health);
  const why = side.reason ? ` · ${side.reason}` : "";
  return { health, text: `${WORD[health]} · ${readLabel(side.read_at)}${why}` };
}

const DIRECTION_WORD: Record<string, string> = {
  enter: "going in", leave: "going out", both: "both ways",
};

/** One line per rule: what it watches, direction, whether the owner has checked it, its state. */
export function ruleLine(r: RuleInfo): string {
  const how = r.test === "cross" ? `line, ${DIRECTION_WORD[r.direction ?? ""] ?? "direction not set"}`
    : r.test === "zone" ? "zone"
    : r.test === "dwell" ? "stays in zone"
    : r.test === "near" ? "comes close to a vehicle"
    : r.test === "tag" ? "tag only, never alerts"
    : "anywhere";
  const check = r.needs_check ? " · needs the owner check" : "";
  const state = r.test === "anywhere" ? ""
    : ` · ${WORD[asCameraHealth(r.health)]}${r.reason ? ` (${r.reason})` : ""}`;
  return `${r.label} (${r.subject}, ${how})${check}${state}`;
}

export interface OverlayShape {
  kind: "zone" | "line";
  points: string;               // "x,y x,y", scaled to a 0..100 box
  label: string;
  unknown: boolean;
  needsCheck: boolean;
}

const pct = (n: number): string => String(Math.round(n * 1000) / 10);
const pts = (list: number[][]): string => list.map(([x, y]) => `${pct(x ?? 0)},${pct(y ?? 0)}`).join(" ");

/** Zones and lines of the rules, ready to draw over a frame. A rule with no geometry draws nothing. */
export function overlayShapes(rules: RuleInfo[]): OverlayShape[] {
  const out: OverlayShape[] = [];
  for (const r of rules) {
    const unknown = r.test !== "anywhere" && asCameraHealth(r.health) !== "ok";
    const base = { label: r.label, unknown, needsCheck: r.needs_check };
    if (r.line) out.push({ kind: "line", points: pts([r.line.a, r.line.b]), ...base });
    else if (r.zone && r.zone.length >= 3) out.push({ kind: "zone", points: pts(r.zone), ...base });
  }
  return out;
}

// ---- metrics strip -----------------------------------------------------------------------

export const CAMERA_COUNT = 12;

export interface MetricCell {
  key: "online" | "nvr" | "rules";
  label: string;
  /** false = the value was not read: render the UNKNOWN cell, never a figure. */
  read: boolean;
  figure: string;
  readAt: string | null;
  health: CameraHealth | null;
}

/** Three figures for the page head. Each is built only from reads already loaded;
 *  an unread source is an UNKNOWN cell (figure "Unknown"), never 0. */
export function cameraMetrics(
  overview: CamerasOverview | null, coverage: Coverage | null, now: Date,
): MetricCell[] {
  const served = overview && ms(overview.served_at) !== null ? overview : null;
  const online: MetricCell = served
    ? {
      key: "online", label: "Online", read: true, readAt: served.served_at, health: null,
      figure: `${twelveCards(served).filter((c) => c.online === true
        && cardHealth(c, now) !== "unknown").length} of ${CAMERA_COUNT}`,
    }
    : { key: "online", label: "Online", read: false, figure: "Unknown", readAt: null, health: null };
  const nvrH = nvrHealth(overview?.nvr ?? null);
  const nvr: MetricCell = nvrH === "unknown"
    ? { key: "nvr", label: "NVR", read: false, figure: "Unknown", readAt: null, health: null }
    : {
      key: "nvr", label: "NVR", read: true, figure: WORD[nvrH],
      readAt: overview?.nvr.read_at ?? null, health: nvrH,
    };
  const covOk = coverage !== null && ms(coverage.read_at) !== null;
  const rules: MetricCell = covOk
    ? {
      key: "rules", label: "Active rules", read: true, readAt: coverage.read_at, health: null,
      figure: String(coverage.cameras.reduce((n, r) => n + r.rules.length, 0)),
    }
    : { key: "rules", label: "Active rules", read: false, figure: "Unknown", readAt: null, health: null };
  return [online, nvr, rules];
}
