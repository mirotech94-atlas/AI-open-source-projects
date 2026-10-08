import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Page, PageHeader } from "../design";
import { ExportMenu } from "../components/ExportMenu";
import { myTicket, myTickets, openTicket, replyToMyTicket } from "../lib/api";
import { useLiveFeed, type LiveStatus } from "../lib/live";
import type { TicketRow } from "../types";
import "../styles/support.css";

/** The customer's reading of a status. Deliberately different words from the
 *  owner console's: "waiting on you" means the opposite thing depending on
 *  which side of the conversation you are on, and getting that backwards is
 *  worse than saying less. */
//
// Each label names WHO MOVES NEXT, which is the only thing a person scanning
// a ticket list needs from a status. "With Vault" and "Answered" described
// the ticket's history; "Waiting on Vault" and "Waiting on you" describe
// what happens now.
/** The socket's state in words. "Connecting" and "Reconnecting" are kept
 *  apart because the first is the ordinary half-second after the page opens,
 *  and calling it a reconnect reads as "something broke" to a customer who
 *  came here because something already had. */
const LIVE_LABEL: Record<LiveStatus, string> = {
  open: "Live", connecting: "Connecting…", closed: "Reconnecting…",
};

const STATUS: Record<string, { label: string; tone: "warn" | "good" | "plain" }> = {
  open: { label: "Waiting on Vault", tone: "plain" },
  answered: { label: "Waiting on you", tone: "warn" },
  resolved: { label: "Resolved", tone: "good" },
};

/** When each ticket was last read here: `{ticketId: last_message_at}`.
 *  A per-browser convenience, which is all an unread dot is -- the thread is
 *  the record, and losing this (private window, cleared storage) only means
 *  every ticket starts read. */
const SEEN_KEY = "guardian.support.seen";
function readSeen(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}") as Record<string, string>; } catch { return {}; }
}
function writeSeen(next: Record<string, string>) {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(next)); } catch { /* storage off: dots just clear on reload */ }
}

/** Vault spoke last, after this browser last read the ticket. */
function isUnread(r: TicketRow, seen: Record<string, string>): boolean {
  const vaultLast = r.status === "answered" || r.last_author === "Vault";
  return vaultLast && !!r.last_message_at && (!seen[r.id] || r.last_message_at > seen[r.id]);
}

/** Short, stable reference a person can read out on the phone. The full id
 *  is a 32-hex uid; the last six characters are unique enough for a tenant's
 *  own tickets and short enough to say. */
function ref(id: string): string {
  return `#${id.slice(-6).toUpperCase()}`;
}

const PRIORITY: Record<string, { label: string; tone: "alert" | "warn" | "plain" }> = {
  high: { label: "High", tone: "alert" },
  normal: { label: "Normal", tone: "plain" },
  low: { label: "Low", tone: "plain" },
};

/** What the ticket is about, in Vault's own words.
 *
 *  A generic desk offers "Billing / Technical / Other", which tells the
 *  person answering nothing. These say what went wrong with a security
 *  product, and `missed` is first because it is the reason a customer of one
 *  writes in at all — and the only category that is also a bug report about
 *  Vault itself. */
const CATEGORY: Record<string, { label: string; ask: string }> = {
  missed: { label: "Vault missed it", ask: "Something Vault did not catch" },
  finding: { label: "A finding", ask: "Something Vault flagged, and I need it explained" },
  device: { label: "A device", ask: "A device is wrong, missing or misnamed" },
  network: { label: "The network", ask: "Scanning, DNS filtering or the sensor itself" },
  account: { label: "Account", ask: "Logins, people, or this account" },
  other: { label: "Something else", ask: "Something else" },
};

/** Where a context chip links to. A ticket about a device should put that
 *  device one click away; a kind Vault cannot route to gets a plain chip
 *  rather than a link that goes nowhere. */
function contextHref(kind: string, id: string): string | null {
  if (kind === "device") return `/assets/devices/${encodeURIComponent(id)}`;
  if (kind === "alert") return `/inbox/${encodeURIComponent(id)}`;
  if (kind === "site") return `/security/sites/${encodeURIComponent(id)}`;
  if (kind === "network") return `/topology`;
  return null;
}

/** How long this has been waiting, in the one form a desk is judged on.
 *  Resolved tickets are not ageing, so they get nothing. */
