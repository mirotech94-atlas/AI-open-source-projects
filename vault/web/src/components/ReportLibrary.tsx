import { Badge } from "../design";
import { EmptyState } from "./EmptyState";
import { day } from "../lib/format";
import type { ReportCard as ReportCardType } from "../types";

export type ReportLibraryProps = {
  library: ReportCardType[];
  onOpen: (card: ReportCardType) => void;
  onShare: (card: ReportCardType) => void;
  onDelete: (card: ReportCardType) => void;
  canShare?: boolean;
  shareTooltip?: string;
  canDelete?: boolean;
  deleteTooltip?: string;
  /** Focuses the builder form above this card — the "Build your first
   *  report" empty-state action. Optional so a caller that never expects an
   *  empty library (there is none today) is not forced to wire it. */
  onBuildFirst?: () => void;
};

/** One card per built report: title, kind badge, period, created date, the
 *  score the hub scored it at, and Open / Print / Share buttons. Print opens
 *  the report's own `url` in a new tab and relies on that page's own
 *  toolbar Print button (a server-rendered report page, not something this
 *  SPA calls `window.print()` on directly) — `window.open` is the one DOM
 *  API this component reaches for besides render. */
export function ReportLibrary({
  library,
  onOpen,
  onShare,
  onDelete,
  canShare = true,
  shareTooltip = "operator role required",
  canDelete = true,
  deleteTooltip = "admin role required",
  onBuildFirst,
}: ReportLibraryProps) {
  if (library.length === 0) {
    return (
      <EmptyState
        title="No reports built yet."
        body="Pick a preset above and build one — it lands here, ready to open, print or share."
        action={onBuildFirst ? { label: "Build your first report", onClick: onBuildFirst } : undefined}
      />
    );
  }

  return (
    <div className="g-reports-library">
      {library.map((card) => (
        <article key={card.id} className="g-reports-card" data-testid="report-card">
          <header className="g-reports-card-head">
            <h3 className="g-reports-card-title">{card.title}</h3>
            <Badge tone="plain">{card.kind}</Badge>
          </header>
          <dl className="g-reports-card-facts">
            <dt>Period</dt>
            <dd>{card.period_days} days</dd>
            <dt>Created</dt>
            <dd>{day(card.created)}</dd>
            <dt>Score</dt>
            <dd>{card.score ?? "—"}</dd>
          </dl>
          <div className="g-reports-card-actions">
            <button type="button" onClick={() => onOpen(card)}>
              Open
            </button>
            <button type="button" onClick={() => window.open(card.url, "_blank", "noopener")}>
              Print
            </button>
            <button
              type="button"
              disabled={!canShare}
              title={canShare ? undefined : shareTooltip}
              onClick={() => onShare(card)}
            >
              Share
            </button>
            <button
              type="button"
              className="g-danger"
              disabled={!canDelete}
              title={canDelete ? undefined : deleteTooltip}
              onClick={() => onDelete(card)}
            >
              Delete
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}

export default ReportLibrary;
