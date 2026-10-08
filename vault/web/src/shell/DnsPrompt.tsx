import { Link } from "react-router-dom";
import type { OnboardingState } from "../types";

/**
 * A quiet, standing reminder that DNS filtering is not switched on.
 *
 * WHY THIS EXISTS AT ALL
 *
 * DNS filtering is one of the main reasons to buy Vault, and it is the only
 * part of setup that needs something Vault cannot supply: somebody has to
 * point the router at the resolver, which needs the router password. Most
 * practices do not have that to hand on their first day, so the setup wizard
 * offers it as a last step that costs nothing to skip.
 *
 * That skip is the right call and it has a price: a headline feature can be
 * passed over once and then never thought about again. A feature nobody is
 * reminded about is a feature most customers never enable, and they will judge
 * the product on what it did for them rather than on what it offered.
 *
 * WHAT MAKES IT A REMINDER RATHER THAN A NAG
 *
 * It is a line, not a modal, and it has no dismiss control. A dismiss button
 * would turn "keeps asking" into "asked once", which is precisely the state
 * this exists to prevent. Being unobtrusive is what earns it the right to
 * stay.
 *
 * It retires itself. `dns: "working"` is the hub's live signal -- the resolver
 * seeing real queries from real clients, not somebody having pressed a button
 * -- so nobody has to remember to turn the prompt off, and it cannot linger
 * claiming a problem that has been fixed.
 *
 * And it stays silent while setup is unfinished, because the wizard is asking
 * for the same router password on the same screen. Two things asking for one
 * thing in the same minute is how a prompt gets learned as noise.
 */
export function DnsPrompt({ state }: { state: OnboardingState | null | undefined }) {
  if (!state) return null;

  // Mid-setup the wizard owns this conversation. `show` is the hub's own
  // answer to "should this person be in the wizard", so it is the right gate
  // rather than re-deriving one here.
  if (state.show) return null;

  const off = (state.networks ?? []).filter((n) => n.dns !== "working");
  if (off.length === 0) return null;

  // Named only when naming it helps. With one network the name adds nothing a
  // customer does not already know; with several, "which one" is the first
  // question they would ask.
  const which =
    off.length === 1 && (state.networks ?? []).length === 1
      ? ""
      : ` on ${off.map((n) => n.name || n.slug).join(", ")}`;

  return (
    <div className="g-dns-prompt" role="status">
      <span>
        DNS filtering is off{which}. It refuses known phishing and malware
        domains before any device can load them.
      </span>
      <Link to="/dns">Switch it on</Link>
    </div>
  );
}

export default DnsPrompt;
