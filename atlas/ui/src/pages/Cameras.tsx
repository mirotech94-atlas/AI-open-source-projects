import { useCallback, useEffect, useState, type JSX } from "react";
import { api } from "../api";
import { HealthBadge } from "../components/Health";
import { useFresh } from "../hooks/useFresh";
import "./cameras.css";
import {
  APPROVALS_HREF, FORGET_WORD, LARGE_REFRESH_MS, MODES, NAME_MAX, SLOT_COUNT,
  forgetReady, peopleView, validName, type PeopleView, OVERVIEW_REFRESH_MS, cardHealth, cardStyleClass,
  clipLine, formatClock, motionLine, nvrHealth, nvrLine, onlineLine, readLabel, snapUrl, twelveCards,
  MODE_HELP, normaliseMode, type CameraCard, type CameraClip, type CameraClips, type CameraMode, type CamerasOverview,
  COVERAGE_REFRESH_MS, cameraMetrics, coverageSide, overlayShapes, ruleLine, twelveCoverage, type Coverage, type MetricCell, type RuleInfo,
} from "./camerasModel";

/**
 * The Cameras tab: twelve snapshot cards read from `/api/cameras/overview`,
 * a large view that polls one frame a second (no RTSP), the clips, detection
 * coverage, people, and the ATLAS-side mode switch. A failed read is twelve UNKNOWN
 * cards, never an empty page; UNKNOWN never takes the OK look.
 */
export function Cameras(): JSX.Element {
  const overview = useFresh(() => api.camerasOverview(), []);
  const clips = useFresh(() => api.cameraClips(), []);
  const coverage = useFresh(() => api.cameraCoverage(), []);
  const [now, setNow] = useState(() => new Date());
  const [open, setOpen] = useState<number | null>(null);
  const [tab, setTab] = useState<CamerasTab>("feeds");
  const [mode, setMode] = useState<CameraMode | null>(null);
  const [modeError, setModeError] = useState<string | null>(null);
  const { reload: reloadOverview } = overview;
  const { reload: reloadClips } = clips;
  const { reload: reloadCoverage } = coverage;

  // coverage reads the NVR 36 times: its own slow timer, never the 5 s one
  useEffect(() => {
    const timer = window.setInterval(reloadCoverage, COVERAGE_REFRESH_MS);
    return (): void => window.clearInterval(timer);
  }, [reloadCoverage]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      reloadOverview();
      reloadClips();
      setNow(new Date());
    }, OVERVIEW_REFRESH_MS);
    return (): void => window.clearInterval(timer);
  }, [reloadOverview, reloadClips]);

  const served = overview.error ? null : overview.data;
  const shownMode = mode ?? (served?.mode ? normaliseMode(served.mode) : null);

  const onMode = useCallback((next: CameraMode): void => {
    setModeError(null);
    void api.session()
      .then((s) => api.setCameraMode(next, s.csrf ?? ""))
      .then(() => { setMode(next); reloadOverview(); })
      .catch((e: unknown) => setModeError(e instanceof Error ? e.message : String(e)));
  }, [reloadOverview]);

  const cards = twelveCards(served);
  const big = open === null ? null : cards.find((c) => c.number === open) ?? null;
  const cov = coverage.error ? null : coverage.data;
  const bigRules = big === null ? [] : twelveCoverage(cov).find((r) => r.number === big.number)?.rules ?? [];
  return (
    <div className="cameras">
      <CamerasHead overview={served} coverage={cov} now={now} error={overview.error} />
      <CamerasTabs tab={tab} onTab={setTab} />
      <div role="tabpanel" id="cam-tab-feeds" aria-labelledby="cam-t-feeds" hidden={tab !== "feeds"}>
        <CamerasView
          overview={served} now={now}
          error={overview.error} mode={shownMode} onOpen={setOpen} onMode={onMode} />
      </div>
      <div role="tabpanel" id="cam-tab-clips" aria-labelledby="cam-t-clips" hidden={tab !== "clips"}>
        <ClipsPanel clips={clips.error ? null : clips.data} />
      </div>
      <div role="tabpanel" id="cam-tab-coverage" aria-labelledby="cam-t-coverage" hidden={tab !== "coverage"}>
        <CoveragePanel coverage={cov} />
      </div>
      <div role="tabpanel" id="cam-tab-people" aria-labelledby="cam-t-people" hidden={tab !== "people"}>
        <PeopleSection />
      </div>
      {modeError && <p className="errorbar">Could not change the mode: {modeError}</p>}
      {big && <LargeView card={big} rules={bigRules} onClose={() => setOpen(null)} />}
    </div>
  );
}

