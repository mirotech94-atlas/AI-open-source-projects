import { useEffect, useState, type JSX, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, type Overview } from "../api";
import { useFresh, type Fresh } from "../hooks/useFresh";
import { AgencyDeskBody, DeskTop, Palette } from "./AgencyDesk";
import { AgencyBody } from "./AgencySeatCards";

/**
 * The Agency desk, as a Command Center section (Miro, 2026-10-28: "the
 * agency tab should have only the agency map"). Everything that used to sit
 * under the map lives here, in the Command Center's order: the team with
 * its seat cards, Sales, SEO, Research, Remediation, the week's schedule and
 * the content calendar, reports, Clients & Projects, settings last.
 * Decisions and activity are the top grid's panels, not repeated. Each
 * section is an anchor (`/agency#<id>`) reachable from the cmd-K palette.
 * It sits on the Agency tab since 2026-10-29 (short Command Center). Reads only.
 */
export function AgencyDeskSection({ reloadKey, overview, schedule }: {
  reloadKey: number;
  overview: Fresh<Overview | null>;
  schedule?: ReactNode;
}): JSX.Element {
  const roster = useFresh(() => api.agents(), [reloadKey]);
  const leads = useFresh(() => api.leads({ limit: 1 }), [reloadKey]);
  const approvals = useFresh(() => api.approvals(), [reloadKey]);
  // One call, grouped on the client: the server applies its row limit across
  // every module before any module filter.
  const actions = useFresh(() => api.activity({ kind: "action", limit: 500 }), [reloadKey]);
  const board = useFresh(() => api.leads({ limit: 500 }), [reloadKey]);
  const sites = useFresh(() => api.sites(), [reloadKey]);
  const calendar = useFresh(() => api.academyCalendar(), [reloadKey]);
  const activity = useFresh(() => api.activity({ limit: 200 }), [reloadKey]);
  const reports = useFresh(() => api.reports({ kind: "weekly" }), [reloadKey]);
  const secrets = useFresh(() => api.settingsSecrets(), [reloadKey]);
  const projects = useFresh(() => api.projects(), [reloadKey]);
  const remediation = useFresh(() => api.remediation(), [reloadKey]);
  const [palette, setPalette] = useState(false);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const { hash } = useLocation();
  // The sections draw once their reads land; scroll to an anchor again then.
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash, overview.fetchedAt === null, board.fetchedAt === null]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setQuery("");
        setPalette((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return (): void => window.removeEventListener("keydown", onKey);
  }, []);
  const now = new Date();
  return (
    <section id="agency-desk" className="desk cmd-desk" aria-label="Agency desk">
      <DeskTop onPalette={() => { setQuery(""); setPalette(true); }} />
      <Palette open={palette} query={query} onQuery={setQuery}
               onClose={() => setPalette(false)}
               onPick={(id) => {
                 setPalette(false);
                 // Needs you stays on the Command Center; the rest is here.
                 navigate(id === "decisions" ? "/#decisions" : `/agency#${id}`);
                 document.getElementById(id)?.scrollIntoView();
               }} />
      <AgencyDeskBody overview={overview} leads={board} sites={sites} approvals={approvals}
                      calendar={calendar} activity={activity} reports={reports}
                      roster={roster} secrets={secrets} projects={projects}
                      remediation={remediation} now={now} schedule={schedule}
                      seats={<AgencyBody overview={overview} roster={roster} leads={leads}
                                         approvals={approvals} actions={actions}
                                         remediation={remediation} now={now} />} />
    </section>
  );
}
