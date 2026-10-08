import { useEffect, useState } from "react";
import type { IntegrationCard } from "../types";
import type { Role } from "../lib/auth";
import { atLeast } from "../lib/auth";
import type { Tone } from "../lib/format";
import { ago } from "../lib/format";
import { Badge } from "../design";
import { InboundHint } from "./InboundHint";
import { RouteMatrix } from "./RouteMatrix";

export type IntegrationSaveBody = {
  enabled: boolean;
  routes: Record<string, boolean>;
  /** Only the fields the admin actually touched this session — a masked
   *  secret's placeholder is never one of these keys. */
  fields: Record<string, string>;
};

export type TestOutcome = { ok: boolean; error: string; detail?: string };

export type IntegrationCardViewProps = {
  card: IntegrationCard;
  role: Role | null;
  onSave: (kind: string, body: IntegrationSaveBody) => void;
  saving: boolean;
  onTest: (kind: string) => void;
  testing: boolean;
  /** The most recent in-session Test result for this card, or `null` before
   *  the admin has clicked Test at all this visit — in which case the card
   *  falls back to the persisted `last_error`/`last_test`. */
  testResult: TestOutcome | null;
  /** Telegram only: the pairing code once minted, and the pair action. Every
   *  other kind passes neither. */
  pairingCode?: string | null;
  /** Telegram only: true once the minted code's `expires` timestamp has
   *  passed without pairing completing — shows an expiry message instead
   *  of "waiting…" and offers pairing again. */
  pairingExpired?: boolean;
  onPair?: () => void;
  pairing?: boolean;
  /** Increments once per successful save of this card. Used only to reset
   *  `replacing` so a secret field re-masks after the hub has accepted the
   *  new value — never read for anything else. */
  saveOkTick?: number;
  /** Present for a configured, disconnectable channel; the parent owns the
   *  confirmation step and the actual delete call. ntfy never gets one — it
   *  falls back to the tenant's default topic and can't really be removed. */
  onDisconnect?: (kind: string) => void;
  disconnecting?: boolean;
};

const ADMIN_TOOLTIP = "admin role required";

// Explicit allow-list rather than `direction.includes("in")` — the hub's
// `direction` values are exactly `"out" | "out+in" | "out+command" |
// "out+status"` (see Task 40/41 plan text), and `"out+command"` (Discord)
// does not contain the substring "in" even though Discord *is*
// inbound-capable (it takes interaction-endpoint calls). List the two
// literals that actually receive inbound calls instead of pattern-matching.
const INBOUND_DIRECTIONS = new Set(["out+in", "out+command"]);

// Which of the four states a card is in, purely off the fields already on
// `card` — nothing here is fetched separately. Order matters: a configured
// channel that last failed its own send is "attention" even if it is also
// disabled, because the failure is the thing that needs a look first.
type CardStateName = "attention" | "connected" | "paused" | "dormant";

function cardStateOf(card: IntegrationCard): CardStateName {
  if (card.configured && card.last_error) return "attention";
  if (card.configured && card.enabled) return "connected";
  if (card.configured) return "paused";
  return "dormant";
}

// The grid's visual order (via CSS `order`, not DOM order — tests and
// screen-reader document order both stay in the hub's own list order).
// Broken comes first because "is anything broken?" is the first question
// this page has to answer; dormant, never-configured channels sink to the
// back since there is nothing to check on them.
const STATE_ORDER: Record<CardStateName, number> = {
  attention: 0,
  connected: 1,
  paused: 2,
  dormant: 3,
};
const STATE_TONE: Record<CardStateName, Tone> = {
  attention: "alert",
  connected: "good",
  paused: "plain",
  dormant: "plain",
};
const STATE_LABEL: Record<CardStateName, string> = {
  attention: "Needs attention",
  connected: "Connected",
  paused: "Paused",
  dormant: "Not connected",
};

/** One integration: title + one-line "what this does for you", the enabled
 *  switch, its `fields` rendered generically by `kind`, the severity
 *  `RouteMatrix`, a Test button with a verbatim result line, the inbound
 *  URL when this integration can receive calls, and (Telegram only) the
 *  pairing flow.
 *
 *  Local edit state is seeded once from `card` at mount and never
 *  re-synced from a later prop — a save that fails (or a background
 *  refetch) must not silently clear what the admin just typed. Read-only
 *  display fields (`configured`, `last_test`, `last_error`, `inbound_url`)
 *  are read straight from the live `card` prop on every render instead,
 *  so a Test result or a completed Telegram pairing still shows up. */
