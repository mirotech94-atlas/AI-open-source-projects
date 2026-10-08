import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { searchHelp } from "../help/articles";
import { visibleTabs } from "./nav";
import { search } from "../lib/api";
import type { SearchHit, SearchResults } from "../lib/api";

const DEBOUNCE_MS = 150;

type ActionItem = {
  kind: "action";
  id: string;
  label: string;
  run: () => void;
};

// One variant per `SearchHit.kind` the hub can send, each carrying the raw
// hit so `runItem` navigates to its own `href` verbatim rather than
// reconstructing a path from `kind` + `id`.
type ResultItem = { kind: SearchHit["kind"]; id: string; label: string; detail: string; item: SearchHit };

/** A navigation entry — one per nav row the current mode shows, parents and
 *  their dropdown children alike. The reference indexed its tabs first of all
 *  (`TABS.forEach(... I.push({k:'tab' ...}))`) and Vault's palette indexed
 *  actions, help and hub entities but never the nav itself, so "take me to
 *  CVEs" had no answer. Children carry their parent as the detail line, which
 *  is what disambiguates "Setup" (DNS filter) from "Sensors" (Config). */
type NavItem = { kind: "nav"; id: string; label: string; detail: string; path: string };

/** A Help article. Matched entirely in the browser — the articles are a data
 *  module in this repo (`help/articles.ts`), not tenant data the hub could
 *  search — so a help match costs no request and appears the instant a
 *  character is typed. */
type HelpItem = { kind: "help"; id: string; label: string; detail: string; path: string };

type PaletteItem = ActionItem | NavItem | ResultItem | HelpItem;

/** The hub's `/api/v1/search` already returns one flat, ranked list — this
 *  just reshapes each `SearchHit` into the palette's row shape (label +
 *  detail text), it never re-groups or re-sorts. */
function resultsToItems(results: SearchResults | null): ResultItem[] {
  if (!results) return [];
  return results.results.map((hit): ResultItem => ({
    kind: hit.kind,
    id: `${hit.kind}:${hit.id}`,
    label: hit.title,
    detail: hit.subtitle,
    item: hit,
  }));
}

export type CommandPaletteProps = {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  /** Simple mode indexes only the nav rows it actually shows — offering a route
   *  the sidebar has hidden would be a back door into it. */
  advanced: boolean;
  onCycleTheme: () => void;
  onOpenChat: () => void;
  onRunScan: () => void;
  onSpeedtest: () => void;
};

/**
 * The ⌘K / Ctrl-K global palette host. Always mounted (so the shortcut works
 * from anywhere) but renders nothing until `open`. Binds the shortcut on
 * `document` in the capture phase itself — not `useHotkeys` — because that
 * hook deliberately ignores keys typed into a focused input/textarea, and a
 * "global" shortcut cannot honour that guard.
 */
export function CommandPalette({ open, onOpen, onClose, ...dialogProps }: CommandPaletteProps) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpen();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onOpen]);

  if (!open) return null;
  // A fresh mount per open — not a reset effect keyed on `open` — is what
  // gives the query/results/selection their clean starting state below.
  return <PaletteDialog onClose={onClose} {...dialogProps} />;
}

type PaletteDialogProps = Omit<CommandPaletteProps, "open" | "onOpen">;

