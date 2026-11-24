/**
 * The Agency tab's glance figures, from reads already loaded. A figure whose
 * read failed is null (UNKNOWN), never 0; every figure carries its read time.
 */
import type { AgentSummary, Approvals, Overview } from "../api";
import { overall } from "./commandModel";
import type { Read } from "./homeModel";
import { rosterRow } from "./opsFloorModel";
import { isRetiredAgent } from "./roster";

export interface GlanceFigure {
  value: number | null;
  readAt: string | null;
}

export interface AgencyGlance {
  registered: GlanceFigure;
  failing: GlanceFigure;
  waiting: GlanceFigure;
  /** Checks that are degraded or failing (UNKNOWN is not counted here). */
  degradedChecks: GlanceFigure;
}

const UNREAD: GlanceFigure = { value: null, readAt: null };

export function agencyGlance(src: {
  agents: Read<{ as_of: string; agents: AgentSummary[] }>;
  approvals: Read<Approvals>;
  overview: Read<Overview>;
}): AgencyGlance {
  const a = src.agents.error === null ? src.agents.data : null;
  const live = a ? a.agents.filter((x) => !isRetiredAgent(x.name)) : null;
  const p = src.approvals.error === null ? src.approvals.data : null;
  const o = src.overview.error === null ? src.overview.data : null;
  const ov = o ? overall(o.modules) : null;
  return {
    registered: a && live ? { value: live.length, readAt: a.as_of } : { ...UNREAD },
    failing: a && live
      ? { value: live.filter((x) => rosterRow({
          status: x.status ?? null, shift_state: x.shift_state ?? null,
        }).state === "failing").length, readAt: a.as_of }
      : { ...UNREAD },
    waiting: p
      ? { value: p.proposals.filter((x) => x.status === "pending").length, readAt: p.as_of }
      : { ...UNREAD },
    degradedChecks: o && ov
      ? { value: ov.degraded + ov.failing, readAt: o.as_of } : { ...UNREAD },
  };
}
