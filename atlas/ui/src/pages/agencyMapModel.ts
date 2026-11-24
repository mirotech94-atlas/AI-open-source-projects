/**
 * The Agency tab's pure model: the map alone (the owner, 2026-10-28), and the
 * one count it carries -- the previews waiting on his card, on the `sites`
 * neuron (the website-clients agent owns the preview -> card -> email flow).
 */
import type { Approvals } from "../api";
import type { NodeBadge } from "../components/NeuralMap";
import { pendingDecisions } from "./alertsModel";
import { hhmm, readOf, type Read } from "./homeModel";

/** The roster agent that owns client previews and their approval cards. */
export const PREVIEW_AGENT = "sites";

/** Cards that put a client preview in front of its owner: the email that
 *  links it (`agency.send_outreach`) and a demo's publish
 *  (`agency.deploy_demo`). Each waits for the owner in #agency-decisions. */
export const PREVIEW_ACTIONS: ReadonlySet<string> = new Set([
  "agency.send_outreach", "agency.deploy_demo",
]);

/** The `sites` node's badge: how many preview cards wait on the owner, read now.
 *  A read that failed is Unknown (grey, dashed, the word), never 0; a read
 *  not back yet says so. An expired card is not counted: it will not run. */
export function previewBadge(approvals: Read<Approvals>, now: number): NodeBadge {
  const r = readOf(approvals);
  if (r.error !== null) {
    return {
      label: "previews: Unknown", unknown: true,
      title: `Previews waiting for your approval: unknown, the cards could not be read (${r.error})`,
    };
  }
  if (!r.data) {
    return { label: "previews: reading…", unknown: true,
             title: "Previews waiting for your approval: not read yet" };
  }
  const n = pendingDecisions(r.data.proposals, now).rows
    .filter((d) => PREVIEW_ACTIONS.has(d.action) && d.expiresIn !== "expired").length;
  const at = approvals.fetchedAt ? `read ${hhmm(approvals.fetchedAt)}` : "no read time";
  return {
    label: `${n} preview${n === 1 ? "" : "s"} to approve`,
    title: `${n} preview card${n === 1 ? "" : "s"} waiting for you in #agency-decisions, ${at}`,
  };
}
