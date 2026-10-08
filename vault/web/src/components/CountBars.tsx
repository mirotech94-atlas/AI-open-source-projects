import { machineClass } from "../lib/machine";
import { num } from "../lib/format";

export type CountBarRow = {
  /** The row's label — a domain, a device name, a vendor. */
  name: string;
  count: number;
  /** Hover text, where the label alone is not the whole story (a device's IP). */
  title?: string;
  /** Stable identity when labels can repeat (two devices both called
   *  "iPhone"). Falls back to `name`, which is unique for domains. */
  id?: string;
};

export type CountBarsProps = {
  rows: CountBarRow[];
  /** `alert` paints the fill red — the reference's own choice for the
   *  "most blocked" list, so the two neighbouring lists cannot be misread
   *  as the same measurement. */
  tone?: "plain" | "alert";
  empty?: string;
  testId?: string;
};

/**
 * A ranked horizontal-bar list — the reference console's `hbar` row: label,
 * track, count. Widths are relative to the largest row so a list dominated by
 * one entry still shows readable bars for the rest, and the track is
 * `aria-hidden` because the count beside it is the real content.
 *
 * Wears the `.g-vendor-*` row classes, which outlived `VendorBars` itself:
 * that component was replaced by `VendorDonut` (a share is a part-to-whole
 * reading, not a race), but this IS still a row of counts and a second
 * near-identical block of CSS would only drift from this one.
 */
export function CountBars({ rows, tone = "plain", empty = "No data yet.", testId }: CountBarsProps) {
  if (rows.length === 0) return <p className="g-asset-empty">{empty}</p>;
  const max = Math.max(1, ...rows.map((r) => r.count));

  return (
    <ul className="g-vendor-bars" data-testid={testId}>
      {rows.map((row) => (
        <li key={row.id ?? row.name} className="g-vendor-row">
          {/* One component, two kinds of label: a vendor ("Apple, Inc.") on
              the assets page and a domain or resolver address ("example.com",
              "127.0.0.1") on DNS. Decided per value rather than per usage, so
              a caller cannot forget. */}
          <span className={`g-vendor-name ${machineClass(row.name)}`.trim()} title={row.title ?? row.name}>
            {row.name}
          </span>
          <span className="g-vendor-track" aria-hidden="true">
            <span
              className={`g-vendor-fill${tone === "alert" ? " g-vendor-fill-alert" : ""}`}
              style={{ width: `${(row.count / max) * 100}%` }}
            />
          </span>
          <span className="g-vendor-count num">{num(row.count)}</span>
        </li>
      ))}
    </ul>
  );
}

export default CountBars;
