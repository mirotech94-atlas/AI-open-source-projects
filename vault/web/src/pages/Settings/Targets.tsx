import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useToast } from "../../design";
import { saveSettingsSection } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import type { HostTarget, SettingsPayload, SiteTarget, SuggestedTarget } from "../../types";
import { ADMIN_TOOLTIP, RoleNotice } from "./shared";

/** The predecessor's Config `#cfgsummary`: one card that answers "what is
 *  Vault actually watching?" before the editing tables below it.
 *
 *  Derived entirely from the settings payload already on screen — no new
 *  route, and nothing asserted that this payload cannot prove. Networks,
 *  devices and DNS state live on their own pages and are deliberately not
 *  restated here from stale numbers.
 */
function MonitoredSummary({ settings }: { settings: SettingsPayload }) {
  const { sites, hosts, sensors, autofix } = settings;
  const publicSites = sites.filter((s) => s.public).length;
  const optionalHosts = hosts.filter((h) => h.optional).length;
  const expectations = hosts.reduce(
    (sum, h) => sum + h.expect_services.length + h.expect_docker.length,
    0,
  );
  // `Sensor.status` is pending|active|revoked (models.py). Count anything
  // that is neither waiting to enrol nor revoked as reporting, rather than
  // pinning one literal that a later status word would silently break.
  const reporting = sensors.filter((s) => s.status !== "pending" && s.status !== "revoked").length;

  const rows: [string, string][] = [
    [
      "Websites",
      sites.length === 0
        ? "none yet"
        : `${sites.length} watched${publicSites > 0 ? `, ${publicSites} on the public status page` : ""}`,
    ],
    [
      "Servers",
      hosts.length === 0
        ? "none yet"
        : `${hosts.length} watched${optionalHosts > 0 ? `, ${optionalHosts} optional` : ""}` +
          `${expectations > 0 ? ` · ${expectations} expected services and containers` : ""}`,
    ],
    [
      "Sensors",
      sensors.length === 0
        ? "none enrolled"
        : `${sensors.length} enrolled, ${reporting} reporting`,
    ],
    [
      "Automatic fixes",
      autofix.enabled
        ? `on · up to ${autofix.max_per_day}/day · attackers handled by ${autofix.attacker_method}`
        : "off — every repair waits for a person",
    ],
  ];

  return (
    <section className="g-settings-summary" data-testid="monitored-summary">
      <h3>What is monitored</h3>
      <dl className="g-settings-summary-list">
        {rows.map(([label, value]) => (
          <div key={label} className="g-settings-summary-row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export type TargetsProps = {
  settings: SettingsPayload;
  role: Role | null;
  onInvalidate: () => void;
};

type Draft<T> = T & { _key: string };

let seq = 0;
function nextKey(): string {
  seq += 1;
  return `row-${seq}`;
}

function draftSites(sites: SiteTarget[]): Draft<SiteTarget>[] {
  return sites.map((s) => ({ ...s, _key: nextKey() }));
}

function draftHosts(hosts: HostTarget[]): Draft<HostTarget>[] {
  return hosts.map((h) => ({ ...h, _key: nextKey() }));
}

function isHttpUrl(url: string): boolean {
  return /^https?:\/\/.+/i.test(url.trim());
}

/** Comma-separated editor <-> string array, for `expect_services` /
 *  `expect_docker`. Blank entries (from a trailing comma, or the field
 *  being cleared to empty) are dropped rather than saved as `""`. */
function parseList(value: string): string[] {
  return value
    .split(",")
    .map((v) => v.trim())
    .filter((v) => v.length > 0);
}

function formatList(values: string[]): string {
  return values.join(", ");
}

/** The same comma-separated editor, for a site's `expect` — the list of
 *  acceptable HTTP status codes (`[200]`, or `[200, 307, 302]` for a site
 *  that redirects). Non-numeric and out-of-range entries are dropped
 *  rather than saved as `NaN`. */
function parseCodes(value: string): number[] {
  return value
    .split(",")
    .map((v) => Number(v.trim()))
    .filter((n) => Number.isInteger(n) && n >= 100 && n <= 599);
}

function formatCodes(values: number[]): string {
  return values.join(", ");
}

/** Sites & targets: two editable tables — Websites and Servers — with
 *  add/remove rows and a single Save that POSTs the whole set. Invalid rows
 *  (no name, or a site URL that isn't http/https) are flagged inline and
 *  block the request entirely rather than letting the hub reject a partial
 *  save the admin can't see the reason for. */
export function Targets({ settings, role, onInvalidate }: TargetsProps) {
  const toast = useToast();
  const canWrite = atLeast(role, "admin");

  const [sites, setSites] = useState<Draft<SiteTarget>[]>(() => draftSites(settings.sites));
  const [initialHosts] = useState<Draft<HostTarget>[]>(() => draftHosts(settings.hosts));
  const [hosts, setHosts] = useState<Draft<HostTarget>[]>(initialHosts);
  // Same raw-text-until-blur treatment the services/containers editors get
  // below, and for the same reason: reformatting from the number array on
  // every keystroke would eat the separator the moment a second code is
  // typed.
  const [expectText, setExpectText] = useState<Record<string, string>>(() =>
    Object.fromEntries(sites.map((s) => [s._key, formatCodes(s.expect)])),
  );
  // A suggestion the customer has already taken must leave the list the
  // moment they take it, not on the next refetch -- otherwise pressing Add
  // twice quietly makes two rows for one server.
  const unwatched = (settings.suggested_sites ?? []).filter(
    (sug) => !sites.some((s) => s.url.trim().toLowerCase() === sug.url.toLowerCase()),
  );

  const [siteErrors, setSiteErrors] = useState<Record<string, string>>({});
  const [hostErrors, setHostErrors] = useState<Record<string, string>>({});

  // The services/containers editors are comma-separated free text, but the
  // draft's array is the source of truth for Save. Reformatting the input's
  // value from that array on every keystroke (e.g. `formatList(parseList(v))`)
  // would eat the trailing ", " the moment a second item is typed, so the
  // raw text is tracked separately here (keyed on the same `_key` as
  // `initialHosts`) and only committed to the array (via `parseList`) on blur.
  const [servicesText, setServicesText] = useState<Record<string, string>>(() =>
    Object.fromEntries(initialHosts.map((h) => [h._key, formatList(h.expect_services)])),
  );
  const [containersText, setContainersText] = useState<Record<string, string>>(() =>
    Object.fromEntries(initialHosts.map((h) => [h._key, formatList(h.expect_docker)])),
  );

  const mutation = useMutation({
    mutationFn: () =>
      saveSettingsSection("targets", {
        sites: sites.map(({ _key, ...s }) => s),
        hosts: hosts.map(({ _key, ...h }) => h),
      }),
    onSuccess: () => {
      onInvalidate();
      toast.show("Targets saved.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function updateSite(key: string, patch: Partial<SiteTarget>) {
    setSites((prev) => prev.map((s) => (s._key === key ? { ...s, ...patch } : s)));
  }

  function updateHost(key: string, patch: Partial<HostTarget>) {
    setHosts((prev) => prev.map((h) => (h._key === key ? { ...h, ...patch } : h)));
  }

  function addSite() {
    setSites((prev) => [
      ...prev,
      { _key: nextKey(), name: "", url: "", expect: [200], public: false, host: "", ssh_host: "" },
    ]);
  }

  /** Move a suggestion into the editable list. It lands as an ordinary
   *  unsaved row: the customer can rename it, and nothing is persisted
   *  until they press Save, exactly like one they typed. */
  function adopt(sug: SuggestedTarget) {
    setSites((prev) => [
      ...prev,
      { _key: nextKey(), name: sug.name, url: sug.url, expect: [200],
        public: false, host: "", ssh_host: "" },
    ]);
  }

  function addHost() {
    const key = nextKey();
    setHosts((prev) => [
      ...prev,
      { _key: key, name: "", target: "", optional: false, expect_services: [], expect_docker: [] },
    ]);
    setServicesText((prev) => ({ ...prev, [key]: "" }));
    setContainersText((prev) => ({ ...prev, [key]: "" }));
  }

  function onSave() {
    const nextSiteErrors: Record<string, string> = {};
    for (const s of sites) {
      if (!s.name.trim()) nextSiteErrors[s._key] = "Name is required.";
      else if (!isHttpUrl(s.url)) nextSiteErrors[s._key] = "URL must start with http:// or https://.";
    }
    const nextHostErrors: Record<string, string> = {};
    for (const h of hosts) {
      if (!h.name.trim()) nextHostErrors[h._key] = "Name is required.";
      else if (!h.target.trim()) nextHostErrors[h._key] = "SSH target is required.";
    }
    setSiteErrors(nextSiteErrors);
    setHostErrors(nextHostErrors);
    if (Object.keys(nextSiteErrors).length === 0 && Object.keys(nextHostErrors).length === 0) {
      mutation.mutate();
    }
  }

  return (
    <div className="g-settings-targets">
      {!canWrite && <RoleNotice role={role} required="admin" />}

      <MonitoredSummary settings={settings} />

      <section>
        <h3>Websites</h3>
        <div className="g-table-wrap">
          <table className="g-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">URL</th>
                <th scope="col">Expected codes</th>
                <th scope="col">Host</th>
                <th scope="col">SSH host</th>
                <th scope="col">Public</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {sites.length === 0 && (
                <tr>
                  <td colSpan={7} className="g-table-empty">
                    No sites configured yet.
                  </td>
                </tr>
              )}
              {sites.map((s) => (
                <tr key={s._key}>
                  <td>
                    <input
                      aria-label="Site name"
                      value={s.name}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => updateSite(s._key, { name: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label="Site URL"
                      value={s.url}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => updateSite(s._key, { url: e.target.value })}
                    />
                    {siteErrors[s._key] && <p className="g-field-error">{siteErrors[s._key]}</p>}
                  </td>
                  <td>
                    <input
                      aria-label="Expected status codes"
                      value={expectText[s._key] ?? formatCodes(s.expect)}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      placeholder="200"
                      onChange={(e) => setExpectText((prev) => ({ ...prev, [s._key]: e.target.value }))}
                      onBlur={(e) => updateSite(s._key, { expect: parseCodes(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label="Site host"
                      value={s.host}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => updateSite(s._key, { host: e.target.value })}
                      placeholder="optional server name"
                    />
                  </td>
                  <td>
                    <select
                      aria-label="SSH host running this site"
                      value={s.ssh_host}
                      disabled={!canWrite}
                      title={
                        canWrite
                          ? "The ssh_hosts entry that actually runs this site's web server -- lets " +
                            "Vault repair things like missing security headers instead of only " +
                            "flagging them."
                          : ADMIN_TOOLTIP
                      }
                      onChange={(e) => updateSite(s._key, { ssh_host: e.target.value })}
                    >
                      <option value="">Not on a host I have SSH to</option>
                      {hosts.map((h) => (
                        <option key={h._key} value={h.name}>
                          {h.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      aria-label="Public"
                      type="checkbox"
                      checked={s.public}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => updateSite(s._key, { public: e.target.checked })}
                    />
                  </td>
                  <td className="g-align-right">
                    <button
                      type="button"
                      className="g-danger"
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onClick={() => setSites((prev) => prev.filter((row) => row._key !== s._key))}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button type="button" disabled={!canWrite} title={canWrite ? undefined : ADMIN_TOOLTIP} onClick={addSite}>
          Add site
        </button>

        {/* Items 43/44: an empty Targets tab on a network full of things
            serving web pages is the product asking the customer to type in
            what it already knows. Vault offers; a person presses Add. It
            never writes a target into the config on its own.

            The heading says "answer on a web port" rather than "websites"
            on purpose: half of these are appliance admin pages, and
            Vault cannot tell which. That is worth offering -- an
            unauthenticated printer panel is worth watching -- but not worth
            miscalling. */}
        {unwatched.length > 0 && (
          <div className="g-suggested" data-testid="suggested-targets">
            <h4>These answer on a web port, and nobody is watching them</h4>
            <ul>
              {unwatched.map((sug) => (
                <li key={sug.url}>
                  <span className="g-suggested-n">{sug.name}</span>
                  <span className="g-suggested-u mono">{sug.url}</span>
                  <span className="g-suggested-w">{sug.why}</span>
                  <button
                    type="button"
                    disabled={!canWrite}
                    title={canWrite ? "Add it to the list above — nothing is saved until you press Save" : ADMIN_TOOLTIP}
                    onClick={() => adopt(sug)}
                  >
                    Add
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section>
        <h3>Servers</h3>
        <div className="g-table-wrap">
          <table className="g-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">SSH target</th>
                <th scope="col">Optional</th>
                <th scope="col">Expected services</th>
                <th scope="col">Expected containers</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {hosts.length === 0 && (
                <tr>
                  <td colSpan={6} className="g-table-empty">
                    No servers configured yet.
                  </td>
                </tr>
              )}
              {hosts.map((h) => (
                <tr key={h._key}>
                  <td>
                    <input
                      aria-label="Server name"
                      value={h.name}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => updateHost(h._key, { name: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label="SSH target"
                      value={h.target}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => updateHost(h._key, { target: e.target.value })}
                      placeholder="user@host"
                    />
                    {hostErrors[h._key] && <p className="g-field-error">{hostErrors[h._key]}</p>}
                  </td>
                  <td>
                    <input
                      aria-label="Optional host"
                      type="checkbox"
                      checked={h.optional}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => updateHost(h._key, { optional: e.target.checked })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label="Expected services"
                      value={servicesText[h._key] ?? formatList(h.expect_services)}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => setServicesText((prev) => ({ ...prev, [h._key]: e.target.value }))}
                      onBlur={(e) => updateHost(h._key, { expect_services: parseList(e.target.value) })}
                      placeholder="nginx, postgresql"
                    />
                  </td>
                  <td>
                    <input
                      aria-label="Expected containers"
                      value={containersText[h._key] ?? formatList(h.expect_docker)}
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onChange={(e) => setContainersText((prev) => ({ ...prev, [h._key]: e.target.value }))}
                      onBlur={(e) => updateHost(h._key, { expect_docker: parseList(e.target.value) })}
                      placeholder="guardian-hub, redis"
                    />
                  </td>
                  <td className="g-align-right">
                    <button
                      type="button"
                      className="g-danger"
                      disabled={!canWrite}
                      title={canWrite ? undefined : ADMIN_TOOLTIP}
                      onClick={() => setHosts((prev) => prev.filter((row) => row._key !== h._key))}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button type="button" disabled={!canWrite} title={canWrite ? undefined : ADMIN_TOOLTIP} onClick={addHost}>
          Add server
        </button>
      </section>

      <button
        type="button"
        onClick={onSave}
        disabled={!canWrite || mutation.isPending}
        title={canWrite ? undefined : ADMIN_TOOLTIP}
      >
        Save
      </button>
    </div>
  );
}

export default Targets;
