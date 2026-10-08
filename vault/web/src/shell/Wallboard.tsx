// The wallboard: the big-screen view meant to be left running on a monitor on
// the wall, which is what the owner's reference console's ⛶ button opens
// (`wall(true)` -> `#wall` + `renderWall()`), and what Vault's ⛶ button did
// NOT do — it called `document.documentElement.requestFullscreen()`, which
// makes the ordinary dense dashboard bigger and nothing else. A wall display is
// read from across a room: it wants a handful of very large figures and the open
// alerts, not a sidebar, a top bar and a twelve-column table.
//
// Deliberate differences from the reference, each for a reason:
//
//  - A 🔊 toggle rather than an automatic `beep()`. `AudioContext` is blocked
//    without a user gesture in every current browser, so a sound that just
//    happens is silence with extra steps on a page that has been sitting
//    untouched for hours — the only state a wallboard is ever in. The toggle
//    IS that gesture: it is clicked once when the screen is set up, it unlocks
//    the context for the session, and after that a new CRIT is spoken across
//    the room, which a toast cannot do from ten feet away. Default off, and it
//    turns itself back off if the browser suspends the context later, because
//    a lit speaker over a silent room is the display lying about being armed.
//    The speaking itself happens in `Shell`'s existing `live.on("alert")`
//    handler, which already holds the `seenCrit` dedup — a second
//    subscription here would say every critical twice.
//  - No "Download Mbps" tile. `OverviewPayload` carries no speed figure and
//    inventing one is worse than showing eight tiles instead of nine.
//  - Two tiles the reference has no equivalent for — the security score with
//    its grade, and open incidents — because Vault computes both and they are
//    exactly the sort of thing a wall display is for.

import { useEffect, useMemo, useRef, useState } from "react";
import { age } from "../lib/format";
import type { Tone } from "../lib/format";
import type { InboxRow, LivePayload, OverviewPayload } from "../types";
import "./wallboard.css";

export type WallboardProps = {
  overview: OverviewPayload | null;
  /** The age of `overview` is measured from when it was fetched, exactly as the
   *  top bar does it, so the meta line keeps counting up between refetches. */
  overviewFetchedAt: number | null;
  live: LivePayload | null;
  rows: InboxRow[];
  maintenanceUntil: string | null;
  onClose: () => void;
  /** Whether this hub can speak at all (`OverviewPayload.voice`). */
  voice: boolean;
  /** Whether new criticals are read aloud. Owned by `Shell`, because the
   *  speaking happens in `Shell`'s alert subscription. */
  speakOn: boolean;
  onToggleSpeak: () => void;
};

type Tile = {
  key: string;
  label: string;
  value: string;
  /** Small trailing text inside the tile — a denominator, a grade. */
  sub?: string;
  tone: Tone;
};

function scoreTone(score: number | null): Tone {
  if (score === null) return "plain";
  if (score >= 80) return "good";
  if (score >= 60) return "warn";
  return "alert";
}

/** Every tile drawn from data the shell already holds. Nothing here reaches for
 *  a field the hub does not send today — the dashboard contract
 *  (`types.ts` <-> `schemas_dash.py`) is not this view's to widen. */
function tilesFor(overview: OverviewPayload | null, live: LivePayload | null): Tile[] {
  const sites = live?.sites ?? [];
  const sitesOk = sites.filter((s) => s.ok === true).length;
  const alerts = overview?.alerts ?? null;
  const devices = overview?.devices ?? null;
  const crit = alerts?.crit ?? 0;
  const warn = alerts?.warn ?? 0;
  const unverified = devices?.unverified ?? 0;
  const incidents = overview?.incidents?.open ?? 0;

  return [
    {
      key: "score",
      label: "Security score",
      value: overview?.score === null || overview?.score === undefined ? "—" : String(overview.score),
      sub: overview?.grade ? `grade ${overview.grade}` : undefined,
      tone: scoreTone(overview?.score ?? null),
    },
    {
      key: "sites",
      label: "Sites up",
      value: sites.length === 0 ? "—" : String(sitesOk),
      sub: sites.length === 0 ? undefined : `of ${sites.length}`,
      tone: sites.length === 0 ? "plain" : sitesOk === sites.length ? "good" : "alert",
    },
    {
      key: "critical",
      label: "Critical",
      value: String(crit),
      tone: crit > 0 ? "alert" : "good",
    },
    {
      key: "warnings",
      label: "Warnings",
      value: String(warn),
      tone: warn > 0 ? "warn" : "good",
    },
    {
      key: "incidents",
      label: "Open incidents",
      value: String(incidents),
      tone: incidents > 0 ? "alert" : "good",
    },
    {
      key: "devices",
      label: "Devices online",
      value: devices === null ? "—" : String(devices.online),
      sub: devices === null ? undefined : `of ${devices.total}`,
      tone: "plain",
    },
    {
      key: "unverified",
      label: "Unverified",
      value: String(unverified),
      tone: unverified > 0 ? "warn" : "good",
    },
    {
      key: "servers",
      label: "Servers",
      value: overview?.assets === null || overview?.assets === undefined ? "—" : String(overview.assets.servers),
      tone: "plain",
    },
    {
      key: "autofix",
      label: "Fixes 24h",
      value: overview?.health === null || overview?.health === undefined ? "—" : String(overview.health.autofix_24h),
      tone: "plain",
    },
  ];
}

const SEV_TONE: Record<string, Tone> = { CRIT: "alert", WARN: "warn", INFO: "plain" };