function PaletteDialog({ onClose, advanced, onCycleTheme, onOpenChat, onRunScan, onSpeedtest }: PaletteDialogProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const requestSeq = useRef(0);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Mount-only: captures whatever had focus before the dialog opened — the
  // top bar's search button for a click, or whatever was focused elsewhere
  // in the app for the ⌘K shortcut — and restores it on unmount (the dialog
  // unmounts wholesale on close; see `CommandPalette`'s `if (!open) return
  // null`). This has to run before the focus-steal effect below moves focus
  // onto the input, which it does: mount effects run in declaration order.
  useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    return () => {
      previousFocusRef.current?.focus?.();
    };
  }, []);

  // Mount-only: focuses the input once the dialog has painted.
  useEffect(() => {
    const handle = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(handle);
  }, []);

  // The debounced search itself is the external system this effect
  // synchronizes with (a timer, then a network call) — an empty query is
  // handled at render time below rather than by resetting state here.
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) return;
    const seq = ++requestSeq.current;
    const handle = setTimeout(() => {
      search(trimmed)
        .then((r) => {
          if (requestSeq.current === seq) setResults(r);
        })
        .catch(() => {
          if (requestSeq.current === seq) setResults(null);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  const actions: ActionItem[] = useMemo(
    () => [
      { kind: "action", id: "action:scan", label: "Run scan", run: onRunScan },
      { kind: "action", id: "action:speedtest", label: "Speed test", run: onSpeedtest },
      { kind: "action", id: "action:report", label: "Build report", run: () => navigate("/reports") },
      { kind: "action", id: "action:theme", label: "Toggle theme", run: onCycleTheme },
      { kind: "action", id: "action:chat", label: "Open chat", run: onOpenChat },
      { kind: "action", id: "action:help", label: "Open help", run: () => navigate("/help") },
    ],
    [navigate, onRunScan, onSpeedtest, onCycleTheme, onOpenChat],
  );

  const navItems: NavItem[] = useMemo(() => {
    const out: NavItem[] = [];
    for (const tab of visibleTabs(advanced)) {
      out.push({ kind: "nav", id: `nav:${tab.key}`, label: tab.label, detail: "Go to", path: tab.path });
      for (const child of tab.children ?? []) {
        out.push({
          kind: "nav",
          id: `nav:${child.key}`,
          label: child.label,
          detail: `Go to · ${tab.label}`,
          path: child.path,
        });
      }
    }
    return out;
  }, [advanced]);

  const trimmedQuery = query.trim().toLowerCase();
  // Help articles are matched word-by-word against title, summary, keywords
  // AND body text (see `helpMatches`), which is what makes "enrol code" find
  // "Where to find your enrolment code" — a title substring match does not.
  const helpItems: HelpItem[] = useMemo(
    () =>
      trimmedQuery
        ? searchHelp(trimmedQuery).map((a) => ({
            kind: "help" as const,
            id: `help:${a.slug}`,
            label: a.title,
            detail: "Help guide",
            path: `/help/${a.slug}`,
          }))
        : [],
    [trimmedQuery],
  );
  // A typed query filters the fixed actions by label too, same as the
  // search results — "kitchen" must produce *only* the matching device, not
  // "Run scan" sitting above it at index 0 by virtue of being listed first.
  const matchingActions = trimmedQuery ? actions.filter((a) => a.label.toLowerCase().includes(trimmedQuery)) : actions;
  // Nav rows appear only once something is typed. Unfiltered they are 40-odd
  // entries, which would bury the six actions the empty palette exists to show.
  const matchingNav = useMemo(
    () =>
      trimmedQuery ? navItems.filter((n) => `${n.label} ${n.detail}`.toLowerCase().includes(trimmedQuery)) : [],
    [navItems, trimmedQuery],
  );
  const effectiveResults = trimmedQuery ? results : null;
  const resultItems = useMemo(() => resultsToItems(effectiveResults), [effectiveResults]);
  // Help before the hub's hits: someone typing a phrase from a guide wants the
  // guide, and the hub's own results are entity matches which read fine below
  // it.
  const items: PaletteItem[] = useMemo(
    () => [...matchingActions, ...matchingNav, ...helpItems, ...resultItems],
    [matchingActions, matchingNav, helpItems, resultItems],
  );
  // Clamped at render rather than reset via an effect keyed on `items.length`
  // — a shrinking result list (or the initial action-only list) always has a
  // valid selection without a second render pass.
  const activeSafe = items.length === 0 ? 0 : Math.min(activeIndex, items.length - 1);

  function runItem(item: PaletteItem) {
    if (item.kind === "action") {
      item.run();
    } else if (item.kind === "nav") {
      navigate(item.path);
    } else if (item.kind === "help") {
      navigate(item.path);
    } else {
      // The hub already computed the right in-app route for this hit
      // (`payloads/search.py`'s `href`) — navigate there verbatim rather
      // than reconstructing a path from `kind` + `id`, which drifts the
      // moment a route changes shape on one side only.
      navigate(item.item.href);
    }
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "Tab") {
      // The search input is the dialog's only focusable control (the list
      // items below run off mouse/keyboard-arrow handlers, not tab order),
      // so trapping focus is exactly "Tab never leaves it" — otherwise it
      // would escape to whatever the browser's default tab order puts next,
      // behind the backdrop.
      e.preventDefault();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex(Math.min(items.length - 1, activeSafe + 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex(Math.max(0, activeSafe - 1));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const item = items[activeSafe];
      if (item) runItem(item);
    }
  }

  const activeId = items[activeSafe]?.id;

  return (
    <div
      className="g-palette-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="g-palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <input
          ref={inputRef}
          role="combobox"
          aria-label="Search Vault"
          aria-expanded="true"
          aria-controls="g-palette-listbox"
          aria-activedescendant={activeId}
          autoComplete="off"
          className="g-palette-input"
          placeholder="Search or run a command…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <ul id="g-palette-listbox" role="listbox" className="g-palette-list">
          {items.map((item, i) => (
            <li
              key={item.id}
              id={item.id}
              role="option"
              aria-selected={i === activeSafe}
              className={`g-palette-item${i === activeSafe ? " g-palette-item-active" : ""}`}
              onMouseEnter={() => setActiveIndex(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                runItem(item);
              }}
            >
              <span>{item.label}</span>
              {item.kind !== "action" && <span className="hint">{item.detail}</span>}
            </li>
          ))}
          {items.length === 0 && <li className="g-palette-empty hint">No matches.</li>}
        </ul>
      </div>
    </div>
  );
}

export default CommandPalette;
