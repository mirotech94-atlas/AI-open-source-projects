import { useMemo } from "react";
import type { HTMLAttributes, ReactNode } from "react";

export type Column<T> = {
  /** Matches a field on the row. Also the sort key and the React key. */
  key: string;
  header: ReactNode;
  align?: "left" | "right" | "center";
  width?: string | number;
  /** Custom cell. Without it the raw `row[key]` is rendered as text. */
  render?: (row: T) => ReactNode;
};

export type SortState = {
  key: string;
  dir: "asc" | "desc";
};

// `rows` is the single inference site for `T`, which means an empty-state
// render — `<Table rows={[]} rowKey={(r) => r.id} …>` — infers `T = never`.
// `Row<T>` swaps that one case back to a loose record (non-distributively, so
// it does not collapse to `never` again) while a real `rows` array keeps full
// field-level typing on `render`, `rowKey` and `onRowClick`.
export type Row<T> = [T] extends [never] ? Record<string, any> : T;

export type TableProps<T> = {
  columns: Column<Row<T>>[];
  rows: T[];
  /** Controlled sort. `null`/undefined leaves the rows in payload order. */
  sort?: SortState | null;
  onSort?: (sort: SortState) => void;
  empty?: ReactNode;
  dense?: boolean;
  rowKey: (row: Row<T>) => string;
  onRowClick?: (row: Row<T>) => void;
  /** `rowKey` of the highlighted row, e.g. the one open in a drawer. */
  selected?: string | null;
  /** Extra class(es) for one row's `<tr>`, e.g. to flag it stuck or failed
   *  without relying on a cell's own colour to carry that alone. Merged
   *  alongside the click/selected classes already applied. */
  rowClassName?: (row: Row<T>) => string | undefined;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

function cellValue(row: unknown, key: string): unknown {
  if (row === null || typeof row !== "object") return undefined;
  return (row as Record<string, unknown>)[key];
}

function isMissing(v: unknown): boolean {
  return v === null || v === undefined || v === "";
}

function compare(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return Number(a) - Number(b);
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

// Sorting is purely local to the rows handed in: it never re-fetches and never
// reorders anything the hub aggregated. Missing values sort last in *both*
// directions — the direction multiplier is applied to the value comparison
// only, so a device with no risk score never tops a descending sort.
function comparator(key: string, dir: 1 | -1) {
  return (rowA: unknown, rowB: unknown): number => {
    const a = cellValue(rowA, key);
    const b = cellValue(rowB, key);
    const aMissing = isMissing(a);
    const bMissing = isMissing(b);
    if (aMissing && bMissing) return 0;
    if (aMissing) return 1;
    if (bMissing) return -1;
    return dir * compare(a, b);
  };
}

export function Table<T>({
  columns,
  rows,
  sort,
  onSort,
  empty = "Nothing to show.",
  dense = false,
  rowKey,
  onRowClick,
  selected = null,
  rowClassName,
  className = "",
  ...rest
}: TableProps<T>) {
  const sorted = useMemo(() => {
    if (!sort) return rows;
    // Copy first: mutating the payload array would fight React Query's cache.
    return [...rows].sort(comparator(sort.key, sort.dir === "desc" ? -1 : 1));
  }, [rows, sort]);

  function headerCell(col: Column<Row<T>>) {
    const align = col.align ?? "left";
    const active = sort?.key === col.key;
    const ariaSort = active ? (sort?.dir === "desc" ? "descending" : "ascending") : "none";
    const style = col.width === undefined ? undefined : { width: col.width };
    return (
      <th
        key={col.key}
        scope="col"
        style={style}
        aria-sort={onSort ? ariaSort : undefined}
        className={align === "left" ? undefined : `g-align-${align}`}
      >
        {onSort ? (
          <button
            type="button"
            className="g-th-sort"
            onClick={() =>
              onSort({ key: col.key, dir: active && sort?.dir === "asc" ? "desc" : "asc" })
            }
          >
            {col.header}
            {active && (
              <span className="g-sort-arrow" aria-hidden="true">
                {sort?.dir === "desc" ? "↓" : "↑"}
              </span>
            )}
          </button>
        ) : (
          col.header
        )}
      </th>
    );
  }

  return (
    <div className={`g-table-wrap ${className}`.trim()} {...rest}>
      <table className={dense ? "g-table g-dense" : "g-table"}>
        <thead>
          <tr>{columns.map(headerCell)}</tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td className="g-table-empty" colSpan={Math.max(1, columns.length)}>
                {empty}
              </td>
            </tr>
          ) : (
            sorted.map((row) => {
              // `T` and `Row<T>` are the same type for every non-empty `rows`;
              // the cast only bridges the `never` case, which has no rows.
              const r = row as Row<T>;
              const key = rowKey(r);
              const classes = [
                onRowClick ? "g-row-click" : "",
                selected !== null && selected === key ? "g-row-selected" : "",
                rowClassName?.(r) ?? "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <tr
                  key={key}
                  className={classes || undefined}
                  // A clickable row is reachable and activatable by keyboard;
                  // an inert row stays out of the tab order.
                  tabIndex={onRowClick ? 0 : undefined}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onRowClick(r);
                          }
                        }
                      : undefined
                  }
                >
                  {columns.map((col) => {
                    const align = col.align ?? "left";
                    const content = col.render
                      ? col.render(r)
                      : formatCell(cellValue(row, col.key));
                    return (
                      <td
                        key={col.key}
                        className={align === "left" ? undefined : `g-align-${align}`}
                      >
                        {content}
                      </td>
                    );
                  })}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(v: unknown): ReactNode {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number" || typeof v === "string") return v;
  return JSON.stringify(v);
}

export default Table;
