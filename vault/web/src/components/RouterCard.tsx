import { useEffect, useState } from "react";
import { Badge } from "../design";
import type { RouterCard as RouterCardData, RouterSaveBody } from "../lib/api";

/** The site gateway router card, in the Integrations tab's "Devices Vault
 *  acts on" section.
 *
 *  It is NOT an `IntegrationCardView`: a notification channel delivers
 *  outbound over routes; this holds a LAN admin credential the sensor -- the
 *  only thing that can reach the gateway -- uses to run a login-only Test. The
 *  password is write-only (never returned), so a configured card shows "saved"
 *  and a Replace control, exactly like an integration secret. Nothing here
 *  changes a router setting; the Test logs in and straight back out. */
export type RouterCardProps = {
  card: RouterCardData;
  saving: boolean;
  testing: boolean;
  disabling: boolean;
  onSave: (body: RouterSaveBody) => void;
  onTest: () => void;
  onRemove: () => void;
  onDisableRemoteAdmin: () => void;
};

type Tone = "good" | "warn" | "alert" | "plain";

/** "17 Sep 2026" from an ISO stamp — a date, because the useful question is
 *  "has this been done", not "at what second". */
function dayLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function statusTone(card: RouterCardData): { tone: Tone; label: string } {
  if (!card.configured || !card.has_credentials) return { tone: "plain", label: "Not set up" };
  if (card.last_test_ok === true) return { tone: "good", label: "Credential verified" };
  if (card.last_test_ok === false) return { tone: "alert", label: "Test failed" };
  return { tone: "warn", label: "Not tested yet" };
}

