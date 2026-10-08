import type { ReactNode } from "react";
import { Sparkline } from "./Sparkline";
import "./skeleton.css";

/**
 * The redesign's one page skeleton, shared by every portal tab and every
 * console section:
 *
 *   page header → optional verdict → stat strip → exactly one feature block → records
 *
 * The verdict is the one genuinely new element: a plain-English, colour-coded
 * answer BEFORE any data, for the owner who is not technical. It is only
 * rendered on pages that really answer a yes/no, and its headline never
 * claims safety — "nothing needs you" is a fact about a queue; "you are
 * protected" is a claim Vault cannot always back.
 *
 * Every colour is an existing --color-* token; there are no new ones, no
 * shadows, radii 2–4px, and a 1px --color-border hairline is the only
 * separator.
 */

export type Tone = "good" | "warn" | "alert" | "plain";

const TONE_INK: Record<Tone, string> = {
  good: "var(--color-good-text)", warn: "var(--color-warn-text)", alert: "var(--color-alert-text)", plain: "var(--color-text)",
};
const TONE_MARK: Record<Tone, string> = {
  good: "var(--color-good)", warn: "var(--color-warn)", alert: "var(--color-alert)", plain: "var(--color-border-strong)",
};

/* ---------------------------------------------------------- page header --- */

export type PageAction = { label: string; onClick?: () => void; href?: string; primary?: boolean; disabled?: boolean; title?: string };

export function PageHeader({ eyebrow, title, sub, actions = [], extra }: {
  eyebrow: string; title: ReactNode; sub?: string; actions?: PageAction[];
  /** A page's own control cluster (tabs, network chips) rendered beside the
   *  buttons, so existing actions keep their component and behaviour. */
  extra?: ReactNode;
}) {
  return (
    <header className="sk-head">
      <div className="sk-head-text">
        <div className="sk-eyebrow">{eyebrow}</div>
        <h1 className="sk-title">{title}</h1>
        {sub && <p className="sk-sub">{sub}</p>}
      </div>
      {(actions.length > 0 || extra) && (
        <div className="sk-actions">
          {extra}
          {actions.map((a) =>
            a.href ? (
              <a key={a.label} className={`sk-btn${a.primary ? " sk-btn-primary" : ""}`} href={a.href} title={a.title}>{a.label}</a>
            ) : (
              <button key={a.label} type="button" className={`sk-btn${a.primary ? " sk-btn-primary" : ""}`}
                      onClick={a.onClick} disabled={a.disabled} title={a.title}>{a.label}</button>
            ),
          )}
        </div>
      )}
    </header>
  );
}

/* --------------------------------------------------------------- verdict --- */

export type VerdictFact = { value: string; label: string; tone?: Tone };

export function Verdict({ tone, headline, body, facts = [], children }: {
  tone: Tone; headline: string; body: string; facts?: VerdictFact[];
  /** An optional visual under the sentence -- a chart that backs it up --
   *  so a verdict beside a tall companion never sits as empty space. */
  children?: ReactNode;
}) {
  return (
    <section className="sk-verdict" data-tone={tone} role="status" aria-label="Verdict">
      <div className="sk-verdict-bar" style={{ background: TONE_MARK[tone] }} aria-hidden="true" />
      <div className="sk-verdict-text">
        <div className="sk-verdict-head" style={{ color: TONE_INK[tone] }}>{headline}</div>
        <div className="sk-verdict-body">{body}</div>
      </div>
      {facts.length > 0 && (
        <div className="sk-verdict-facts">
          {facts.slice(0, 3).map((f) => (
            <div key={f.label} className="sk-fact">
              <div className="sk-fact-v" style={{ color: TONE_INK[f.tone ?? "plain"] }}>{f.value}</div>
              <div className="sk-fact-l">{f.label}</div>
            </div>
          ))}
        </div>
      )}
      {children && <div className="sk-verdict-visual">{children}</div>}
    </section>
  );
}

/* ------------------------------------------------------------ stat strip --- */

export type StatItem = {
  label: string; value: string;
  /** Short mono qualifier beside the value — "+2", "ago", "all up". */
  delta?: string; deltaTone?: Tone;
  /** Recent shape, oldest first. Omitted or shorter than 2 draws nothing. */
  spark?: number[]; sparkTone?: Tone;
};

