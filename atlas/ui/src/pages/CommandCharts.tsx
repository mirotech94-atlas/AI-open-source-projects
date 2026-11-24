import type { JSX } from "react";
import { Link } from "react-router-dom";
import type { Lead, PipelineRead } from "../api";
import { hhmm, readOf, type Read } from "./homeModel";
import { CPanel, Unread, stamp } from "./cmdParts";
import { safeText } from "./retired";
import type { RosterAgent } from "./opsFloorModel";
import { stageBreakdown } from "./stageBreakdown";
import { isoBar, latestLeads, pipelineBars, scaleHeights, shiftBars } from "./chartModel";

/**
 * 3D charts and the lead grid for the Command Center (Miro 2026-10-30).
 * Inline SVG only: extruded bars with front, top and side faces over an
 * isometric floor grid. Reads only. A bar that was not read is a grey dashed
 * box with the word UNKNOWN, never a zero; an empty real result is a real 0
 * with its read time (invariants 6 and 7).
 */

const U = (): JSX.Element => <span className="cmd-word-unknown">Unknown</span>;

export interface ChartBar { key: string; label: string; count: number | null }

const W = 360, H = 170, BASE = 128, BW = 26, DEPTH = 12, MAXH = 82, LEFT = 14;

/** One isometric bar chart. `bars` empty is a real empty chart (floor only). */
export function IsoChart({ bars, caption }: { bars: ChartBar[]; caption: string }): JSX.Element {
  const shown = bars.slice(0, 8);
  const heights = scaleHeights(shown.map((b) => b.count), MAXH);
  const step = Math.min(44, (W - LEFT - DEPTH - 8) / Math.max(1, shown.length));
  const floorW = LEFT + step * Math.max(1, shown.length) + DEPTH;
  const grid: JSX.Element[] = [];
  for (let i = 0; i <= 4; i += 1) {
    const y = BASE - (MAXH / 4) * i;
    grid.push(<line key={`h${i}`} x1={LEFT - 6} y1={y} x2={floorW} y2={y} />);
  }
  for (let i = 0; i <= shown.length; i += 1) {
    const x = LEFT - 6 + step * i;
    grid.push(<line key={`v${i}`} x1={x} y1={BASE} x2={x + DEPTH} y2={BASE - DEPTH / 2} />);
  }
  return (
    <svg className="cmd-iso" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={caption}
         preserveAspectRatio="xMidYMid meet">
      <title>{caption}</title>
      <g className="iso-grid" aria-hidden="true">{grid}</g>
      {shown.map((b, i) => {
        const h = heights[i] ?? null;
        const x = LEFT + step * i;
        const cx = x + BW / 2 + DEPTH / 2;
        const label = safeText(b.label).slice(0, 9);
        if (h === null) {
          return (
            <g key={b.key} className="iso-unknown">
              <rect x={x} y={BASE - 40} width={BW} height={40} />
              <text x={cx - DEPTH / 2} y={BASE - 18} textAnchor="middle">UNKNOWN</text>
              <text className="iso-lab" x={cx} y={BASE + 16} textAnchor="middle">{label}</text>
            </g>
          );
        }
        const f = isoBar(x, BASE, BW, DEPTH, h);
        return (
          <g key={b.key} className="iso-bar" style={{ animationDelay: `${i * 60}ms` }}>
            <g className="iso-rise">
              <polygon className="iso-front" points={f.front} />
              <polygon className="iso-side" points={f.side} />
              <polygon className="iso-top" points={f.top} />
            </g>
            <text className="iso-val" x={cx} y={BASE - h - DEPTH / 2 - 4} textAnchor="middle">
              {b.count}
            </text>
            <text className="iso-lab" x={cx} y={BASE + 16} textAnchor="middle">{label}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** The stage bar: counts per stage in one hue (stages are counts, not health).
 *  "no stage" is a real count, solid and never hatched. Unread is one hatched
 *  UNKNOWN bar with no legend and no figure. */
function StageBar({ pipe, loading }: { pipe: Read<PipelineRead>; loading: boolean }): JSX.Element | null {
  const read = readOf(pipe);
  if (loading) return null;
  const b = stageBreakdown(read.data, read.error);
  if (b === null) {
    return (
      <div className="cmd-stagebar" data-health="unknown" role="group" aria-label="Pipeline stages: unknown">
        <div className="cmd-sb-bar"><span className="cmd-sb-seg hatch" data-health="unknown" /></div>
        <p className="cmd-sb-unknown"><U /> <span>{read.error ? "read failed" : "pipeline not read"}</span></p>
      </div>
    );
  }
  return (
    <div className="cmd-stagebar" role="group" aria-label={`Pipeline stages, ${b.total} in total`}>
      <p className="v2-fig cmd-sb-fig">{b.total}</p>
      <p className="cmd-sb-read">read {hhmm(b.readAt)}</p>
      {b.total > 0 && (
        <>
          <div className="cmd-sb-bar">
            {b.stages.map((s, i) => (
              <span key={s.stage} className={`cmd-sb-seg${s.stage === "no stage" ? " cmd-sb-none" : ""}`}
                    data-step={Math.min(i, 5)} style={{ flexGrow: s.count }}
                    title={`${safeText(s.stage)}: ${s.count}`} />
            ))}
          </div>
          <ul className="cmd-sb-legend">
            {b.stages.map((s, i) => (
              <li key={s.stage}>
                <i className={`cmd-sb-sw${s.stage === "no stage" ? " cmd-sb-none" : ""}`}
                   data-step={Math.min(i, 5)} aria-hidden="true" />
                <span>{safeText(s.stage)}</span><b>{s.count}</b>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function PipelineChart({ pipe }: {
  pipe: Read<PipelineRead>;
}): JSX.Element {
  const read = readOf(pipe);
  const m = pipelineBars(read.error ? null : read.data);
  const loading = !read.error && !read.data;
  return (
    <CPanel id="chart-pipeline" title="Pipeline by stage" className="cmd-chart"
            at={stamp(pipe.fetchedAt, read.error !== null)}
            more={{ to: "/agency#pipeline", label: "Detail" }}>
      {read.error && <Unread>Pipeline: {safeText(read.error)}</Unread>}
      {!read.error && m.state === "unknown" && !loading && <Unread>{safeText(m.reason)}</Unread>}
      <StageBar pipe={pipe} loading={loading} />
      {loading ? <p className="cmd-empty v2-loading"><U /> reading…</p>
        : m.state === "unknown" ? <div className="cmd-chart-unknown" data-health="unknown"><U /></div>
        : <>
          <IsoChart bars={[
            ...m.bars.map((b) => ({ key: b.stage, label: b.stage, count: b.count })),
            ...(m.unstaged > 0 ? [{ key: "_u", label: "no stage", count: null }] : []),
          ]} caption={`Pipeline by stage: ${m.bars.map((b) => `${b.stage} ${b.count}`).join(", ")
            || "no rows"}`} />
          <p className="cmd-note">
            {m.total === 0 ? "0 clients or leads in the pipeline"
              : `${m.total} in the pipeline${m.unstaged > 0 ? `, ${m.unstaged} without a stage` : ""}`}
            {" "}· read {hhmm(m.readAt)}
          </p>
        </>}
    </CPanel>
  );
}

export function ShiftsChart({ agents, error, at }: {
  agents: RosterAgent[] | null; error: string | null; at: Date | null;
}): JSX.Element {
  const m = shiftBars(error ? null : agents);
  return (
    <CPanel id="chart-shifts" title="Shifts today" className="cmd-chart"
            at={stamp(at, error !== null)}>
      {error && <Unread>Agents: {safeText(error)}</Unread>}
      {!error && !agents ? <p className="cmd-empty v2-loading"><U /> reading…</p>
        : m.state === "unknown" ? <div className="cmd-chart-unknown" data-health="unknown"><U /></div>
        : <>
          <IsoChart bars={m.bars.map((b) => ({ key: b.name, label: b.name, count: b.count }))}
                    caption={`Shifts today per agent: ${m.bars.map((b) =>
                      `${b.name} ${b.count ?? "unknown"}`).join(", ") || "no agents"}`} />
          <p className="cmd-note">
            {m.bars.length === 0 ? "No agents on the roster"
              : `${m.bars.length} agents`}{m.readAt ? ` · read ${hhmm(m.readAt)}` : " · not read"}
          </p>
        </>}
    </CPanel>
  );
}

type LeadsRead = { leads: Lead[]; total: number };

export function LeadsGrid({ leads }: { leads: Read<LeadsRead> }): JSX.Element {
  const read = readOf(leads);
  const rows = read.data ? latestLeads(read.data.leads) : null;
  return (
    <CPanel id="leads-grid" title="Latest leads" className="cmd-chart"
            at={stamp(leads.fetchedAt, read.error !== null)}
            more={{ to: "/leads", label: "All leads" }}>
      {read.error && <Unread>Leads: {safeText(read.error)}</Unread>}
      {!rows && !read.error && <p className="cmd-empty">Reading…</p>}
      {rows && rows.length === 0 && <p className="cmd-empty">
        0 leads · read {leads.fetchedAt ? hhmm(leads.fetchedAt) : "—"}</p>}
      {rows && rows.length > 0 && (
        <table className="cmd-roster-t cmd-leads-t">
          <thead>
            <tr><th>Business</th><th>Status</th><th>Outreach</th><th>Score</th><th>Touched</th></tr>
          </thead>
          <tbody>
            {rows.map((l) => (
              <tr key={l.id}>
                <td className="cmd-roster-name"><Link to={`/leads?id=${l.id}`}>{safeText(l.business)}</Link></td>
                <td>{safeText(l.status)}</td>
                <td>{l.outreach ? safeText(l.outreach) : <span className="cmd-dim">none</span>}</td>
                <td>{l.score === null ? <U /> : l.score}</td>
                <td className="cmd-dim">{l.at ? hhmm(l.at) : <U />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </CPanel>
  );
}
