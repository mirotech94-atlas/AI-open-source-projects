import { Link } from "react-router-dom";
import { Stat } from "../design";
import { age } from "../lib/format";
import type { Tone } from "../lib/format";
import type { OverviewPayload } from "../types";

/** Where the "What changed?" card jumps to. The card is a link to an anchor on
 *  the same page rather than a div with a scroll handler, so it is keyboard
 *  operable and shows a destination on hover. */
export const CHANGED_ANCHOR = "g-changed";

export type HeroCardsProps = {
  payload: OverviewPayload;
  className?: string;
};

function plural(n: number, one: string): string {
  return n === 1 ? one : `${one}s`;
}

type Hero = {
  key: string;
  to: string;
  label: string;
  value: string;
  unit?: string;
  sub: string;
  tone: Tone;
};

/** The five questions the owner actually asks, each answered by one hub-side
 *  figure. Ordered and worded after the reference console's own hero row. */
function cards(p: OverviewPayload): Hero[] {
  const h = p.health;
  const live = h.sensor_state === "online";
  const watchingSub = live
    ? `${h.scans_last_hour ?? "—"} ${plural(h.scans_last_hour ?? 0, "scan")} in the last hour · last event ${age(h.last_event_age_s)} ago`
    : p.age_s === null
      ? "no scan has ever landed — is a sensor enrolled?"
      : `no scan for ${age(p.age_s)} — sensor asleep?`;

  const dangerTone: Tone = p.incidents.crit > 0 ? "alert" : p.incidents.open > 0 ? "warn" : "good";
  const doTone: Tone = p.recs.critical > 0 ? "alert" : p.recs.high > 0 ? "warn" : "good";

  return [
    {
      key: "watching",
      to: "/history",
      label: "Are you watching?",
      value: live ? "● ACTIVE" : "● STALE",
      sub: watchingSub,
      tone: live ? "good" : "alert",
    },
    {
      key: "have",
      to: "/assets/devices",
      label: "What do I have?",
      value: String(p.assets.total),
      unit: plural(p.assets.total, "asset"),
      sub: `${p.devices.online} ${plural(p.devices.online, "device")} recently seen online · ${p.assets.servers} ${plural(p.assets.servers, "server")} · ${p.assets.sites} ${plural(p.assets.sites, "site")}`,
      tone: "plain",
    },
    {
      key: "changed",
      to: `#${CHANGED_ANCHOR}`,
      label: "What changed?",
      value: String(p.changed_24h),
      unit: "in 24h",
      sub: "alerts opened or resolved, fixes applied",
      tone: p.changed_24h === 0 ? "good" : "plain",
    },
    {
      key: "dangerous",
      to: "/inbox?filter=alerts",
      label: "What is dangerous?",
      value: String(p.incidents.open),
      unit: plural(p.incidents.open, "incident"),
      sub: `score ${p.score ?? "—"}/100 · grade ${p.grade || "—"}`,
      tone: dangerTone,
    },
    {
      key: "should",
      to: "/inbox?filter=recs",
      label: "What should I do?",
      value: String(p.recs.open),
      unit: plural(p.recs.open, "action"),
      sub: p.recs.top || "nothing pending",
      tone: doTone,
    },
  ];
}

/** The hero row: five clickable cards, each one question with one number and a
 *  sub-line that says where the number came from. Pure — every figure is a
 *  field on `payload`. */
export function HeroCards({ payload, className = "" }: HeroCardsProps) {
  return (
    <div className={`g-hero-cards ${className}`.trim()}>
      {cards(payload).map((c) => (
        <Link key={c.key} to={c.to} className="g-hero-card" data-testid={`hero-${c.key}`}>
          <Stat label={c.label} value={c.value} unit={c.unit} sub={c.sub} tone={c.tone} mono />
        </Link>
      ))}
    </div>
  );
}

export default HeroCards;
