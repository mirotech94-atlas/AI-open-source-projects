import { useEffect, useId, useRef, useState } from "react";
import { reportPreview } from "../lib/api";
import type { ReportPreset, ReportSection } from "../types";

export type BuildRequest = {
  days: number;
  preset: string;
  sections: string[];
  title: string;
};

export type ReportBuilderProps = {
  presets: ReportPreset[];
  sections: ReportSection[];
  onBuild: (req: BuildRequest) => void;
  /** True while the parent's build mutation is in flight — disables the
   *  button and shows a progress line rather than a second click. */
  building?: boolean;
  canBuild?: boolean;
  defaultPreset?: string;
  buildTooltip?: string;
};

// The reference console's own four periods: last 24 hours, 7, 30, 90 days.
const PERIOD_PRESETS = [1, 7, 30, 90] as const;
const PREVIEW_DEBOUNCE_MS = 400;
const DEFAULT_PRESET = "full";

/** Left: preset dropdown, section checkboxes (pre-ticked from the preset,
 *  edited into a custom selection from there), a period selector, and a
 *  title field. Right: a live preview of the server-rendered report,
 *  refreshed 400ms after the last change to preset/sections/period.
 *
 *  The preview fetches `reportPreview()` itself rather than taking the HTML
 *  as a prop — it is the one thing in this component with no bearing on
 *  the eventual `onBuild` call, so there is no reason to route it through
 *  the parent. The iframe uses `srcdoc` (not `src` pointed at the hub
 *  route) so the fetch goes through the same typed helper the rest of the
 *  app uses, and stays same-origin without a second network round trip the
 *  browser would make on its own. */
