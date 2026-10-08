import type { ChatProposal } from "../../types";

export type ProposalCardProps = {
  proposal: ChatProposal;
  onConfirm: () => void;
  onDismiss: () => void;
};

function isExpired(expires: string): boolean {
  const t = Date.parse(expires);
  return !Number.isNaN(t) && t <= Date.now();
}

/** A tier-3 action Vault is asking permission for. `expires` is a hub
 *  ISO timestamp — once it has passed the card visually fades and both
 *  buttons disable, since the hub's own `confirm_token` has expired by then
 *  too and a click would just come back as a fresh proposal. */
export function ProposalCard({ proposal, onConfirm, onDismiss }: ProposalCardProps) {
  const expired = isExpired(proposal.expires);

  return (
    <div className={`g-chat-proposal${expired ? " g-chat-proposal-expired" : ""}`} data-testid="proposal">
      <p className="g-chat-proposal-summary">{proposal.summary}</p>
      <ul className="g-chat-proposal-actions">
        {proposal.actions.map((action, i) => (
          <li key={i}>
            <span className="g-chat-proposal-host">{action.host}</span>
            <span className="g-chat-proposal-effect">{action.effect}</span>
            <span className="g-chat-proposal-reversible">
              {action.reversible ? "reversible" : "not reversible"}
            </span>
          </li>
        ))}
      </ul>
      {expired && <p className="g-chat-proposal-expired-note">This offer has expired — ask again to retry.</p>}
      <div className="g-chat-proposal-buttons">
        <button type="button" disabled={expired} onClick={onConfirm}>
          Yes, do it
        </button>
        <button type="button" disabled={expired} className="g-chat-proposal-no" onClick={onDismiss}>
          No
        </button>
      </div>
    </div>
  );
}

export default ProposalCard;
