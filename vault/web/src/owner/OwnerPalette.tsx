import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { PlatformTenantRow } from "../types";

/**
 * ⌘K for the owner console.
 *
 * The design reference asks for "a command palette over assets, incidents and
 * hostnames". This console's estate is customers rather than devices, so it
 * indexes what it actually has: every customer by name and slug, and every
 * section of the console itself.
 *
 * WHY NOT THE TENANT APP'S PALETTE
 *
 * `shell/CommandPalette` exists and looks exactly like this. It is not reused,
 * because almost nothing in it survives the move: it searches `/api/v1/search`,
 * which is tenant-scoped by construction; it indexes `visibleTabs`, which is
 * the customer's nav; and it offers Run scan, Speedtest and the Help articles,
 * none of which the owner console has. Parameterising all of that would leave a
 * component whose every input is a prop -- a worse thing to read than two
 * small, honest components.
 *
 * What IS shared is the appearance and the keyboard contract: the same
 * `g-palette-*` classes, the same combobox/listbox roles, the same Escape and
 * arrow handling. The two palettes should feel like one control, because to
 * the owner moving between the two surfaces they are one.
 *
 * It searches only what is already in memory. The customer list arrives with
 * the shell's platform payload, so there is no request, no debounce and no
 * empty-then-filled flicker -- a palette that hesitates is one people stop
 * opening.
 */

type Item = {
  id: string;
  label: string;
  detail: string;
  path: string;
};

export type OwnerPaletteProps = {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  /** Every customer, from the payload the shell already holds. */
  rows: PlatformTenantRow[];
  /** The console's own sections, so "take me to billing" has an answer. */
  sections: { to: string; label: string; hint: string }[];
};

/** Always mounted so the shortcut works from anywhere; renders nothing until
 *  open. The listener is bound in the capture phase on `document` because the
 *  shortcut has to fire while a field has focus -- including this console's
 *  own customer picker. */
export function OwnerPalette({ open, onOpen, onClose, rows, sections }: OwnerPaletteProps) {
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
  // A fresh mount per open, so query and selection start clean without a
  // reset effect keyed on `open`.
  return <Dialog onClose={onClose} rows={rows} sections={sections} />;
}

function Dialog({ onClose, rows, sections }: Omit<OwnerPaletteProps, "open" | "onOpen">) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Restore focus to whatever opened this. Declared before the focus-steal
  // below, because mount effects run in order and this must capture the old
  // focus before the input takes it.
  useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    return () => previousFocusRef.current?.focus?.();
  }, []);
  useEffect(() => { inputRef.current?.focus(); }, []);

  const items = useMemo<Item[]>(() => {
    const q = query.trim().toLowerCase();
    const customers: Item[] = rows.map((r) => ({
      id: `customer:${r.id}`,
      label: r.name,
      // The slug is how the owner refers to a customer in a shell or a log, so
      // it is both searchable and shown.
      detail: r.slug,
      path: `/owner/accounts/${r.id}`,
    }));
    const navs: Item[] = sections.map((s) => ({
      id: `nav:${s.to}`, label: s.label, detail: s.hint, path: s.to,
    }));
    // Customers first: the palette exists because somebody is on the phone.
    const all = [...customers, ...navs];
    if (q === "") return all;
    return all.filter((i) =>
      i.label.toLowerCase().includes(q) || i.detail.toLowerCase().includes(q));
  }, [query, rows, sections]);

  const activeSafe = Math.min(activeIndex, Math.max(0, items.length - 1));

  function run(item: Item) {
    onClose();
    navigate(item.path);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (items.length === 0 ? 0 : (i + 1) % items.length));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (items.length === 0 ? 0 : (i - 1 + items.length) % items.length));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const hit = items[activeSafe];
      if (hit) run(hit);
    }
  }

  const activeId = items[activeSafe]?.id;

  return (
    <div
      className="g-palette-backdrop"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="g-palette" role="dialog" aria-modal="true" aria-label="Find a customer or a screen">
        <input
          ref={inputRef}
          role="combobox"
          aria-label="Find a customer or a screen"
          aria-expanded="true"
          aria-controls="g-owner-palette-listbox"
          aria-activedescendant={activeId}
          autoComplete="off"
          className="g-palette-input"
          placeholder="Find a customer or a screen…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setActiveIndex(0); }}
          onKeyDown={onKeyDown}
        />
        <ul id="g-owner-palette-listbox" role="listbox" className="g-palette-list">
          {items.map((item, i) => (
            <li
              key={item.id}
              id={item.id}
              role="option"
              aria-selected={i === activeSafe}
              className={`g-palette-item${i === activeSafe ? " g-palette-item-active" : ""}`}
              onMouseEnter={() => setActiveIndex(i)}
              onMouseDown={(e) => { e.preventDefault(); run(item); }}
            >
              <span>{item.label}</span>
              <span className="hint">{item.detail}</span>
            </li>
          ))}
          {items.length === 0 && <li className="g-palette-empty hint">No customer or screen by that name.</li>}
        </ul>
      </div>
    </div>
  );
}

export default OwnerPalette;
