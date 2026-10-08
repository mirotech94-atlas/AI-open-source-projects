import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, chat, queueStatus } from "../../lib/api";
import type { AlertHandoff, LiveEvent } from "../../types";
import type { ChatBullet, ChatProposal, ChatResponse, ToolStep } from "../../types";

/** One turn in the panel's transcript. A `user` entry is only ever text; a
 *  `guardian` entry carries the full structured reply so `Message` can render
 *  the strict verdict/bullets/next format and `ToolSteps`/`ProposalCard` can
 *  render the rest. */
export type ChatEntry = {
  id: string;
  role: "user" | "guardian";
  text: string;
  verdict: string;
  bullets: ChatBullet[];
  next: ChatResponse["next"];
  toolSteps: ToolStep[];
  proposal: ChatProposal | null;
  /** Which rung of the provider ladder answered this turn (`groq/<model>`,
   *  `anthropic/<model>` or `rules`), for the per-reply provenance line. Empty
   *  on a user entry and on a locally-sourced notice. */
  provider: string;
  /** Set on a guardian entry that stands in for a failed turn (network error
   *  after the one retry, or a rate limit) — rendered like any other reply,
   *  just sourced locally rather than from the hub. */
  isNotice?: boolean;
};

let seq = 0;
function nextId(): string {
  seq += 1;
  return `chat-${seq}`;
}

function entryFromResponse(res: ChatResponse): ChatEntry {
  return {
    id: nextId(),
    role: "guardian",
    text: res.text,
    verdict: res.verdict,
    bullets: res.bullets,
    next: res.next,
    toolSteps: res.tool_steps,
    proposal: res.proposal,
    provider: res.provider,
  };
}

function notice(text: string): ChatEntry {
  return {
    id: nextId(),
    role: "guardian",
    text,
    verdict: "",
    bullets: [],
    next: [],
    toolSteps: [],
    proposal: null,
    provider: "",
    isNotice: true,
  };
}

/** The subset of `LiveFeed` this hook needs — narrowed so `chat.test.tsx`
 *  can hand it a bare `{on}` stub instead of a whole feed. */
export type QueueLiveSource = {
  on: (type: string, fn: (event: LiveEvent) => void) => () => void;
};

export type UseChatOptions = {
  /** The current route's tab key, sent as `ChatRequest.tab` so Vault knows
   *  what the user is looking at. */
  tab: string;
  live: QueueLiveSource;
};

/**
 * Owns one Vault chat thread. The thread lives only in memory (a fresh
 * load starts clean) — kept here rather than `sessionStorage` because a
 * page-scoped panel like this one loses nothing by resetting on reload, and
 * `thread_id` round-trips through the hub's own response so nothing needs to
 * be reconstructed locally.
 */
