import { useEffect, useState, type JSX } from "react";
import type { Overview } from "../api";
import { overall } from "../pages/commandModel";
import { stamp } from "../pages/cmdParts";
import { readOf, type Read } from "../pages/homeModel";
import { ringLook, ringState, tickPath } from "./bootRingModel";
import { useWhy } from "./WhyPanel";
import "./bootRing.css";

/**
 * The ATLAS boot ring, centred on the Command Center: concentric rings with
 * arc segments that draw in and turn at different speeds, tick marks on the
 * rim and a core that breathes. It is a status light, not decoration: the
 * ring follows the same health the header pill reads (`ringState`). Unknown
 * or unreadable is grey, dashed, still and says UNKNOWN. Pure SVG + CSS
 * transforms; reduced motion renders static rings with no animation class.
 * Reads only; it acts on nothing.
 */

let bootPlayed = false;
const BOOT_MS = 2800;

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const q = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = (): void => setReduced(q.matches);
    q.addEventListener("change", on);
    return (): void => q.removeEventListener("change", on);
  }, []);
  return reduced;
}

export function BootRing({ overview, watchOnly, reducedMotion, boot }: {
  overview: Read<Overview | null>;
  /** From the session read; null when it has not been read. */
  watchOnly: boolean | null;
  /** Test hooks: override the media query and the first-load boot. */
  reducedMotion?: boolean;
  boot?: boolean;
}): JSX.Element {
  const why = useWhy();
  const mq = useReducedMotion();
  const reduced = reducedMotion ?? mq;
  const [booting, setBooting] = useState(() => boot ?? !bootPlayed);
  useEffect(() => {
    if (!booting) return undefined;
    bootPlayed = true;
    const id = window.setTimeout(() => setBooting(false), BOOT_MS);
    return (): void => window.clearTimeout(id);
  }, [booting]);

  const read = readOf(overview);
  const failed = read.error !== null || !read.data;
  const o = overall(read.error ? null : read.data?.modules ?? null);
  const state = ringState(o, failed);
  const look = ringLook(state, reduced);
  const at = stamp(overview.fetchedAt, read.error !== null);
  const mode = watchOnly === null ? "mode unknown" : watchOnly ? "watch-only" : "acting enabled";
  const label = `ATLAS status: ${look.word.toLowerCase()}, ${at}, ${mode}`;
  const anim = !reduced;
  const cls = ["br", `br-s-${state}`, anim ? "br-anim" : "", anim && booting ? "br-boot" : "",
    look.spinning && !booting ? "br-spin" : "", look.pulsing && !booting ? "br-pulse" : ""]
    .filter(Boolean).join(" ");
  const dash = (solid: string): string | undefined => (look.dashed ? "3 7" : solid);
  const seg = look.segment === "none" ? null : `br-seg-${look.segment}`;

  return (
    <figure className="br-wrap" data-health={state} data-clickable role="button" tabIndex={0}
            onClick={why} title="Why ATLAS reads this: click for the checks behind it"
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); why(); } }}>
      <div className="br-hero">
        <p className="br-eyebrow">ATLAS status</p>
        {state === "unknown" ? (
          <p className="br-state" data-health="unknown">
            <span className="cmd-word-unknown">Unknown</span>
            {failed && <span className="br-why">checks not read</span>}
          </p>
        ) : (
          <p className="br-state" data-health={state}>
            <i className="br-sq" aria-hidden="true" />
            {look.word.charAt(0) + look.word.slice(1).toLowerCase()}
          </p>
        )}
        {!failed && (
          <dl className="br-stats">
            <div className="br-stat" data-health="ok"><dt>OK</dt><dd>{o.ok}</dd></div>
            <div className="br-stat" data-health={o.degraded + o.failing > 0 ? "degraded" : "ok"}>
              <dt>Need attention</dt><dd>{o.degraded + o.failing}</dd></div>
            <div className="br-stat" data-health="unknown"><dt>Unknown</dt><dd>{o.unknown}</dd></div>
          </dl>
        )}
        <p className="br-read">{at}</p>
      </div>
      <svg className={cls} viewBox="0 0 400 400" role="img" aria-label={label}>
        <defs>
          <radialGradient id="br-core-g">
            <stop offset="0" stopColor="currentColor" stopOpacity="0.55" />
            <stop offset="0.6" stopColor="currentColor" stopOpacity="0.14" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
        </defs>
        <g className="br-r br-r0"><path className="br-tick" d={tickPath(200, 196, 4, 120, 10)} /></g>
        <g className="br-r br-r1"><circle className="br-line" cx="200" cy="200" r="178" pathLength="100"
          strokeDasharray={dash("26 3 12 3 34 3 6 13")} /></g>
        <g className="br-r br-r2">
          <circle className="br-thin" cx="200" cy="200" r="156" />
          {seg && <circle className={`br-arc ${seg}`} cx="200" cy="200" r="156" pathLength="100"
            strokeDasharray="22 78" />}
          <circle className="br-arc" cx="200" cy="200" r="156" pathLength="100"
            strokeDasharray={look.dashed ? "3 7" : "14 36"} strokeDashoffset="-50" />
        </g>
        <g className="br-r br-r3"><circle className="br-dots" cx="200" cy="200" r="134" pathLength="100"
          strokeDasharray="0.4 1.6" /></g>
        <g className="br-r br-r4"><circle className="br-arc br-fat" cx="200" cy="200" r="112" pathLength="100"
          strokeDasharray={look.dashed ? "3 7" : "18 32"} /></g>
        <g className="br-core">
          {look.lit && <circle className="br-halo" cx="200" cy="200" r="96" fill="url(#br-core-g)" />}
          <circle className="br-disc" cx="200" cy="200" r="84" />
          <text className="br-name" x="200" y="205" textAnchor="middle">ATLAS</text>
          <text className="br-word" x="200" y="232" textAnchor="middle">{look.word}</text>
        </g>
      </svg>
      <figcaption className="br-cap">
        <span className="br-time">{at}</span>
        <span className="br-mode" data-mode={watchOnly === null ? "unknown" : watchOnly ? "watch" : "live"}>
          {watchOnly === null ? "Mode unknown" : watchOnly ? "Watch-only" : "Acting enabled"}
        </span>
      </figcaption>
    </figure>
  );
}
