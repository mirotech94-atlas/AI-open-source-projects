import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Page, PageHeader, Stat, StatStrip, useToast } from "../design";
import { ApiTokenPanel, liveTokenCount, useApiTokens } from "../components/ApiTokenPanel";
import type { StatItem } from "../design";
import { IntegrationCardView } from "../components/IntegrationCardView";
import type { IntegrationSaveBody, TestOutcome } from "../components/IntegrationCardView";
import { RouterCard } from "../components/RouterCard";
import { ConfirmDialog } from "./Settings/shared";
import {
  deleteIntegration,
  deleteRouter,
  disableRemoteAdmin,
  disableRemoteAdminConfirm,
  integrationsPayload,
  overviewPayload,
  pairTelegram,
  queueStatus,
  recordersPayload,
  routerCard,
  saveIntegration,
  saveRouter,
  testIntegration,
  testRouter,
} from "../lib/api";
import type { RouterCard as RouterCardData, RouterSaveBody } from "../lib/api";
import { atLeast, roleOf, useMe } from "../lib/auth";
import type { IntegrationsPayload } from "../types";

const EMPTY: IntegrationsPayload = { integrations: [] };

const EMPTY_ROUTER: RouterCardData = {
  configured: false,
  vendor: "fios",
  vendor_label: "Verizon Fios (CR1000A / CR1000B)",
  base_url: "",
  username: "",
  has_credentials: false,
  enabled: true,
  status: "unconfigured",
  last_test_ok: null,
  last_test_msg: "",
  last_test: "",
  // No record of a disable run, which reads as "Vault has never done this"
  // — never as "remote administration is on". The gateway offers no way to
  // read the live value, so the card only ever speaks about its own actions.
  remote_admin_off_at: "",
  remote_admin_msg: "",
};

// Telegram's pairing poll interval — short enough that "send /start <code>
// to your bot" feels responsive, long enough not to hammer the hub while
// the admin is off in Telegram typing it.
const PAIR_POLL_MS = 3000;
// While a router credential Test is running on the sensor, poll the card so
// its result lands without a manual refresh — the sensor answers in seconds.
// Bounded: if no sensor picks the job up, the poll gives up after
// ROUTER_TEST_MAX_POLLS ticks and re-enables the button rather than spinning
// forever (a persisted "testing" state was the bug this avoids).
const ROUTER_TEST_POLL_MS = 2500;
const ROUTER_TEST_MAX_POLLS = 16;

/** Integrations: the two things Vault connects to, kept visibly apart.
 *
 *  1. **Notification channels** — the places Vault *talks through* (Slack,
 *     Teams, Discord, a webhook…). Each delivers outbound and chooses which
 *     severities it carries.
 *  2. **Devices on your network** — the things Vault *acts on*, held as a
 *     LAN credential the on-site sensor uses. Today that is the home gateway
 *     router; cameras live on their own Recorders surface.
 *
 *  The whole tab is admin-only, reads included: the cards carry masked
 *  credentials and, for an inbound-capable provider, a live webhook URL — so
 *  `GET /api/v1/integrations` and `GET /api/v1/router` are admin-gated and a
 *  viewer gets a plain explanation here rather than an empty list and a 403. */
