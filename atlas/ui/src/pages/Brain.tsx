import { useState, type JSX } from "react";
import { Link } from "react-router-dom";
import type { Brain, BrainDepartment, BrainLayer, BrainRuns, SopStep } from "../api";
import { BrainMap } from "../components/BrainMap";
import {
  LAYER_LABEL, LAYER_SHORT, STEP_WORD, countText, describe, isUnknown, pctText,
  ranState, ranText, runsDetail, seatsLine, seatsOf, sopFor, unknownWhy, verifyLine,
} from "./brainModel";

/**
 * The Brain: ATLAS's real agents, jobs and actions as a company. A command
 * strip for the whole business, the constellation of departments around the
 * Knowledge Core, one card per department, and -- for any seat picked --
 * its standard operating procedure, generated from code by the server.
 *
 * Everything here renders what `GET /api/agency/brain` said, with the time
 * it was read. An unknown figure is drawn with UNKNOWN's grey, dashed
 * treatment and the word itself, never as 0% or 100% (invariant 7).
 */

function Pct({ runs, days, watchOnly, big = false }: {
  runs: BrainRuns | null | undefined; days: number; watchOnly: boolean; big?: boolean;
}): JSX.Element {
  const text = pctText(runs);
  const unknown = isUnknown(runs);
  return (
    <span className="bn-pct" data-unknown={unknown || undefined} data-big={big || undefined}
          title={unknown ? `Unknown: ${unknownWhy(runs, days, watchOnly)}` : runsDetail(runs)}>
      {unknown ? "UNKNOWN" : text}
    </span>
  );
}

export function LayerChip({ layer }: { layer: BrainLayer }): JSX.Element {
  return (
    <span className="bn-chip" data-layer={layer} title={LAYER_LABEL[layer]}>
      {LAYER_SHORT[layer]}
    </span>
  );
}

export function LayerLegend({ brain }: { brain: Brain }): JSX.Element {
  return (
    <ul className="bn-layers" aria-label="Layers">
      {brain.layers.map((l) => (
        <li key={l.key}><LayerChip layer={l.key} /> <strong>{l.label}</strong>{" "}
          <span className="stamp">— {l.meaning}</span></li>
      ))}
    </ul>
  );
}

/** The whole business in one strip. */
export function CommandStrip({ brain }: { brain: Brain }): JSX.Element {
  const w = brain.whole;
  const days = brain.window_days;
  const paused = w.paused;
  return (
    <div className="bn-strip" role="group" aria-label="Whole business">
      <div className="bn-cell">
        <span className="k">Runs without you · {days}d</span>
        <Pct runs={w.runs_without_you} days={days} watchOnly={brain.watch_only} big />
        <span className="stamp">
          {isUnknown(w.runs_without_you)
            ? unknownWhy(w.runs_without_you, days, brain.watch_only)
            : runsDetail(w.runs_without_you)}
        </span>
      </div>
      <Link className="bn-cell bn-waiting" to="/tasks"
            data-unknown={w.waiting_on_you === null || undefined}
            data-some={(w.waiting_on_you ?? 0) > 0 || undefined}>
        <span className="k">Waiting on you</span>
        <span className="bn-num">{w.waiting_on_you === null ? "UNKNOWN" : w.waiting_on_you}</span>
        <span className="stamp">open the approvals →</span>
      </Link>
      <div className="bn-cell" data-unknown={paused === null || undefined}>
        <span className="k">Paused</span>
        <span className="bn-num">{paused === null ? "UNKNOWN" : paused.length}</span>
        <span className="stamp">
          {paused === null ? "the pause state could not be read"
            : paused.length === 0 ? "nothing is paused"
              : paused.map((p) => p.scope + (p.shadow ? " (shadow)" : "")).join(", ")}
        </span>
      </div>
      <div className="bn-cell" data-unknown={w.verify === null || undefined}>
        <span className="k">Read-backs · {days}d</span>
        <span className="bn-num bn-small">{verifyLine(w.verify, brain.verify_after)}</span>
        <span className="stamp">{brain.verify_after ? "read-back is on" : "read-back is off"}</span>
      </div>
      {brain.watch_only && (
        <p className="bn-note">Watch-only: ATLAS reports and does not act, so every share reads unknown.</p>
      )}
    </div>
  );
}

