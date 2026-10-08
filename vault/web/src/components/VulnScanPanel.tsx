import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, useToast } from "../design";
import { startVulnScan, vulnScans } from "../lib/api";
import type { VulnFinding, VulnScanRow } from "../types";

const SEV_ORDER = ["critical", "high", "medium", "low", "info"] as const;
const SEV_TONE: Record<string, "alert" | "warn" | "plain" | "good"> = {
  critical: "alert", high: "alert", medium: "warn", low: "plain", info: "plain",
};

function when(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** The one-line summary a row gets in the history table. */
function summaryText(s: VulnScanRow): string {
  if (s.status === "failed") return s.error || "failed";
  if (s.status !== "done") return s.status === "running" ? "running…" : "queued";
  const parts = SEV_ORDER.filter((k) => (s.summary[k] ?? 0) > 0 && k !== "info").map((k) => `${s.summary[k]} ${k}`);
  return parts.length ? parts.join(" · ") : "nothing to report";
}

/** Manual vulnerability scans -- the "look at this one hard" button. The
 *  scheduled sweep watches everything shallowly; this points at one target
 *  and reports open ports, service versions, TLS and web-header problems,
 *  and on servers the things only root can see. Connect-and-look only: it
 *  never logs in. */
/** Item 23: the categories a client already thinks in, in the order they
 *  would look. "The whole network" leads because it is the one thing that
 *  is not a single machine, and it is what an owner reaches for first. */
const TARGET_GROUPS = [
  { kind: "lan", label: "The whole network" },
  { kind: "host", label: "Servers" },
  { kind: "site", label: "Websites" },
  { kind: "device", label: "Devices" },
] as const;
const GROUPED_KINDS: Set<string> = new Set(TARGET_GROUPS.map((g) => g.kind));

export function VulnScanPanel({ canWrite, isAdmin }: { canWrite: boolean; isAdmin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({
    queryKey: ["vuln-scans"],
    queryFn: vulnScans,
    // Poll while anything is in flight; otherwise leave it alone.
    refetchInterval: (q) => (q.state.data?.scans.some((s) => s.status === "queued" || s.status === "running") ? 4_000 : false),
  });
  const [target, setTarget] = useState<string>("");
  const [profile, setProfile] = useState<"quick" | "standard" | "full">("standard");
  const [open, setOpen] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: () => {
      const t = query.data?.targets.find((x) => `${x.kind}:${x.target}` === target);
      return startVulnScan(t?.target ?? "lan", t?.kind ?? "lan", profile);
    },
    onSuccess: (ack) => {
      toast.show(`Scan queued (${ack.status})`);
      qc.invalidateQueries({ queryKey: ["vuln-scans"] });
    },
    onError: (e: Error) => toast.show(e.message || "Could not queue the scan"),
  });

  const payload = query.data;
  const targets = (payload?.targets ?? []).filter((t) => t.kind !== "lan" || isAdmin);
  const scans = payload?.scans ?? [];
  const selected = scans.find((s) => s.id === open) ?? null;

  return (
    <div className="g-vuln" data-testid="vuln-scans">
      <div className="g-vuln-run">
        <label htmlFor="vuln-target">Target</label>
        {/* Item 23: grouped. A flat list of every device, server and site
            made a client read the whole thing to find the one they meant;
            the groups are the categories they already think in. */}
        <select id="vuln-target" value={target} onChange={(e) => setTarget(e.target.value)} disabled={!canWrite || targets.length === 0}>
          <option value="">Pick what to scan…</option>
          {TARGET_GROUPS.map(({ kind, label }) => {
            const inGroup = targets.filter((t) => t.kind === kind);
            if (inGroup.length === 0) return null;
            return (
              <optgroup key={kind} label={label}>
                {inGroup.map((t) => (
                  <option key={`${t.kind}:${t.target}`} value={`${t.kind}:${t.target}`}>
                    {t.label}
                  </option>
                ))}
              </optgroup>
            );
          })}
          {targets.filter((t) => !GROUPED_KINDS.has(t.kind)).map((t) => (
            <option key={`${t.kind}:${t.target}`} value={`${t.kind}:${t.target}`}>
              {t.label}
            </option>
          ))}
        </select>
        <label htmlFor="vuln-profile">Depth</label>
        <select id="vuln-profile" value={profile} onChange={(e) => setProfile(e.target.value as typeof profile)} disabled={!canWrite}>
          <option value="quick">Quick — 66 common ports, about a minute</option>
          <option value="standard">Standard — adds database, container and remote-access ports</option>
          <option value="full">Full — every privileged port and more, several minutes</option>
        </select>
        <button type="button" disabled={!canWrite || !target || start.isPending} onClick={() => start.mutate()}>
          {start.isPending ? "Queuing…" : "Run scan"}
        </button>
        <p className="hint">
          Connect-and-look only: open ports, service versions, TLS and web headers; on servers, listeners, SSH login policy and fixable CVEs. It never logs in or exploits anything.
        </p>
      </div>

      {scans.length === 0 ? (
        <p className="hint">No manual scans yet. Pick a target above — the whole network is admin-only.</p>
      ) : (
        <table className="g-vuln-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Target</th>
              <th>Depth</th>
              <th>Score</th>
              <th>Findings</th>
              <th>By</th>
            </tr>
          </thead>
          <tbody>
            {scans.map((s) => (
              <tr key={s.id} className={open === s.id ? "g-vuln-row-open" : undefined} onClick={() => setOpen(open === s.id ? null : s.id)} data-testid="vuln-row">
                <td>{when(s.finished_at ?? s.queued_at)}</td>
                <td>
                  <span className="mono">{s.target}</span> <span className="hint">{s.kind}</span>
                </td>
                <td>{s.profile}</td>
                <td>{s.score === null ? "—" : <b>{s.score}</b>}</td>
                <td>
                  <Badge tone={s.status === "failed" ? "alert" : s.status !== "done" ? "plain" : (s.summary.critical || s.summary.high) ? "alert" : s.summary.medium ? "warn" : "good"}>
                    {summaryText(s)}
                  </Badge>
                </td>
                <td className="hint">{s.who}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && selected.status === "done" && (
        <div className="g-vuln-detail" data-testid="vuln-detail">
          <h3>
            {selected.target} · score {selected.score ?? "—"} · {selected.duration_s ?? "?"}s
          </h3>
          {selected.ports.length > 0 && (
            <p className="hint">
              Open ports: {selected.ports.map((p) => `${p.port}/${p.service}${p.tls_version ? ` (${p.tls_version})` : ""}`).join(", ")}
            </p>
          )}
          <ul className="g-vuln-findings">
            {selected.findings.map((f: VulnFinding, i) => (
              <li key={`${f.title}-${i}`}>
                <Badge tone={SEV_TONE[f.severity] ?? "plain"}>{f.severity}</Badge>
                <span className="g-vuln-finding-title">
                  {f.target ? <span className="mono">{f.target} · </span> : null}
                  {f.title}
                  {f.port ? <span className="hint"> · port {f.port}</span> : null}
                </span>
                {f.evidence && <span className="hint g-vuln-evidence">{f.evidence}</span>}
                {f.fix && <span className="hint">Vault fix: <code>{f.fix.replace(/_/g, " ")}</code></span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default VulnScanPanel;
