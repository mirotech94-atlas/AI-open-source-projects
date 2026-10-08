import type { JSX } from "react";
import { api, type AgendaEntry } from "../api";
import { useFresh } from "../hooks/useFresh";

function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString([], {
    weekday: "long", day: "numeric", month: "long",
  });
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function until(iso: string): string {
  const delta = new Date(iso).getTime() - Date.now();
  if (delta <= 0) return "passed";
  const mins = Math.floor(delta / 60000);
  if (mins < 60) return `in ${Math.max(1, mins)}m`;
  const hours = Math.floor(mins / 60);
  return hours < 24 ? `in ${hours}h ${mins % 60}m` : `in ${Math.floor(hours / 24)}d`;
}

export function Calendar({ reloadKey }: { reloadKey: number }): JSX.Element {
  const agenda = useFresh(() => api.agenda(), [reloadKey]);
  const items = agenda.data?.items ?? [];

  // Grouped by local day rather than by "today / later": the horizon is 72
  // hours, which is three days a person thinks about separately.
  const days = new Map<string, AgendaEntry[]>();
  for (const item of items) {
    const key = new Date(item.when).toDateString();
    const bucket = days.get(key) ?? [];
    bucket.push(item);
    days.set(key, bucket);
  }

  return (
    <>
      <h2 className="label">Calendar</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {agenda.fetchedAt ? `Read ${agenda.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {agenda.data && ` · next ${agenda.data.horizon_hours}h · ${items.length} item${items.length === 1 ? "" : "s"}`}
      </p>

      {/* A diary ATLAS could not read is not an empty diary, and this is the
          line that keeps the two apart. */}
      {agenda.data?.unavailable && (
        <p className="errorbar">Could not read the agenda: {agenda.data.unavailable}</p>
      )}
      {agenda.error && (
        <p className="errorbar">Could not reach the agenda: {agenda.error}</p>
      )}

      {agenda.data && !agenda.data.unavailable && items.length === 0 && (
        <p className="empty">Nothing scheduled inside {agenda.data.horizon_hours} hours.</p>
      )}

      {[...days.entries()].map(([key, entries]) => (
        <div className="panel" style={{ marginBottom: 14 }} key={key}>
          <div className="inner">
            <h3 className="label">
              {entries[0] ? dayLabel(entries[0].when) : key}
            </h3>
            <table className="sheet">
              <thead>
                <tr><th>Time</th><th>What</th><th>Kind</th><th>From</th><th>When</th></tr>
              </thead>
              <tbody>
                {entries.map((item) => (
                  <tr key={`${item.when}-${item.title}`}>
                    <td>{clock(item.when)}</td>
                    <td>
                      {item.title}
                      {item.detail && <span className="stamp"> · {item.detail}</span>}
                    </td>
                    <td>{item.kind}</td>
                    <td>{item.source || "—"}</td>
                    <td>{until(item.when)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
  );
}
