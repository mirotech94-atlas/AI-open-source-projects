import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { ChatEntity, NextAction } from "../../types";
import type { ChatEntry } from "./useChat";
import { ProposalCard } from "./ProposalCard";
import { ToolSteps } from "./ToolSteps";

/** Splits a strict-format bullet ("Name · IP · fact") into its three parts.
 *  Anything that does not match the shape (a rules-fallback bullet, say)
 *  comes back as `{ raw }` so the caller can fall back to plain text. */
function splitBulletText(text: string): { name: string; ip: string; fact: string } | { raw: string } {
  const parts = text.split(" · ");
  if (parts.length !== 3) return { raw: text };
  const [name, ip, fact] = parts;
  return { name, ip, fact };
}

/** Turns `*emphasis*` into `<em>` and leaves every other character — HTML
 *  tags included — as literal text. This is the entire "markdown" support a
 *  model reply gets: React only ever receives text nodes here, so nothing a
 *  model writes can inject markup. */
function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*([^*]+)\*/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = re.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    out.push(<em key={key++}>{match[1]}</em>);
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Builds the link for a bullet's name from the hub's own `entity` field —
 *  never guessed from the IP shape (a device id is a MAC, not an IP, so
 *  IP-based guessing pointed at the wrong route). Both `kind` and `id` null
 *  means the bullet's subject did not resolve to anything Vault knows,
 *  so the name renders as plain text. */
function entityLink(name: string, entity: ChatEntity): ReactNode {
  if (!name) return name;
  if (!entity.id) return name;
  switch (entity.kind) {
    case "device":
      return <Link to={`/assets/devices/${encodeURIComponent(entity.id)}`}>{name}</Link>;
    case "host":
      return <Link to={`/assets/hosts/${encodeURIComponent(entity.id)}`}>{name}</Link>;
    case "site":
      return <Link to={`/security/sites/${encodeURIComponent(entity.id)}`}>{name}</Link>;
    default:
      return name;
  }
}

/** The part of an unstructured reply that is not already on screen.
 *
 *  `parse_answer` (hub-side) splits every answer into verdict / bullets /
 *  `Next:` but still returns the raw `text` in full. When there are no
 *  bullets this component falls back to rendering that raw text — which,
 *  for a rules-fallback answer, repeated the verdict line and the `Next:`
 *  line that are already rendered as their own rows ("Everything is up.
 *  Everything is up. Next: … Next: …"). Drop both here, and render what is
 *  left — nothing at all, for a two-line answer.
 *
 *  The `Next:` match must stay identical to the hub's own `_NEXT_RE`
 *  (`^next:\s*(.+)$`, `chat/prompt.py`) -- no space allowed before the
 *  colon. A looser `/^next\s*:/i` here used to drop a line the hub never
 *  recognized as `Next:` in the first place (e.g. a literal "Next : x"
 *  in the raw text), so it vanished from the reply instead of rendering
 *  once; a stricter match would let a real `Next:` line the hub already
 *  rendered separately show up here too, duplicated. */
function unstructuredBody(text: string, verdict: string): string {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line, i) => !(i === 0 && line === verdict) && !/^next:/i.test(line))
    .join(" ");
}

/** The reference console's per-reply provenance line (`.m .prov`): which
 *  tools Vault ran for this answer and which rung of the ladder wrote it.
 *  "investigated with a, b · via X" when tools ran, plain "via X" when none
 *  did. A locally-sourced notice has no provider and so no line at all. */
function provenance(entry: ChatEntry): string {
  if (!entry.provider) return "";
  const tools = entry.toolSteps.map((s) => s.tool).filter(Boolean);
  const via = `via ${entry.provider}`;
  return tools.length > 0 ? `investigated with ${tools.join(", ")} · ${via}` : via;
}

export type MessageProps = {
  entry: ChatEntry;
  onConfirmProposal: (entry: ChatEntry) => void;
  onDismissProposal: (entry: ChatEntry) => void;
  /** Dispatches a `Next:` offer exactly as `VerdictHero`'s primary button
   *  does (`runAction(next[0])`) — omitted (no button rendered) when the
   *  offer has no `path` to POST, e.g. the rules fallback's
   *  "Next: nothing needed." */
  onRunNext?: (action: NextAction) => void;
  nextBusy?: boolean;
};

/** Renders one transcript entry. A `user` entry is a plain bubble; a
 *  `guardian` entry renders the strict answer format (verdict heading, up to
 *  six `Name · IP · fact` bullets, a distinct `Next:` offer row), then its
 *  tool steps and any tier-3 proposal. */
export function Message({ entry, onConfirmProposal, onDismissProposal, onRunNext, nextBusy }: MessageProps) {
  if (entry.role === "user") {
    return (
      <div className="g-chat-msg g-chat-msg-user">
        <p>{entry.text}</p>
      </div>
    );
  }

  // Bullets are the only reliable signal that the reply is in strict
  // format — the `Next:` offer can show up on an unstructured reply too
  // (e.g. the rules fallback's "Next: nothing needed."), so it renders
  // independently of whether the bullet list did. When there are no
  // bullets, the raw `text` is shown as-is (still through the same
  // emphasis-only inline renderer, never as HTML) rather than dropped —
  // that is how a refusal or a plain rules-fallback answer is rendered.
  const structured = entry.bullets.length > 0;
  const body = structured ? "" : unstructuredBody(entry.text, entry.verdict);
  const prov = provenance(entry);

  return (
    <div className={`g-chat-msg g-chat-msg-guardian${entry.isNotice ? " g-chat-msg-notice" : ""}`}>
      {entry.verdict && <h3 className="g-chat-verdict">{entry.verdict}</h3>}
      {structured ? (
        <ul className="g-chat-bullets">
          {entry.bullets.slice(0, 6).map((bullet, i) => {
            const parts = splitBulletText(bullet.text);
            return (
              <li key={i} data-testid="chat-bullet">
                {"raw" in parts ? renderInline(parts.raw) : (
                  <>
                    {entityLink(parts.name, bullet.entity)} · {parts.ip} · {renderInline(parts.fact)}
                  </>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        body && <p className="g-chat-text">{renderInline(body)}</p>
      )}
      {entry.next.length > 0 && (
        <p className="g-chat-next" data-testid="chat-next">
          <strong>Next:</strong> {renderInline(entry.next[0].label)}
          {entry.next[0].path && onRunNext && (
            <button
              type="button"
              className="g-chat-next-btn"
              disabled={nextBusy}
              onClick={() => onRunNext(entry.next[0])}
            >
              Do it
            </button>
          )}
        </p>
      )}
      {entry.toolSteps.length > 0 && <ToolSteps steps={entry.toolSteps} />}
      {entry.proposal && (
        <ProposalCard
          proposal={entry.proposal}
          onConfirm={() => onConfirmProposal(entry)}
          onDismiss={() => onDismissProposal(entry)}
        />
      )}
      {/* Last child of the bubble, as in the reference (`addMsg` appends
          `.prov` after everything else) — so it never splits a tier-3
          proposal card from the tool steps that produced it. */}
      {prov && (
        <span className="g-chat-prov" data-testid="chat-prov">
          {prov}
        </span>
      )}
    </div>
  );
}

export default Message;