export type CamerasTab = "feeds" | "clips" | "coverage" | "people";
const TABS: ReadonlyArray<{ id: CamerasTab; text: string }> = [
  { id: "feeds", text: "Feeds" }, { id: "clips", text: "Clips" },
  { id: "coverage", text: "Coverage" }, { id: "people", text: "People" },
];

/** Page head and the three metric cells. Every figure shows its read time; unread is UNKNOWN. */
export function CamerasHead({ overview, coverage, now, error }: {
  overview: CamerasOverview | null; coverage: Coverage | null; now: Date; error: string | null;
}): JSX.Element {
  const cells = cameraMetrics(overview, coverage, now);
  return (
    <header className="cam-top">
      <div className="cam-top-title">
        <span className="v2-eyebrow">Home security</span>
        <h2 className="v2-h1">Cameras</h2>
        <p className="cam-readline">
          {overview ? `read ${formatClock(overview.served_at)}` : "not read"}
          {error ? " · read failed" : ""}
        </p>
      </div>
      <div className="cam-metrics" role="group" aria-label="Camera figures">
        {cells.map((c) => <MetricBox key={c.key} cell={c} />)}
      </div>
    </header>
  );
}

function MetricBox({ cell }: { cell: MetricCell }): JSX.Element {
  const health = cell.read ? (cell.health ?? "ok") : "unknown";
  return (
    <div className={`cam-cell ${cell.read ? "" : "cam-unknown hatch-edge"}`}
         data-cell={cell.key} data-health={cell.read && cell.key !== "nvr" ? "read" : health}>
      <span className="k">{cell.label}</span>
      <span className="cam-fig">{cell.figure}</span>
      <span className="cam-cell-read">{cell.read ? readLabel(cell.readAt) : "not read"}</span>
    </div>
  );
}

export function CamerasTabs({ tab, onTab }: {
  tab: CamerasTab; onTab: (t: CamerasTab) => void;
}): JSX.Element {
  return (
    <div className="cam-tabs" role="tablist" aria-label="Cameras sections">
      {TABS.map((t, i) => (
        <button key={t.id} type="button" role="tab"
                onKeyDown={(e) => {
                  const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                  if (!d) return;
                  e.preventDefault();
                  const next = TABS[(i + d + TABS.length) % TABS.length]!.id;
                  onTab(next);
                  // Roving tabindex: focus follows the selected tab.
                  document.getElementById(`cam-t-${next}`)?.focus();
                }} id={`cam-t-${t.id}`}
                aria-selected={tab === t.id} aria-controls={`cam-tab-${t.id}`}
                tabIndex={tab === t.id ? 0 : -1} className="cam-tab"
                onClick={() => onTab(t.id)}>{t.text}</button>
      ))}
    </div>
  );
}

