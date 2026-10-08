const W = 88;
const H = 22;

/** The strip's "visual that produces live data" (the owner's own phrase):
 *  the site's real `LiveSite.spark` history, one point per scan, never a
 *  synthesized line. Renders nothing (not a flat line) when the hub has
 *  fewer than two points yet -- a single point has no shape to draw and a
 *  fabricated flat line would look like a real reading. */
export function Sparkline({ points, tone }: { points: number[] | undefined; tone: "good" | "warn" | "alert" | "plain" }) {
  if (!points || points.length < 2) return null;
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const n = points.length;
  const d = points
    .map((v, i) => {
      const x = (i / (n - 1)) * (W - 2) + 1;
      const y = H - 2 - ((v - lo) / span) * (H - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="g-latency-spark" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <polyline
        className={`g-latency-spark-line g-tone-${tone}`}
        points={d}
        fill="none"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default Sparkline;
