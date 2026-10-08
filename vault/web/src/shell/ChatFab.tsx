import type { RefObject } from "react";
import ChatMark from "./ChatMark";

export type ChatFabProps = {
  open: boolean;
  onToggle: () => void;
  /** Number of open CRIT alerts. The reference console's floating button
   *  carries exactly this number in its red corner pill (`#fabb`, fed from
   *  `renderNav()`'s `al.filter(a => a.sev === 'CRIT').length`) — it is an
   *  attention indicator for the network, not an unread-message count — and
   *  it is hidden entirely at zero rather than shown as a `0`. */
  critCount: number;
  /** The id of the popover this button controls, for `aria-controls`. */
  controls: string;
  buttonRef: RefObject<HTMLButtonElement | null>;
};

/** The round floating chat button, bottom-right, that opens the Vault chat
 *  popover above itself. It is the only chat entry point in the header-free
 *  reference layout; the ⌘K palette's "Ask Vault" row is the second one
 *  (the reference's `ask()`). */
export function ChatFab({ open, onToggle, critCount, controls, buttonRef }: ChatFabProps) {
  const label =
    critCount > 0
      ? `Toggle Vault chat — ${critCount} critical alert${critCount === 1 ? "" : "s"}`
      : "Toggle Vault chat";
  return (
    <button
      ref={buttonRef}
      type="button"
      className="g-chat-fab"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={controls}
      aria-label={label}
      title="Ask Vault"
    >
      <span aria-hidden="true" className="g-chat-fab-mark">
        <ChatMark size={30} inverted />
      </span>
      {critCount > 0 && (
        <span className="g-chat-fab-badge num" aria-hidden="true">
          {critCount}
        </span>
      )}
    </button>
  );
}

export default ChatFab;
