import type { JSX } from "react";
import { api } from "../api";
import { useFresh } from "../hooks/useFresh";

function stamp(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString([], { hour12: false }) : "—";
}

const SOURCE_WORD: Record<string, string> = {
  voice: "Voice",
  messaging: "Messaging",
  messages: "Direct",
};

export function Conversations({ reloadKey }: { reloadKey: number }): JSX.Element {
  const convos = useFresh(() => api.conversations(80), [reloadKey]);
  const rows = convos.data?.conversations ?? [];

  return (
    <>
      <h2 className="label">Conversations</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {convos.fetchedAt ? `Read ${convos.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {convos.data && ` · ${rows.length} shown · ${convos.data.unanswered} unanswered`}
      </p>

      {convos.error && (
        <p className="errorbar">Could not read conversations: {convos.error}</p>
      )}

      {/* Everything in this table arrived from outside. It is rendered as
          text, never as markup, and it is never read back as an instruction —
          the same rule that governs it before it reaches a prompt. */}
      <div className="panel">
        <div className="inner">
          {convos.data && rows.length === 0 ? (
            <p className="empty">Nothing said yet.</p>
          ) : (
            <table className="sheet">
              <thead>
                <tr>
                  <th>When</th><th>Where</th><th>Who</th>
                  <th>Said</th><th>Answer</th><th>Replied</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={`${c.source}-${c.id}`}>
                    <td>{stamp(c.at)}</td>
                    <td>
                      {SOURCE_WORD[c.source] ?? c.source}
                      {c.platform !== "voice" && <span className="stamp"> · {c.platform}</span>}
                    </td>
                    <td>{c.who}</td>
                    <td>
                      {c.label && <span className="stamp">{c.label} · </span>}
                      {c.text}
                    </td>
                    <td>{c.answer ?? (c.draft ? `draft: ${c.draft}` : "—")}</td>
                    <td>
                      {c.source === "voice"
                        ? "—"
                        : c.replied_at
                          ? stamp(c.replied_at)
                          : "waiting"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
