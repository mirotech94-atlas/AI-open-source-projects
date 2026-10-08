import { useEffect, useState } from "react";
import { Badge, Table, useToast } from "../design";
import type { Column } from "../design";
import { ago, ms } from "../lib/format";
import { heartbeatSetup, rotateHeartbeat } from "../lib/api";
import type { DomainRow, ExtraCheck, HeartbeatRow, HeartbeatSetup } from "../types";

/** Three small tables the predecessor had and Vault did not, all reading
 *  data the sensor has always collected (Task 58 gap G2):
 *
 *  - `ExtraChecks` — the `config.checks` probes (TCP / ICMP / DNS) that sit
 *    beyond the HTTP site checks;
 *  - `Heartbeats` — dead-man switches, jobs that must ping
 *    `/hb/<name>/<token>` on a cadence;
 *  - `Domains` — registrable-domain expiry from the daily whois.
 *
 *  All three are read-only here. Declaring a check or minting a heartbeat
 *  token is a Config surface and is not part of this pass.
 */

function okTone(ok: boolean | null): "good" | "alert" | "plain" {
  if (ok === true) return "good";
  if (ok === false) return "alert";
  return "plain";
}

function okLabel(ok: boolean | null): string {
  if (ok === true) return "Pass";
  if (ok === false) return "Fail";
  return "Not run";
}

const CHECK_COLUMNS: Column<ExtraCheck>[] = [
  {
    key: "ok",
    header: "Result",
    width: 92,
    render: (row) => <Badge tone={okTone(row.ok)}>{okLabel(row.ok)}</Badge>,
  },
  { key: "name", header: "Check" },
  { key: "type", header: "Kind", width: 72, render: (row) => row.type || "—" },
  {
    key: "target",
    header: "Target",
    render: (row) => (
      <span className="mono">
        {row.target || "—"}
        {row.port === null ? "" : `:${row.port}`}
      </span>
    ),
  },
  {
    key: "latency_ms",
    header: "Time",
    align: "right",
    width: 80,
    render: (row) => <span className="num">{row.latency_ms === null ? "—" : ms(row.latency_ms)}</span>,
  },
  { key: "detail", header: "Detail", render: (row) => <span className="mono">{row.detail || "—"}</span> },
];

export function ExtraChecks({ checks }: { checks: ExtraCheck[] }) {
  return (
    <Table
      columns={CHECK_COLUMNS}
      rows={checks}
      rowKey={(row) => row.name}
      dense
      empty="No custom checks are configured."
    />
  );
}

const HEARTBEAT_COLUMNS: Column<HeartbeatRow>[] = [
  {
    key: "late",
    header: "State",
    width: 92,
    render: (row) => <Badge tone={row.late ? "alert" : "good"}>{row.late ? "Late" : "On time"}</Badge>,
  },
  { key: "name", header: "Job" },
  {
    key: "every_min",
    header: "Expected every",
    align: "right",
    width: 128,
    render: (row) => <span className="num">{row.every_min > 0 ? `${row.every_min} min` : "—"}</span>,
  },
  {
    key: "age_min",
    header: "Last ping",
    align: "right",
    width: 112,
    // `age_min === null` is "it has never pinged at all", which is a
    // different statement from "we don't know" — say so rather than dashing.
    render: (row) => <span className="num">{row.last_ping === "" ? "never" : ago(row.last_ping)}</span>,
  },
  { key: "note", header: "Note", render: (row) => row.note || "—" },
];

/** The setup line for one heartbeat.
 *
 *  This is the only place in the product that renders a heartbeat token,
 *  and it exists because the token had to come out of the alert message:
 *  alerts travel to push, to chat integrations and into reports that are
 *  shareable over unauthenticated links, so a credential in one is a
 *  credential published. Here it is behind an admin session, on the app's
 *  own origin, fetched only when an admin presses the button — and the hub
 *  audits both the reveal and the rotation.
 *
 *  A user told "this never pinged" and not told how to fix it is worse off
 *  than before, so the alert points at this panel by name.
 */
