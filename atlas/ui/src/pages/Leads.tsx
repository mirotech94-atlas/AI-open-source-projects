import { Fragment, useMemo, useState, type JSX } from "react";
import { api, type Lead, type LeadNextStep } from "../api";
import { useFresh } from "../hooks/useFresh";
import {
  buildChips, defaultSortDir, filterLeads, leadsToCsv, paginate, sortLeads,
  SORT_COLUMNS, toggleDrawer, type SortDir, type SortKey,
} from "./leadsModel";

const PAGE_SIZE = 25;

/** A slug for the CSS attribute selector -- "waiting on you" has spaces. */
function stateAttr(state: Lead["outreach_state"]): string {
  return state ? state.replace(/\s+/g, "-") : "none";
}

function stamp(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString([], { hour12: false }) : "—";
}

function time(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "—";
}

function downloadCsv(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function NextStep({ step }: { step: LeadNextStep | null }): JSX.Element {
  if (!step) return <span className="empty">—</span>;
  if (step.kind === "proposal") {
    return (
      <a href="/tasks" title={step.expires_at ? `expires ${stamp(step.expires_at)}` : ""}>
        #{step.card_id} · {step.label}
      </a>
    );
  }
  return <span style={{ color: "var(--text-dim)" }}>{step.label}</span>;
}

/** The drawer: audit summary, score reasons, the outreach draft's actual
 *  text, the actions_log timeline naming this lead, and the card link --
 *  fetched only once a row is open, the same `useFresh(() => open ? … :
 *  Promise.resolve(null))` shape Reports.tsx uses for its own detail pane. */
function LeadDrawer({ leadId }: { leadId: number }): JSX.Element {
  const detail = useFresh(() => api.leadDetail(leadId), [leadId]);

  if (detail.error) return <p className="errorbar">Could not read this lead: {detail.error}</p>;
  if (!detail.data) return <p className="stamp">Reading…</p>;

  const { lead, score_features: features, audits, outreach, timeline } = detail.data;
  const latestAudit = audits[0] ?? null;

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div>
        <h4 className="label" style={{ margin: "0 0 6px" }}>Contact</h4>
        <p className="stamp" style={{ margin: 0 }}>
          {lead.address ?? "no address on file"}
          {lead.contact.phone && ` · ${lead.contact.phone}`}
          {lead.contact.site && (
            <> · <a href={lead.contact.site} target="_blank" rel="noreferrer">
              {lead.contact.site}
            </a></>
          )}
        </p>
      </div>

      <div>
        <h4 className="label" style={{ margin: "0 0 6px" }}>Audit</h4>
        {latestAudit === null
          ? <p className="empty">Not audited yet.</p>
          : (
            <>
              <p className="stamp" style={{ margin: "0 0 4px" }}>
                Read {stamp(latestAudit.at)} · severity {latestAudit.severity ?? "—"}
              </p>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                {latestAudit.findings.map((f, i) => (
                  <li key={i}>{f.detail ?? f.check}</li>
                ))}
              </ul>
            </>
          )}
      </div>

      <div>
        <h4 className="label" style={{ margin: "0 0 6px" }}>Score reasons</h4>
        {lead.score === null
          ? <p className="empty">Not scored yet -- no number, no read time.</p>
          : (
            <>
              <p className="stamp" style={{ margin: "0 0 4px" }}>
                {lead.score.toFixed(2)} · read {time(lead.score_read_at)}
              </p>
              {features && (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                  {Object.entries(features).map(([k, v]) => (
                    <li key={k}>{k}: {String(v)}</li>
                  ))}
                </ul>
              )}
            </>
          )}
      </div>

      <div>
        <h4 className="label" style={{ margin: "0 0 6px" }}>Outreach</h4>
        {outreach.length === 0
          ? <p className="empty">Nothing drafted for this lead yet.</p>
          : outreach.map((o) => (
            <div key={o.id} style={{ marginBottom: 8 }}>
              <p className="stamp" style={{ margin: "0 0 4px" }}>
                {o.kind} · {o.status} · {stamp(o.at)}
                {o.to && ` · to ${o.to}`}
              </p>
              {o.subject && <p style={{ fontWeight: 700, margin: "0 0 4px" }}>{o.subject}</p>}
              <pre className="pre" style={{ maxHeight: 220 }}>{o.body}</pre>
            </div>
          ))}
      </div>

      <div>
        <h4 className="label" style={{ margin: "0 0 6px" }}>Timeline</h4>
        {timeline.length === 0
          ? <p className="empty">No recorded actions for this lead yet.</p>
          : (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {timeline.map((t) => (
                <li key={t.id}>
                  <span className="stamp">{stamp(t.at)}</span> — {t.action}
                  {t.detail && `: ${t.detail}`}
                </li>
              ))}
            </ul>
          )}
      </div>

      <div>
        <NextStep step={lead.next_step} />
      </div>
    </div>
  );
}

