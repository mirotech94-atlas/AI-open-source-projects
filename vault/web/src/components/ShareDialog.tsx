import { useQuery } from "@tanstack/react-query";
import { get, del } from "../lib/api";
import { useId, useState } from "react";
import type { ShareResult } from "../lib/api";
import type { ReportCard } from "../types";

export type { ShareResult };

export type ShareDialogProps = {
  card: ReportCard;
  /** Creates or rotates a time-limited public report link. */
  onShare: (hours: number, push?: boolean) => Promise<ShareResult>;
};

const EXPIRY_OPTIONS = [
  { label: "24 hours", hours: 24 },
  { label: "7 days", hours: 168 },
  { label: "30 days", hours: 720 },
];

/** The expiry selector, the resulting link with a copy button, a plain
 *  warning that anyone with it can read the report, and a rotate action. */
export function ShareDialog({ card, onShare }: ShareDialogProps) {
  const expiryId = useId();
  const urlId = useId();
  const [hours, setHours] = useState(EXPIRY_OPTIONS[1].hours);
  const [shareUrl, setShareUrl] = useState(card.share_url ?? "");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pushed, setPushed] = useState(false);
  const [error, setError] = useState("");
  const receipts = useQuery({queryKey:["report-deliveries",card.id], enabled:pushed,
    queryFn:()=>get<{deliveries:{channel:string;status:string;detail:string}[]}>(`/api/v1/reports/${card.id}/deliveries`),refetchInterval:pushed?5000:false});

  async function create(push = false) {
    setBusy(true);
    try {
      const result = await onShare(hours, push);
      setShareUrl(result.share_url);
      setCopied(false);
      setPushed(push && result.pushed !== false);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not share the report");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Intentionally silent: `navigator.clipboard` can throw (permission
      // denied, insecure context, unsupported browser) and there is no
      // secondary copy mechanism to fall back to here — the link stays
      // visible and selectable in the read-only input either way, so a
      // failed copy is a no-op rather than an error worth surfacing.
    }
  }

  return (
    <div className="g-reports-share">
      <label className="g-reports-field">
        <span id={expiryId}>Link expires after</span>
        <select aria-labelledby={expiryId} value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {EXPIRY_OPTIONS.map((o) => (
            <option key={o.hours} value={o.hours}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <button type="button" disabled={busy} onClick={() => void create()}>
        {shareUrl ? "Rotate link" : "Create link"}
      </button>

      {/* The predecessor's "push link to phone": mints the link and sends it
          over the tenant's notification channels in one step, so the reader
          never has to copy a URL out of the dashboard. Always a fresh link —
          pushing a token that is about to expire would be worse than useless. */}
      <button type="button" disabled={busy} onClick={() => void create(true)}>
        Send to my phone
      </button>

      {pushed && (
        // "Sent to" not "delivered": the hub hands the send off in the
        // background, and a maintenance window suppresses delivery outright,
        // so this must not claim the phone actually got it.
        <p className="g-reports-share-pushed" role="status">
          Link queued for delivery to your notification channels. Delivery is not yet confirmed.
        </p>
      )}

      {shareUrl && (
        <div className="g-reports-share-url">
          <label htmlFor={urlId}>Share link</label>
          <input id={urlId} type="text" readOnly value={shareUrl} onFocus={(e) => e.target.select()} />
          <button type="button" onClick={() => void copy()}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}

      <p className="g-reports-share-warning">
        Anyone with the link can read this report — no sign-in required. Rotating invalidates the
        previous link.
      </p>
    {shareUrl && <button type="button" disabled={busy} onClick={async () => {
        setBusy(true); try { await del(`/api/v1/reports/${card.id}/share`); setShareUrl(""); }
        catch { setError("Could not revoke the link. Try again."); } finally { setBusy(false); }
      }}>Revoke shared link</button>}
      {receipts.data?.deliveries.map((d,i)=><p key={i} role="status">{d.channel}: {d.status}{d.detail ? ` — ${d.detail}` : ""}</p>)}
      {error && <p role="alert">{error}</p>}
      </div>
  );
}

export default ShareDialog;