function HeartbeatSetupPanel({ name, onClose }: { name: string; onClose: () => void }) {
  const toast = useToast();
  const [setup, setSetup] = useState<HeartbeatSetup | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // The panel is mounted with `key={name}` below, so it starts fresh for
  // each heartbeat and this effect never has to clear stale state — it only
  // does the fetch, which is exactly the external system an effect is for.
  useEffect(() => {
    let live = true;
    heartbeatSetup(name)
      .then((s) => {
        if (live) setSetup(s);
      })
      .catch((e: Error) => {
        if (live) setError(e.message || "could not load the setup command");
      });
    return () => {
      live = false;
    };
  }, [name]);

  async function rotate() {
    setBusy(true);
    try {
      const next = await rotateHeartbeat(name);
      setSetup(next);
      toast.show(
        `Rotated ${name} — the old URL stopped working just now, give the job the new line.`,
        "good",
      );
    } catch (e) {
      toast.show(`Could not rotate ${name}: ${(e as Error).message}`, "alert");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="g-hb-setup">
      <div className="g-hb-setup-head">
        <strong>{name}</strong>
        <button type="button" onClick={onClose}>
          Hide
        </button>
      </div>
      {error ? (
        <p className="g-hb-setup-note">{error}</p>
      ) : setup === null ? (
        <p className="g-hb-setup-note">Loading…</p>
      ) : (
        <>
          <p className="g-hb-setup-note">
            Add this to the end of that job. It contains this heartbeat&rsquo;s secret, so treat it
            like a password — it is shown here and nowhere else.
          </p>
          <code className="mono g-hb-setup-cmd">{setup.command}</code>
          <div className="g-hb-setup-actions">
            <button type="button" disabled={busy} onClick={() => void rotate()}>
              {busy ? "Rotating…" : "Rotate token"}
            </button>
            <span className="g-hb-setup-note">
              Rotating invalidates the line above immediately; the job will fail its check-in until
              you paste the new one.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

export function Heartbeats({
  heartbeats,
  canReveal = false,
}: {
  heartbeats: HeartbeatRow[];
  canReveal?: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const columns = canReveal
    ? [
        ...HEARTBEAT_COLUMNS,
        {
          key: "setup",
          header: "Setup",
          width: 132,
          render: (row: HeartbeatRow) => (
            <button
              type="button"
              onClick={() => setOpen(open === row.name ? null : row.name)}
              aria-expanded={open === row.name}
            >
              {open === row.name ? "Hide command" : "Setup command"}
            </button>
          ),
        } as Column<HeartbeatRow>,
      ]
    : HEARTBEAT_COLUMNS;
  return (
    <>
      <Table
        columns={columns}
        rows={heartbeats}
        rowKey={(row) => row.name}
        dense
        empty={
          /* Item 27: "no heartbeats" is not a pass and not a failure, and
             the old line left a reader unsure which. A dead-man heartbeat
             is the only check that catches a job which stopped running --
             every other check here reacts to something that happened, and
             a backup that silently stopped happening produces nothing to
             react to. Worth explaining rather than stating. */
          "No dead-man heartbeats are set up. A heartbeat is a job — a backup, " +
          "a sync, a cron — that calls Vault each time it finishes. If the call " +
          "stops arriving, Vault tells you. It is the only check here that " +
          "notices something that stopped happening rather than something that did."
        }
      />
      {open === null ? null : (
        <HeartbeatSetupPanel key={open} name={open} onClose={() => setOpen(null)} />
      )}
    </>
  );
}

/** Under 30 days is a warning, under 7 is an alert — the same shape as the
 *  certificate-days thresholds, and deliberately not configurable: a domain
 *  lapsing is not a tunable. */
function expiryTone(days: number | null): "good" | "warn" | "alert" | "plain" {
  if (days === null) return "plain";
  if (days <= 7) return "alert";
  if (days <= 30) return "warn";
  return "good";
}

const DOMAIN_COLUMNS: Column<DomainRow>[] = [
  { key: "domain", header: "Domain", render: (row) => <span className="mono">{row.domain}</span> },
  {
    key: "days_left",
    header: "Days left",
    align: "right",
    width: 104,
    render: (row) => (
      <Badge tone={expiryTone(row.days_left)}>
        <span className="num">{row.days_left === null ? "unknown" : row.days_left}</span>
      </Badge>
    ),
  },
  {
    key: "expires",
    header: "Expires",
    width: 128,
    render: (row) => <span className="num">{row.expires || "—"}</span>,
  },
  { key: "error", header: "Note", render: (row) => row.error || "—" },
];

export function Domains({ domains }: { domains: DomainRow[] }) {
  return (
    <Table
      columns={DOMAIN_COLUMNS}
      rows={domains}
      rowKey={(row) => row.domain}
      dense
      empty="No domain registrations have been looked up yet."
    />
  );
}
