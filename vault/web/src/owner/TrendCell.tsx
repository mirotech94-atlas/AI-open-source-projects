import { useQuery } from "@tanstack/react-query";
import { Sparkline } from "../design";
import { platformTrends } from "../lib/api";
import type { PlatformTrend } from "../types";

/**
 * The Ledger and Fleet trend column: thirty days of a customer's new alerts.
 *
 * Those tables say what is true NOW -- open alerts, sensors reporting, devices.
 * That is the right answer to "how is this customer" and the wrong one to "is
 * this getting better or worse", which is the question that decides who to
 * call. Two customers both showing three open criticals are not the same
 * customer when one of them is coming down from twenty.
 *
 * ONE FETCH FOR THE WHOLE COLUMN
 *
 * Every cell calls this hook, and they all share one request: react-query
 * dedupes by key, so a table of fifty rows issues one `GET /platform/trends`,
 * not fifty. The alternative -- passing the payload down from each table --
 * would mean both tables growing a prop for it and the Fleet page fetching a
 * second copy when the owner switches to it.
 *
 * WHY IT IS NOT RED
 *
 * The console spends its one red on "this customer needs you today", and a
 * rising line is not that: a customer whose alerts doubled from one to two is
 * trending badly and is nobody's emergency. The neighbouring "Open alerts"
 * column already carries the red for what is open now. This column answers
 * which way, that one answers how bad -- and giving both the same colour would
 * make the ledger look alarming in proportion to how much detail it shows.
 */

export function usePlatformTrends() {
  return useQuery({
    queryKey: ["platform-trends"],
    queryFn: () => platformTrends(30),
    // Slower than the ledger's own 60s: a thirty-day daily count cannot change
    // meaningfully inside a minute, and this is the heavier of the two reads.
    refetchInterval: 300_000,
    staleTime: 120_000,
  });
}

export function TrendCell({ tenantId }: { tenantId: string }) {
  const q = usePlatformTrends();

  // Nothing at all while it loads, rather than a placeholder: the column is
  // the last thing to arrive and a skeleton in every row draws the eye to the
  // one part of the table that is not ready yet.
  if (q.isPending) return <span className="g-led-quiet">·</span>;

  const trend: PlatformTrend | undefined = q.data?.trends.find((t) => t.tenant_id === tenantId);

  // No alerts in the window. Deliberately not a flat line at zero: a flat line
  // says "we measured and it was nothing", which is a different claim from
  // "there is nothing to draw" — and for a customer whose sensor has never
  // reported, only the second one is true.
  if (!trend || trend.total === 0) return <span className="g-led-quiet">—</span>;

  return (
    <span className="g-own-trendcell">
      <Sparkline
        points={trend.points}
        height={22}
        tone="plain"
        // The shape carries no number, so the label is what makes it readable
        // to a screen reader and on hover.
        aria-label={`${trend.total} new alerts over ${q.data!.days} days`}
      />
      <span className="g-own-trendtotal">{trend.total}</span>
    </span>
  );
}

export default TrendCell;
