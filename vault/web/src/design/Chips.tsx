import type { HTMLAttributes, ReactNode } from "react";

export type ChipItem = {
  value: string;
  label: ReactNode;
  /** Shown after the label, e.g. how many devices are in the group. */
  count?: number | null;
};

type ChipsBase = {
  items: ChipItem[];
  /** Names the group for assistive tech, e.g. "Filter by device kind". */
  label?: string;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children" | "onChange">;

// A discriminated union on `multiple`, so `onChange` is either a
// `string[]` callback or a `string | null` one — never both, and never `any`.
export type ChipsProps = ChipsBase &
  (
    | { multiple: true; value: string[]; onChange: (next: string[]) => void }
    | { multiple?: false; value: string | null; onChange: (next: string | null) => void }
  );

export function Chips(props: ChipsProps) {
  // The union members are destructured out so they never reach the DOM as
  // attributes; `props` itself stays the union, so `props.multiple` below still
  // narrows `value`/`onChange` to the matching pair.
  const {
    items,
    label,
    className = "",
    multiple: _multiple,
    value: _value,
    onChange: _onChange,
    ...rest
  } = props;

  function isOn(v: string): boolean {
    return props.multiple ? props.value.includes(v) : props.value === v;
  }

  function toggle(v: string) {
    if (props.multiple) {
      const on = props.value.includes(v);
      props.onChange(on ? props.value.filter((x) => x !== v) : [...props.value, v]);
      return;
    }
    // Single-select chips are a filter, so clicking the active chip clears it.
    props.onChange(props.value === v ? null : v);
  }

  return (
    <div
      role="group"
      aria-label={label}
      className={`g-chips ${className}`.trim()}
      {...rest}
    >
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className="g-chip"
          aria-pressed={isOn(item.value)}
          onClick={() => toggle(item.value)}
        >
          <span>{item.label}</span>
          {item.count !== undefined && item.count !== null && (
            <span className="g-chip-count">{item.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export default Chips;