export function StatStrip({ items }: { items: StatItem[] }) {
  // 2–4 figures by design; more than four is a table, not a strip.
  const shown = items.slice(0, 4);
  return (
    <div className="sk-stats" role="list">
      {shown.map((s) => (
        <div key={s.label} className="sk-stat" role="listitem">
          <div className="sk-stat-l">{s.label}</div>
          {/* Item 04: the sparkline rides IN the value row rather than as a
              block beneath it. As a block, only the cells that happened to
              have a series were tall, the grid row took the tallest, and
              every other cell in the strip carried the difference as dead
              space -- the "big gap" under Devices watched and Blocked. */}
          <div className="sk-stat-row">
            <span className="sk-stat-v">{s.value}</span>
            {s.delta && <span className="sk-stat-d" style={{ color: TONE_INK[s.deltaTone ?? "plain"] }}>{s.delta}</span>}
            {s.spark && s.spark.length > 1 && (
              <Sparkline points={s.spark} height={16} tone={s.sparkTone ?? "plain"} className="sk-stat-spark" />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------- feature block --- */

export function Feature({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="sk-feature">
      <header className="sk-feature-head">
        <h2 className="sk-section-title">{title}</h2>
        {note && <span className="sk-note">{note}</span>}
      </header>
      {children}
    </section>
  );
}

/** Stacked bars: primary over warn, 150px tall, 4px gap, 1px bottom rule. */
export function Bars({ bars, axis, empty }: {
  bars: { a: number; b?: number; title?: string }[]; axis?: [string, string];
  /** What to say when every bar is zero. A 150px chart of nothing is just a
   *  void with a baseline under it, so the sentence takes its place. */
  empty?: string;
}) {
  const total = bars.reduce((n, b) => n + b.a + (b.b ?? 0), 0);
  if (total === 0 && empty) return <p className="sk-empty sk-bars-empty">{empty}</p>;
  const peak = Math.max(1, ...bars.map((b) => b.a + (b.b ?? 0)));
  return (
    <div className="sk-bars-wrap">
      <div className="sk-bars" aria-hidden="true">
        {bars.map((b, i) => (
          <div key={i} className="sk-bar" title={b.title}>
            <span className="sk-bar-b" style={{ height: `${((b.b ?? 0) / peak) * 100}%` }} />
            <span className="sk-bar-a" style={{ height: `${(b.a / peak) * 100}%` }} />
          </div>
        ))}
      </div>
      {axis && <div className="sk-axis"><span>{axis[0]}</span><span>{axis[1]}</span></div>}
    </div>
  );
}

/** Area + dashed comparison: 600×170 viewBox, primary fill @14%, 1.8px line. */
export function Area({ points, compare, axis }: { points: number[]; compare?: number[]; axis?: [string, string] }) {
  const W = 600, H = 170, P = 8;
  const all = [...points, ...(compare ?? [])].filter(Number.isFinite);
  const max = Math.max(1, ...all), min = Math.min(0, ...all);
  const x = (i: number, n: number) => P + (i / Math.max(1, n - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / (max - min || 1)) * (H - 2 * P);
  const path = (pts: number[]) => pts.map((v, i) => `${i ? "L" : "M"}${x(i, pts.length).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const areaD = points.length > 1 ? `${path(points)} L${x(points.length - 1, points.length).toFixed(1)},${H - P} L${P},${H - P} Z` : "";
  return (
    <div className="sk-area-wrap">
      <svg className="sk-area" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        {areaD && <path d={areaD} className="sk-area-fill" />}
        {points.length > 1 && <path d={path(points)} className="sk-area-line" />}
        {compare && compare.length > 1 && <path d={path(compare)} className="sk-area-compare" />}
      </svg>
      {axis && <div className="sk-axis"><span>{axis[0]}</span><span>{axis[1]}</span></div>}
    </div>
  );
}

/** Ranked bars: name + meta / 7px bar on --color-track / mono value / optional action. */
export type RankedRow = { name: string; meta?: string; value: number; display?: string; tone?: Tone; action?: PageAction };
export function Ranked({ rows }: { rows: RankedRow[] }) {
  const peak = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ol className="sk-ranked">
      {rows.map((r) => (
        <li key={r.name} className="sk-ranked-row">
          <div className="sk-ranked-name"><span>{r.name}</span>{r.meta && <em>{r.meta}</em>}</div>
          <div className="sk-ranked-track" aria-hidden="true"><i style={{ width: `${(r.value / peak) * 100}%`, background: TONE_MARK[r.tone ?? "plain"] === TONE_MARK.plain ? "var(--color-primary)" : TONE_MARK[r.tone ?? "plain"] }} /></div>
          <div className="sk-ranked-v">{r.display ?? r.value.toLocaleString()}</div>
          {r.action && <button type="button" className="sk-rowact" onClick={r.action.onClick}>{r.action.label}</button>}
        </li>
      ))}
    </ol>
  );
}

/** Checks: 2-up grid, status dot + label + state pill + one plain sentence. */
export type CheckRow = { label: string; state: string; tone: Tone; sentence: string; action?: PageAction };
export function Checks({ rows }: { rows: CheckRow[] }) {
  return (
    <ul className="sk-checks">
      {rows.map((c) => (
        <li key={c.label} className="sk-check" data-tone={c.tone}>
          <div className="sk-check-top">
            <span className="sk-dot" style={{ background: TONE_MARK[c.tone] }} aria-hidden="true" />
            <span className="sk-check-label">{c.label}</span>
            <span className="sk-pill" style={{ color: TONE_INK[c.tone], borderColor: TONE_MARK[c.tone] }}>{c.state}</span>
          </div>
          <p className="sk-check-sentence">{c.sentence}</p>
          {c.action && <button type="button" className="sk-rowact" onClick={c.action.onClick}>{c.action.label}</button>}
        </li>
      ))}
    </ul>
  );
}

/** Heatmap: 7 × 24, 17px cells, 2px gap, primary mixed 12 → 92%. */
export function Heat({ rows, cols, cells, rowLabel, colLabel }: {
  rows: string[]; cols: number[]; cells: { row: string; col: number; value: number }[];
  rowLabel?: (r: string) => string; colLabel?: (c: number) => string;
}) {
  const peak = Math.max(1, ...cells.map((c) => c.value));
  const at = new Map(cells.map((c) => [`${c.row}|${c.col}`, c.value]));
  const mix = (v: number) => (v <= 0 ? 0 : [12, 32, 60, 92][Math.min(3, Math.floor((v / peak) * 4))] );
  return (
    <div className="sk-heat" role="img" aria-label="Activity heatmap">
      <div className="sk-heat-grid" style={{ gridTemplateColumns: `auto repeat(${cols.length}, 17px)` }}>
        {rows.map((r) => (
          <>
            <div key={`l-${r}`} className="sk-heat-rl">{rowLabel ? rowLabel(r) : r}</div>
            {cols.map((c) => {
              const v = at.get(`${r}|${c}`) ?? 0; const m = mix(v);
              return <div key={`${r}-${c}`} className="sk-heat-cell" title={`${r} ${colLabel ? colLabel(c) : c}: ${v}`}
                          style={{ background: m ? `color-mix(in srgb, var(--color-primary) ${m}%, var(--color-surface))` : "var(--color-track)" }} />;
            })}
          </>
        ))}
      </div>
    </div>
  );
}

/** Tile grid: auto-fit minmax(180px,1fr), 1px gaps over --color-border. */
export type Tile = { title: string; body?: string; meta?: string; tone?: Tone; onClick?: () => void; href?: string };
export function Tiles({ tiles }: { tiles: Tile[] }) {
  return (
    <div className="sk-tiles">
      {tiles.map((t) => {
        const inner = (<><span className="sk-tile-t">{t.title}</span>{t.body && <span className="sk-tile-b">{t.body}</span>}{t.meta && <span className="sk-tile-m" style={{ color: TONE_INK[t.tone ?? "plain"] }}>{t.meta}</span>}</>);
        return t.href ? <a key={t.title} className="sk-tile" href={t.href}>{inner}</a>
          : t.onClick ? <button key={t.title} type="button" className="sk-tile" onClick={t.onClick}>{inner}</button>
          : <div key={t.title} className="sk-tile">{inner}</div>;
      })}
    </div>
  );
}

/** Toggles: ruled rows, 40×22 switch, track primary/track, 16px knob. */
export type ToggleRow = { label: string; detail?: string; on: boolean; onChange?: (on: boolean) => void; disabled?: boolean };
export function Toggles({ rows }: { rows: ToggleRow[] }) {
  return (
    <ul className="sk-toggles">
      {rows.map((r) => (
        <li key={r.label} className="sk-toggle-row">
          <div className="sk-toggle-text"><span className="sk-toggle-l">{r.label}</span>{r.detail && <span className="sk-toggle-d">{r.detail}</span>}</div>
          <button type="button" role="switch" aria-checked={r.on} aria-label={r.label} className="sk-switch" disabled={r.disabled}
                  onClick={() => r.onChange?.(!r.on)}><span className="sk-knob" /></button>
        </li>
      ))}
    </ul>
  );
}

/* --------------------------------------------------------------- records --- */

export type RecordCol = { key: string; header: string; align?: "left" | "right"; width?: string };
export type RecordRow = { id: string; cells: ReactNode[]; action?: PageAction };

export function Records({ title, columns, rows, empty = "Nothing here." }: {
  title: string; columns: RecordCol[]; rows: RecordRow[]; empty?: ReactNode;
}) {
  return (
    <section className="sk-records">
      <header className="sk-feature-head"><h2 className="sk-section-title">{title}</h2></header>
      {rows.length === 0 ? (
        <p className="sk-empty">{empty}</p>
      ) : (
        <table className="sk-table">
          <thead><tr>{columns.map((c) => <th key={c.key} style={{ textAlign: c.align ?? "left", width: c.width }}>{c.header}</th>)}{rows.some((r) => r.action) && <th aria-label="Action" />}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                {r.cells.map((cell, i) => <td key={i} style={{ textAlign: columns[i]?.align ?? "left" }}>{cell}</td>)}
                {rows.some((x) => x.action) && <td className="sk-td-act">{r.action && (r.action.href
                  ? <a className="sk-rowact" href={r.action.href}>{r.action.label}</a>
                  : <button type="button" className="sk-rowact" onClick={r.action.onClick}>{r.action.label}</button>)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** The page wrapper: 22px 28px 56px padding, 24px gaps, 1360px max. */
export function Page({ children }: { children: ReactNode }) {
  return <div className="sk-page">{children}</div>;
}