function DepartmentCard({ d, brain, selected, onPick, highlighted }: {
  d: BrainDepartment; brain: Brain; selected: string | null;
  onPick: (id: string) => void; highlighted: boolean;
}): JSX.Element {
  const days = brain.window_days;
  const seats = seatsOf(d);
  return (
    <article className="bn-dept" data-dept={d.key} data-core={d.core || undefined}
             data-highlighted={highlighted || undefined} id={`bn-dept-${d.key}`}>
      <header>
        <h4>{d.name}</h4>
        <span className="bn-head" title={d.head.why}>
          Head: <strong>{d.head.agent === null ? "YOU" : d.head.label}</strong>
          {d.head.agent !== null && <span className="stamp"> · {d.head.title}</span>}
        </span>
      </header>
      <div className="bn-stats">
        <span><span className="k">without you</span>
          <Pct runs={d.runs_without_you} days={days} watchOnly={brain.watch_only} /></span>
        <span data-unknown={d.waiting_on_you === null || undefined}>
          <span className="k">waiting</span>
          <span className="bn-num bn-small" data-some={(d.waiting_on_you ?? 0) > 0 || undefined}>
            {countText(d.waiting_on_you)}</span></span>
        <span><span className="k">Tier 2</span>
          <span className="bn-num bn-small">{d.tier2_actions}/{d.actions.length}</span></span>
      </div>
      <p className="stamp">{seatsLine(d)}</p>
      {d.paused && d.paused.length > 0 && (
        <p className="bn-paused">{d.paused.map((p) => p.says).join(" · ")}</p>
      )}
      <ul className="bn-seats">
        {seats.map((s) => (
          <li key={s.id}>
            <button type="button" className="bn-seat" data-ran={ranState(s)}
                    aria-pressed={selected === s.id} onClick={() => onPick(s.id)}
                    title={`${ranText(s, days)} · open its SOP`}>
              <i className="bn-dot" aria-hidden="true" />
              <span className="n">{s.label}</span>
              <span className="t">{s.kind === "agent" ? s.title : "job"}</span>
              <LayerChip layer={s.layer} />
            </button>
          </li>
        ))}
      </ul>
      {d.actions.length > 0 && (
        <details className="bn-actions">
          <summary>{d.actions.length} action{d.actions.length === 1 ? "" : "s"}</summary>
          <ul>
            {d.actions.map((a) => (
              <li key={a.id}>
                <button type="button" className="bn-action" onClick={() => onPick(a.id)}
                        aria-pressed={selected === a.id}>
                  <span className="bn-tier" data-tier={a.tier}>T{a.tier}</span> {a.name}
                  {a.card_only && <span className="stamp"> · card-only</span>}
                  {a.verifier && <span className="stamp"> · read back</span>}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {d.disabled_modules.length > 0 && (
        <p className="stamp bn-off">
          Off in this process: {d.disabled_modules.map((m) => m.module).join(", ")}
        </p>
      )}
    </article>
  );
}

/** The SOP: a vertical list of steps, each action with its tier and gate. */
export function Sop({ brain, id, onClose }: {
  brain: Brain; id: string; onClose?: () => void;
}): JSX.Element | null {
  const steps = sopFor(brain, id);
  if (!steps) return null;
  const what = describe(brain, id);
  return (
    <section className="bn-sop" aria-label={`Standard operating procedure: ${what.title}`}>
      <header>
        <span className="k">SOP</span>
        <h4>{what.title}</h4>
        {what.layer && <LayerChip layer={what.layer} />}
        <span className="stamp">{what.sub}</span>
        {onClose && <button type="button" className="btn" onClick={onClose}>Close</button>}
      </header>
      <ol className="bn-steps">
        {steps.map((s, i) => <Step key={`${s.kind}-${i}`} step={s} />)}
      </ol>
      <p className="stamp">Generated from the code: the job's policy and schedule, each action's
        classified tier, card-only flag, topic and verifier.</p>
    </section>
  );
}

function Step({ step }: { step: SopStep }): JSX.Element {
  return (
    <li className="bn-step" data-kind={step.kind}>
      <span className="bn-step-k">{STEP_WORD[step.kind]}</span>
      <span className="bn-step-t">{step.text}</span>
      {step.actions && step.actions.length > 0 && step.kind !== "signoff" && (
        <ul className="bn-step-actions">
          {step.actions.map((a) => (
            <li key={a.name}>
              <span className="bn-tier" data-tier={a.tier}>T{a.tier}</span>{" "}
              <code>{a.name}</code>
              {a.needs_you && <span className="bn-you"> needs you</span>}
              {a.card_only && <span className="stamp"> · card-only</span>}
              <span className="stamp"> · #{a.topic}
                {a.topic_depends_on ? ` (by ${a.topic_depends_on})` : ""}
                {a.verifier ? " · read back" : " · no verifier"}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** Everything but the fetch: static-renderable for the tests. */
export function BrainView({ brain, selected, onPick }: {
  brain: Brain; selected: string | null; onPick: (id: string | null) => void;
}): JSX.Element {
  const pickedDept = selected?.startsWith("dept:") ? selected.slice(5) : null;
  const sopId = selected && !selected.startsWith("dept:") && selected !== "you" ? selected : null;
  const errors = Object.entries(brain.errors);
  return (
    <div className="bn-view">
      {errors.length > 0 && (
        <p className="errorbar">
          Some of the record could not be read, so those figures read UNKNOWN:{" "}
          {errors.map(([k, v]) => `${k} (${v})`).join("; ")}
        </p>
      )}
      <CommandStrip brain={brain} />
      <LayerLegend brain={brain} />
      <BrainMap brain={brain} selected={selected} onActivate={(id) => onPick(selected === id ? null : id)} />
      {sopId && <Sop brain={brain} id={sopId} onClose={() => onPick(null)} />}
      <div className="bn-depts">
        {brain.departments.map((d) => (
          <DepartmentCard key={d.key} d={d} brain={brain} selected={selected}
                          onPick={(id) => onPick(selected === id ? null : id)}
                          highlighted={pickedDept === d.key} />
        ))}
      </div>
      {(brain.unmapped.executions > 0 || (brain.unmapped.waiting_on_you ?? 0) > 0) && (
        <p className="stamp">
          Not in any department: {brain.unmapped.executions} execution(s)
          {brain.unmapped.actions.length > 0 ? ` of ${brain.unmapped.actions.join(", ")}` : ""}
          {" and "}{countText(brain.unmapped.waiting_on_you)} waiting card(s) — actions no
          longer registered.
        </p>
      )}
    </div>
  );
}

export function BrainPanel({ brain, error, fetchedAt }: {
  brain: Brain | null; error: string | null; fetchedAt: Date | null;
}): JSX.Element {
  const [selected, setSelected] = useState<string | null>(null);
  // Whatever was picked is brought into view: a seat clicked in a card
  // sits below the map, and its SOP opens above it -- out of sight on a
  // phone without this. After the render, so the SOP exists to scroll to.
  function pick(id: string | null): void {
    setSelected(id);
    if (!id || id === "you") return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    window.requestAnimationFrame(() => {
      const target = id.startsWith("dept:")
        ? document.getElementById(`bn-dept-${id.slice(5)}`)
        : document.querySelector<HTMLElement>(".bn-sop");
      target?.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "nearest" });
    });
  }
  return (
    <div className="panel bn-panel">
      <div className="inner">
        <header className="agency-map-head">
          <h3 className="label">The Brain</h3>
          <span className="stamp">
            {brain
              ? `Departments from the code; figures from the last ${brain.window_days} days, read ${new Date(brain.read_at).toLocaleTimeString()}`
              : fetchedAt ? "" : "Reading…"}
          </span>
        </header>
        {error && (
          <p className="errorbar">
            Could not read the brain: {error}
            {brain ? " — what is shown is the earlier read." : " — nothing is shown rather than guesses."}
          </p>
        )}
        {brain && <BrainView brain={brain} selected={selected} onPick={pick} />}
      </div>
    </div>
  );
}
