import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { StatStrip, Verdict } from "../design";
import { sitesPayload } from "../lib/api";
import { shortDate, useActiveTenant } from "./net";

/** Every certificate Vault watches, soonest to expire first. Red under
 *  14 days, amber under 30 -- the reference's thresholds. A site whose
 *  certificate could not be read says so rather than sitting at the bottom
 *  looking healthy. */
function Certificates() {
  const tenant = useActiveTenant();
  const q = useQuery({ queryKey: ["sites"], queryFn: sitesPayload, refetchInterval: 60_000, enabled: tenant !== null });

  if (tenant === null) return <p className="g-own-lede">Pick a customer in the bar to see their certificates.</p>;
  if (q.isPending) return <p role="status">Reading certificates…</p>;

  const rows = [...(q.data?.sites ?? [])].sort((a, b) => (a.cert_days ?? 9999) - (b.cert_days ?? 9999));
  const soon = rows.filter((s) => s.cert_days !== null && s.cert_days < 30).length;

  const urgent = rows.filter((s) => s.cert_days !== null && s.cert_days < 14).length;
  const unread = rows.filter((s) => s.cert_days === null).length;
  return (
    <div className="g-own-page">
      <Verdict tone={urgent > 0 ? "alert" : soon > 0 ? "warn" : "good"}
               headline={urgent > 0 ? `${urgent} certificate${urgent === 1 ? "" : "s"} expire${urgent === 1 ? "s" : ""} within 14 days`
                 : soon > 0 ? `${soon} certificate${soon === 1 ? "" : "s"} within 30 days` : "Nothing needs you"}
               body={rows.length === 0 ? "No websites configured, so nothing to watch." : "Vault warns early; renewing is yours to do. Under 14 days is red, under 30 amber."} />
      <StatStrip items={[
        { label: "Watched", value: String(rows.length) },
        { label: "Within 30 days", value: String(soon), deltaTone: soon > 0 ? "warn" : undefined },
        { label: "Within 14 days", value: String(urgent), deltaTone: urgent > 0 ? "alert" : undefined },
        { label: "Could not be read", value: String(unread), deltaTone: unread > 0 ? "warn" : undefined },
      ]} />
      <section aria-label="Certificates" className="g-own-section">
        <h2>Certificates <span className="g-own-meta">{rows.length} watched · {soon} within 30 d</span></h2>
        {rows.length === 0 ? (
          <p className="g-own-quiet">No websites configured for {tenant.name}, so no certificates to watch.</p>
        ) : (
          <div className="g-own-tablewrap">
            <table className="g-own-table">
              <thead>
                <tr>
                  <th scope="col">Host</th>
                  <th scope="col">Issuer</th>
                  <th scope="col">Expires</th>
                  <th scope="col" className="g-own-num">Days left</th>
                  <th scope="col" className="g-own-num">State</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  let host = s.url;
                  try { host = new URL(s.url).host; } catch { /* raw */ }
                  const d = s.cert_days;
                  const tone = d === null ? "plain" : d < 14 ? "crit" : d < 30 ? "warn" : "good";
                  return (
                    <tr key={s.name}>
                      <td><Link to={`/owner/assets/site/${encodeURIComponent(s.name)}`} className="g-own-mono">{host}</Link></td>
                      <td className="g-own-dim">{s.cert_issuer || "—"}</td>
                      <td className="g-own-mono g-own-dim">{s.cert_expires ? shortDate(s.cert_expires) : "—"}</td>
                      <td className={`g-own-num g-own-mono${tone === "crit" ? " g-own-crit" : tone === "warn" ? " g-own-warn" : ""}`}>{d === null ? "—" : d}</td>
                      <td className="g-own-num">
                        <span className={`g-own-pill g-own-pill-${tone}`}>
                          {d === null ? "not read" : d < 0 ? "expired" : d < 14 ? `cert ${d} d` : d < 30 ? "renew soon" : "valid"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="g-own-note">Vault reads each certificate on every check of the site. Renewal is the site's own job; Vault tells you when it is late.</p>
      </section>
    </div>
  );
}

export default Certificates;
