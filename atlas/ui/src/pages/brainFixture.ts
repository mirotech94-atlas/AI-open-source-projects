/** A small Brain payload in the server's exact shape, for the UI tests. */
import type { Brain, BrainDepartment, BrainRuns, BrainSeat } from "../api";

export function runs(auto: number, approved: number, excluded = 0): BrainRuns {
  const executions = auto + approved;
  return {
    auto, approved, excluded, executions,
    pct: executions ? Math.round((1000 * auto) / executions) / 10 : null,
  };
}

function seat(over: Partial<BrainSeat> & { name: string }): BrainSeat {
  return {
    id: `${over.kind ?? "agent"}:${over.name}`, kind: "agent", label: over.name,
    title: "", layer: "agent", ran_7d: 0, ...over,
  };
}

function dept(over: Partial<BrainDepartment> & { key: string }): BrainDepartment {
  return {
    name: over.key, core: false,
    head: { agent: null, label: "You", title: "sign-off", why: "none leads it" },
    modules: [], disabled_modules: [], agents: [], jobs: [], actions: [],
    tier2_actions: 0,
    seats: { agents: 0, agents_ran: 0, jobs: 0, jobs_recorded: 0, jobs_ran: 0 },
    runs_without_you: runs(0, 0), waiting_on_you: 0, verify: {}, paused: [],
    layers: {}, ...over,
  };
}

export function brainFixture(): Brain {
  const money = dept({
    key: "money", name: "Money & Markets",
    head: { agent: "markets", label: "Ivan", title: "Markets (watch only)", why: "owns markets" },
    agents: [seat({ name: "markets", label: "Ivan", title: "Markets", ran_7d: 4 })],
    jobs: [
      seat({ name: "playlist", kind: "job", label: "playlist", layer: "agentic", ran_7d: 1, recorded: true }),
      seat({ name: "market_alerts", kind: "job", label: "market alerts", layer: "automation", ran_7d: null, recorded: false }),
    ],
    actions: [
      { id: "action:markets.alert", name: "markets.alert", tier: 1, card_only: false, verifier: false },
      { id: "action:markets.paper_idea", name: "markets.paper_idea", tier: 2, card_only: true, verifier: true },
    ],
    tier2_actions: 1,
    seats: { agents: 1, agents_ran: 1, jobs: 2, jobs_recorded: 1, jobs_ran: 1 },
    runs_without_you: runs(3, 1), waiting_on_you: 2,
    paused: [{ scope: "markets", says: "the markets module is paused", shadow: false }],
  });
  const ops = dept({
    key: "ops", name: "Ops & Infra",
    head: { agent: "ops", label: "Marcus", title: "Operations", why: "handoffs" },
    agents: [seat({ name: "ops", label: "Marcus", title: "Operations", ran_7d: 0 })],
    jobs: [seat({ name: "checks", kind: "job", label: "checks", layer: "automation", ran_7d: null, recorded: false })],
    seats: { agents: 1, agents_ran: 0, jobs: 1, jobs_recorded: 0, jobs_ran: 0 },
  });
  const knowledge = dept({
    key: "knowledge", name: "Knowledge Core", core: true,
    head: { agent: "concierge", label: "Ava", title: "Concierge", why: "front door" },
    agents: [seat({ name: "concierge", label: "Ava", title: "Concierge", layer: "agentic", ran_7d: 9 })],
    seats: { agents: 1, agents_ran: 1, jobs: 0, jobs_recorded: 0, jobs_ran: 0 },
    runs_without_you: runs(5, 0),
  });
  const strategy = dept({ key: "strategy", name: "Strategy",
    jobs: [seat({ name: "brief", kind: "job", label: "brief", layer: "automation", ran_7d: 7, recorded: true })],
    seats: { agents: 0, agents_ran: 0, jobs: 1, jobs_recorded: 1, jobs_ran: 1 } });
  return {
    read_at: "2026-09-29T12:00:00Z", since: "2026-09-22T12:00:00Z", window_days: 7,
    watch_only: false, verify_after: true,
    layers: [
      { key: "automation", label: "Automation", meaning: "a fixed job" },
      { key: "agent", label: "AI agent", meaning: "one agent" },
      { key: "agentic", label: "Agentic system", meaning: "several agents" },
    ],
    whole: {
      runs_without_you: runs(8, 1), waiting_on_you: 3,
      paused: [{ scope: "markets", says: "paused", shadow: false, department: "money" }],
      verify: { verified: 4, contradicted: 1, unknown: 2 },
    },
    departments: [knowledge, money, ops, strategy],
    unmapped: { executions: 0, actions: [], waiting_on_you: 1 },
    sops: {
      "job:playlist": [
        { kind: "trigger", text: "weekly, Sunday 15:00 New York · missed-run policy run-once-on-recovery" },
        { kind: "reads", text: "closes" },
        { kind: "decides", text: "target weights" },
        { kind: "acts", text: "0 on its own, 1 as a card", actions: [{
          name: "markets.paper_rebalance", tier: 2, summary: "rebalance", card_only: true,
          verifier: true, topic: "markets", topic_depends_on: null, own_channel: false,
          department: "money", principal_only: false, needs_you: true }] },
        { kind: "signoff", text: "needs your sign-off on 1: markets.paper_rebalance" },
        { kind: "result", text: "#markets, the default Slack channel" },
      ],
      "agent:markets": [{ kind: "trigger", text: "when you ask it" }],
      "action:markets.paper_idea": [{ kind: "signoff", text: "Tier 2" }],
    },
    errors: {},
  };
}