export function CamerasView({ overview, now, error, mode, onOpen, onMode }: {
  overview: CamerasOverview | null;
  now: Date;
  error: string | null;
  mode: CameraMode | null;
  onOpen: (number: number) => void;
  onMode: (mode: CameraMode) => void;
}): JSX.Element {
  const cards = twelveCards(overview);
  const t = overview ? new Date(overview.served_at).getTime() : now.getTime();
  return (
    <>
      <p className="stamp cam-head" role="status">
        <HealthBadge health={nvrHealth(overview?.nvr ?? null)} />{" "}
        {nvrLine(overview?.nvr ?? null)}
        {overview?.nvr.reason ? ` · ${overview.nvr.reason}` : ""}
        {error ? ` · cameras could not be read: ${error}` : ""}
      </p>
      <div className="cam-mode" role="group" aria-label="Mode">
        <span className="k">Mode</span>
        {MODES.map((m) => (
          <button key={m} type="button" className="cam-mode-btn" aria-pressed={mode === m}
                  onClick={() => onMode(m)}>
            {m === "home" ? "Home" : "Away"}
          </button>
        ))}
        {mode === null && <span className="stamp">mode not read</span>}
        <ul className="cam-mode-help">
          {MODES.map((m) => (
            <li key={m} className="stamp"><b>{m === "home" ? "Home" : "Away"}:</b> {MODE_HELP[m]}</li>
          ))}
        </ul>
      </div>
      <div className="cam-grid">
        {cards.map((c) => <Card key={c.number} card={c} now={now} t={t} onOpen={onOpen} />)}
      </div>
    </>
  );
}

function Card({ card, now, t, onOpen }: {
  card: CameraCard; now: Date; t: number; onOpen: (n: number) => void;
}): JSX.Element {
  const health = cardHealth(card, now);
  const hasFrame = card.frame_at !== null;
  return (
    <button type="button" className={`cam-card ${cardStyleClass(health)}`}
            data-health={health} onClick={() => onOpen(card.number)}>
      <span className="cam-title">
        <b>{card.name}</b> <span className="cam-id">Camera {card.number}</span>
      </span>
      <span className="stamp">{card.model ?? "model unknown"} · {onlineLine(card)}</span>
      <span className="cam-frame">
        {hasFrame && health !== "unknown"
          ? <img src={snapUrl(card.snapshot_url, t)} alt={`${card.name} latest frame`}
                 loading="lazy"
                 onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
          : (
            <span className="cam-noframe">
              <b>{health === "unknown" ? "Unknown" : "No current frame"}</b>
              {card.reason && health !== "ok" && <small>{card.reason}</small>}
            </span>
          )}
        <span className="cam-stamp">{readLabel(card.frame_at)}</span>
      </span>
      <span className="stamp">Last motion: {motionLine(card)}</span>
      <span className="cam-foot">
        <HealthBadge health={health} />
        <span className="stamp">{readLabel(card.read_at)}</span>
      </span>
    </button>
  );
}

/** The large view: one frame about every second, fetched so the frame's own
 *  X-Read-At is what is shown. A 503 or any failure is UNKNOWN, not the last
 *  good frame passed off as live. */
function LargeView({ card, rules, onClose }: {
  card: CameraCard; rules: RuleInfo[]; onClose: () => void;
}): JSX.Element {
  const [src, setSrc] = useState<string | null>(null);
  const [readAt, setReadAt] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const tick = async (): Promise<void> => {
      try {
        const r = await fetch(snapUrl(card.snapshot_url, Date.now(), true),
          { credentials: "same-origin", cache: "no-store" });
        if (!r.ok) throw new Error(r.status === 503 ? "camera unreadable" : `HTTP ${r.status}`);
        const stamp = r.headers.get("X-Read-At");
        const blob = await r.blob();
        if (stop) return;
        // data: URL, not blob: -- the page's CSP allows `img-src 'self' data:`
        // only, so a blob: URL renders as a broken image.
        const next = await new Promise<string>((resolve, reject) => {
          const fr = new FileReader();
          fr.onload = (): void => resolve(String(fr.result));
          fr.onerror = (): void => reject(new Error("frame unreadable"));
          fr.readAsDataURL(blob);
        });
        if (stop) return;
        setSrc(next);
        setReadAt(stamp);
        setFailed(null);
      } catch (e: unknown) {
        if (stop) return;
        setSrc(null);
        setReadAt(null);
        setFailed(e instanceof Error ? e.message : String(e));
      }
      if (!stop) timer = window.setTimeout(() => { void tick(); }, LARGE_REFRESH_MS);
    };
    void tick();
    return (): void => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [card.snapshot_url]);

  const known = src !== null && readAt !== null;
  return (
    <div className="cam-large" role="dialog" aria-label={`${card.name} large view`}>
      <div className="panel inner">
        <p className="cam-title">
          <b>{card.name}</b> <span className="cam-id">Camera {card.number}</span>{" "}
          <button type="button" className="cam-close" onClick={onClose}>Close</button>
        </p>
        <div className={`cam-frame ${known ? "" : "hatch-edge"}`}
             data-health={known ? "ok" : "unknown"}>
          {known
            ? <img src={src} alt={`${card.name} live frame`} />
            : <span className="cam-noframe">Unknown{failed ? ` · ${failed}` : ""}</span>}
          <RuleShapes rules={rules} />
        </div>
        <p className="stamp">{readLabel(readAt)} · refreshes about once a second</p>
        <RuleList rules={rules} />
      </div>
    </div>
  );
}