function waiting(r: TicketRow): string {
  if (r.status === "resolved") return "";
  const since = new Date(r.last_message_at || r.created).getTime();
  if (Number.isNaN(since)) return "";
  const hours = Math.floor((Date.now() - since) / 3_600_000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

type Filter = "all" | "open" | "answered" | "resolved";

function when(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/**
 * Get help: raise a ticket, and follow the answer here.
 *
 * The thread in this page is the record of the conversation. Vault also
 * emails every reply, so nothing has to be watched for — but the customer can
 * always come back and read what was said, which an email thread alone does
 * not reliably give them.
 */
export function Support() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["my-tickets"], queryFn: myTickets, refetchInterval: 60_000 });
  // The hub publishes a `ticket` frame on every new ticket, reply and status
  // change. The client used to drop it as an unknown type, so a reply from
  // Vault only appeared on the next 60-second poll. Now the list and the open
  // thread refresh the moment it lands.
  const live = useLiveFeed();
  const [seen, setSeen] = useState<Record<string, string>>(() => readSeen());
  // Where "New reply" goes in the open thread: the last-read time as it was
  // when the thread opened, before opening it marked everything read.
  const [readBefore, setReadBefore] = useState<string>("");
  // Vault's reply email links to /support/:id, so following it opens that
  // thread instead of whatever happens to be newest.
  const { id: fromUrl } = useParams<{ id?: string }>();
  const [selected, setSelected] = useState<string | null>(fromUrl ?? null);
  const [composing, setComposing] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [priority, setPriority] = useState("normal");
  const [category, setCategory] = useState("missed");
  // Raised from somewhere else in the app: "Ask about this" on a device or a
  // finding lands here with the thing already attached, so the customer does
  // not have to describe what Vault already knows.
  const [params] = useSearchParams();
  const about = useMemo(() => {
    const kind = params.get("about_kind") ?? "";
    const id = params.get("about_id") ?? "";
    const label = params.get("about_label") ?? "";
    return kind && id && label ? { kind, id, label } : null;
  }, [params]);
  const [draft, setDraft] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");

  const rows = list.data?.tickets ?? [];
  const counts = useMemo(() => ({
    all: rows.length,
    open: rows.filter((r) => r.status === "open").length,
    answered: rows.filter((r) => r.status === "answered").length,
    resolved: rows.filter((r) => r.status === "resolved").length,
  }), [rows]);
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows
      .filter((r) => filter === "all" || r.status === filter)
      .filter((r) => !q || r.subject.toLowerCase().includes(q) || ref(r.id).toLowerCase().includes(q));
  }, [rows, filter, search]);
  const replySla = list.data?.support_reply_sla ?? "";
  const directEmail = list.data?.support_direct_email ?? "";
  const supportEmail = list.data?.support_email ?? "";
  useEffect(() => {
    if (fromUrl) setSelected(fromUrl);
  }, [fromUrl]);
  useEffect(() => {
    if (!about) return;
    setComposing(true);
    setSelected(null);
    setSubject((v) => v || about.label);
    setCategory(params.get("about_category") || "missed");
  }, [about, params]);
  useEffect(() => {
    if (!selected && rows.length && !composing) setSelected(rows[0].id);
  }, [rows, selected, composing]);

  const thread = useQuery({
    queryKey: ["my-ticket", selected],
    queryFn: () => myTicket(selected as string),
    enabled: !!selected && !composing,
  });

  useEffect(() => live.on("ticket", (event) => {
    void qc.invalidateQueries({ queryKey: ["my-tickets"] });
    const id = (event.data as { id?: string }).id;
    if (id) void qc.invalidateQueries({ queryKey: ["my-ticket", id] });
  }), [live, qc]);

  // Opening a thread reads it. Remember where the reader had got to first,
  // so the thread can mark what arrived since.
  useEffect(() => { setReadBefore(selected ? seen[selected] ?? "" : ""); }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps
  const openedLast = thread.data?.ticket.last_message_at ?? "";
  useEffect(() => {
    if (!selected || !openedLast || seen[selected] === openedLast) return;
    const next = { ...seen, [selected]: openedLast };
    setSeen(next);
    writeSeen(next);
  }, [selected, openedLast]); // eslint-disable-line react-hooks/exhaustive-deps

  const raise = useMutation({
    mutationFn: () => openTicket({
      subject: subject.trim(), body: body.trim(), priority, category,
      context_kind: about?.kind ?? "", context_id: about?.id ?? "",
      context_label: about?.label ?? "",
    }),
    onSuccess: (detail) => {
      setComposing(false);
      setSubject(""); setBody(""); setPriority("normal"); setCategory("missed");
      setSelected(detail.ticket.id);
      qc.invalidateQueries({ queryKey: ["my-tickets"] });
    },
  });

  const reply = useMutation({
    mutationFn: (text: string) => replyToMyTicket(selected as string, text),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["my-tickets"] });
      qc.invalidateQueries({ queryKey: ["my-ticket", selected] });
    },
  });

  const t = thread.data?.ticket;

  return (
    <Page>
      <PageHeader
        eyebrow="Account · a person, when you need one"
        title="Support"
        sub={
          list.data && !list.data.mail_works
            ? "Ask about anything Vault is or is not doing. Answers arrive here — email delivery is not switched on for this hub yet, so check back on this page."
            : "Ask about anything Vault is or is not doing. Answers arrive here and by email."
        }
        actions={[{ label: "New ticket", primary: true, onClick: () => { setComposing(true); setSelected(null); } }]}
        extra={
          <>
            <span className="g-sup-live" data-state={live.status} data-testid="support-live">
              {LIVE_LABEL[live.status]}
            </span>
            <ExportMenu path="/api/v1/export/tickets" label="Export tickets" query={{ days: 365 }} disabled={rows.length === 0} />
          </>
        }
      />
    <section className="g-sup" data-testid="support">

      {/* The numbers a ticket desk is read by. "Waiting on you" is the one
          that matters: Vault has answered and the next move is the
          customer's, which an inbox full of "answered" badges does not say. */}
      <ul className="g-sup-stats" aria-label="Ticket counts">
        <li data-zero={counts.open === 0 ? "1" : undefined}><span className="g-sup-stat-n">{counts.open}</span><span className="g-sup-stat-l">Waiting on Vault</span><span className="g-sup-stat-note">Vault owes a reply</span></li>
        <li data-warn={counts.answered > 0 ? "1" : undefined} data-zero={counts.answered === 0 ? "1" : undefined}><span className="g-sup-stat-n">{counts.answered}</span><span className="g-sup-stat-l">Waiting on you</span><span className="g-sup-stat-note">Vault has replied</span></li>
        <li data-zero={counts.resolved === 0 ? "1" : undefined}><span className="g-sup-stat-n">{counts.resolved}</span><span className="g-sup-stat-l">Resolved</span><span className="g-sup-stat-note">closed by Vault</span></li>
        <li>
          <span className="g-sup-stat-n g-sup-stat-text">{replySla || "—"}</span>
          <span className="g-sup-stat-l">Typical reply</span>
          <span className="g-sup-stat-note">{list.data?.support_email ? `also emailed to ${list.data.support_email}` : "replies arrive here and by email"}</span>
        </li>
      </ul>

      <div className="g-sup-panes">
        <nav className="g-sup-list" aria-label="Your tickets">
          <div className="g-sup-tools">
            <div className="g-sup-filters" role="group" aria-label="Filter tickets">
              {(["all", "open", "answered", "resolved"] as Filter[]).map((f) => (
                <button key={f} type="button" className="g-sup-filter" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {f === "all" ? "All" : f === "open" ? "Waiting on Vault" : f === "answered" ? "Waiting on you" : "Resolved"} · {counts[f]}
                </button>
              ))}
            </div>
            <input
              className="g-sup-search"
              type="search"
              placeholder="Search subject or #ref"
              aria-label="Search tickets"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {rows.length === 0 && !list.isLoading && !composing && (
            <p className="g-sup-none">Nothing yet. Open a ticket when something needs looking at.</p>
          )}
          {rows.length > 0 && shown.length === 0 && (
            <p className="g-sup-none">No tickets match.</p>
          )}
          {shown.map((r: TicketRow) => {
            const s = STATUS[r.status] ?? STATUS.open;
            const pr = PRIORITY[r.priority] ?? PRIORITY.normal;
            return (
              <button
                key={r.id}
                type="button"
                className={`g-sup-item${r.id === selected ? " is-active" : ""}`}
                aria-current={r.id === selected}
                data-status={r.status}
                data-unread={isUnread(r, seen) && r.id !== selected ? "1" : undefined}
                onClick={() => { setComposing(false); setSelected(r.id); setDraft(""); }}
              >
                <span className="g-sup-item-top">
                  {isUnread(r, seen) && r.id !== selected && (
                    <span className="g-sup-unread" data-testid="ticket-unread" aria-label="New reply" />
                  )}
                  <span className="g-sup-ref mono">{ref(r.id)}</span>
                  {r.priority === "high" && <Badge tone={pr.tone}>{pr.label}</Badge>}
                  <Badge tone={s.tone}>{s.label}</Badge>
                </span>
                <span className="g-sup-subj">{r.subject}</span>
                <span className="g-sup-cat">{(CATEGORY[r.category] ?? CATEGORY.other).label}</span>
                {r.context_label && <span className="g-sup-about-chip">{r.context_label}</span>}
                <span className="g-sup-item-foot">
                  {waiting(r) && <span className="g-sup-age" data-old={waiting(r).endsWith("d") ? "1" : undefined}>waiting {waiting(r)}</span>}
                  <span className="g-sup-last">
                    {r.last_author ? `${r.last_author === "Vault" || r.status === "answered" ? "Vault" : r.last_author} · ` : ""}
                    {when(r.last_message_at)}
                  </span>
                  <span className="g-sup-count">{r.messages} {r.messages === 1 ? "message" : "messages"}</span>
                </span>
              </button>
            );
          })}
        </nav>

        <div className="g-sup-main">
          {composing && (
            <form
              className="g-sup-new"
              onSubmit={(e) => { e.preventDefault(); if (subject.trim() && body.trim()) raise.mutate(); }}
            >
              {/* v2's framing: the box is for what Vault did NOT catch,
                  which is the only reason to write in at all. */}
              <p className="g-sup-lede">Something wrong that Vault has not caught?</p>
              {about && (
                <p className="g-sup-about" data-testid="ticket-about">
                  <span className="g-sup-about-l">About</span>
                  <span className="g-sup-about-v">{about.label}</span>
                </p>
              )}
              {/* Asked before the subject line, because the answer changes
                  how urgently the operator reads everything below it. */}
              <label htmlFor="g-sup-category">What kind of problem is it?</label>
              <select id="g-sup-category" value={category} onChange={(e) => setCategory(e.target.value)}>
                {Object.entries(CATEGORY).map(([key, c]) => (
                  <option key={key} value={key}>{c.ask}</option>
                ))}
              </select>
              <label htmlFor="g-sup-subject">What is it about?</label>
              <input
                id="g-sup-subject"
                value={subject}
                maxLength={160}
                placeholder="The sensor keeps dropping off"
                onChange={(e) => setSubject(e.target.value)}
              />
              <label htmlFor="g-sup-body">What is happening?</label>
              <textarea
                id="g-sup-body"
                rows={6}
                value={body}
                placeholder="The Wi-Fi in the back office keeps dropping out in the afternoons…"
                onChange={(e) => setBody(e.target.value)}
              />
              <label htmlFor="g-sup-priority">How urgent is it?</label>
              <select id="g-sup-priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
                <option value="low">Low — whenever you get to it</option>
                <option value="normal">Normal</option>
                <option value="high">High — something is not working</option>
              </select>
              <div className="g-sup-new-foot">
                <button type="button" className="g-btn-ghost" onClick={() => setComposing(false)}>Cancel</button>
                <button type="submit" className="g-btn-primary" disabled={!subject.trim() || !body.trim() || raise.isPending}>
                  {raise.isPending ? "Sending…" : "Send it"}
                </button>
              </div>
              {/* Rendered only when an operator has actually set one. An
                  unmet reply time is worse than promising nothing. */}
              {replySla && <p className="g-sup-sla">Typical reply: {replySla}</p>}
              {raise.isError && <p className="g-sup-err" role="status">That did not send. Try again.</p>}
            </form>
          )}

          {!composing && !t && (
            <p className="g-sup-none">{thread.isError ? "That ticket could not be read." : "Pick a ticket, or open a new one."}</p>
          )}

          {!composing && t && (
            <>
              <div className="g-sup-thread-head">
                <div>
                  <p className="g-sup-ref-line">
                    <span className="g-sup-ref mono">{ref(t.id)}</span>
                    <Badge tone={(PRIORITY[t.priority] ?? PRIORITY.normal).tone}>
                      {(PRIORITY[t.priority] ?? PRIORITY.normal).label} priority
                    </Badge>
                  </p>
                  <h2>{t.subject}</h2>
                  <p className="g-sup-meta">
                    {(CATEGORY[t.category] ?? CATEGORY.other).label} · opened {when(t.created)} by {t.opened_by}
                    {" · "}{t.messages} {t.messages === 1 ? "message" : "messages"}
                  </p>
                  {t.context_label && (
                    <p className="g-sup-about" data-testid="thread-about">
                      <span className="g-sup-about-l">About</span>
                      {contextHref(t.context_kind, t.context_id)
                        ? <Link className="g-sup-about-v" to={contextHref(t.context_kind, t.context_id) as string}>{t.context_label}</Link>
                        : <span className="g-sup-about-v">{t.context_label}</span>}
                    </p>
                  )}
                </div>
                <Badge tone={(STATUS[t.status] ?? STATUS.open).tone}>
                  {(STATUS[t.status] ?? STATUS.open).label}
                </Badge>
              </div>

              {/* Where the ticket is in its life, as three fixed stops. A
                  reply after Resolved reopens it, and the hub says so. */}
              <ol className="g-sup-steps" aria-label="Ticket progress">
                <li data-done="1"><span>Opened</span><em>{when(t.created)}</em></li>
                <li data-done={t.status !== "open" ? "1" : undefined}><span>Vault replied</span><em>{t.status !== "open" ? when(t.last_message_at) : "not yet"}</em></li>
                <li data-done={t.status === "resolved" ? "1" : undefined}><span>Resolved</span><em>{t.status === "resolved" ? when(t.resolved_at) : "—"}</em></li>
              </ol>

              {t.status === "resolved" && (
                <p className="g-sup-resolved" role="status">
                  Vault marked this resolved on {when(t.resolved_at)}. If it is not actually
                  fixed, reply below and the ticket reopens.
                </p>
              )}

              <ol className="g-sup-msgs">
                {(thread.data?.messages ?? []).map((msg, i, all) => (
                  <li key={msg.id} className={`g-sup-msg is-${msg.side}`}
                      data-new-from={readBefore && msg.side === "owner" && msg.created > readBefore
                        && !(i > 0 && all[i - 1].side === "owner" && all[i - 1].created > readBefore) ? "1" : undefined}>
                    <span className="g-sup-msg-head">
                      <strong>{msg.side === "owner" ? "Vault" : msg.author}</strong>
                      <span className="g-sup-msg-when">{when(msg.created)}</span>
                    </span>
                    <p className="g-sup-msg-body">{msg.body}</p>
                  </li>
                ))}
              </ol>

              <form
                className="g-sup-reply"
                onSubmit={(e) => { e.preventDefault(); if (draft.trim()) reply.mutate(draft.trim()); }}
              >
                <label htmlFor="g-sup-draft">Reply</label>
                <textarea
                  id="g-sup-draft"
                  rows={3}
                  value={draft}
                  placeholder={t.status === "resolved" ? "Reply to reopen this ticket" : "Write a reply"}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && draft.trim() && !reply.isPending) {
                      e.preventDefault();
                      reply.mutate(draft.trim());
                    }
                  }}
                />
                <button type="submit" className="g-btn-primary" disabled={!draft.trim() || reply.isPending}>
                  {reply.isPending ? "Sending…" : "Send"}
                </button>
                {reply.isError && <p className="g-sup-err" role="status">That did not send. Try again.</p>}
              </form>
            </>
          )}
        </div>
      </div>

      {/* Two addresses, two jobs, and no phone line by design: a number that
          rings out during an outage is worse than none. Rendered only when
          the operator has set the direct address. */}
      {directEmail && (
        <aside className="g-sup-urgent">
          <h3>If it is urgent</h3>
          <a className="g-sup-tel" href={`mailto:${directEmail}`}>{directEmail}</a>
          <p>
            Reaches a person directly, for anything affecting a whole site.
            {supportEmail && <> For everything else, <a href={`mailto:${supportEmail}`}>{supportEmail}</a> — tickets opened here land there too.</>}
          </p>
        </aside>
      )}
    </section>
    </Page>
  );
}

export default Support;