export function ReportBuilder({
  presets,
  sections,
  onBuild,
  building = false,
  defaultPreset = DEFAULT_PRESET,
  canBuild = true,
  buildTooltip = "operator role required",
}: ReportBuilderProps) {
  const presetId = useId();
  const periodId = useId();
  const customDaysId = useId();
  const titleId = useId();

  const initialPreset = presets.find((p) => p.key === defaultPreset) ?? presets[0];

  const [presetKey, setPresetKey] = useState(initialPreset?.key ?? "");
  const [selectedSections, setSelectedSections] = useState<string[]>(initialPreset?.sections ?? []);
  const [period, setPeriod] = useState<string>("7");
  const [customDays, setCustomDays] = useState(7);
  const [title, setTitle] = useState("");
  const [html, setHtml] = useState("");
  const [loadingPreview, setLoadingPreview] = useState(false);

  const days = period === "custom" ? customDays : Number(period);

  // `Reports.tsx` mounts this component immediately against an empty
  // placeholder payload (`presets: []`) while the real `["reports"]` query
  // is still loading, so the `useState` initializers above see no presets
  // and seed nothing. Once the query resolves and `presets` goes from empty
  // to populated, re-seed the default preset and its sections exactly
  // once — guarded by `seededRef` so a later prop change (e.g. the cache
  // update after a build) never clobbers a selection the user already made.
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current || presets.length === 0) return;
    seededRef.current = true;
    const preset = presets.find((p) => p.key === defaultPreset) ?? presets[0];
    setPresetKey(preset?.key ?? "");
    setSelectedSections(preset?.sections ?? []);
  }, [presets, defaultPreset]);

  function applyPreset(key: string) {
    setPresetKey(key);
    const preset = presets.find((p) => p.key === key);
    setSelectedSections(preset?.sections ?? []);
  }

  function toggleSection(key: string) {
    setSelectedSections((prev) => (prev.includes(key) ? prev.filter((s) => s !== key) : [...prev, key]));
  }

  // Debounced live preview: any change to preset/sections/period re-fetches
  // the rendered HTML 400ms after the last change, cancelling a still-
  // pending fetch rather than piling requests up. Clearing the timer only
  // stops a fetch that hasn't started yet — once two fetches are in flight
  // (e.g. a second edit lands while the first request is still pending),
  // whichever response arrives *later* must win regardless of which was
  // sent first. `previewGenRef` tags every fetch with an increasing
  // generation number, so a response only gets applied if it is still the
  // most recent one requested.
  const previewGenRef = useRef(0);
  const [fullScreen, setFullScreen] = useState(false);

  // Escape leaves full screen: it is the key every full-screen thing on a
  // computer answers to, and a preview that traps you is worse than a small
  // one.
  useEffect(() => {
    if (!fullScreen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullScreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullScreen]);
  useEffect(() => {
    setLoadingPreview(true);
    const timer = window.setTimeout(() => {
      const generation = ++previewGenRef.current;
      reportPreview(days, presetKey, selectedSections)
        .then((markup) => {
          if (previewGenRef.current === generation) setHtml(markup);
        })
        .catch(() => {
          if (previewGenRef.current === generation) setHtml("<p>Preview unavailable.</p>");
        })
        .finally(() => {
          if (previewGenRef.current === generation) setLoadingPreview(false);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetKey, selectedSections.join(","), days]);

  return (
    <div className="g-reports-builder">
      <div className="g-reports-builder-form">
        <label className="g-reports-field">
          <span>Preset</span>
          <select
            id={presetId}
            aria-label="Preset"
            value={presetKey}
            onChange={(e) => applyPreset(e.target.value)}
          >
            {presets.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        {/* `exec_report` is not built from a `sections` subset (see
            `reports/presets.py`'s `SPECIAL_PRESETS`) -- it is a fixed
            one-page document, so the checkbox list has nothing to offer
            and would just look like broken controls. */}
        {presetKey !== "exec_report" && (
          <fieldset className="g-reports-sections">
            <legend>Sections</legend>
            {sections.map((s) => (
              <label key={s.key} className="g-reports-section-row">
                <input
                  type="checkbox"
                  aria-label={s.title}
                  checked={selectedSections.includes(s.key)}
                  onChange={() => toggleSection(s.key)}
                />
                <span>{s.title}</span>
              </label>
            ))}
          </fieldset>
        )}

        <label className="g-reports-field">
          <span>Period</span>
          <select id={periodId} aria-label="Period" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIOD_PRESETS.map((d) => (
              <option key={d} value={d}>
                {d === 1 ? "last 24 hours" : `last ${d} days`}
              </option>
            ))}
            <option value="custom">Custom</option>
          </select>
        </label>

        {period === "custom" && (
          <label className="g-reports-field">
            <span>Custom days</span>
            <input
              id={customDaysId}
              aria-label="Custom days"
              type="number"
              min={1}
              max={365}
              value={customDays}
              onChange={(e) => setCustomDays(Number(e.target.value) || 1)}
            />
          </label>
        )}

        <label className="g-reports-field">
          <span>Title</span>
          <input
            id={titleId}
            aria-label="Title"
            type="text"
            placeholder="e.g. Week 37"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>

        <button
          type="button"
          disabled={!canBuild || building}
          title={canBuild ? undefined : buildTooltip}
          onClick={() => onBuild({ days, preset: presetKey, sections: selectedSections, title })}
        >
          {building ? "Building…" : "Build report"}
        </button>
      </div>

      <div className={`g-reports-preview${fullScreen ? " is-full" : ""}`}>
        <div className="g-reports-preview-head">
          <span>Live preview</span>
          {loadingPreview && <span className="hint">Refreshing…</span>}
          {/* Item 40: a preview you have to squint at is not a preview. Full
              screen is the only way to read a page-sized document on a
              page-sized screen, and Escape gets you back. */}
          <button type="button" className="g-reports-preview-full"
                  aria-pressed={fullScreen}
                  onClick={() => setFullScreen((v) => !v)}>
            {fullScreen ? "Exit full screen" : "Full screen"}
          </button>
        </div>
        <iframe
          title="Report preview"
          className="g-reports-preview-frame"
          sandbox="allow-same-origin"
          srcDoc={html}
        />
      </div>
    </div>
  );
}

export default ReportBuilder;
