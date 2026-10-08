import type { CertExpiry, CoverageRow } from "../types";

/**
 * What the sensor can currently SEE, and which certificates are running out.
 *
 * Two cards from the design reference's Overview, and both single-network
 * questions -- which is why they live on the customer portal and not on the
 * owner console, whose subject is customers rather than one estate.
 *
 * COVERAGE is not a count of what exists. The hub upserts whatever the sensor
 * sends and never reconciles what it stopped sending, so a site dropped from
 * the sensor's config keeps its last result forever and shows a frozen check
 * as a current finding (`payloads/staleness` exists because of exactly that).
 * "12 certificates" can be perfectly true while nothing has looked at four of
 * them for a month, and this bar is the difference.
 */

/** Percent, rounded, guarding the empty class. A class with nothing in it is
 *  filtered out by the hub rather than shown as 0/0 -- a row reading "0 / 0"
 *  is an absence, not a measurement, and presenting it as one invites somebody
 *  to go looking for the missing servers. */
function pct(seen: number, total: number): number {
  return total <= 0 ? 0 : Math.round((seen / total) * 100);
}

export function CoveragePanel({ rows }: { rows: CoverageRow[] }) {
  if (rows.length === 0) {
    return <p className="hint">Nothing enrolled yet, so there is nothing to cover.</p>;
  }
  return (
    <ul className="g-cov">
      {rows.map((r) => {
        const p = pct(r.seen, r.total);
        // Full coverage is the ordinary case and recedes; anything short of it
        // is the reason the card exists, so it takes the warning tone. Red is
        // not spent here -- an unwatched asset is work, not an emergency.
        const short = p < 100;
        return (
          <li key={r.label} className="g-cov-row">
            <span className="g-cov-label">{r.label}</span>
            <span className="g-cov-count mono">
              {r.seen} <span className="hint">/ {r.total}</span>
            </span>
            <span className={`g-cov-pct mono${short ? " g-cov-short" : ""}`}>{p}%</span>
            <span className="g-cov-track" aria-hidden="true">
              <i className={short ? "g-cov-fill g-cov-fill-short" : "g-cov-fill"}
                 style={{ width: `${p}%` }} />
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Days left, coloured by how close it is. Under a fortnight is red because a
 *  certificate that lapses takes the site down; under a month is amber. */
function certTone(days: number | null): string {
  if (days === null) return "";
  if (days < 14) return " g-cert-crit";
  if (days < 30) return " g-cert-warn";
  return "";
}

/**
 * `read` and `sites` come from the Coverage card's own Certificates row, so
 * the two cards cannot disagree about the same three sites.
 *
 * An empty list means one of two very different things, and the first
 * version of this card said the reassuring one regardless: "nothing expiring"
 * -- while the Coverage card beside it read 0 / 3, meaning Vault had not
 * read a certificate for ANY site. Silence about certificates is not good news
 * about certificates. It is the absence of the check, and a card that reports
 * it as safety is the exact thing `payloads/staleness` exists to stop.
 */
export function CertsExpiringPanel({ rows, read, sites }: { rows: CertExpiry[]; read: number; sites: number }) {
  if (rows.length === 0) {
    if (sites > 0 && read === 0) {
      return (
        <p className="hint g-cert-unread">
          Vault has not read a certificate for any of your {sites}{" "}
          {sites === 1 ? "site" : "sites"} yet, so it cannot say when one expires.
          That is a gap in what it can see, not a clean bill.
        </p>
      );
    }
    if (sites > 0 && read < sites) {
      return (
        <p className="hint">
          Nothing expiring in the next 90 days among the {read} of {sites}{" "}
          {sites === 1 ? "site" : "sites"} Vault has a certificate for. The other{" "}
          {sites - read} {sites - read === 1 ? "is" : "are"} unread.
        </p>
      );
    }
    return (
      <p className="hint">
        Nothing expiring in the next 90 days. Vault renews what it is allowed to
        renew without asking.
      </p>
    );
  }
  return (
    <ul className="g-cert">
      {rows.map((r) => (
        <li key={r.host} className="g-cert-row">
          <span className="g-cert-host">
            <span className="mono">{r.host}</span>
            {r.issuer !== "" && <span className="hint">{r.issuer}</span>}
          </span>
          <span className="g-cert-days">
            <b className={`mono${certTone(r.days_left)}`}>{r.days_left ?? "—"}</b>
            <span className="hint">days left</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
