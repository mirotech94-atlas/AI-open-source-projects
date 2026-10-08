import { useRef } from "react";
import type { HTMLAttributes, ReactNode } from "react";

export type TabItem = {
  value: string;
  label: ReactNode;
  /** Optional badge count, e.g. unread inbox rows. */
  count?: number | null;
};

export type TabsProps = {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  /** Names the tablist for assistive tech when the surrounding heading is not
   *  adjacent (a drawer's sub-navigation, for instance). */
  label?: string;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children" | "onChange">;

export function Tabs({
  items,
  value,
  onChange,
  label,
  className = "",
  ...rest
}: TabsProps) {
  const listRef = useRef<HTMLDivElement | null>(null);

  // Left/Right (and Home/End) move between tabs, which is what a tablist is
  // expected to do; Tab itself moves out of the group.
  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(e.key)) return;
    const i = items.findIndex((t) => t.value === value);
    if (i < 0 || items.length === 0) return;
    e.preventDefault();
    let next = i;
    if (e.key === "ArrowLeft") next = (i - 1 + items.length) % items.length;
    if (e.key === "ArrowRight") next = (i + 1) % items.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = items.length - 1;
    onChange(items[next].value);
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>("button.g-tab");
    buttons?.[next]?.focus();
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      className={`g-tabs ${className}`.trim()}
      onKeyDown={onKeyDown}
      {...rest}
    >
      {items.map((t) => {
        const selected = t.value === value;
        return (
          <button
            key={t.value}
            type="button"
            role="tab"
            className="g-tab"
            aria-selected={selected}
            // Roving tabindex: only the selected tab is a tab stop.
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.value)}
          >
            {t.label}
            {t.count !== undefined && t.count !== null && (
              <span className="g-tab-count">{t.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default Tabs;
