import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, useToast } from "../design";
import { EmptyState } from "../components/EmptyState";
import { alertHandoff, incidentDetail, runAction } from "../lib/api";
import type { Ack } from "../lib/api";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { useChatHandoff } from "../shell/chat/handoff";
import { ago, sevTone } from "../lib/format";
import type { IncidentAsset, IncidentGraph as IncidentGraphT, RowAction } from "../types";

const OPERATOR_TOOLTIP = "operator role required";

function findAction(actions: RowAction[], kind: string): RowAction | undefined {
  return actions.find((a) => a.kind === kind);
}

/** One row of "What is involved" — the icon is decorative (kind alone tells
 *  a screen reader nothing more than the name/subtitle already do). */
function AssetRow({ asset }: { asset: IncidentAsset }) {
  return (
    <>
      <span className="g-incident-asset-ico" aria-hidden="true">
        {asset.kind === "external" ? "🌐" : asset.kind === "device" ? "📶" : "🖥"}
      </span>
      <span className="g-incident-asset-name">
        <span className="g-incident-asset-nm">{asset.name}</span>
        {asset.subtitle !== "" && <span className="g-incident-asset-sub">{asset.subtitle}</span>}
      </span>
      <span className="g-incident-asset-risk">
        {asset.risk !== null ? (
          <>
            <b className="num">{asset.risk}</b>
            <span>risk</span>
          </>
        ) : (
          <>
            <b>—</b>
            <span>{asset.kind === "external" ? "external" : "unscored"}</span>
          </>
        )}
      </span>
    </>
  );
}

/** A small static layout, deliberately not the force-directed topology map:
 *  externals on top, the hosts/devices they targeted below, drawn from
 *  whatever the hub actually correlated. Never more than a handful of nodes
 *  — see `guardian_hub/payloads/incidents._graph`. */
