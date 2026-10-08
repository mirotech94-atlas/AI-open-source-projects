import { Badge, Drawer } from "../design";
import type { ThreatClass, ThreatDetail, ThreatFinding } from "../types";

/** How each stance reads on the card, and what tone it earns.
 *
 *  `effect-only` and `indirect` are the honest ones: they exist so a card can
 *  say Vault does NOT stop this, in the same visual language as the cards
 *  that say it does. Never promote one of them to make the grid look better.
 */
const STANCE: Record<string, { word: string; tone: "good" | "warn" | "alert" | "plain" }> = {
  blocking: { word: "Blocked", tone: "good" },
  watching: { word: "Watched", tone: "good" },
  "effect-only": { word: "Damage only", tone: "warn" },
  indirect: { word: "Odds reduced", tone: "warn" },
};

const SEV_TONE: Record<string, "alert" | "warn" | "plain"> = {
  CRIT: "alert", WARN: "warn", INFO: "plain",
};

/**
 * One card per named kind of attack, answering the only question a person
 * actually has: does Vault do anything about this, and is anything wrong
 * right now?
 *
 * The card leads with the stance, not the marketing. A card whose stance is
 * `effect-only` states plainly that Vault watches the damage and cannot
 * stop the attack — that admission is the feature.
 */
export type ThreatMatrixProps = {
  threats: ThreatClass[];
  /** Called with the class key when a card is opened. The caller owns the
   *  fetch — this component only reports which card was chosen, the same
   *  division `AttackMap` uses for its own drawer. */
  onOpen?: (key: string) => void;
  /** The detail for the card currently open, once it has arrived. Absent
   *  while loading, which is why the drawer renders its own waiting line
   *  rather than assuming data. */
  detail?: ThreatDetail | null;
};

/** How one finding reads.
 *
 *  The distinction that matters: a settled alert WITH a fix means Vault
 *  repaired something; a settled alert with none means a person looked at it
 *  and decided it needed no repair. Those are different stories and the drawer
 *  must not merge them — "resolved" alone would let the second read as the
 *  first, which is Vault taking credit for work nobody did.
 */
function outcome(f: ThreatFinding): string {
  if (!f.resolved) return "Still open";
  if (f.fix) return `Vault ran ${f.fix}`;
  const who = f.resolved_by ? ` by ${f.resolved_by}` : "";
  return `Settled${who} without a repair`;
}

export function ThreatMatrix({ threats, onOpen, detail }: ThreatMatrixProps) {
  if (threats.length === 0) return null;
  return (
    <section className="g-threats" data-testid="threats">
      <h3>Attacks, and what Vault does about each</h3>
      <ul className="g-threat-grid">
        {threats.map((t) => {
          const st = STANCE[t.stance] ?? { word: t.stance, tone: "plain" as const };
          return (
            <li key={t.key} className="g-threat" data-stance={t.stance} data-open={t.open_count > 0 ? "1" : undefined}>
              <header>
                {/* The whole card is the control. A card states a verdict, and
                    opening it shows the evidence for that verdict -- the same
                    move `/security/checks/detail` makes for a failing check. */}
                <button type="button" className="g-threat-open" onClick={() => onOpen?.(t.key)}>
                  <h4>{t.label}</h4>
                </button>
                <Badge tone={st.tone}>{st.word}</Badge>
              </header>
              <p className="g-threat-what">{t.what}</p>
              <p className="g-threat-does">{t.does}</p>
              {t.limit && <p className="g-threat-limit">{t.limit}</p>}

              <div className="g-threat-now">
                {t.open_count === 0 ? (
                  <span className="g-threat-clear">
                    Nothing open{t.blocked > 0 ? ` · ${t.blocked.toLocaleString()} blocked this week` : ""}
                  </span>
                ) : (
                  <>
                    <span className="g-threat-count">
                      {t.open_count} open {t.open_count === 1 ? "signal" : "signals"}
                    </span>
                    <ul className="g-threat-signals">
                      {t.signals.map((s) => (
                        <li key={s.key}>
                          <Badge tone={SEV_TONE[s.severity] ?? "plain"}>{s.severity}</Badge>
                          <span>{s.title}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <Drawer
        open={Boolean(detail)}
        onClose={() => onOpen?.("")}
        title={detail?.label ?? ""}
        subtitle={detail?.what || undefined}
      >
        {detail && (
          <>
            <p className="g-threat-does">{detail.does}</p>
            {/* Carried through from the card on purpose. For `dos` and
                `ransomware` the honest answer to "how did you solve it" is
                that Vault did not and cannot; `ThreatMatrix`'s own rule is
                that the admission is the feature, and a drawer that dropped it
                would turn the honest card into a misleading popup. */}
            {detail.limit && <p className="g-threat-limit">{detail.limit}</p>}

            <p className="g-threat-tally">
              {/* Two counts, never one. `blocked` is lookups refused over the
                  window; findings are alerts. Summing them would print a
                  number that means nothing. */}
              {detail.open_count} open · {detail.resolved_count} settled in {detail.days} days
              {detail.blocked > 0 && ` · ${detail.blocked.toLocaleString()} lookups refused`}
            </p>

            {detail.findings.length === 0 ? (
              <p className="g-threat-clear">Nothing found for this in the last {detail.days} days.</p>
            ) : (
              <ul className="g-threat-findings">
                {detail.findings.map((f) => (
                  <li key={f.key} data-resolved={f.resolved ? "1" : undefined}>
                    <div className="g-threat-finding-head">
                      <Badge tone={SEV_TONE[f.severity] ?? "plain"}>{f.severity}</Badge>
                      <strong>{f.title}</strong>
                    </div>
                    {f.why && <p className="g-threat-why">{f.why}</p>}
                    <p className="g-threat-outcome">{outcome(f)}</p>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Drawer>
    </section>
  );
}

export default ThreatMatrix;
