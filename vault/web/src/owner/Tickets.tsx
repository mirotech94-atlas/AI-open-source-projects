import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "../design";
import { ownerReply, ownerTicket, ownerTickets, setTicketStatus } from "../lib/api";
import type { TicketRow } from "../types";

/** The operator's reading of a category. Shorter than the customer's wording
 *  because this is a queue, and "Vault missed it" is the only one that
 *  needs to stop the eye. */
const OWNER_CATEGORY: Record<string, string> = {
  missed: "Vault missed it",
  finding: "A finding",
  device: "A device",
  network: "Network",
  account: "Account",
  other: "Other",
};

/** How a status reads, and how strongly. `open` is the only one that is
 *  asking for something, so it is the only one with a tone. */
const STATUS: Record<string, { label: string; tone: "warn" | "good" | "plain" }> = {
  open: { label: "Waiting on you", tone: "warn" },
  answered: { label: "Waiting on them", tone: "plain" },
  resolved: { label: "Resolved", tone: "good" },
};

function ago(iso: string): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "";
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function when(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/**
 * The operator's support queue.
 *
 * Two panes, because the two questions are different: the list answers "what
 * needs me", the thread answers "what is this about". The queue is ordered by
 * the hub with everything waiting on us first, so the top of the list is
 * always the work — recency alone would float a thread you just answered.
 *
 * Every reply posts to the customer's in-app thread AND emails them, which is
 * the arrangement the customer was promised. The thread is the record; the
 * letter is the tap on the shoulder, because guarder.io does not receive mail
 * into the app.
 */
export function Tickets() {
  const qc = useQueryClient();
  const queue = useQuery({ queryKey: ["owner-tickets"], queryFn: () => ownerTickets(), refetchInterval: 60_000 });
  // A ticket letter links to /owner/tickets/:id, so the operator lands on the
  // thread the letter was about rather than on the top of the queue.
  const { id: fromUrl } = useParams<{ id?: string }>();
  const [selected, setSelected] = useState<string | null>(fromUrl ?? null);
  const [draft, setDraft] = useState("");

  const rows = queue.data?.tickets ?? [];
  useEffect(() => {
    if (fromUrl) setSelected(fromUrl);
  }, [fromUrl]);
  // Otherwise land on the thread that needs answering, not an empty pane.
  useEffect(() => {
    if (!selected && rows.length) setSelected(rows[0].id);
  }, [rows, selected]);

  const thread = useQuery({
    queryKey: ["owner-ticket", selected],
    queryFn: () => ownerTicket(selected as string),
    enabled: !!selected,
  });

  const settle = (id: string) => {
    qc.invalidateQueries({ queryKey: ["owner-tickets"] });
    qc.invalidateQueries({ queryKey: ["owner-ticket", id] });
  };

  const reply = useMutation({
    mutationFn: (body: string) => ownerReply(selected as string, body),
    onSuccess: () => {
      setDraft("");
      if (selected) settle(selected);
    },
  });

  const status = useMutation({
    mutationFn: (next: "resolved" | "open") => setTicketStatus(selected as string, next),
    onSuccess: () => selected && settle(selected),
  });

  const t = thread.data?.ticket;
  const messages = thread.data?.messages ?? [];

  return (
    <section className="g-tix" data-testid="owner-tickets">
      <header className="g-tix-head">
        <p className="g-tix-sub">
          {queue.isError
            ? "The queue could not be read."
            : `${queue.data?.open_count ?? 0} waiting on you, ${rows.length} in total. Customers reach you at ${queue.data?.support_email || "the support mailbox"}.`}
        </p>
      </header>

      <div className="g-tix-panes">
        <nav className="g-tix-queue" aria-label="Tickets">
          {rows.length === 0 && !queue.isLoading && (
            <p className="g-tix-none">No tickets yet.</p>
          )}
          {rows.map((r: TicketRow) => {
            const s = STATUS[r.status] ?? STATUS.open;
            return (
              <button
                key={r.id}
                type="button"
                className={`g-tix-item${r.id === selected ? " is-active" : ""}`}
                aria-current={r.id === selected}
                onClick={() => { setSelected(r.id); setDraft(""); }}
              >
                <span className="g-tix-item-top">
                  <span className="g-tix-cust">{r.tenant_name}</span>
                  <span className="g-tix-age">{ago(r.last_message_at)}</span>
                </span>
                <span className="g-tix-subj">{r.subject}</span>
                {/* Item 46: what the customer said it was about. "Vault
                    missed it" is a bug report about the product, and it has
                    to be legible from the queue rather than one click in. */}
                <span className="g-tix-item-foot">
                  <Badge tone={r.category === "missed" ? "alert" : "plain"}>
                    {OWNER_CATEGORY[r.category] ?? OWNER_CATEGORY.other}
                  </Badge>
                  <Badge tone={s.tone}>{s.label}</Badge>
                  {r.priority === "high" && <span className="g-tix-pri">high</span>}
                  <span className="g-tix-count">{r.messages} message{r.messages === 1 ? "" : "s"}</span>
                </span>
              </button>
            );
          })}
        </nav>

        <div className="g-tix-thread">
          {!t && <p className="g-tix-none">{thread.isError ? "That ticket could not be read." : "Pick a ticket."}</p>}
          {t && (
            <>
              <div className="g-tix-thread-head">
                <div>
                  <h2>{t.subject}</h2>
                  <p className="g-tix-meta">
                    {t.tenant_name} · raised by {t.opened_by} · {when(t.created)}
                    {" · "}{OWNER_CATEGORY[t.category] ?? OWNER_CATEGORY.other}
                  </p>
                  {t.context_label && (
                    <p className="g-tix-meta" data-testid="owner-thread-about">
                      About {t.context_kind}: <strong>{t.context_label}</strong>
                    </p>
                  )}
                </div>
                {/* The status, and the one control that changes it. Resolving
                    is the operator's call: it is a claim that the problem is
                    dealt with, and a customer's reply reopens it. */}
                <aside className="g-tix-state">
                  <Badge tone={(STATUS[t.status] ?? STATUS.open).tone}>
                    {(STATUS[t.status] ?? STATUS.open).label}
                  </Badge>
                  {t.status === "resolved" ? (
                    <>
                      <p className="g-tix-state-note">
                        Closed by {t.resolved_by} on {when(t.resolved_at)}.
                      </p>
                      <button
                        type="button"
                        className="g-btn-ghost"
                        disabled={status.isPending}
                        onClick={() => status.mutate("open")}
                      >
                        Reopen
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="g-btn-primary"
                      disabled={status.isPending}
                      onClick={() => status.mutate("resolved")}
                    >
                      {status.isPending ? "Closing…" : "Mark resolved"}
                    </button>
                  )}
                </aside>
              </div>

              <ol className="g-tix-msgs">
                {messages.map((msg) => (
                  <li key={msg.id} className={`g-tix-msg is-${msg.side}`}>
                    <span className="g-tix-msg-head">
                      <strong>{msg.side === "owner" ? "You" : msg.author}</strong>
                      <span className="g-tix-msg-when">{when(msg.created)}</span>
                      {/* Delivery is worth showing here and nowhere else: a
                          reply that reached nobody (a customer with no address
                          on file) should not look delivered. */}
                      {msg.side === "owner" && !msg.emailed && (
                        <span className="g-tix-msg-nomail">in-app only</span>
                      )}
                    </span>
                    <p className="g-tix-msg-body">{msg.body}</p>
                  </li>
                ))}
              </ol>

              <form
                className="g-tix-reply"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (draft.trim()) reply.mutate(draft.trim());
                }}
              >
                <label htmlFor="g-tix-draft">Reply to {t.tenant_name}</label>
                <textarea
                  id="g-tix-draft"
                  rows={4}
                  value={draft}
                  placeholder="What you found, and what you did about it."
                  onChange={(e) => setDraft(e.target.value)}
                />
                <div className="g-tix-reply-foot">
                  <span className="g-tix-reply-hint">
                    Posts to their thread in the app and emails them.
                  </span>
                  <button type="submit" className="g-btn-primary" disabled={!draft.trim() || reply.isPending}>
                    {reply.isPending ? "Sending…" : "Send reply"}
                  </button>
                </div>
                {reply.isError && <p className="g-tix-err" role="status">That reply did not send. Try again.</p>}
              </form>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

export default Tickets;
