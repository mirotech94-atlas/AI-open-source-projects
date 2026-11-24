import { useState, type JSX } from "react";
import { api, type PreviewItem, type PreviewsList } from "../api";
import { useFresh } from "../hooks/useFresh";
import { safeText } from "./retired";

/**
 * Previews: the built demo sites, framed inside the portal. Each frame is
 * sandboxed (scripts only, no same-origin; tel: and mailto: may leave it) and the server adds a sandbox CSP
 * of its own, so generated HTML never runs as the dashboard. A list that
 * cannot be read is UNKNOWN (grey, dashed), never an empty panel (invariant 7).
 */
export const PHONE_PX = 390;

export function previewSrc(slug: string): string {
  return `/api/agency/preview/${encodeURIComponent(slug)}/`;
}

export function frameWidth(mode: "desktop" | "phone"): string {
  return mode === "phone" ? `${PHONE_PX}px` : "100%";
}

function readTime(iso: string | null): string {
  if (!iso) return "not read yet";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "not read yet";
  return `read ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

function Card({ item }: { item: PreviewItem }): JSX.Element {
  const [mode, setMode] = useState<"desktop" | "phone">("desktop");
  return (
    <article className="prev-card" data-slug={item.slug}>
      <header>
        <h3>{safeText(item.name)}</h3>
        <div className="prev-toggle" role="group" aria-label="Frame width">
          {(["desktop", "phone"] as const).map((m) => (
            <button key={m} type="button" className="desk-chip" aria-pressed={mode === m}
                    onClick={() => setMode(m)}>{m}</button>
          ))}
        </div>
      </header>
      {item.built ? (
        <div className="prev-stage">
          <iframe title={`Preview of ${item.name}`} sandbox="allow-scripts allow-top-navigation-to-custom-protocols"
                  src={item.src ?? previewSrc(item.slug)} loading="lazy"
                  style={{ width: frameWidth(mode) }} />
        </div>
      ) : (
        <p className="prev-unknown" data-health="unknown">
          <b>Not built</b> no index.html for {safeText(item.slug)}.
        </p>
      )}
    </article>
  );
}

export function PreviewsView({ data, error, at }: {
  data: PreviewsList | null; error: string | null; at: string | null;
}): JSX.Element {
  return (
    <div className="prev" id="previews" data-previews="">
      <h3 className="prev-h">Previews <small>{readTime(error ? null : at)}</small></h3>
      {error ? (
        <p className="prev-unknown" data-health="unknown">
          <b>Unknown</b> The built sites could not be read: {safeText(error)}
        </p>
      ) : data === null ? (
        <p className="desk-note">Reading the built sites.</p>
      ) : data.previews.length === 0 ? (
        <p className="desk-note">No demo site has been built yet.</p>
      ) : (
        <div className="prev-grid">
          {data.previews.map((p) => <Card key={p.slug} item={p} />)}
        </div>
      )}
    </div>
  );
}

export function PreviewsPanel({ reloadKey }: { reloadKey: number }): JSX.Element {
  const read = useFresh(() => api.agencyPreviews(), [reloadKey]);
  return (
    <PreviewsView data={read.error ? null : read.data} error={read.error}
                  at={read.data?.read_at ?? read.fetchedAt?.toISOString() ?? null} />
  );
}
