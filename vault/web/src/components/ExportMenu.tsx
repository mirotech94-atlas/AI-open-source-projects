import { useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";

export type ExportFormat = "pdf" | "xlsx" | "csv";

const CHOICES: { fmt: ExportFormat; label: string; note: string }[] = [
  { fmt: "pdf", label: "PDF", note: "opens ready to print" },
  { fmt: "xlsx", label: "Excel", note: "one sheet, frozen header" },
  { fmt: "csv", label: "CSV", note: "for another tool" },
];

/**
 * The one export control. Wherever the product says "Export", it says it
 * with this, so a client never has to learn that one tab gives a CSV and
 * another gives something else.
 *
 * `path` is a hub export endpoint that accepts `?fmt=`; the component adds
 * the format and hands the rest of the query through untouched, so each page
 * exports exactly what it is showing rather than the whole table.
 */
export function ExportMenu({ path, query = {}, label = "Export", disabled = false, onError }: {
  path: string;
  query?: Record<string, string | number | null | undefined>;
  label?: string;
  disabled?: boolean;
  onError?: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function away(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    function escape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  async function run(fmt: ExportFormat) {
    setBusy(fmt);
    try {
      const q = new URLSearchParams({ fmt });
      for (const [k, v] of Object.entries(query)) {
        if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
      }
      const url = `${path}?${q.toString()}`;
      if (fmt === "pdf") {
        // The hub serves the printable page `inline`, so a new tab is where
        // the person presses Print. Downloading an .html would leave them a
        // file to find and open by hand before they could print it.
        window.open(url, "_blank", "noopener");
        setOpen(false);
        return;
      }
      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) throw new ApiError(res.status, `Export failed (${res.status})`);
      const disposition = res.headers.get("content-disposition") ?? "";
      const named = /filename="([^"]+)"/.exec(disposition);
      const href = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = href;
      link.download = named?.[1] ?? `vault-export.${fmt}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
      setOpen(false);
    } catch (err) {
      onError?.((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="g-exportmenu" ref={wrap}>
      <button type="button" className="sk-btn" disabled={disabled} aria-haspopup="menu"
              aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {busy ? "Exporting…" : label}
      </button>
      {open && (
        <div className="g-exportmenu-pop" role="menu" aria-label={`${label} as`}>
          {CHOICES.map((c) => (
            <button key={c.fmt} type="button" role="menuitem" className="g-exportmenu-item"
                    disabled={busy !== null} onClick={() => void run(c.fmt)}>
              <span className="g-exportmenu-fmt">{c.label}</span>
              <span className="g-exportmenu-note">{c.note}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default ExportMenu;