export function Leads({ reloadKey }: { reloadKey: number }): JSX.Element {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey | null>(null);
  const [dir, setDir] = useState<SortDir | null>(null);
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);

  // The whole table, read once per refresh -- the Places budget caps it at
  // a few hundred rows a month, so chips, search and sort below can all be
  // instant, plain-Python^Wplain-JavaScript filtering rather than a network
  // round trip on every keystroke.
  const list = useFresh(() => api.leads({ limit: 500 }), [reloadKey]);
  const allLeads = list.data?.leads ?? [];

  const filtered = useMemo(() => filterLeads(allLeads, q, status),
    [allLeads, q, status]);
  const sorted = useMemo(() => sortLeads(filtered, sort, dir), [filtered, sort, dir]);
  const maxPage = Math.max(0, Math.ceil(sorted.length / PAGE_SIZE) - 1);
  const pageIndex = Math.min(page, maxPage);
  const pageRows = useMemo(() => paginate(sorted, pageIndex * PAGE_SIZE, PAGE_SIZE),
    [sorted, pageIndex]);

  const chips = buildChips(list.data?.counts ?? {});

  function onSortClick(key: SortKey): void {
    if (sort === key) {
      setDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSort(key);
      setDir(defaultSortDir(key));
    }
    setPage(0);
  }

  function onChip(key: string): void {
    setStatus((current) => (current === key ? null : key));
    setPage(0);
  }

  return (
    <>
      <h2 className="label">Lead pipeline</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {list.fetchedAt ? `Read ${list.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {list.data && ` · ${allLeads.length} leads · ${sorted.length} shown`}
        {" · read-only — approvals happen in Tasks"}
      </p>

      {list.error && <p className="errorbar">Could not read the pipeline: {list.error}</p>}

      {list.data && allLeads.length === 0 && (
        <p className="empty">{list.data.why_empty}</p>
      )}

      {allLeads.length > 0 && (
        <>
          {/* The pipeline as it flows: where the leads are, stage by stage,
              the lead's own status on top and the outreach it is in below.
              Each stage is the filter for itself; the widths are the counts. */}
          <div className="lead-flow" role="group" aria-label="Filter by stage">
            {[
              { name: "Leads", keys: ["new", "audited", "mocked", "contacted", "closed"], off: ["dropped"] },
              { name: "Outreach", keys: ["drafted", "waiting on you", "sent", "replied"], off: ["bounced"] },
            ].map((row) => {
              const byKey = new Map(chips.map((c) => [c.key, c]));
              const main = row.keys.map((k) => byKey.get(k)).filter((c): c is typeof chips[number] => !!c);
              const side = row.off.map((k) => byKey.get(k)).filter((c): c is typeof chips[number] => !!c);
              if (main.length === 0 && side.length === 0) return null;
              return (
                <div className="lead-flow-row" key={row.name}>
                  <span className="lead-flow-name">{row.name}</span>
                  <div className="lead-flow-stages">
                    {main.map((chip) => (
                      <button key={chip.key} type="button" className="lead-stage"
                              data-chip={chip.key} aria-pressed={status === chip.key}
                              style={{ flexGrow: Math.max(1, chip.n) }}
                              onClick={() => onChip(chip.key)}>
                        <b>{chip.n}</b><span>{chip.label}</span>
                      </button>
                    ))}
                  </div>
                  {side.map((chip) => (
                    <button key={chip.key} type="button" className="lead-stage lead-stage-off"
                            data-chip={chip.key} aria-pressed={status === chip.key}
                            onClick={() => onChip(chip.key)}>
                      <b>{chip.n}</b><span>{chip.label}</span>
                    </button>
                  ))}
                </div>
              );
            })}
            {chips.filter((c) => ![
              "new", "audited", "mocked", "contacted", "closed", "dropped",
              "drafted", "waiting on you", "sent", "replied", "bounced",
            ].includes(c.key)).map((chip) => (
              <button key={chip.key} type="button" className="lead-stage lead-stage-off"
                      data-chip={chip.key} aria-pressed={status === chip.key}
                      onClick={() => onChip(chip.key)}>
                <b>{chip.n}</b><span>{chip.label}</span>
              </button>
            ))}
          </div>

          <div className="filters">
            <button type="button" className="btn" aria-pressed={status === null}
                    onClick={() => { setStatus(null); setPage(0); }}>
              {status === null ? `All ${allLeads.length}` : `Showing ${status} · show all`}
            </button>
            <input
              type="search" placeholder="search business, contact, address…"
              value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }}
              aria-label="Search leads"
            />
            <button type="button" className="btn"
                    onClick={() => downloadCsv(leadsToCsv(sorted), "leads.csv")}>
              Export CSV
            </button>
          </div>

          {sorted.length === 0 ? (
            <p className="empty">No leads match your search or filter.</p>
          ) : (
            <>
              <div className="panel leads-table-wrap">
                <table className="sheet">
                  <thead>
                    <tr>
                      {SORT_COLUMNS.map((col) => (
                        <th key={col.key} style={{ cursor: "pointer" }}
                            onClick={() => onSortClick(col.key)}>
                          {col.label}{sort === col.key ? (dir === "asc" ? " ▲" : " ▼") : ""}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((lead) => (
                      <Fragment key={lead.id}>
                        <tr className="expandable"
                            onClick={() => setOpenId(toggleDrawer(openId, lead.id))}>
                          <td>
                            {lead.business}
                            <div style={{ color: "var(--text-faint)", fontSize: 11 }}>
                              {lead.category}
                            </div>
                          </td>
                          <td>{lead.source}</td>
                          <td>
                            <span className="lead-status" data-status={lead.status}>{lead.status}</span>
                            {lead.outreach_state && (
                              <div style={{ marginTop: 3 }}>
                                <span className="lead-outreach"
                                      data-state={stateAttr(lead.outreach_state)}>
                                  {lead.outreach_state}
                                </span>
                              </div>
                            )}
                          </td>
                          <td>
                            {lead.score === null
                              ? <span className="empty">—</span>
                              : (
                                <>
                                  <span className="lead-score">
                                    <span className="meter" aria-hidden="true">
                                      <i style={{ width: `${Math.round(lead.score * 100)}%` }} />
                                    </span>
                                    {lead.score.toFixed(2)}
                                  </span>
                                  <div className="stamp">read {time(lead.score_read_at)}</div>
                                </>
                              )}
                          </td>
                          <td className="stamp">{stamp(lead.last_touch_at)}</td>
                          <td><NextStep step={lead.next_step} /></td>
                        </tr>
                        {openId === lead.id && (
                          <tr className="detail">
                            <td colSpan={SORT_COLUMNS.length}>
                              <LeadDrawer leadId={lead.id} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Phone width: cards, not a table -- css/hud.css hides
                  whichever of the two the viewport does not need. */}
              <div className="lead-cards">
                {pageRows.map((lead) => (
                  <div key={lead.id} className="lead-card"
                       onClick={() => setOpenId(toggleDrawer(openId, lead.id))}>
                    <div className="lead-card-head">
                      <strong>{lead.business}</strong>
                      <span className="stamp">{lead.category}</span>
                    </div>
                    <div className="lead-card-row">
                      <span>{lead.status}</span>
                      {lead.outreach_state && (
                        <span className="lead-outreach" data-state={stateAttr(lead.outreach_state)}>
                          {lead.outreach_state}
                        </span>
                      )}
                    </div>
                    <div className="lead-card-row">
                      <span>
                        {lead.score === null ? "no score" : `score ${lead.score.toFixed(2)}`}
                      </span>
                      <span className="stamp">{stamp(lead.last_touch_at)}</span>
                    </div>
                    <div className="lead-card-row"><NextStep step={lead.next_step} /></div>
                    {openId === lead.id && (
                      <div className="lead-card-drawer" onClick={(e) => e.stopPropagation()}>
                        <LeadDrawer leadId={lead.id} />
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className="filters" style={{ justifyContent: "space-between" }}>
                <span className="stamp">
                  {pageIndex * PAGE_SIZE + 1}–{Math.min(sorted.length, (pageIndex + 1) * PAGE_SIZE)}
                  {" "}of {sorted.length}
                </span>
                <div style={{ display: "flex", gap: 8 }}>
                  <button type="button" className="btn" disabled={pageIndex === 0}
                          onClick={() => setPage(pageIndex - 1)}>Prev</button>
                  <button type="button" className="btn" disabled={pageIndex >= maxPage}
                          onClick={() => setPage(pageIndex + 1)}>Next</button>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
