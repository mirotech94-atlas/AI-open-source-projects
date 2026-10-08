import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { alertHandoff, chat } from "../lib/api";
import type { ChatProposal } from "../types";

/**
 * The conversation about one incident, inside the incident.
 *
 * An incident that says "needs you" has to say what it needs, in the place
 * the owner is looking. This opens the same hand-off the customer's inbox
 * uses (`POST /chat/handoff`): Vault's own explanation of what it can and
 * cannot do about this alert, on a thread that keeps the context, and a
 * composer for the follow-up. A tier-3 proposal comes back as a real button;
 * nothing runs until it is pressed, and that press is the single-use token
 * going back -- the same rule the plan strip's Approve follows.
 */
type Msg = { role: "guardian" | "you"; text: string; bullets?: string[] };

export function IncidentChat({ alertKey }: { alertKey: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [proposal, setProposal] = useState<ChatProposal | null>(null);
  const thread = useRef<string | null>(null);
  const opened = useRef(false);

  const open = useMutation({
    mutationFn: () => alertHandoff(alertKey, thread.current),
    onSuccess: (h) => {
      thread.current = h.thread_id || thread.current;
      setMsgs([{ role: "guardian", text: h.opening }]);
      setProposal(h.proposal);
    },
    onError: (err: Error) => setMsgs([{ role: "guardian", text: `Could not open the conversation: ${err.message}` }]),
  });

  const send = useMutation({
    mutationFn: ({ message, token }: { message: string; token: string | null }) =>
      chat({ message, thread: thread.current, tab: "incidents", confirm_token: token }),
    onMutate: ({ message, token }) => {
      setMsgs((m) => [...m, { role: "you", text: token ? `Yes — ${message}` : message }]);
      setDraft("");
    },
    onSuccess: (r) => {
      thread.current = r.thread_id || thread.current;
      setMsgs((m) => [...m, { role: "guardian", text: r.text, bullets: r.bullets.map((b) => b.text) }]);
      setProposal(r.proposal);
    },
    onError: (err: Error) => setMsgs((m) => [...m, { role: "guardian", text: `That did not go through: ${err.message}` }]),
  });

  useEffect(() => {
    if (!opened.current) { opened.current = true; open.mutate(); }
    // The hand-off is fetched once per open; `open` is stable for the mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="g-own-chat" data-testid="incident-chat">
      <ol className="g-own-chat-log">
        {open.isPending && msgs.length === 0 && <li className="g-own-chat-msg is-guardian"><span className="g-own-chat-who">Vault</span><p>Reading the incident…</p></li>}
        {msgs.map((m, i) => (
          <li key={i} className={`g-own-chat-msg is-${m.role}`}>
            <span className="g-own-chat-who">{m.role === "guardian" ? "Vault" : "You"}</span>
            <p>{m.text}</p>
            {m.bullets && m.bullets.length > 0 && (
              <ul className="g-own-chat-bullets">{m.bullets.map((b, j) => <li key={j}>{b}</li>)}</ul>
            )}
          </li>
        ))}
      </ol>
      {/* The proposal is the one thing here that changes the network, so it
          is a button with the effect on it, never a line of text to agree to. */}
      {proposal && (
        <div className="g-own-plan g-own-chat-proposal">
          <span className="g-own-plan-l">Proposed</span>
          <span className="g-own-plan-cmd g-own-plan-prose" title={proposal.summary}>{proposal.summary}</span>
          <span className="g-own-plan-side">
            <button type="button" className="g-own-plan-go" disabled={send.isPending}
                    onClick={() => send.mutate({ message: proposal.summary, token: proposal.token })}>Confirm and run</button>
          </span>
        </div>
      )}
      <form className="g-own-chat-form" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) send.mutate({ message: draft.trim(), token: null }); }}>
        <input className="g-own-chat-in" value={draft} onChange={(e) => setDraft(e.target.value)}
               placeholder="Ask what this means, or tell Vault what to do" aria-label="Message to Vault" />
        <button type="submit" className="g-own-btn g-own-btn-primary" disabled={!draft.trim() || send.isPending}>{send.isPending ? "…" : "Send"}</button>
      </form>
    </div>
  );
}

export default IncidentChat;