function IncidentGraphSvg({ graph }: { graph: IncidentGraphT }) {
  const externals = graph.nodes.filter((n) => n.kind === "external");
  const others = graph.nodes.filter((n) => n.kind !== "external");
  if (graph.nodes.length === 0) {
    return <p className="hint">Vault did not resolve a graph for this incident.</p>;
  }
  const width = 320;
  const rowY = { top: 26, bottom: 110 };
  const xFor = (i: number, n: number) => (n <= 1 ? width / 2 : (width / (n + 1)) * (i + 1));
  const posById = new Map<string, { x: number; y: number }>();
  externals.forEach((n, i) => posById.set(n.id, { x: xFor(i, externals.length), y: rowY.top }));
  others.forEach((n, i) => posById.set(n.id, { x: xFor(i, others.length), y: rowY.bottom }));
  const height = others.length > 0 || externals.length > 0 ? 150 : 40;

  return (
    <svg className="g-incident-graph" viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label={`${graph.nodes.length} assets this incident touched`}>
      {graph.links.map((l, i) => {
        const s = posById.get(l.source);
        const t = posById.get(l.target);
        if (!s || !t) return null;
        return (
          <line key={i} className="g-incident-edge" x1={s.x} y1={s.y + 12} x2={t.x} y2={t.y - 12} />
        );
      })}
      {graph.nodes.map((n) => {
        const p = posById.get(n.id);
        if (!p) return null;
        const w = Math.min(96, Math.max(56, n.label.length * 6 + 20));
        return (
          <g key={n.id}>
            <rect className={`g-incident-node ${n.kind === "external" ? "g-incident-node-hostile" : ""}`}
                  x={p.x - w / 2} y={p.y - 13} width={w} height={26} rx={7} />
            <text className="g-incident-node-label" x={p.x} y={p.y + 4} textAnchor="middle">
              {n.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** One incident, one page (Task 83, patterns A1/A2): the written narrative
 *  first, then the one decision the outcome panel asks for, then — for
 *  anyone who wants more — how it unfolded, what it touched and the raw
 *  evidence rows. */
export function IncidentDetail() {
  const { iid = "" } = useParams<{ iid: string }>();
  const queryClient = useQueryClient();
  const toast = useToast();
  const openHandoff = useChatHandoff();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canWrite = atLeast(role, "operator");

  const queryKey = ["incidentDetail", iid];
  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: () => incidentDetail(iid),
    enabled: iid !== "",
  });

  const handoffThread = useRef<string | null>(null);
  const handoffMutation = useMutation({
    mutationFn: () => alertHandoff(data?.primary_key ?? "", handoffThread.current),
    onSuccess: (handoff) => {
      handoffThread.current = handoff.thread_id || handoffThread.current;
      openHandoff(handoff);
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const actionMutation = useMutation({
    mutationFn: (action: RowAction) => runAction<Ack>(action),
    onSuccess: (_ack, action) => {
      toast.show(action.kind === "fix" && data?.remedy ? `Running: ${data.remedy.summary}` : "Done.", "good");
      void queryClient.invalidateQueries({ queryKey });
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  if (iid === "") {
    return <EmptyState title="No incident named." body="This link is missing its incident id." />;
  }
  if (isLoading) {
    return (
      <Card title="Incident">
        <p className="hint">Loading…</p>
      </Card>
    );
  }
  if (isError || !data) {
    return (
      <EmptyState
        title="This incident could not be found."
        body="It may have been resolved and cleared, or the link is wrong."
        action={{ label: "Back to Inbox", href: "/inbox?filter=incidents" }}
      />
    );
  }

  const n = data.narrative;
  const tone = sevTone(data.sev);
  const ackAction = findAction(data.actions, "ack");
  const snoozeAction = findAction(data.actions, "snooze");
  const resolveAction = findAction(data.actions, "resolve");
  const fixAction = findAction(data.actions, "fix");
  const remedy = data.remedy && data.remedy.kind !== "none" ? data.remedy : null;
  const needsOwner = remedy !== null && remedy.kind !== "auto";
  const pending = actionMutation.isPending;

  return (
    <div className="g-incident-detail">
      <p className="g-incident-crumbs">
        <Link to="/inbox?filter=incidents">Inbox</Link> › <Link to="/inbox?filter=incidents">Incidents</Link> ›{" "}
        {data.title}
      </p>

      <Card tone={tone} className="g-incident-story">
        <div className="g-incident-story-top">
          <span className="g-incident-sevbar" aria-hidden="true" />
          <div className="g-incident-story-head">
            <h1 className="g-incident-h1">{data.title}</h1>
            <div className="g-incident-meta">
              <Badge tone={tone}>{data.sev}</Badge>
              <span className="hint">since {ago(data.opened)}</span>
              <span className="hint">{data.status === "open" ? "still open" : "resolved"}</span>
              <Badge tone="plain">
                {data.alert_count} alert{data.alert_count === 1 ? "" : "s"} grouped
              </Badge>
            </div>
          </div>
        </div>

        <dl className="g-incident-para">
          {n.what_happened !== "" && (
            <>
              <dt>What happened</dt>
              <dd>{n.what_happened}</dd>
            </>
          )}
          {n.what_it_means !== "" && (
            <>
              <dt>What it means</dt>
              <dd>{n.what_it_means}</dd>
            </>
          )}
          <dt>How sure I am</dt>
          <dd>
            {n.confidence !== null ? (
              <span className="g-incident-sure">
                <span className="g-incident-sure-track">
                  <span className="g-incident-sure-fill" style={{ width: `${n.confidence}%` }} />
                </span>
                <span className="num">{n.confidence}%</span>
                <span className="hint">— {n.confidence_basis}</span>
              </span>
            ) : (
              <span className="hint">Vault has not scored the evidence for this incident yet.</span>
            )}
          </dd>
          {n.what_next !== "" && (
            <>
              <dt>What happens next</dt>
              <dd>
                {n.what_next}
                {n.need !== "" && <span className="g-incident-need"> {n.need}</span>}
              </dd>
            </>
          )}
        </dl>

        <div className="g-incident-outcome">
          <div className="g-incident-outcome-actions">
            {fixAction && (
              <button
                type="button"
                className="primary"
                disabled={pending}
                onClick={() => actionMutation.mutate(fixAction)}
              >
                {remedy ? `Yes, ${fixAction.label.toLowerCase()}` : fixAction.label}
              </button>
            )}
            {!fixAction && needsOwner && (
              <button
                type="button"
                className="primary"
                disabled={handoffMutation.isPending || data.primary_key === ""}
                onClick={() => handoffMutation.mutate()}
              >
                Ask Vault…
              </button>
            )}
            <button
              type="button"
              disabled={!ackAction || pending}
              title={ackAction ? undefined : OPERATOR_TOOLTIP}
              onClick={() => ackAction && actionMutation.mutate(ackAction)}
            >
              Ack
            </button>
            <button
              type="button"
              disabled={!snoozeAction || pending}
              title={snoozeAction ? undefined : OPERATOR_TOOLTIP}
              onClick={() => snoozeAction && actionMutation.mutate(snoozeAction)}
            >
              Snooze
            </button>
            <button
              type="button"
              disabled={!resolveAction || pending}
              title={resolveAction ? undefined : OPERATOR_TOOLTIP}
              onClick={() => resolveAction && actionMutation.mutate(resolveAction)}
            >
              Not a problem
            </button>
          </div>
          {!canWrite && (
            <p className="hint">Read-only for your role — {OPERATOR_TOOLTIP} to act on this incident.</p>
          )}
        </div>
      </Card>

      <div className="g-incident-cols">
        <div className="g-incident-col">
          <Card title="How it unfolded" subtitle="the alerts in this incident, in order">
            {data.timeline.length === 0 ? (
              <p className="hint">Nothing recorded yet.</p>
            ) : (
              <ol className="g-incident-timeline">
                {data.timeline.map((it, i) => (
                  <li key={`${it.ts}-${i}`} className={`g-tone-${it.tone}`}>
                    <time>{ago(it.ts)}</time>
                    <p>{it.text}</p>
                  </li>
                ))}
              </ol>
            )}
          </Card>

          <Card title="Evidence" subtitle="what Vault actually observed — the rows the story above was built from">
            {data.evidence.length === 0 ? (
              <p className="hint">No evidence rows.</p>
            ) : (
              <div className="g-incident-ev-wrap">
                <table className="g-incident-ev">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Where</th>
                      <th>What Vault saw</th>
                      <th>Seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.evidence.map((row, i) => (
                      <tr key={i}>
                        <td className="num">{ago(row.ts)}</td>
                        <td className="num">{row.where}</td>
                        <td>{row.what}</td>
                        <td className="num">{row.seen}×</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        <div className="g-incident-col">
          <Card title="What is involved" subtitle="two or three things, not the whole estate">
            {data.assets.length === 0 ? (
              <p className="hint">Vault could not resolve an asset for this incident.</p>
            ) : (
              <ul className="g-incident-assets">
                {data.assets.map((a) => (
                  <li key={a.id}>
                    {a.href !== "" ? (
                      <Link to={a.href} className="g-incident-asset">
                        <AssetRow asset={a} />
                      </Link>
                    ) : (
                      <span className="g-incident-asset">
                        <AssetRow asset={a} />
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="What it touched" subtitle="this incident only — the full map stays under Topology">
            <IncidentGraphSvg graph={data.graph} />
          </Card>
        </div>
      </div>
    </div>
  );
}

export default IncidentDetail;