/** Fetches the people read and runs the three writes (name, assign, forget). */
function PeopleSection(): JSX.Element {
  const people = useFresh(() => api.cameraPeople(), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removed, setRemoved] = useState<number | null>(null);
  const { reload } = people;
  useEffect(() => {
    const t = window.setInterval(reload, OVERVIEW_REFRESH_MS);
    return (): void => window.clearInterval(t);
  }, [reload]);

  const run = useCallback((go: (csrf: string) => Promise<unknown>, after?: (r: unknown) => void): void => {
    setError(null);
    setBusy(true);
    void api.session()
      .then((s) => go(s.csrf ?? ""))
      .then((r) => { after?.(r); reload(); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [reload]);

  const view = peopleView(people.error ? null : people.data);
  return (
    <PeoplePanel
      view={view} busy={busy} error={error} removed={removed}
      onSaveName={(slot, name) => { setRemoved(null); run((c) => api.setPersonName(slot, name, c)); }}
      onAssign={(id, slot) => { setRemoved(null); run((c) => api.assignCluster(id, slot, c)); }}
      onHousehold={(id, slot) => {
        setRemoved(null); run((c) => api.markClusterHousehold(id, slot, c));
      }}
      onForget={(scope, slot) => run((c) => api.forgetFaces(scope, slot, c),
        (r) => setRemoved((r as { removed: number }).removed))} />
  );
}

export function PeoplePanel({ view, busy, error, removed, onSaveName, onAssign, onHousehold,
  onForget }: {
  view: PeopleView;
  busy: boolean;
  error: string | null;
  removed: number | null;
  onSaveName: (slot: number, name: string) => void;
  onAssign: (id: string, slot: number | null) => void;
  onHousehold: (id: string, slot: number | null) => void;
  onForget: (scope: "all" | "slot", slot: number | null) => void;
}): JSX.Element {
  return (
    <section className="panel cam-people" aria-label="People">
      <h3 className="cam-h">People</h3>
      <p className="stamp" role="status">
        {view.chips.map((c) => (
          <span key={c.label} className={`cam-chip ${c.unknown ? "cam-unknown hatch-edge" : "cam-ok"}`}
                data-health={c.unknown ? "unknown" : "ok"}>
            {c.unknown ? "Unknown · " : ""}{c.text}
          </span>
        ))}{" "}
        {view.readLine}
      </p>
      {!view.readable ? <p className="stamp">Unknown · people could not be read.</p> : (
        <>
          <ul className="cam-slots">
            {view.slots.map((s) => <SlotRow key={s.slot} s={s} busy={busy} onSave={onSaveName} />)}
          </ul>
          {view.empty ? <p className="stamp">No faces stored · {view.readLine}</p> : (
            <ul className="cam-clusters">
              {view.clusters.map((c) => (
                <li key={c.id} className="cam-cluster">
                  {c.thumb
                    ? <img src={c.thumb} alt="Stored face" loading="lazy" />
                    : <span className="cam-noframe">No picture</span>}{" "}
                  <b>{c.household ? "household" : "unknown"}</b>{" "}
                  <span className="stamp">
                    {c.sightings} sighting(s) · {c.where} · first {c.first} · last {c.last} ({view.readLine})
                  </span>
                  {c.household && (
                    <label> Person{" "}
                      <select value={c.slot ?? ""} disabled={busy}
                              onChange={(e) => onAssign(c.id, e.target.value === "" ? null : Number(e.target.value))}>
                        <option value="">none</option>
                        {view.slots.map((s) => <option key={s.slot} value={s.slot}>{s.text}</option>)}
                      </select>
                    </label>
                  )}
                  {!c.household && (
                    <HouseholdMark id={c.id} slots={view.slots} busy={busy} onMark={onHousehold} />
                  )}
                  {c.note && (
                    <span className="stamp"> {c.note}
                      {c.cardLink && <> · <a href={APPROVALS_HREF}>Open Approvals</a></>}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          <ForgetBox slots={view.slots.map((s) => s.slot)} busy={busy} removed={removed}
                     onForget={onForget} />
        </>
      )}
      {error && <p className="errorbar">People action failed: {error}</p>}
    </section>
  );
}

/** Marks an unknown face group as a household member; the page only shows the
 *  result after the server has answered and the list is re-read. */
function HouseholdMark({ id, slots, busy, onMark }: {
  id: string; slots: PeopleView["slots"]; busy: boolean;
  onMark: (id: string, slot: number | null) => void;
}): JSX.Element {
  const [slot, setSlot] = useState("");
  return (
    <span className="cam-household">{" "}
      <select value={slot} disabled={busy} aria-label={`Household person for ${id}`}
              onChange={(e) => setSlot(e.target.value)}>
        <option value="">no person yet</option>
        {slots.map((s) => <option key={s.slot} value={s.slot}>{s.text}</option>)}
      </select>{" "}
      <button type="button" disabled={busy}
              onClick={() => onMark(id, slot === "" ? null : Number(slot))}>
        This is a household member
      </button>
    </span>
  );
}

function SlotRow({ s, busy, onSave }: {
  s: PeopleView["slots"][number]; busy: boolean; onSave: (slot: number, name: string) => void;
}): JSX.Element {
  const [text, setText] = useState(s.name ?? "");
  useEffect(() => setText(s.name ?? ""), [s.name]);
  const ok = validName(text) !== null;
  return (
    <li className="cam-slot">
      <span>{s.text}</span>{" "}
      <input type="text" value={text} maxLength={NAME_MAX} aria-label={`Name for slot ${s.slot}`}
             onChange={(e) => setText(e.target.value)} />{" "}
      <button type="button" disabled={busy || !ok}
              onClick={() => { const n = validName(text); if (n) onSave(s.slot, n); }}>Save</button>
    </li>
  );
}

function ForgetBox({ slots, busy, removed, onForget }: {
  slots: number[]; busy: boolean; removed: number | null;
  onForget: (scope: "all" | "slot", slot: number | null) => void;
}): JSX.Element {
  const [typed, setTyped] = useState("");
  const [scope, setScope] = useState<"all" | "slot">("all");
  const [slot, setSlot] = useState(1);
  return (
    <div className="cam-forget">
      <h4 className="cam-h">Forget face data</h4>
      <p className="stamp">Deletes stored faces for good. Type {FORGET_WORD} to enable.</p>
      <select value={scope} onChange={(e) => setScope(e.target.value === "slot" ? "slot" : "all")}
              aria-label="Forget scope">
        <option value="all">All people</option>
        <option value="slot">One person</option>
      </select>{" "}
      {scope === "slot" && (
        <select value={slot} onChange={(e) => setSlot(Number(e.target.value))} aria-label="Forget slot">
          {slots.slice(0, SLOT_COUNT).map((n) => <option key={n} value={n}>Slot {n}</option>)}
        </select>
      )}{" "}
      <input type="text" value={typed} placeholder={FORGET_WORD} aria-label="Type FORGET"
             onChange={(e) => setTyped(e.target.value)} />{" "}
      <button type="button" className="cam-danger" disabled={busy || !forgetReady(typed)}
              onClick={() => { onForget(scope, scope === "slot" ? slot : null); setTyped(""); }}>
        Forget face data
      </button>
      {removed !== null && <p className="stamp" role="status">Removed {removed} face record(s).</p>}
    </div>
  );
}

/**
 * The clips recorded for person alerts: a newest-first list and one player. The
 * video comes from this server behind the sign-in only; nothing is public.
 */
export function ClipsPanel({ clips }: { clips: CameraClips | null }): JSX.Element {
  const [picked, setPicked] = useState<string | null>(null);
  const list: CameraClip[] = clips?.clips ?? [];
  const shown = list.find((c) => c.url === picked) ?? list[0] ?? null;
  return (
    <section className="panel cam-clips">
      <h3 className="cam-h">Clips</h3>
      {clips === null ? <p className="stamp">Clips not read.</p>
        : list.length === 0
          ? <p className="stamp">No clips yet · read {formatClock(clips.served_at)}</p>
          : (
            <>
              {shown && (
                <video className="cam-clip-player" controls preload="metadata"
                  key={shown.url} src={shown.url} aria-label={clipLine(shown)} />
              )}
              <ul>
                {list.map((c) => (
                  <li key={c.url}>
                    <button type="button" className="cam-clip-pick"
                      aria-pressed={shown?.url === c.url}
                      onClick={() => setPicked(c.url)}>{clipLine(c)}</button>
                  </li>
                ))}
              </ul>
              <p className="stamp">read {formatClock(clips.served_at)}</p>
            </>
          )}
    </section>
  );
}

// ---- coverage and rule overlay ----------------------------------------------------------

/** Recorder detection against ATLAS detection for all twelve cameras. UNKNOWN is grey,
 *  dashed and says the word, with the reason; every side carries its read time. */
export function CoveragePanel({ coverage }: { coverage: Coverage | null }): JSX.Element {
  const rows = twelveCoverage(coverage);
  return (
    <section className="panel cam-coverage" aria-label="Detection coverage">
      <h3 className="cam-h">Detection coverage</h3>
      {coverage === null
        ? <p className="stamp">Coverage not read</p>
        : <p className="stamp">{coverage.rules_note}</p>}
      <ul>
        {rows.map((r) => {
          const rec = coverageSide(r.recorder);
          const atl = coverageSide(r.atlas);
          return (
            <li key={r.number} data-camera={r.number}>
              <b>{r.number}. {r.name}</b>
              <div className={`cam-side ${rec.health === "ok" ? "" : "cam-unknown hatch-edge"}`}
                   data-health={rec.health}>Recorder: {rec.text}</div>
              <div className={`cam-side ${atl.health === "ok" ? "" : "cam-unknown hatch-edge"}`}
                   data-health={atl.health}>ATLAS: {atl.text}</div>
              {r.busy_frames > 0 && <div className="stamp">{r.busy_frames} busy frames</div>}
              {r.suggestion && <div className="stamp">{r.suggestion}</div>}
              {r.rules.length > 0 && (
                <ul className="cam-rule-list">
                  {r.rules.map((x) => <li key={x.label}>{ruleLine(x)}</li>)}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** The zones and lines, drawn over a frame. Dashed grey while the rule cannot be judged. */
export function RuleShapes({ rules }: { rules: RuleInfo[] }): JSX.Element | null {
  const shapes = overlayShapes(rules);
  if (shapes.length === 0) return null;
  return (
    <svg className="cam-rule-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      {shapes.map((sh) => {
        const cls = `cam-rule ${sh.unknown ? "cam-rule-unknown" : ""}`;
        return sh.kind === "line"
          ? <polyline key={sh.label} className={cls} points={sh.points}><title>{sh.label}</title></polyline>
          : <polygon key={sh.label} className={cls} points={sh.points}><title>{sh.label}</title></polygon>;
      })}
    </svg>
  );
}

/** One line per rule, with the check note and its state. */
export function RuleList({ rules }: { rules: RuleInfo[] }): JSX.Element | null {
  if (rules.length === 0) return null;
  return (
    <ul className="cam-rule-list">
      {rules.map((r) => <li key={r.label}>{ruleLine(r)}</li>)}
    </ul>
  );
}

export function RuleOverlay({ rules }: { rules: RuleInfo[] }): JSX.Element | null {
  if (rules.length === 0) return null;
  return <><RuleShapes rules={rules} /><RuleList rules={rules} /></>;
}
