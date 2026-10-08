// Overview's camera census row (Task 83, item 4 / pattern A5). "Can a
// stranger watch my house" is the single most visceral question this
// product answers, so it gets a named row rather than staying folded into
// a per-network chip.
//
// Honesty is the whole point: `known` is every camera Vault has actually
// seen answer. `expected` is only ever >= `known` — the gap is named in
// `gaps`, one line per recorder that has not listed as many channels as the
// owner told Vault to expect. A quiet recorder must read as "N cameras
// unaccounted for", never as "there are now fewer cameras".

import { Link, useInRouterContext } from "react-router-dom";
import { EmptyState } from "./EmptyState";
import type { CameraCensus } from "../types";

export type CameraCensusPanelProps = {
  census: CameraCensus;
};

export function CameraCensusPanel({ census }: CameraCensusPanelProps) {
  const inRouter = useInRouterContext();
  const { known, online, expected, exposed, exposed_ports, gaps, unobserved, offline, exposure_known } = census;

  if (known === 0 && expected === 0) {
    return (
      <EmptyState
        title="No cameras found yet."
        body="Vault files a camera the moment it sees one — by vendor, by name, or by the ports it answers on."
      />
    );
  }

  const short = expected - known;
  const countLine = `${known} discovered of ${expected} expected cameras; ${online} answering`;

  const cameraLink = (
    <span>
      {countLine}
      {inRouter && (
        <>
          {" · "}
          <Link to="/assets/devices">see them</Link>
        </>
      )}
    </span>
  );

  return (
    <div className="g-camera-census" data-testid="camera-census">
      <div className={`g-camera-census-row g-tone-${short > 0 || online < known ? "warn" : "good"}`}>
        <span className="g-dot" aria-hidden="true" />
        {cameraLink}
        {unobserved !== undefined && <span> · {unobserved} unobserved now · {offline ?? 0} confirmed offline</span>}
      </div>

      <div className={`g-camera-census-row g-tone-${exposure_known === false ? "warn" : exposed ? "alert" : "good"}`}>
        <span className="g-dot" aria-hidden="true" />
        <span>
          {exposure_known === false ? (exposed ? `Previously reachable on ports ${exposed_ports.join(", ")}; a fresh exposure check is needed` : "Internet exposure has not been recently verified") : exposed
            ? `reachable from the internet on port${exposed_ports.length === 1 ? "" : "s"} ${exposed_ports.join(", ")}`
            : "none reachable from the internet"}
        </span>
      </div>

      {gaps.length > 0 && (
        <ul className="g-camera-census-gaps">
          {gaps.map((gap) => (
            <li key={gap.name}>
              <strong>{gap.name}</strong>: {gap.known} of {gap.expected} confirmed — {gap.reason}
              {inRouter && (
                <>
                  {" "}
                  <Link to="/settings/recorders">check it</Link>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default CameraCensusPanel;
