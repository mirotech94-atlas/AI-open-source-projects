import { Badge } from "../design";
import type { ProtectionPanel as Data, ThreatDetail } from "../types";
import { ThreatMatrix } from "./ThreatMatrix";
import "../styles/protection.css";

/** What each block category means, in one line, so a count is readable
 *  without knowing which blocklist it came from. */
const MEANS: Record<string, string> = {
  "Dark web": "Tor addresses and the sites that proxy them",
  Malware: "domains known to serve or control malware",
  Phishing: "pages built to steal a password",
  "Ads and malware": "ad and tracker domains, malware among them",
  Adult: "adult content, because you asked for the family filter",
  "Blocked by you": "your own block list",
};

const TONE: Record<string, "good" | "warn" | "alert" | "plain"> = {
  on: "good", off: "alert", unknown: "plain",
};

/**
 * Malware and dark-web protection, and whether this machine defends itself.
 *
 * Two halves, and the split is deliberate. The left is what Vault
 * actually stopped — counted from the DNS log, so every number can be
 * checked against the rows beneath it. The right is the posture of the one
 * machine Vault can see inside.
 *
 * It is NOT called antivirus anywhere. Vault has no agent on phones or
 * TVs and cannot scan them, and a panel that implied otherwise would be
 * the most dangerous thing on the page.
 */
export type ProtectionPanelProps = {
  data: Data;
  /** Opening a threat card is reported upward rather than fetched here: these
   *  tests render this component with no query client, and that is the point —
   *  it stays presentational. `Security` owns the request, the same division
   *  `AttackMap` uses for its own drawer. */
  onOpenThreat?: (key: string) => void;
  threat?: ThreatDetail | null;
};

export function ProtectionPanel({ data, onOpenThreat, threat }: ProtectionPanelProps) {
  const { blocked, blocked_total, recent, defences, filtering, days } = data;
  const weak = defences.filter((d) => d.state === "off");
  // The dark web gets its own line because it is the one category a person
  // asked for by name, and burying it in a list of five would hide it.
  const darkweb = blocked.find((b) => b.category === "Dark web");

  return (
    <div className="g-prot" data-testid="protection">
      <div className="g-prot-cols">
        <section className="g-prot-col">
          <h3>Blocked before it loaded</h3>
          {!filtering ? (
            <p className="g-prot-quiet">
              Vault&rsquo;s DNS filter has not answered a lookup in the last {days} days, so
              nothing here is being filtered. That is a setup problem, not a clean bill of health.
            </p>
          ) : blocked_total === 0 ? (
            <p className="g-prot-quiet">
              Nothing was blocked in the last {days} days. The filter is answering, so this is
              genuinely quiet rather than switched off.
            </p>
          ) : (
            <>
              <p className="g-prot-total">
                <strong>{blocked_total.toLocaleString()}</strong> lookups refused in {days} days
              </p>
              <ul className="g-prot-bars">
                {blocked.map((b) => (
                  <li key={b.category} data-darkweb={b.category === "Dark web" ? "1" : undefined}>
                    <span className="g-prot-bar-label">
                      {b.category}
                      <em>{MEANS[b.category] ?? ""}</em>
                    </span>
                    <span className="g-prot-bar-track" aria-hidden="true">
                      <span
                        className="g-prot-bar-fill"
                        style={{ width: `${Math.max(2, Math.round((b.count / blocked_total) * 100))}%` }}
                      />
                    </span>
                    <span className="g-prot-bar-n">{b.count.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {darkweb === undefined && filtering && (
            <p className="g-prot-note">
              No device has tried to reach the dark web. Vault refuses those names whether
              or not anything asks.
            </p>
          )}

          {recent.length > 0 && (
            <>
              <h4>Most recent</h4>
              <ul className="g-prot-recent">
                {recent.map((r, i) => (
                  <li key={`${r.name}-${i}`}>
                    <code>{r.name}</code>
                    <span>{r.device}</span>
                    <Badge tone={r.category === "Dark web" ? "alert" : "warn"}>{r.category}</Badge>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        <section className="g-prot-col">
          <h3>{data.defences_host || "This machine"}&rsquo;s own defences</h3>
          {defences.length === 0 ? (
            <p className="g-prot-quiet">
              No sensor has reported its host yet, so there is nothing to show.
            </p>
          ) : (
            <>
              <ul className="g-prot-defences">
                {defences.map((d) => (
                  <li key={d.key} data-state={d.state}>
                    <Badge tone={TONE[d.state] ?? "plain"}>
                      {d.state === "on" ? "on" : d.state === "off" ? "off" : "can’t tell"}
                    </Badge>
                    <span className="g-prot-def-label">{d.label}</span>
                    <span className="g-prot-def-buys">{d.buys}</span>
                  </li>
                ))}
              </ul>
              <p className="g-prot-note">
                {weak.length === 0
                  ? "Nothing here is switched off."
                  : `${weak.length} protection${weak.length === 1 ? " is" : "s are"} off. Each has a one-line fix in System Settings.`}
              </p>
            </>
          )}
          <p className="g-prot-scope">
            Vault checks the machine its sensor runs on. It has no agent on phones, tablets
            or TVs and does not scan them for viruses — those are protected by the filtering on
            the left, which covers every device on the network.
          </p>
        </section>
      </div>

      <ThreatMatrix threats={data.threats ?? []} onOpen={onOpenThreat} detail={threat ?? null} />
    </div>
  );
}

export default ProtectionPanel;
