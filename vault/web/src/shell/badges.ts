// Nav counts, in one module because three surfaces draw them: the sidebar's
// parent rows, the sidebar's dropdown children, and the mobile bar + its
// "More" sheet. Before this, `badgeFor` lived inside `Sidebar` and keyed off
// the *parent* key only — so Recommendations, which is a child inside the
// Inbox dropdown, had no way to carry a number at all, and the mobile bar
// carried none of them.

import type { Tone } from "../lib/format";
import type { TabDef } from "./nav";

/** Every figure any nav row needs, gathered once by `Shell`. */
export type NavBadges = {
  /** Inbox rows a human has not looked at yet — the parent's own figure. */
  inboxUnread: number;
  /** Open alerts, and how they break down by severity (drives the tone). */
  alertsOpen: number;
  alertsCrit: number;
  alertsWarn: number;
  /** Open (unclosed) incidents. */
  incidents: number;
  /** Open recommendations — the yellow number the owner could not see. */
  recs: number;
  /** Security's own row: the same crit/warn split, shown on the parent. */
  securityCrit: number;
  securityWarn: number;
  /** Updates has no count, only "something is waiting on a reboot". */
  updatesWarn: boolean;
};

export const NO_BADGES: NavBadges = {
  inboxUnread: 0,
  alertsOpen: 0,
  alertsCrit: 0,
  alertsWarn: 0,
  incidents: 0,
  recs: 0,
  securityCrit: 0,
  securityWarn: 0,
  updatesWarn: false,
};

/** One rendered badge: what it says, what colour it is, and — because a
 *  rolled-up number must never read to a screen reader as if it were the
 *  parent's own count — what it actually counts. `label` is composed onto the
 *  row's own accessible name by the caller ("Inbox, 2 open alerts, …"); it is
 *  never put on the badge element itself, which is a bare `<span>` and may not
 *  carry an `aria-label` under ARIA 1.2. */
export type NavBadge = {
  text: string;
  tone: Tone;
  /** Read instead of `text` by assistive tech. */
  label: string;
  /** Sort key for "worst tone wins" when several roll up into one. */
  rank: number;
};

const RANK: Record<Tone, number> = { plain: 0, good: 0, warn: 1, alert: 2 };

/** `singular` with the trailing "s" restored for anything but exactly one —
 *  every noun a badge carries ("open alert(s)", "security warning(s)"…) is a
 *  plain regular plural, so one shared rule covers them all rather than a
 *  literal plural string per call site drifting out of sync with the count. */
function plural(count: number, singular: string): string {
  return count === 1 ? singular : `${singular}s`;
}

function badge(count: number, tone: Tone, singularNoun: string): NavBadge | null {
  if (count <= 0) return null;
  return { text: String(count), tone, label: `${count} ${plural(count, singularNoun)}`, rank: RANK[tone] };
}

/** The badge a row carries in its own right, parent or child, by `key`. */
export function ownBadge(key: string, b: NavBadges): NavBadge | null {
  switch (key) {
    case "inbox": {
      // Inbox used to carry a dropdown, and its number was the roll-up of the
      // three child rows. The dropdown is gone (the page has its own tabs), so
      // the roll-up has to happen here or the row would show the unread count
      // alone -- which is 0 almost always, and losing "you have 26 things
      // waiting" from the sidebar is not what flattening the nav was for.
      const total = b.alertsOpen + b.incidents + b.recs;
      if (total === 0) return badge(b.inboxUnread, "alert", "unread");
      const tone: Tone = b.alertsCrit > 0 || b.incidents > 0 ? "alert" : "warn";
      const say = (n: number, noun: string) => (n ? `${n} ${plural(n, noun)}` : "");
      const parts = [
        say(b.alertsOpen, "open alert"),
        say(b.incidents, "open incident"),
        say(b.recs, "recommendation"),
      ].filter(Boolean);
      return { text: String(total), tone, label: parts.join(", "), rank: RANK[tone] };
    }
    case "inbox-alerts":
      return badge(b.alertsOpen, b.alertsCrit > 0 ? "alert" : "warn", "open alert");
    case "inbox-incidents":
      return badge(b.incidents, "alert", "open incident");
    case "inbox-recs":
      return badge(b.recs, "warn", "recommendation");
    case "security":
      return b.securityCrit > 0
        ? badge(b.securityCrit, "alert", "critical security alert")
        : badge(b.securityWarn, "warn", "security warning");
    case "updates":
      return b.updatesWarn
        ? { text: "!", tone: "warn", label: "updates need attention", rank: 1 }
        : null;
    default:
      return null;
  }
}

/** The children's badges added together — the number a parent shows while its
 *  dropdown is shut, so a count never vanishes exactly when the sidebar goes
 *  narrow. Null when no child carries one. */
export function rolledUpBadge(tab: TabDef, b: NavBadges): NavBadge | null {
  const parts = (tab.children ?? [])
    .map((c) => ownBadge(c.key, b))
    .filter((x): x is NavBadge => x !== null);
  if (parts.length === 0) return null;
  const total = parts.reduce((sum, p) => sum + (Number(p.text) || 0), 0);
  const worst = parts.reduce((acc, p) => (p.rank > acc.rank ? p : acc), parts[0]);
  if (total === 0) return worst;
  return {
    text: String(total),
    tone: worst.tone,
    label: parts.map((p) => p.label).join(", "),
    rank: worst.rank,
  };
}

/** What a parent row actually shows: its own badge while the dropdown is
 *  open (each child then carries its own), the children's roll-up while it is
 *  shut or the sidebar is collapsed. */
export function parentBadge(tab: TabDef, b: NavBadges, open: boolean): NavBadge | null {
  const own = ownBadge(tab.key, b);
  if (open) return own;
  return rolledUpBadge(tab, b) ?? own;
}