export function Wallboard({
  overview,
  overviewFetchedAt,
  live,
  rows,
  maintenanceUntil,
  onClose,
  voice,
  speakOn,
  onToggleSpeak,
}: WallboardProps) {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const speakRef = useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  // Only the meta line's clock ticks locally. The FIGURES refresh on their own,
  // because `Shell` already polls `/overview` every 10s and `/live` every 4s and
  // this view renders straight off those queries — a wallboard needs no interval
  // of its own, and adding one would double the request rate for nothing.
  const [now, setNow] = useState(() => Date.now());

  // Same mechanics as `CommandPalette`: capture what had focus, restore it on
  // unmount, so closing the wallboard with Escape puts the caret back on the
  // top bar's button rather than at the top of the document.
  useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    return () => {
      previousFocusRef.current?.focus?.();
    };
  }, []);

  useEffect(() => {
    const handle = requestAnimationFrame(() => closeRef.current?.focus());
    return () => cancelAnimationFrame(handle);
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const tiles = useMemo(() => tilesFor(overview, live), [overview, live]);

  // Everything that is not merely informational, worst first — the same cut the
  // reference makes (`al.filter(a => a.sev !== 'INFO').slice(0, 8)`).
  const open = useMemo(
    () =>
      rows
        .filter((r) => r.sev !== "INFO" && r.status !== "resolved")
        .slice(0, 8),
    [rows],
  );

  const baseAge = overview?.age_s ?? null;
  const elapsed = overviewFetchedAt !== null ? Math.max(0, Math.floor((now - overviewFetchedAt) / 1000)) : 0;
  const liveAge = baseAge !== null ? baseAge + elapsed : null;
  const scanning = live?.scanning === true;
  const maintenance = (() => {
    if (maintenanceUntil === null) return null;
    const at = Date.parse(maintenanceUntil);
    if (Number.isNaN(at)) return null;
    const mins = Math.ceil((at - now) / 60_000);
    return mins > 0 ? mins : null;
  })();

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "Tab") {
      // Same reasoning as `CommandPalette`'s Tab guard -- focus must never
      // walk into the dashboard behind the overlay, which is inert to the eye
      // but not to the keyboard -- but here there can be ONE focusable
      // control or TWO: the close button always, and the speaker toggle when
      // `voice` is true. A guard written for "there is only one" and left
      // alone when a second control was added would make that control
      // present to the eye and unreachable by keyboard, which axe cannot
      // catch because it checks roles, not tab order. So this cycles between
      // whichever controls are actually rendered rather than refusing to
      // move at all.
      e.preventDefault();
      const controls = [speakRef.current, closeRef.current].filter(
        (el): el is HTMLButtonElement => el !== null,
      );
      if (controls.length < 2) return;
      const at = controls.indexOf(document.activeElement as HTMLButtonElement);
      const dir = e.shiftKey ? -1 : 1;
      const next = controls[(at + dir + controls.length) % controls.length];
      next.focus();
    }
  }

  return (
    <div
      className="g-wallboard"
      role="dialog"
      aria-modal="true"
      aria-label="Wallboard"
      onKeyDown={onKeyDown}
    >
      <div className="g-wallboard-head">
        <h1 className="g-wallboard-title">
          <b>Vault</b> wallboard
        </h1>
        <span
          className={`g-pulse-dot g-tone-${scanning ? "plain" : "good"}${scanning ? " g-pulse-busy" : ""}`}
          role="img"
          aria-label={scanning ? "Scanning now" : "Live"}
        />
        <span className="g-wallboard-meta num">
          {liveAge === null ? "no scan yet" : `last scan ${age(liveAge)} ago`}
          {maintenance !== null && ` · maintenance ${maintenance}m left`}
        </span>
        <span className="g-wallboard-clock num">{new Date(now).toLocaleTimeString()}</span>
        {voice && (
          <button
            type="button"
            className="g-wallboard-speak"
            aria-label="Speak new criticals"
            aria-pressed={speakOn}
            onClick={onToggleSpeak}
            ref={speakRef}
          >
            {speakOn ? "🔊" : "🔈"}
          </button>
        )}
        <button type="button" className="g-wallboard-close" onClick={onClose} ref={closeRef}>
          Close wallboard
          <kbd>Esc</kbd>
        </button>
      </div>

      <ul className="g-wallboard-grid">
        {tiles.map((t) => (
          <li key={t.key} className={`g-wallboard-tile g-tone-${t.tone}`}>
            <span className="g-wallboard-tile-label">{t.label}</span>
            <span className="g-wallboard-tile-value num">
              {t.value}
              {t.sub !== undefined && <small> {t.sub}</small>}
            </span>
          </li>
        ))}
      </ul>

      <section className="g-wallboard-alerts" aria-labelledby="g-wallboard-alerts-h">
        <h2 id="g-wallboard-alerts-h">Open alerts</h2>
        {open.length === 0 ? (
          <p className="g-wallboard-quiet">All quiet.</p>
        ) : (
          <ul className="g-wallboard-alert-list">
            {open.map((r) => (
              <li key={r.id} className={`g-wallboard-alert g-tone-${SEV_TONE[r.sev] ?? "plain"}`}>
                <span className="g-wallboard-sev">{r.sev}</span>
                <span className="g-wallboard-alert-title">{r.title}</span>
                {r.entity !== "" && <span className="g-wallboard-alert-entity">{r.entity}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default Wallboard;