export function useChat({ tab, live }: UseChatOptions) {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const threadRef = useRef<string | null>(null);
  const tabRef = useRef(tab);
  // Refreshed in an effect, not during render — same rationale as
  // `lib/live.ts`'s `optionsRef`: `runTurn` reads `tabRef.current` at call
  // time, not render time, so this only needs to be current by then.
  useEffect(() => {
    tabRef.current = tab;
  });

  const applyQueueFrame = useCallback((event: LiveEvent) => {
    const id = event.data?.id;
    const status = event.data?.status;
    if (typeof id !== "string" || typeof status !== "string") return;
    setEntries((prev) =>
      prev.map((entry) => {
        if (entry.role !== "guardian" || entry.toolSteps.length === 0) return entry;
        let changed = false;
        const toolSteps = entry.toolSteps.map((step) => {
          if (step.queue_id !== id || step.status === status) return step;
          changed = true;
          return { ...step, status };
        });
        return changed ? { ...entry, toolSteps } : entry;
      }),
    );
  }, []);

  useEffect(() => live.on("queue", applyQueueFrame), [live, applyQueueFrame]);

  // Applies a fetched `QueueStatus` (status + the tool's actual result body)
  // to the matching step, wherever it lives in the transcript.
  const applyStepResult = useCallback(
    (id: string, status: string, result: Record<string, unknown> | null) => {
      setEntries((prev) =>
        prev.map((entry) => {
          if (entry.role !== "guardian" || entry.toolSteps.length === 0) return entry;
          let changed = false;
          const toolSteps = entry.toolSteps.map((step) => {
            if (step.queue_id !== id) return step;
            changed = true;
            return { ...step, status, result };
          });
          return changed ? { ...entry, toolSteps } : entry;
        }),
      );
    },
    [],
  );

  // A queued/running tool step never gets a result from the "queue" live
  // frame alone (it only ever carries `{id, status}`) — this polls
  // `GET /api/v1/queue/{id}` for the real result body until the step reaches
  // a terminal status, or gives up after `MAX_POLLS` and leaves a "still
  // running" note rather than polling forever. `pollersRef` guards against
  // starting a second poller for a queue id that already has one running.
  const pollersRef = useRef(new Map<string, ReturnType<typeof setInterval>>());
  const entriesRef = useRef(entries);
  const POLL_MS = 5_000;
  const MAX_POLLS = 12; // ~60s

  useEffect(() => {
    entriesRef.current = entries;
  }, [entries]);

  // True once the given queue id's step is no longer queued/running in the
  // *current* transcript — read from `entriesRef` rather than the closure's
  // `entries` so a status the "queue" live frame set after this poller
  // started (see `applyQueueFrame` above) stops it too, instead of the
  // poller's own stale view racing a fresh network fetch against it.
  function stillPending(id: string): boolean {
    for (const entry of entriesRef.current) {
      if (entry.role !== "guardian") continue;
      for (const step of entry.toolSteps) {
        if (step.queue_id === id) return step.status === "queued" || step.status === "running";
      }
    }
    return false;
  }

  useEffect(() => {
    for (const entry of entries) {
      if (entry.role !== "guardian") continue;
      for (const step of entry.toolSteps) {
        const id = step.queue_id;
        if (!id) continue;
        const pending = step.status === "queued" || step.status === "running";
        if (!pending || pollersRef.current.has(id)) continue;

        let attempts = 0;
        const stop = () => {
          const timer = pollersRef.current.get(id);
          if (timer) clearInterval(timer);
          pollersRef.current.delete(id);
        };
        const poll = async () => {
          if (!stillPending(id)) {
            stop();
            return;
          }
          attempts += 1;
          try {
            const res = await queueStatus(id);
            const terminal = res.status !== "queued" && res.status !== "running";
            if (terminal) {
              stop();
              applyStepResult(id, res.status, res.result);
              return;
            }
          } catch {
            // A transport hiccup is worth another try before the cap.
          }
          if (attempts >= MAX_POLLS) {
            stop();
            applyStepResult(id, step.status, { text: "still running — check back later" });
          }
        };

        void poll();
        pollersRef.current.set(id, setInterval(poll, POLL_MS));
      }
    }
  }, [entries, applyStepResult]);

  // Stop every in-flight poller on unmount — including a tenant switch,
  // which remounts `ChatPanel` (see `Shell.tsx`) and therefore this hook.
  // The `Map` is captured into a variable inside the effect body (not read
  // from `.current` inside the cleanup closure itself) — it is the same
  // instance for the hook's whole lifetime either way.
  useEffect(() => {
    const pollers = pollersRef.current;
    return () => {
      for (const timer of pollers.values()) clearInterval(timer);
      pollers.clear();
    };
  }, []);

  const runTurn = useCallback(
    async (message: string, confirmToken: string | null) => {
      setBusy(true);
      try {
        let res: ChatResponse;
        try {
          res = await chat({ message, thread: threadRef.current, tab: tabRef.current, confirm_token: confirmToken });
        } catch (err) {
          // One silent retry on a network error only — a rejected request
          // that never reached the hub (no `ApiError`, e.g. `TypeError:
          // Failed to fetch`) is worth one more try before bothering the
          // user with it. Never when a `confirm_token` rides along: the hub
          // burns it single-use, so a retry of a request that DID land
          // would be rejected as already-consumed and tell the user to
          // re-confirm — worse than just surfacing the original error and
          // letting them say "yes" again.
          if (err instanceof ApiError || confirmToken !== null) throw err;
          res = await chat({ message, thread: threadRef.current, tab: tabRef.current, confirm_token: confirmToken });
        }
        threadRef.current = res.thread_id;
        setEntries((prev) => [...prev, entryFromResponse(res)]);
      } catch (err) {
        const text =
          err instanceof ApiError && err.status === 429
            ? "Vault is catching up — try again in a moment."
            : "Vault could not be reached. Try again in a moment.";
        setEntries((prev) => [...prev, notice(text)]);
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const send = useCallback(
    (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || busy) return;
      setEntries((prev) => [
        ...prev,
        {
          id: nextId(), role: "user", text: trimmed, verdict: "", bullets: [],
          next: [], toolSteps: [], proposal: null, provider: "",
        },
      ]);
      void runTurn(trimmed, null);
    },
    [busy, runTurn],
  );

  const confirmProposal = useCallback(
    (proposal: ChatProposal) => {
      if (busy) return;
      void runTurn("yes", proposal.token);
    },
    [busy, runTurn],
  );

  /** Seeds the transcript with Vault's opening message about one alert —
   *  the hub's own text, and the hub's own tier-3 proposal when the repair
   *  only needs a yes — so the panel opens explaining the problem instead of
   *  an empty box the owner has to re-describe it to.
   *
   *  Rendered through the same `Message` component as any reply: the alert's
   *  own line becomes the verdict heading, what Vault can and cannot do is
   *  the body, and what it needs from the owner is the `Next:` row. The hub
   *  recorded the same opening on `thread_id`, so the owner's reply continues
   *  a conversation Vault already has the context for. Seeding the same
   *  hand-off twice (a re-render, a second click) is a no-op. */
  const seededRef = useRef("");
  const seedHandoff = useCallback((handoff: AlertHandoff) => {
    const stamp = `${handoff.key}|${handoff.proposal?.token ?? ""}|${handoff.thread_id}`;
    if (seededRef.current === stamp) return;
    seededRef.current = stamp;
    if (handoff.thread_id) threadRef.current = handoff.thread_id;

    const paragraphs = handoff.opening.split("\n\n").map((p) => p.trim()).filter(Boolean);
    const need = handoff.remedy.need;
    const verdict = paragraphs[0] ?? "";
    const body = paragraphs
      .slice(1)
      .filter((p) => need === "" || !p.includes(need))
      .join(" ");

    setEntries((prev) => [
      ...prev,
      {
        id: nextId(),
        role: "guardian",
        text: body,
        verdict,
        bullets: [],
        next: need === "" ? [] : [{ label: need, method: "POST", path: "", body: {} }],
        toolSteps: [],
        proposal: handoff.proposal,
        provider: "",
      },
    ]);
  }, []);

  const dismissProposal = useCallback((entryId: string) => {
    setEntries((prev) => prev.map((e) => (e.id === entryId ? { ...e, proposal: null } : e)));
  }, []);

  return { entries, busy, send, confirmProposal, dismissProposal, seedHandoff };
}
