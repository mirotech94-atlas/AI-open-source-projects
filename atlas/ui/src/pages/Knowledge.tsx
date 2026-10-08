import { useEffect, useState, type JSX } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type VaultDocument } from "../api";
import { useFresh } from "../hooks/useFresh";

function stamp(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString([], { hour12: false }) : "—";
}

/** `?open=report:17` / `?open=document:4` / `?open=mockup:2` — set by a row
 *  click here, or by a spoken "open the guardian audit report". */
function parseOpen(value: string | null): { kind: string; id: number } | null {
  const match = /^(report|document|mockup):(\d+)$/.exec(value ?? "");
  const [, kind, id] = match ?? [];
  return kind && id ? { kind, id: Number(id) } : null;
}

function extension(path: string): string {
  const dot = path.lastIndexOf(".");
  return dot < 0 ? "" : path.slice(dot).toLowerCase();
}

/** A vault document's own text (.md/.txt), fetched from the mesh-only file
 *  endpoint and shown as text — never rendered as markup. */
function DocumentText({ id }: { id: number }): JSX.Element {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [readAt, setReadAt] = useState<Date | null>(null);
  useEffect(() => {
    let cancelled = false;
    setText(null);
    setError(null);
    void fetch(`/api/knowledge/file/${id}`, { credentials: "same-origin" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((body) => {
        if (cancelled) return;
        setText(body);
        setReadAt(new Date());
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return (): void => { cancelled = true; };
  }, [id]);
  if (error) return <p className="errorbar">Could not read the document: {error}</p>;
  if (text === null) return <p className="stamp">Reading…</p>;
  return (
    <>
      <p className="stamp">Read {readAt?.toLocaleTimeString()}</p>
      <pre style={{ whiteSpace: "pre-wrap", maxHeight: "60vh", overflow: "auto" }}>{text}</pre>
    </>
  );
}

function ReportText({ id }: { id: number }): JSX.Element {
  const report = useFresh(() => api.report(id), [id]);
  if (report.error) return <p className="errorbar">Could not read report #{id}: {report.error}</p>;
  const r = report.data?.report;
  if (!r) return <p className="stamp">Reading…</p>;
  return (
    <>
      <h3 className="label">{r.title}</h3>
      <p className="stamp">
        {r.kind} · written {stamp(r.at)}
        {report.fetchedAt && ` · read ${report.fetchedAt.toLocaleTimeString()}`}
      </p>
      <pre style={{ whiteSpace: "pre-wrap", maxHeight: "60vh", overflow: "auto" }}>{r.body}</pre>
    </>
  );
}

function Viewer({ open, documents, onClose }: {
  open: { kind: string; id: number };
  documents: VaultDocument[];
  onClose: () => void;
}): JSX.Element {
  let body: JSX.Element;
  if (open.kind === "report") {
    body = <ReportText id={open.id} />;
  } else if (open.kind === "document") {
    const doc = documents.find((d) => d.id === open.id);
    const ext = doc ? extension(doc.path) : "";
    const src = `/api/knowledge/file/${open.id}`;
    if (!doc) {
      body = <p className="empty">Document #{open.id} is not in the vault index.</p>;
    } else if (ext === ".pdf") {
      body = (
        <>
          <h3 className="label">{doc.title}</h3>
          <p className="stamp">
            Indexed {stamp(doc.indexed_at)} · <a href={src} target="_blank" rel="noreferrer">open in a new tab ›</a>
          </p>
          <iframe title={doc.title} src={src}
                  style={{ width: "100%", height: "70vh", border: "none" }} />
        </>
      );
    } else if (ext === ".md" || ext === ".txt") {
      body = (
        <>
          <h3 className="label">{doc.title}</h3>
          <DocumentText id={open.id} />
        </>
      );
    } else {
      body = (
        <p className="empty">
          {doc.title}: no viewer for {ext || "this"} files. Its indexed text is in memory.
        </p>
      );
    }
  } else {
    // Mockups are generated HTML, partly built from other people's sites.
    // Served from this origin they would run as the dashboard, so they are
    // never rendered here.
    body = (
      <p className="empty">
        Mockup #{open.id} is generated HTML and is not rendered on the dashboard. Open it
        from the agency workspace.
      </p>
    );
  }
  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <div className="inner">
        <p style={{ textAlign: "right", margin: 0 }}>
          <button type="button" className="btn" onClick={onClose}>close ×</button>
        </p>
        {body}
      </div>
    </div>
  );
}

export function Knowledge({ reloadKey }: { reloadKey: number }): JSX.Element {
  const knowledge = useFresh(() => api.knowledge(), [reloadKey]);
  const data = knowledge.data;
  const [params, setParams] = useSearchParams();
  const open = parseOpen(params.get("open"));
  const show = (kind: string, id: number): void => setParams({ open: `${kind}:${id}` });

  // A finished research item points at the report it became, and that report
  // is also in `reports` where kind='research'. Listing both would show the
  // same piece of work twice under two different titles, so the report rows
  // shown here are only the ones no research row already claims.
  const claimed = new Set(
    (data?.research ?? []).map((r) => r.report_id).filter((id) => id !== null));
  const orphanReports = (data?.reports ?? []).filter((r) => !claimed.has(r.id));

  return (
    <>
      <h2 className="label">Knowledge base</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {knowledge.fetchedAt ? `Read ${knowledge.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {data && ` · ${data.counts.documents} documents · ${data.counts.library} library`
          + ` · ${data.counts.research} research`}
      </p>

      {knowledge.error && (
        <p className="errorbar">Could not read the knowledge base: {knowledge.error}</p>
      )}

      {open && (
        <Viewer open={open} documents={data?.documents ?? []}
                onClose={() => setParams({})} />
      )}

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="inner">
          <h3 className="label">Vault</h3>
          {data && data.documents.length === 0 ? (
            <p className="empty">No documents indexed.</p>
          ) : (
            <table className="sheet">
              <thead>
                <tr><th>Title</th><th>Kind</th><th>Chunks</th><th>Indexed</th></tr>
              </thead>
              <tbody>
                {(data?.documents ?? []).map((d) => (
                  <tr key={d.id}>
                    <td title={d.path}>
                      <a href={`?open=document:${d.id}`}
                         onClick={(e) => { e.preventDefault(); show("document", d.id); }}>
                        {d.title}
                      </a>
                    </td>
                    <td>{d.kind}</td>
                    <td>{d.chunks}</td>
                    <td>{stamp(d.indexed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="inner">
          <h3 className="label">Library</h3>
          {data && data.library.length === 0 ? (
            <p className="empty">Nothing kept yet.</p>
          ) : (
            <table className="sheet">
              <thead><tr><th>Title</th><th>Kind</th><th>Notes</th><th>Added</th></tr></thead>
              <tbody>
                {(data?.library ?? []).map((l) => (
                  <tr key={l.id}>
                    <td title={l.source ?? undefined}>{l.title}</td>
                    <td>{l.kind}</td>
                    <td>{l.notes}</td>
                    <td>{stamp(l.at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="inner">
          <h3 className="label">Research</h3>
          {data && data.research.length === 0 && orphanReports.length === 0 ? (
            <p className="empty">No finished research.</p>
          ) : (
            <table className="sheet">
              <thead>
                <tr><th>Question</th><th>Sources</th><th>Finished</th><th>Report</th></tr>
              </thead>
              <tbody>
                {(data?.research ?? []).map((r) => (
                  <tr key={r.id}>
                    <td>{r.question}</td>
                    <td>{r.sources ?? "—"}</td>
                    <td>{stamp(r.at)}</td>
                    <td>
                      {/* report_id is a reports.id. /f/ is the findings
                          permalink and would resolve to a different row, or
                          to nothing. */}
                      {r.report_id === null
                        ? "—"
                        : <a href={`/reports/${r.report_id}`}>read ›</a>}
                    </td>
                  </tr>
                ))}
                {orphanReports.map((r) => (
                  <tr key={`report-${r.id}`}>
                    <td>{r.title}</td>
                    <td>—</td>
                    <td>{stamp(r.at)}</td>
                    <td>
                      <a href={`?open=report:${r.id}`}
                         onClick={(e) => { e.preventDefault(); show("report", r.id); }}>
                        read ›
                      </a>{" "}
                      <Link to={`/reports`}>reports ›</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