export function RouterCard({ card, saving, testing, disabling, onSave, onTest, onRemove, onDisableRemoteAdmin }: RouterCardProps) {
  const [baseUrl, setBaseUrl] = useState(card.base_url || "https://192.168.1.1");
  const [username, setUsername] = useState(card.username || "");
  // Write-only secret UX: a configured card keeps its stored password until
  // the admin explicitly chooses Replace, mirroring an integration secret.
  const [replacing, setReplacing] = useState(!card.has_credentials);
  const [password, setPassword] = useState("");

  useEffect(() => {
    setBaseUrl(card.base_url || "https://192.168.1.1");
    setUsername(card.username || "");
    setReplacing(!card.has_credentials);
    setPassword("");
  }, [card.base_url, card.username, card.has_credentials, card.last_test]);

  const { tone, label } = statusTone(card);
  const canTest = card.has_credentials && !replacing && card.enabled;
  // Vault has run the disable on this gateway at least once. This is a
  // record of the ACT, not a reading of the router: the gateway exposes no way
  // to query the current value, so the copy below credits the run and dates it
  // rather than asserting the setting is off right now. Before this existed
  // the card re-offered the action to an owner who had already pressed it and
  // watched it succeed.
  const done = !!card.remote_admin_off_at;
  const whenOff = done ? dayLabel(card.remote_admin_off_at) : "";

  function save() {
    const body: RouterSaveBody = { base_url: baseUrl.trim(), username: username.trim() };
    // Only send the password when the admin actually typed a new one — an
    // untouched configured card keeps its stored credential.
    if (replacing) body.password = password;
    onSave(body);
  }

  const savable = baseUrl.trim().length > 0 && (!replacing || password.length > 0);

  return (
    <article className="g-router-card" data-testid="router-card" data-tone={tone}>
      <header className="g-router-card-head">
        <div className="g-router-card-title">
          <span className="g-router-card-icon" aria-hidden="true">
            {/* A router: three status lights over a slab. */}
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <rect x="3" y="13" width="18" height="7" rx="1.6" />
              <circle cx="7" cy="16.5" r="0.9" fill="currentColor" stroke="none" />
              <path d="M12 13V9" />
              <path d="M9 9.5a4 4 0 0 1 6 0" />
              <path d="M7 7.5a7 7 0 0 1 10 0" />
            </svg>
          </span>
          <div>
            <h3>{card.vendor_label || "Home gateway"}</h3>
            <p className="g-router-card-sub">The router Vault can administer on your LAN</p>
          </div>
        </div>
        <Badge tone={tone}>{label}</Badge>
      </header>

      <p className="g-router-card-note">
        Vault&rsquo;s on-site sensor is the only thing that can reach your gateway. Save the admin
        password here and it logs in to confirm it works. It never changes a router setting on its own.
      </p>

      <div className="g-router-card-fields">
        <label className="g-router-field">
          <span>Router address</span>
          <input
            type="text"
            value={baseUrl}
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://192.168.1.1"
          />
          <span className="g-router-field-hint">A private LAN address, e.g. https://192.168.1.1</span>
        </label>

        <label className="g-router-field">
          <span>Username <em>optional</em></span>
          <input
            type="text"
            value={username}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Leave blank to use the gateway's default, admin"
          />
        </label>

        <div className="g-router-field">
          <span>Admin password</span>
          {card.has_credentials && !replacing ? (
            <div className="g-router-secret-saved">
              <span className="g-router-secret-mask" aria-label="A password is stored">
                •••••••• saved
              </span>
              <button type="button" className="g-btn-ghost" onClick={() => setReplacing(true)}>
                Replace
              </button>
            </div>
          ) : (
            <>
              <input
                type="password"
                value={password}
                autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)}
                placeholder={card.has_credentials ? "Enter a new password" : "Router admin password"}
              />
              {card.has_credentials && (
                <button
                  type="button"
                  className="g-btn-ghost g-router-secret-cancel"
                  onClick={() => {
                    setReplacing(false);
                    setPassword("");
                  }}
                >
                  Cancel
                </button>
              )}
              <span className="g-router-field-hint">Stored encrypted. It is never shown again after saving.</span>
            </>
          )}
        </div>
      </div>

      {card.last_test_msg && (
        <p className={`g-router-card-result g-router-card-result-${card.last_test_ok ? "ok" : "bad"}`} role="status">
          {card.last_test_ok ? "Last test: " : "Last test failed: "}
          {card.last_test_msg}
        </p>
      )}

      {card.last_test_ok === true && (
        <div className="g-router-action" data-done={done ? "1" : undefined}>
          <div className="g-router-action-copy">
            <h4>Remote administration</h4>
            {done ? (
              <p>
                Vault turned this off on {whenOff}. Remote management over IPv4 and IPv6, and
                HTTPS admin, were all set to disabled. Your router doesn&rsquo;t report this setting
                back, so run it again if it was switched on from the router&rsquo;s own page.
              </p>
            ) : (
              <p>
                Turn off remote management so the router admin page can&rsquo;t be reached from the
                internet. Vault writes this one setting; local admin from your own network still works.
              </p>
            )}
          </div>
          <button
            type="button"
            className={done ? "g-btn-ghost" : "g-btn-warn"}
            onClick={onDisableRemoteAdmin}
            disabled={disabling}
          >
            {disabling
              ? "Turning off…"
              : done
                ? "Turn off again"
                : "Turn off remote administration"}
          </button>
        </div>
      )}

      <footer className="g-router-card-actions">
        <button type="button" className="g-btn-primary" onClick={save} disabled={saving || !savable}>
          {saving ? "Saving…" : card.configured ? "Save changes" : "Save router"}
        </button>
        <button type="button" className="g-btn" onClick={onTest} disabled={!canTest || testing}>
          {testing ? "Testing…" : "Test connection"}
        </button>
        {card.configured && (
          <button type="button" className="g-btn-ghost g-router-remove" onClick={onRemove} disabled={saving}>
            Remove
          </button>
        )}
      </footer>
      {!canTest && card.has_credentials && replacing && (
        <p className="g-router-card-hint">Save the new password before you can test it.</p>
      )}
    </article>
  );
}

export default RouterCard;
