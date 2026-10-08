import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { dnsPayload, sitesPayload } from "../lib/api";
import { useActiveTenant } from "./net";

/** Websites & DNS -- every endpoint Vault checks, and the resolver. The
 *  reference pairs websites with DNS zones; Vault's DNS is its own
 *  resolver on the customer's network, so the right-hand card reports that
 *  instead: whether it is running, how much it sees, how much it blocks. */
function Sites() {
  const tenant = useActiveTenant();
  const on = tenant !== null;
  const sites = useQuery({ queryKey: ["sites"], queryFn: sitesPayload, refetchInterval: 60_000, enabled: on });
  const dns = useQuery({ queryKey: ["dns", 24, null], queryFn: () => dnsPayload(24, null), refetchInterval: 60_000, enabled: on });

  if (tenant === null) return <p className="g-own-lede">Pick a customer in the bar to see their websites.</p>;
  if (sites.isPending) return <p role="status">Reading endpoints…</p>;

  const rows = sites.data?.sites ?? [];
  const st = dns.data?.status;
  const dnsState = !st ? { label: "not read", tone: "plain" }
    : !st.enabled ? { label: "off", tone: "plain" }
    : !st.running ? { label: "down", tone: "crit" }
    : { label: "running", tone: "good" };

  return (
    <div className="g-own-split g-own-split-sites">
      <section aria-label="Websites" className="g-own-section">
        <h2>Websites <span className="g-own-meta">{rows.length} {rows.length === 1 ? "endpoint" : "endpoints"}</span></h2>
        {rows.length === 0 ? (
          <p className="g-own-quiet">No websites configured for {tenant.name}.</p>
        ) : (
          <ul className="g-own-rows">
            {rows.map((s) => {
              let host = s.url;
              try { host = new URL(s.url).host; } catch { /* raw */ }
              const note = s.stale ? s.stale_note || "Not checked recently"
                : s.ok === false ? `Down${s.code ? ` · HTTP ${s.code}` : ""}`
                : s.cert_days !== null && s.cert_days < 30 ? `Certificate expires in ${s.cert_days} days`
                : s.cert_issuer ? `${s.cert_issuer}${s.public ? "" : " · internal"}` : s.public ? "public" : "internal";
              return (
                <li key={s.name} className="g-own-site">
                  <span className={`g-own-dot g-tone-${s.ok === true && !s.stale ? "good" : s.ok === false ? "alert" : "plain"}`} aria-hidden="true" />
                  <span className="g-own-site-main">
                    <Link to={`/owner/assets/site/${encodeURIComponent(s.name)}`} className="g-own-site-host">{host}</Link>
                    <span className={`g-own-site-note${s.ok === false || (s.cert_days !== null && s.cert_days < 14) ? " g-own-crit" : ""}`}>{note}</span>
                  </span>
                  <span className="g-own-site-side">
                    <span className="g-own-site-ms">{s.ms === null ? "—" : `${s.ms} ms`}</span>
                    <span className="g-own-site-up">{s.uptime_7d === null ? "no 7-day figure" : `${s.uptime_7d}% · 7 d`}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-label="DNS" className="g-own-section">
        <h2>DNS <span className={`g-own-pill g-own-pill-${dnsState.tone} g-own-h2-pill`}>{dnsState.label}</span></h2>
        <p className="g-own-sub">Vault's own resolver on this network. A device that never asks it is invisible to these figures.</p>
        {st ? (
          <dl className="g-own-grid2">
            <div><dt>Queries, 24 h</dt><dd>{st.queries_24h.toLocaleString()}</dd></div>
            <div><dt>Blocked, 24 h</dt><dd>{st.blocked_24h.toLocaleString()} <span className="g-own-dim">· {st.block_pct}%</span></dd></div>
            <div><dt>Devices using it</dt><dd className={st.coverage_pct < 100 ? "g-own-warn" : ""}>{st.clients_using} / {st.devices_total}</dd></div>
            <div><dt>Upstream</dt><dd>{st.upstream_ms_avg === null ? "—" : `${Math.round(st.upstream_ms_avg)} ms`}</dd></div>
          </dl>
        ) : (
          <p className="g-own-quiet">{dns.isPending ? "Reading the resolver…" : "The resolver has not reported."}</p>
        )}
      </section>
    </div>
  );
}

export default Sites;
