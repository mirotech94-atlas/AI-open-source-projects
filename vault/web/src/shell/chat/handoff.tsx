import { createContext, useContext } from "react";
import type { AlertHandoff } from "../../types";

/** Opens the chat panel with Vault already explaining one alert — see
 *  `Shell`, which provides the real implementation, and `Inbox`, which calls
 *  it when an alert's remedy needs the owner. */
export type OpenHandoff = (handoff: AlertHandoff) => void;

/** Opens the chat panel and asks Vault one question, as if the reader had
 *  typed it. Used by the Help section's "Ask Vault about this" — an article
 *  explains how the product works, and this asks Vault what is true of
 *  *this* site right now.
 *
 *  Deliberately NOT the alert hand-off's route: `POST /chat/handoff` resolves
 *  an alert key and 404s without one, so a help article has nothing to hand
 *  off. This goes through the ordinary `POST /chat` turn instead, which is
 *  what makes the answer live rather than canned. */
export type AskGuardian = (question: string) => void;

export type ChatHandoff = {
  openAlert: OpenHandoff;
  askGuardian: AskGuardian;
};

/** No-op defaults rather than thrown errors: a page rendered outside the shell
 *  (a unit test, a future embed) must still work, it just has no chat panel to
 *  open. Only `Shell` ever provides real values. */
const noop: ChatHandoff = {
  openAlert: () => {},
  askGuardian: () => {},
};

export const ChatHandoffContext = createContext<ChatHandoff>(noop);

/** The alert hand-off, unchanged for its existing caller (`Inbox`). */
export function useChatHandoff(): OpenHandoff {
  return useContext(ChatHandoffContext).openAlert;
}

/** Ask Vault a question in the chat panel. */
export function useAskGuardian(): AskGuardian {
  return useContext(ChatHandoffContext).askGuardian;
}