export function IntegrationCardView({
  card,
  role,
  onSave,
  saving,
  onTest,
  testing,
  testResult,
  pairingCode = null,
  pairingExpired = false,
  onPair,
  pairing = false,
  saveOkTick = 0,
  onDisconnect,
  disconnecting = false,
}: IntegrationCardViewProps) {
  const canWrite = atLeast(role, "admin");
  const state = cardStateOf(card);
  const tone = STATE_TONE[state];

  // Seeded once, from the card this component first mounted with — never
  // re-synced from a later prop, so a failed save (or a background
  // refetch that changes `card` identity) cannot silently clear what the
  // admin is mid-way through typing. Kept in state rather than a ref: a
  // ref's `.current` must not be read during render.
  const [originalFields] = useState(() => card.fields);
  const [enabled, setEnabled] = useState(card.enabled);
  const [routes, setRoutes] = useState<Record<string, boolean>>(card.routes);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      originalFields.map((f) => [f.name, f.kind === "secret" ? "" : f.value]),
    ),
  );
  const [replacing, setReplacing] = useState<Set<string>>(new Set());
  // A channel nobody has set up is one line -- its name, what it does, and
  // a "Set up" button -- not an empty form. Nine empty forms at once was
  // most of the whitespace on this page. Opens on click and stays open;
  // a configured channel is always open, because its fields are its state.
  const [open, setOpen] = useState<boolean>(card.configured);

  // A successful save means the hub accepted whatever secret was typed —
  // re-mask it rather than leaving the (now stale) plaintext input open.
  useEffect(() => {
    if (saveOkTick > 0) setReplacing(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveOkTick]);

  function startReplace(name: string) {
    setReplacing((prev) => new Set(prev).add(name));
    setValues((prev) => ({ ...prev, [name]: "" }));
  }

  function cancelReplace(name: string) {
    setReplacing((prev) => {
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
    setValues((prev) => ({ ...prev, [name]: "" }));
  }

  function setValue(name: string, value: string) {
    setValues((prev) => ({ ...prev, [name]: value }));
  }

  function changedFields(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const f of originalFields) {
      if (f.kind === "secret") {
        if (replacing.has(f.name) && values[f.name])
          out[f.name] = values[f.name];
      } else if (values[f.name] !== f.value) {
        out[f.name] = values[f.name];
      }
    }
    return out;
  }

  function handleSave() {
    onSave(card.kind, { enabled, routes, fields: changedFields() });
  }

  const errorText = testResult
    ? testResult.ok
      ? ""
      : testResult.error
    : card.last_error;
  const showOk = testResult?.ok === true;

  return (
    <article
      className={`g-integrations-card g-tone-${tone}`}
      data-testid="integration-card"
      data-state={state}
      style={{ order: STATE_ORDER[state] }}
    >
      <header className="g-integrations-card-head">
        <div>
          <div className="g-integrations-card-title-row">
            <h3 className="g-integrations-card-title">{card.title}</h3>
            <Badge tone={tone}>{STATE_LABEL[state]}</Badge>
          </div>
          <p className="g-integrations-card-help">{card.help}</p>
        </div>
        {open ? (
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label={`${card.title} enabled`}
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            className={enabled ? "g-toggle-on" : undefined}
            onClick={() => canWrite && setEnabled((v) => !v)}
          >
            {enabled ? "On" : "Off"}
          </button>
        ) : (
          <button
            type="button"
            className="g-integrations-setup"
            aria-label={`Set up ${card.title}`}
            disabled={!canWrite}
            title={canWrite ? undefined : ADMIN_TOOLTIP}
            onClick={() => setOpen(true)}
          >
            Set up
          </button>
        )}
      </header>

      {open && (
        <>
          <div className="g-integrations-fields">
            {originalFields.map((f) => {
              const inputId = `integration-${card.kind}-${f.name}`;
              const isSecret = f.kind === "secret";
              const isReplacing = replacing.has(f.name);

              if (isSecret && !isReplacing) {
                return (
                  <div className="g-integrations-field" key={f.name}>
                    <span className="g-integrations-field-label">
                      {f.label}
                    </span>
                    <div className="g-integrations-field-secret">
                      <span className="mono">{f.value || "not set"}</span>
                      <button
                        type="button"
                        disabled={!canWrite}
                        title={canWrite ? undefined : ADMIN_TOOLTIP}
                        onClick={() => startReplace(f.name)}
                      >
                        Replace
                      </button>
                    </div>
                  </div>
                );
              }

              const showReplaceHelp = isSecret && isReplacing;

              return (
                <div className="g-integrations-field" key={f.name}>
                  <label htmlFor={inputId}>{f.label}</label>
                  {showReplaceHelp && (
                    <p className="hint">
                      Leave blank to keep the current secret.{" "}
                      <button
                        type="button"
                        onClick={() => cancelReplace(f.name)}
                      >
                        Cancel
                      </button>
                    </p>
                  )}
                  {f.kind === "select" ? (
                    <select
                      id={inputId}
                      value={values[f.name] ?? ""}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => setValue(f.name, e.target.value)}
                    >
                      {f.options.map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={inputId}
                      type={
                        isSecret
                          ? "password"
                          : f.kind === "url"
                            ? "url"
                            : "text"
                      }
                      value={values[f.name] ?? ""}
                      required={f.required}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => setValue(f.name, e.target.value)}
                    />
                  )}
                </div>
              );
            })}
          </div>

          <RouteMatrix
            routes={routes}
            disabled={!canWrite}
            disabledTitle={ADMIN_TOOLTIP}
            onChange={(sev, checked) =>
              setRoutes((prev) => ({ ...prev, [sev]: checked }))
            }
          />

          {INBOUND_DIRECTIONS.has(card.direction) && card.inbound_url && (
            <InboundHint kind={card.kind} url={card.inbound_url} />
          )}

          {card.kind === "telegram" && onPair && (
            <div className="g-integrations-pair">
              {card.configured ? (
                <p className="hint">Paired.</p>
              ) : pairingExpired ? (
                <p>
                  Pairing code expired —{" "}
                  <button
                    type="button"
                    disabled={!canWrite || pairing}
                    title={canWrite ? undefined : ADMIN_TOOLTIP}
                    onClick={onPair}
                  >
                    pair again
                  </button>
                  .
                </p>
              ) : pairingCode ? (
                <p>
                  Pairing code: <code className="mono">{pairingCode}</code> —
                  send “/start {pairingCode}” to the bot. Waiting for it to be
                  used…
                </p>
              ) : (
                <button
                  type="button"
                  disabled={!canWrite || pairing}
                  title={canWrite ? undefined : ADMIN_TOOLTIP}
                  onClick={onPair}
                >
                  {pairing ? "Pairing…" : "Pair"}
                </button>
              )}
            </div>
          )}

          <footer className="g-integrations-card-foot">
            <div className="g-integrations-test">
              <button
                type="button"
                disabled={!canWrite || testing}
                title={canWrite ? undefined : ADMIN_TOOLTIP}
                onClick={() => onTest(card.kind)}
              >
                {testing ? "Testing…" : "Test"}
              </button>
              {showOk && (
                <span className="g-tone-good">
                  OK{testResult?.detail ? ` · ${testResult.detail}` : ""}
                </span>
              )}
              {!showOk && errorText && (
                <span className="g-tone-alert">{errorText}</span>
              )}
              <span className="hint">
                Last tested: {card.last_test ? ago(card.last_test) : "never"}
              </span>
            </div>

            <div className="g-integrations-card-actions">
              {card.configured && card.kind !== "ntfy" && onDisconnect && (
                <button
                  type="button"
                  className="g-danger"
                  disabled={!canWrite || disconnecting}
                  title={canWrite ? undefined : ADMIN_TOOLTIP}
                  onClick={() => onDisconnect(card.kind)}
                >
                  {disconnecting ? "Disconnecting…" : "Disconnect"}
                </button>
              )}
              <button
                type="button"
                disabled={!canWrite || saving}
                title={canWrite ? undefined : ADMIN_TOOLTIP}
                onClick={handleSave}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </footer>
        </>
      )}
    </article>
  );
}

export default IntegrationCardView;