export function Integrations() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canWrite = atLeast(role, "admin");

  // Not just disabled-on-render: the reads themselves are admin-gated, so
  // asking as a viewer would only produce a 403.
  const integrationsQuery = useQuery({
    queryKey: ["integrations"],
    queryFn: integrationsPayload,
    enabled: canWrite,
  });
  const payload = integrationsQuery.data ?? EMPTY;

  // Item 42: enough to know whether this network has any cameras at all.
  const camerasQuery = useQuery({ queryKey: ["overview"], queryFn: overviewPayload });
  const recordersQuery = useQuery({ queryKey: ["recorders"], queryFn: recordersPayload });

  // Fetched even while the section is shut: the count is the whole point of a
  // quiet section -- "2 active" is what tells an admin whether to open it. The
  // panel below shares this exact query key, so opening it costs no second
  // request.
  const tokensQuery = useApiTokens(canWrite);
  const liveTokens = liveTokenCount(tokensQuery.data?.tokens ?? []);
  const [apiOpen, setApiOpen] = useState(false);

  const routerQuery = useQuery({
    queryKey: ["router"],
    queryFn: routerCard,
    enabled: canWrite,
  });
  const router = routerQuery.data ?? EMPTY_ROUTER;

  const connected = payload.integrations.filter((i) => i.configured).length;
  // A channel that holds credentials and last failed its own send: the one
  // figure of the three that is about something being wrong.
  const failingCards = payload.integrations.filter((i) => i.configured && i.last_error !== "");
  const failing = failingCards.length;

  const [savingKind, setSavingKind] = useState<string | null>(null);
  const [testingKind, setTestingKind] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestOutcome>>({});
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingExpires, setPairingExpires] = useState<string | null>(null);
  const [pairingExpired, setPairingExpired] = useState(false);
  const [pairingActive, setPairingActive] = useState(false);
  // Bumped once per successful save, per kind — the card view watches its
  // own kind's tick to re-mask a secret it just replaced. Not read for
  // anything else (see IntegrationCardView's `saveOkTick` prop doc).
  const [saveOkTicks, setSaveOkTicks] = useState<Record<string, number>>({});
  // Disconnect is destructive (it deletes the stored credentials), so it
  // goes through a named confirmation rather than firing on the first
  // click — this just remembers which card asked for one.
  const [disconnectTarget, setDisconnectTarget] = useState<{ kind: string; title: string } | null>(null);
  const [routerRemoveOpen, setRouterRemoveOpen] = useState(false);
  const [routerTestPolling, setRouterTestPolling] = useState(false);
  // The disable-remote-admin flow: a mint holds the tier-3 token + the summary
  // to show, a ConfirmDialog gates the burn, and the outcome is polled off the
  // queue (the sensor runs it asynchronously, like the Test).
  const [disableConfirm, setDisableConfirm] = useState<{ token: string; summary: string } | null>(null);
  const [routerDisabling, setRouterDisabling] = useState(false);
  // The card's `last_test` timestamp captured when a Test was fired: the poll
  // stops as soon as the value changes (a fresh result landed), or after the
  // attempt cap. A ref, not state, so bumping it never re-runs the effect.
  const routerTestBaseline = useRef<string>("");
  const routerTestAttempts = useRef<number>(0);

  const saveMutation = useMutation({
    mutationFn: ({ kind, body }: { kind: string; body: IntegrationSaveBody }) => saveIntegration(kind, body),
    onMutate: ({ kind }) => setSavingKind(kind),
    onSuccess: (_ack, { kind, body }) => {
      setSavingKind(null);
      queryClient.setQueryData<IntegrationsPayload>(["integrations"], (prev) =>
        prev
          ? {
              ...prev,
              integrations: prev.integrations.map((c) =>
                c.kind === kind
                  ? {
                      ...c,
                      enabled: body.enabled,
                      routes: body.routes,
                      configured: c.configured || Object.keys(body.fields).length > 0,
                    }
                  : c,
              ),
            }
          : prev,
      );
      setSaveOkTicks((prev) => ({ ...prev, [kind]: (prev[kind] ?? 0) + 1 }));
      toast.show(`${kind} saved.`, "good");
    },
    onError: (err: Error) => {
      setSavingKind(null);
      toast.show(err.message, "alert");
    },
  });

  const testMutation = useMutation({
    mutationFn: (kind: string) => testIntegration(kind),
    onMutate: (kind) => setTestingKind(kind),
    onSuccess: (ack, kind) => {
      const result: TestOutcome = {
        ok: Boolean(ack.ok),
        error: String(ack.error ?? ""),
        // What a successful test found (the PSA cards say which id each
        // typed name resolved to); most providers send nothing here.
        detail: ack.detail ? String(ack.detail) : "",
      };
      setTestingKind(null);
      setTestResults((prev) => ({ ...prev, [kind]: result }));
      queryClient.setQueryData<IntegrationsPayload>(["integrations"], (prev) =>
        prev
          ? {
              ...prev,
              integrations: prev.integrations.map((c) =>
                c.kind === kind ? { ...c, last_test: new Date().toISOString(), last_error: result.error } : c,
              ),
            }
          : prev,
      );
    },
    onError: (err: Error) => {
      setTestingKind(null);
      toast.show(err.message, "alert");
    },
  });

  // The hub deletes the row outright — there is no partial-wipe endpoint —
  // so the reset shape (unconfigured, disabled, blank fields) is whatever
  // the registry's own default card looks like. Rather than guess that
  // shape here, just refetch: `GET /api/v1/integrations` builds a fresh
  // default card for a kind with no row (see `routers/integrations.py`'s
  // `_card`), so the refetched payload is already correct.
  const deleteMutation = useMutation({
    mutationFn: (kind: string) => deleteIntegration(kind),
    onSuccess: (_ack, kind) => {
      const title = payload.integrations.find((c) => c.kind === kind)?.title ?? kind;
      toast.show(`${title} disconnected.`, "good");
      queryClient.invalidateQueries({ queryKey: ["integrations"] });
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const pairMutation = useMutation({
    mutationFn: pairTelegram,
    onMutate: () => setPairingExpired(false),
    onSuccess: (ack) => {
      setPairingCode(String(ack.code ?? ""));
      setPairingExpires(ack.expires ? String(ack.expires) : null);
      setPairingActive(true);
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const routerSaveMutation = useMutation({
    mutationFn: (body: RouterSaveBody) => saveRouter(body),
    onSuccess: (card) => {
      queryClient.setQueryData(["router"], card);
      toast.show("Router saved.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const routerTestMutation = useMutation({
    mutationFn: testRouter,
    onSuccess: () => {
      // The sensor answers the queued Test asynchronously. Hold the pending
      // state on the client (never persisted to the row) and poll for a fresh
      // result, bounded so an offline sensor can't strand the button.
      routerTestBaseline.current = router.last_test;
      routerTestAttempts.current = 0;
      setRouterTestPolling(true);
      toast.show("Testing the router login…", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const routerDeleteMutation = useMutation({
    mutationFn: deleteRouter,
    onSuccess: () => {
      queryClient.setQueryData(["router"], EMPTY_ROUTER);
      toast.show("Router removed.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  // Step 1: mint the tier-3 confirmation and open the dialog with its summary.
  const routerDisableConfirmMutation = useMutation({
    mutationFn: disableRemoteAdminConfirm,
    onSuccess: (conf) => setDisableConfirm({ token: conf.token, summary: conf.summary }),
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  // Step 2: burn the token, then poll the queue for the sensor's result.
  const routerDisableMutation = useMutation({
    mutationFn: (token: string) => disableRemoteAdmin(token),
    onSuccess: (ack) => {
      const qid = String(ack.id ?? "");
      if (!qid) {
        setRouterDisabling(false);
        toast.show("Remote administration change was queued.", "good");
        return;
      }
      let attempts = 0;
      let poll = 0;
      const check = () => {
        attempts += 1;
        const gaveUp = attempts > ROUTER_TEST_MAX_POLLS;
        queueStatus(qid)
          .then((q) => {
            const done = q.status === "done" || q.status === "failed";
            if (done || gaveUp) {
              if (poll) window.clearInterval(poll);
              setRouterDisabling(false);
              const out = String((q.result as { out?: string } | null)?.out ?? "");
              if (q.status === "done") {
                // Re-read the card: the hub has just stamped
                // `remote_admin_off_at` from this very result, and without
                // this the panel keeps offering the action it just completed
                // until the next full reload.
                queryClient.invalidateQueries({ queryKey: ["router"] });
                toast.show(out || "Remote administration turned off.", "good");
              } else if (q.status === "failed") {
                toast.show(out || "Could not turn remote administration off.", "alert");
              } else {
                toast.show(
                  "No result yet. The on-site sensor may be offline — it runs when it next checks in.",
                  "alert",
                );
              }
            }
          })
          .catch(() => {
            if (gaveUp && poll) {
              window.clearInterval(poll);
              setRouterDisabling(false);
            }
          });
      };
      // Check straight away (the sensor may already be done), then on a cadence.
      check();
      poll = window.setInterval(check, ROUTER_TEST_POLL_MS);
    },
    onError: (err: Error) => {
      setRouterDisabling(false);
      toast.show(err.message, "alert");
    },
  });

  // Poll `["integrations"]` until Telegram's `configured` flips true (the
  // hub sets it once `/start <code>` binds a `chat_id`), then stop. This is
  // the one place the tab refetches on its own initiative rather than on a
  // user action — everywhere else follows the "silent refresh, not a poll
  // loop" rule, but pairing has no other signal that the bot side finished.
  // The code itself also expires (the hub mints it with a short TTL) —
  // once that passes with no pairing, stop polling and tell the admin to
  // mint a new one rather than polling forever against a dead code.
  useEffect(() => {
    if (!pairingActive) return;
    const interval = window.setInterval(() => {
      if (pairingExpires && Date.now() > new Date(pairingExpires).getTime()) {
        setPairingActive(false);
        setPairingExpired(true);
        return;
      }
      integrationsPayload()
        .then((fresh) => {
          queryClient.setQueryData(["integrations"], fresh);
          const tg = fresh.integrations.find((i) => i.kind === "telegram");
          if (tg?.configured) setPairingActive(false);
        })
        .catch(() => {
          // A transient failure just tries again on the next tick.
        });
    }, PAIR_POLL_MS);
    return () => window.clearInterval(interval);
  }, [pairingActive, pairingExpires, queryClient]);

  // A running router Test resolves on the sensor, not in this request — poll
  // the card until a fresh result lands (its `last_test` timestamp changes),
  // then stop. Bounded by an attempt cap so an offline or unenrolled sensor
  // makes the poll give up and re-enable the button, never a standing loop.
  useEffect(() => {
    if (!routerTestPolling) return;
    const interval = window.setInterval(() => {
      routerTestAttempts.current += 1;
      const gaveUp = routerTestAttempts.current > ROUTER_TEST_MAX_POLLS;
      routerCard()
        .then((fresh) => {
          const landed = fresh.last_test !== routerTestBaseline.current;
          if (landed) queryClient.setQueryData(["router"], fresh);
          if (landed || gaveUp) {
            setRouterTestPolling(false);
            if (!landed && gaveUp) {
              toast.show(
                "No result yet. The on-site sensor may be offline — the test runs when it next checks in.",
                "alert",
              );
            }
          }
        })
        .catch(() => {
          // transient — try again next tick, still bounded by the cap
          if (gaveUp) setRouterTestPolling(false);
        });
    }, ROUTER_TEST_POLL_MS);
    return () => window.clearInterval(interval);
  }, [routerTestPolling, queryClient, toast]);

  // Connected channels that answer something. `commands` is only populated
  // for a configured card (see routers/integrations.py), so this is already
  // "what is actually live for this tenant".
  // `?? []` is not defensive clutter: a browser can hold a bundle older or
  // newer than the hub it is talking to for the length of a deploy, and a
  // page that throws on a field the other side has not started sending yet
  // takes the whole tab down rather than degrading.
  const talkative = payload.integrations.filter((c) => (c.commands ?? []).length > 0);
  // Cameras only matter on a network that has some. `?? 0` rather than a
  // guard: a browser can hold a bundle older than the hub for the length of
  // a deploy, and a missing field should hide the card, never throw.
  const cameras = camerasQuery.data?.cameras ?? { known: 0 };
  const recorderCount = recordersQuery.data?.recorders.length ?? 0;

  const intStats: StatItem[] = [
    { label: "Connected", value: String(connected), deltaTone: connected > 0 ? "good" : undefined },
    { label: "Available", value: String(payload.integrations.length) },
    { label: "Answering commands", value: String(talkative.length) },
    { label: "Last test failed", value: String(failing), deltaTone: failing > 0 ? "alert" : undefined },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Account · how Vault reaches you"
        title="Integrations"
        sub="The channels Vault reaches you through, and the devices on your network it can act on. Admin-only, because these cards hold credentials."
      />
      {canWrite && <StatStrip items={intStats} />}
    <section className="g-integrations">
      <header className="g-integrations-head">
        {!canWrite && (
          <p className="g-settings-role-notice">
            Integrations is an admin-only area — signed in as {role ?? "viewer"}. These channels hold
            credentials, so they are not readable at your role. Ask an admin to add, test or change a
            channel, or to grant you the admin role.
          </p>
        )}
      </header>

      {/* The tab, its heading and this explanation render for every role: a
          surface never disappears from the nav because of a role, only its
          data does. */}
      {!canWrite ? null : integrationsQuery.isLoading ? (
        <p className="hint">Loading integrations…</p>
      ) : (
        <>
          <section className="g-int-section" aria-labelledby="g-int-channels-h">
            <div className="g-int-section-head">
              <div className="g-int-section-heading">
                <h2 id="g-int-channels-h">Notification channels</h2>
                <p>Where Vault reaches you. Each channel chooses which severities it delivers.</p>
              </div>
              <div className="g-int-section-meta" data-testid="integration-kpis">
                <Stat label="Connected" value={connected} tone="plain" mono />
                <Stat label="Available" value={payload.integrations.length} tone="plain" mono />
                <Stat
                  label="Needs attention"
                  value={failing}
                  tone={failing > 0 ? "warn" : "plain"}
                  mono
                />
              </div>
            </div>

            {/* Names the broken channel(s) up front, next to the count that
                already said how many — so the answer to "is anything broken?"
                doesn't require scanning nine cards to spell out. */}
            {failing > 0 && (
              <p className="g-integrations-attention-summary" role="status">
                Needs attention: {failingCards.map((c) => c.title).join(", ")}.
              </p>
            )}

            <div className="g-integrations-list">
              {/* Connected channels first, then the ones needing attention,
                  then the rest -- what is live sits at the top of the page. */}
              {[...payload.integrations]
                .sort((a, b) => Number(b.configured) - Number(a.configured))
                .map((card) => (
                  <IntegrationCardView
                    key={card.kind}
                    card={card}
                    role={role}
                    saving={savingKind === card.kind}
                    onSave={(kind, body) => saveMutation.mutate({ kind, body })}
                    testing={testingKind === card.kind}
                    onTest={(kind) => testMutation.mutate(kind)}
                    testResult={testResults[card.kind] ?? null}
                    onDisconnect={
                      card.configured && card.kind !== "ntfy"
                        ? () => setDisconnectTarget({ kind: card.kind, title: card.title })
                        : undefined
                    }
                    disconnecting={deleteMutation.isPending && deleteMutation.variables === card.kind}
                    pairingCode={card.kind === "telegram" ? pairingCode : null}
                    pairingExpired={card.kind === "telegram" && pairingExpired}
                    onPair={card.kind === "telegram" ? () => pairMutation.mutate() : undefined}
                    pairing={card.kind === "telegram" && pairMutation.isPending}
                    saveOkTick={saveOkTicks[card.kind] ?? 0}
                  />
                ))}
            </div>

            {/* What you can SAY to the channels you have connected.
                Vault's two-way channels answer real commands and almost
                nobody knows it, because the only place it was written down
                was a field label and a docstring.

                Deliberately only for CONNECTED channels: a tenant has nine
                cards and typically one or two set up, so listing every
                provider's commands would be a catalogue of things that do
                not answer. The lines come from the provider class that
                implements the handler, so the page cannot advertise a
                command the code ignores. */}
            {talkative.length > 0 && (
              <div className="g-int-commands" data-testid="channel-commands">
                <h3>Once connected, you can talk back</h3>
                <p className="hint">
                  These channels take instructions as well as alerts. Same engine as Ask Vault,
                  same role gate, and a change still has to be confirmed before it runs.
                </p>
                <div className="g-int-commands-grid">
                  {talkative.map((card) => (
                    <div key={card.kind} className="g-int-commands-card">
                      <h4>{card.title}</h4>
                      <ul>
                        {(card.commands ?? []).map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="g-int-section" aria-labelledby="g-int-devices-h">
            <div className="g-int-section-head">
              <div className="g-int-section-heading">
                <h2 id="g-int-devices-h">Devices on your network</h2>
                <p>
                  What Vault can act on. These hold a credential your on-site sensor uses — the hub
                  never reaches your LAN directly.
                </p>
              </div>
            </div>

            <div className="g-int-devices-grid">
              <RouterCard
                card={router}
                saving={routerSaveMutation.isPending}
                testing={routerTestMutation.isPending || routerTestPolling}
                disabling={routerDisableConfirmMutation.isPending || routerDisabling}
                onSave={(body) => routerSaveMutation.mutate(body)}
                onTest={() => routerTestMutation.mutate()}
                onRemove={() => setRouterRemoveOpen(true)}
                onDisableRemoteAdmin={() => routerDisableConfirmMutation.mutate()}
              />

              {/* Item 42: a network with no cameras and no recorder gets no
                  camera card. A section that only ever says "there is nothing
                  here" teaches a client to skip the region where something
                  would appear. */}
              {(cameras.known > 0 || recorderCount > 0) && (
              <article className="g-int-pointer-card">
                <h3>Cameras &amp; recorders</h3>
                <p>
                  A camera recorder (NVR/DVR) is a device Vault reads over its own API to enumerate
                  the cameras behind it. Those live on their own surface.
                </p>
                <Link className="g-int-pointer-link" to="/settings/recorders">
                  Manage recorders →
                </Link>
              </article>
              )}
            </div>
          </section>

          {payload.ai && (
            <section className="g-int-section" aria-labelledby="g-int-ai-h" data-testid="ai-failover">
              <div className="g-int-section-head">
                <div className="g-int-section-heading">
                  <h2 id="g-int-ai-h">AI failover</h2>
                  <p>
                    How Vault keeps answering when a model is down or rate-limited. It walks the
                    ladder in order; within Groq, a model that hits its limit hands the turn to the
                    next one. Keys live on the hub and are never shown here.
                  </p>
                </div>
                <div className="g-int-section-meta">
                  <Stat label="Providers ready" value={payload.ai.ladder.filter((r) => r.configured).length} tone="plain" mono />
                  <Stat
                    label="Models parked"
                    value={payload.ai.models.filter((m) => m.cooling_s > 0).length}
                    tone={payload.ai.models.some((m) => m.cooling_s > 0) ? "warn" : "plain"}
                    mono
                  />
                </div>
              </div>

              <ol className="g-ai-ladder" aria-label="Provider order">
                {payload.ai.ladder.map((rung, i) => (
                  <li key={rung.name} className="g-ai-rung" data-configured={rung.configured}>
                    <span className="g-ai-rung-n" aria-hidden="true">{i + 1}</span>
                    <div className="g-ai-rung-body">
                      <strong>{rung.name === "rules" ? "Built-in rules" : rung.name === "groq" ? "Groq" : "Anthropic"}</strong>
                      <span>{rung.note}</span>
                      {/* The condition that moves you down a rung, said
                          between the rungs rather than once at the top: the
                          ladder is a sequence, and a reader scanning it
                          should not have to hold the rule in their head. */}
                      {i < (payload.ai?.ladder.length ?? 0) - 1 && (
                        <span className="g-ai-rung-next">if it fails or is rate-limited</span>
                      )}
                    </div>
                    <Badge tone={rung.configured ? "good" : "plain"}>{rung.configured ? "ready" : "not set up"}</Badge>
                  </li>
                ))}
              </ol>

              {/* Narrow on purpose. What is actually guaranteed is that a
                  CONFIRMATION is single-use, so a change you approved cannot
                  be made twice by a retry. Saying anything broader would be
                  claiming something the ladder does not promise. */}
              <p className="g-ai-note">
                A change you approved runs once. The confirmation is single-use, so if a provider
                drops the turn and the next one picks it up, it cannot repeat something already done.
              </p>

              <div className="g-ai-models-wrap">
                <table className="g-ai-models">
                  <thead>
                    <tr>
                      <th scope="col">Groq model</th>
                      <th scope="col">Role</th>
                      <th scope="col">Answers you</th>
                      <th scope="col">Per minute</th>
                      <th scope="col">Per day</th>
                      <th scope="col">Now</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payload.ai.models.map((mdl) => (
                      <tr key={mdl.model} data-in-pool={mdl.in_pool}>
                        <td>
                          <code>{mdl.model}</code>
                          {mdl.preferred === "first" && <span className="g-ai-pref"> first choice</span>}
                          {mdl.preferred === "tools" && <span className="g-ai-pref"> for actions</span>}
                        </td>
                        <td>{mdl.role === "chat" ? "chat" : mdl.role === "guard" ? "prompt guard" : mdl.role === "moderation" ? "moderation" : "Arabic chat"}</td>
                        <td>{mdl.in_pool ? "yes" : "no"}</td>
                        <td>{mdl.rpm} req · {mdl.tpm.toLocaleString()} tok</td>
                        <td>{mdl.rpd?.toLocaleString() ?? "—"} req · {mdl.tpd?.toLocaleString() ?? "no limit"} tok</td>
                        <td>
                          {mdl.cooling_s > 0 ? (
                            <Badge tone="warn">parked {Math.ceil(mdl.cooling_s)}s</Badge>
                          ) : mdl.in_pool ? (
                            <Badge tone="good">ready</Badge>
                          ) : (
                            <Badge tone="plain">not used</Badge>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {/* Kept shut by default, and mounted only when opened.
            *
            * This page is deliberately visible in simple mode -- "set up my
            * notifications" is a first-week task -- but minting a long-lived
            * credential is not, and most accounts never need one. A permanently
            * open Create-token form on a first-week page is an invitation to
            * make keys nobody asked for. The count answers "is anything using
            * the API?" without the form being the loudest thing here.
            *
            * The panel is the same component the Settings page mounts. Two
            * entrances, one implementation. */}
          <section className="g-int-section" aria-labelledby="g-int-api-h" data-testid="api-access">
            <div className="g-int-section-head">
              <div className="g-int-section-heading">
                <h2 id="g-int-api-h">API access</h2>
                <p>
                  Tokens your own code uses to read Vault and act on alerts. Most accounts
                  never need one.
                </p>
              </div>
              <div className="g-int-section-meta">
                <Stat label="Active" value={liveTokens} tone="plain" mono />
              </div>
            </div>

            <button
              type="button"
              className="g-int-api-toggle"
              aria-expanded={apiOpen}
              aria-controls="g-int-api-panel"
              onClick={() => setApiOpen((v) => !v)}
            >
              {apiOpen ? "Hide tokens" : "Manage tokens"}
            </button>

            {apiOpen && (
              <div id="g-int-api-panel">
                <ApiTokenPanel chrome="bare" />
              </div>
            )}
          </section>

          {/* The end of a page about credentials and third-party services is
              exactly where somebody discovers theirs will not connect. One
              line, pointing at the ticket system rather than at an inbox:
              a thread keeps the history where both sides can read it. */}
          <p className="g-int-help-out">
            Something here refusing to connect? <Link to="/support">Open a support ticket</Link> and
            Vault&rsquo;s own team picks it up.
          </p>
        </>
      )}

      <ConfirmDialog
        open={disconnectTarget !== null}
        title={`Disconnect ${disconnectTarget?.title ?? "this integration"}?`}
        body={`This removes ${disconnectTarget?.title ?? "the"} from Integrations along with its saved credentials. Alerts stop going there until you reconnect it.`}
        confirmLabel="Disconnect"
        onCancel={() => setDisconnectTarget(null)}
        onConfirm={() => {
          if (disconnectTarget) deleteMutation.mutate(disconnectTarget.kind);
          setDisconnectTarget(null);
        }}
      />

      <ConfirmDialog
        open={routerRemoveOpen}
        title="Remove this router?"
        body="This deletes the stored router address and admin password from Vault. It does not change anything on the router itself."
        confirmLabel="Remove"
        onCancel={() => setRouterRemoveOpen(false)}
        onConfirm={() => {
          routerDeleteMutation.mutate();
          setRouterRemoveOpen(false);
        }}
      />

      <ConfirmDialog
        open={disableConfirm !== null}
        title="Turn off remote administration?"
        body={disableConfirm?.summary ?? ""}
        confirmLabel="Turn it off"
        onCancel={() => setDisableConfirm(null)}
        onConfirm={() => {
          if (disableConfirm) {
            setRouterDisabling(true);
            routerDisableMutation.mutate(disableConfirm.token);
          }
          setDisableConfirm(null);
        }}
      />
    </section>
    </Page>
  );
}

export default Integrations;
