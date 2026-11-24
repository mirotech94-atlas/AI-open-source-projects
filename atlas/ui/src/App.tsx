import {
  Suspense, lazy, useCallback, useEffect, useRef, useState, type JSX,
} from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import { ACTIVITY_REFRESH_MS } from "./pages/agentActivityModel";
import { useFresh } from "./hooks/useFresh";
import { useLiveRead } from "./hooks/useLiveRead";
import { WhyContext, WhyPanel } from "./components/WhyPanel";
import { useStream } from "./hooks/useStream";
import { useHand, toggleHandTracking, type HandState } from "./hooks/useHand";
import { HandCursor } from "./components/HandCursor";
import { Login } from "./components/Login";
import { Wordmark } from "./components/Wordmark";
import { Companion } from "./pages/Companion";
import { Pocket } from "./pages/Pocket";
import { useVoice } from "./voice/useVoice";
import { MicBadge } from "./voice/VoiceOverlay";
import { handBadgeTitle } from "./voice/detectorCore";
import { owedAfter, planFocus } from "./voice/focus";
import type { GestureEvent } from "./voice/gestures";
import { AgencyTab } from "./pages/AgencyTab";
import { REDIRECTS, nextTab } from "./nav";
import { waitingCount } from "./pages/alertsModel";
import { CommandCenter, CommandFooter, CommandHeader } from "./pages/CommandCenter";
import { runningAgents } from "./pages/commandModel";

// Lazy: the wake detector and its WASM must cost nothing on a page that is
// only being read.
const VoiceOverlay = lazy(() =>
  import("./voice/VoiceOverlay").then((m) => ({ default: m.VoiceOverlay })));
// Lazy too: three.js and the hand model's runtime load only on this page.
const AirGlass = lazy(() =>
  import("./pages/AirGlass").then((m) => ({ default: m.AirGlass })));

const Hud = lazy(() => import("./pages/Hud").then((m) => ({ default: m.Hud })));

const STALE_FEED_MS = 60_000;

/** off / watching / cursor / grab, read from the device -- the same rule the
 * mic badge follows. `watching` is also the camera-on indicator Task 16's
 * own review asked for: a hand held up is never the only sign the camera is
 * live, this badge is. Clicking it stops the tracks outright -- and since
 * pinch-to-wake shares this same camera (`useHand.ts`), turning the hand off
 * while pinch is the live wake detector silently disables it too -- and so
 * does "Tap to mute" on the mic badge. `pinch` ("live" / "muted" / "none")
 * lets the tooltip (`handBadgeTitle`) say both plainly, and say when
 * pinch-to-wake is muted, rather than leave either as a surprise. */
function HandBadge({ state, pinch, lent = false }: {
  state: HandState;
  pinch: "live" | "muted" | "none";
  /** Air Glass has the camera: the shared hand tracking is paused, not off. */
  lent?: boolean;
}): JSX.Element {
  const label: Record<HandState, string> = {
    off: "Hand off",
    watching: "Watching for a hand",
    cursor: "Hand cursor",
    grab: "Dragging",
  };
  if (lent) {
    return (
      <span className="micbadge handbadge" data-hand="off"
            title="Air Glass has the camera. Hand tracking and pinch-to-wake resume when you leave the page.">
        <span className="micdot" aria-hidden="true" />
        Camera: Air Glass
      </span>
    );
  }
  const title = handBadgeTitle(state !== "off", pinch);
  return (
    <button
      className="micbadge handbadge"
      data-hand={state}
      onClick={toggleHandTracking}
      title={title}
    >
      <span className="micdot" aria-hidden="true" />
      {label[state]}
    </button>
  );
}

export function App(): JSX.Element {
  const [session, setSession] = useState<{
    authenticated: boolean;
    configured: boolean;
  } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);
  // Not a clock: this exists only so the network cell stops saying
  // "Excellent" on its own once the last event ages past a minute. Five
  // seconds is well inside that boundary and a twelfth of the re-renders.
  const [now, setNow] = useState(() => new Date());
  const location = useLocation();
  const navigate = useNavigate();
  const refresh = useCallback(() => {
    setReloadKey((n) => n + 1);
    setLastFetch(new Date());
  }, []);
  // The scheduler runs its due checks together, so events arrive in bursts:
  // one re-read 1.5 s after the last of a burst, not one per event. Each
  // re-read is still a fresh query, and each figure still carries its time.
  const burst = useRef<number | undefined>(undefined);
  // Agent runs as the stream reports them, so the AI core strip can say
  // "Planning" while one is in progress. Only what arrived while connected.
  const [agentEvents, setAgentEvents] = useState<Array<{ agent: string; phase: string; at: number }>>([]);
  const onStreamEvent = useCallback((kind: string, data: Record<string, unknown> | null) => {
    if (kind === "agent" && data && typeof data.agent === "string" && typeof data.phase === "string") {
      const ev = { agent: data.agent, phase: data.phase, at: Date.now() };
      setAgentEvents((prev) => [...prev.slice(-199), ev]);
    }
    if (burst.current !== undefined) window.clearTimeout(burst.current);
    burst.current = window.setTimeout(() => {
      burst.current = undefined;
      refresh();
    }, 1500);
  }, [refresh]);
  useEffect(() => (): void => window.clearTimeout(burst.current), []);
  const stream = useStream(onStreamEvent);
  // Air Glass has its own ears while it is open (it listens all the time and
  // understands words with the hand), so the dashboard's wake detector
  // stands down there -- one microphone owner, like the camera -- and
  // "ATLAS, look" is not answered twice.
  const voice = useVoice(session?.authenticated === true && location.pathname !== "/airglass");

  // The HUD has no tab (Miro, 2026-11-24): it lives in the backend and comes up
  // when ATLAS is called, then the screen goes back to where it was. Navigation
  // only: it reads, it never acts.
  const hudReturn = useRef<string | null>(null);
  useEffect(() => {
    const awake = voice.phase !== "dormant";
    if (awake && hudReturn.current === null && location.pathname !== "/airglass") {
      hudReturn.current = location.pathname;
      if (location.pathname !== "/hud") navigate("/hud");
    } else if (!awake && hudReturn.current !== null) {
      const back = hudReturn.current;
      hudReturn.current = null;
      if (location.pathname === "/hud" && back !== "/hud") navigate(back);
    }
  }, [voice.phase, location.pathname, navigate]);

  // An answer may say where to look (`voice/focus.ts`). A view command or a
  // system check moves the screen now; a spoken status naming a module shows
  // its card beside the answer and moves the screen once the conversation
  // is dismissed. Navigation only: it reads, it never acts.
  //
  // Each focus object is handled exactly once. `navigate` changes identity
  // on every pathname change (BrowserRouter), and the focus outlives the
  // conversation, so without this every later navigation would re-run the
  // effect and send him back to the screen a finished answer pointed at.
  // A fresh object per answer keeps "the same command twice" navigating twice.
  //
  // The navigation owed on close survives later turns that name nothing
  // ("thanks, atlas" included), and is cleared when a new conversation
  // starts (`owedAfter`).
  //
  // Never on the pocket (the menu-bar popover): it has no screens to move
  // to, and a spoken "show me the agents" would put the whole command
  // center into a 380px popover with no way back but a reload.
  const pocket = location.pathname === "/pocket";
  const afterDismiss = useRef<string | null>(null);
  const handledFocus = useRef<typeof voice.focus>(null);
  useEffect(() => {
    if (voice.focus === handledFocus.current) return;
    handledFocus.current = voice.focus;
    if (pocket) {
      afterDismiss.current = null;
      return;
    }
    afterDismiss.current = owedAfter(afterDismiss.current, voice.focus);
    const plan = planFocus(voice.focus);
    if (plan.now) navigate(plan.now);
  }, [voice.focus, navigate, pocket]);
  useEffect(() => {
    if (voice.phase === "waking") afterDismiss.current = null;
    if (voice.phase !== "dormant" || !afterDismiss.current) return;
    const to = afterDismiss.current;
    afterDismiss.current = null;
    navigate(to);
  }, [voice.phase, navigate]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 5000);
    return (): void => window.clearInterval(timer);
  }, []);

  // Swipe moves through the sidebar in NAV order; two-held dismisses whatever
  // the voice overlay is doing. Both come from the SAME hand as pinch-wake
  // above -- `useHand` shares one camera and one landmarker across every
  // consumer on the page, `useVoice`'s pinch detector included.
  //
  // `event.dir` already matches the hand a person is watching move --
  // `useHand` mirrors the raw camera frame before it ever reaches
  // `GestureStream`, so "right" here means a real rightward sweep, not a
  // mirror image of one. The mapping onto NAV is the deliberate half: a
  // rightward swipe goes back (delta -1), a leftward one goes forward
  // (delta +1), the same sense as swiping through a photo strip.
  const onHandEvent = useCallback((event: GestureEvent): void => {
    if (event.kind === "swipe") {
      navigate(nextTab(location.pathname, event.dir));
    } else if (event.kind === "dismiss") {
      voice.dismiss();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, navigate]);

  // The pocket (menu-bar popover) has no screens to swipe through and no
  // room for a hand cursor: it never asks for the camera. Air Glass has the
  // camera to itself (it borrows it through `lendCamera`); the cursor and
  // the swipes stand down there, so a swipe cannot pull the page away.
  const airglass = location.pathname === "/airglass";
  const hand = useHand(
    session?.authenticated === true && voice.capabilities?.gestures === true && !pocket
      && !airglass,
    onHandEvent,
  );

  const loadSession = useCallback((): void => {
    void api
      .session()
      .then((s) => setSession({ authenticated: s.authenticated, configured: s.configured }))
      .catch(() => setSession({ authenticated: false, configured: false }));
  }, []);
  useEffect(loadSession, [loadSession]);

  // A slow poll behind the stream. If SSE drops the screen still refreshes,
  // and the indicator still says so rather than quietly looking fine. It
  // re-reads the session too: there is no sign-out button (Miro, 2026-10-28),
  // so an expired session is how the login gate comes back.
  useEffect(() => {
    if (!session?.authenticated) return;
    // Only a read that SAYS the session is gone shows the gate; a failed read
    // (the box restarting, a dropped network) leaves the page as it is.
    const recheck = (): void => {
      void api.session()
        .then((s) => { if (!s.authenticated) setSession({ authenticated: false, configured: s.configured }); })
        .catch(() => undefined);
    };
    const timer = window.setInterval(() => { refresh(); recheck(); }, ACTIVITY_REFRESH_MS);
    return (): void => window.clearInterval(timer);
  }, [session?.authenticated, refresh]);

  // The pill, the ring and the "Why" list read this: every ~10 s on its own,
  // paused while the tab is hidden, at once on focus, and a failed read shows
  // UNKNOWN "checks unreachable" (never the last good count).
  const overview = useLiveRead(
    () => api.overview(),
    session?.authenticated === true,
    reloadKey,
    "overview",
  );
  const [whyOpen, setWhyOpen] = useState(false);
  const openWhy = useCallback(() => setWhyOpen(true), []);
  const closeWhy = useCallback(() => setWhyOpen(false), []);
  const counters = useFresh(
    () => (session?.authenticated ? api.status() : Promise.resolve(null)),
    [reloadKey, session?.authenticated],
  );
  const approvals = useFresh(
    () => (session?.authenticated ? api.approvals() : Promise.resolve(null)),
    [reloadKey, session?.authenticated],
  );
  // The running build (ATLAS_DEPLOY_SHA, set by ops/deploy/deploy.sh) and
  // when this process started: once per sign-in, it cannot change under it.
  const build = useFresh(
    () => (session?.authenticated ? api.self() : Promise.resolve(null)),
    [session?.authenticated],
  );
  const buildRead = build.error ? null : build.data;
  const version = buildRead?.deploy_sha
    ? {
        text: `${buildRead.deploy_sha.slice(0, 7)}${buildRead.started_at
          ? ` · running since ${new Date(buildRead.started_at).toLocaleString([], {
            day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}`
          : ""}`,
        known: true,
      }
    : { text: build.error ? "build unknown: read failed" : buildRead ? "build not recorded" : "not read yet",
        known: false };

  // In the pocket (the menu-bar popover) the gate is centred in the window.
  const gate = (el: JSX.Element): JSX.Element => (pocket ? <div className="pk-gate">{el}</div> : el);
  if (session === null) {
    return gate(
      <div className="login">
        <p className="login-brand">
          <img src="/favicon.svg" width={44} height={44} alt="" />
          <Wordmark />
        </p>
        <p>Connecting…</p>
      </div>,
    );
  }
  if (!session.authenticated) {
    return gate(<Login configured={session.configured} onSignedIn={loadSession} />);
  }

  // The same cards the Decisions panel lists (retired lanes dropped), so the
  // header and the panel agree; the server's total counts every lane.
  const waiting = approvals.error ? null
    : waitingCount(approvals.data?.proposals ?? null, now.getTime());

  // The feed is only "Excellent" while it is both connected and recent. A
  // socket that is open and silent for a minute is a dead feed wearing a live
  // badge, which is the one thing this indicator exists to prevent.
  const lastEvent = stream.lastEventAt ?? lastFetch;
  const feedFresh = stream.state === "live" && lastEvent !== null
    && now.getTime() - lastEvent.getTime() < STALE_FEED_MS;
  const netWord = feedFresh ? "Excellent"
    : stream.state === "connecting" ? "Connecting" : "Disconnected";

  const gaze = hand.state === "cursor" || hand.state === "grab" ? hand.cursor : null;

  // The companion window (/Applications/ATLAS.app) is the figure and the
  // conversation alone, without the command center's chrome around it.
  if (location.pathname === "/companion") {
    return <Companion voice={voice} reloadKey={reloadKey} />;
  }

  // The pocket (bootstrap/mac/menubar, "ATLAS Menu") is the conversation
  // alone, in a menu-bar popover.
  if (pocket) {
    return <Pocket voice={voice} />;
  }

  // ATLAS Menu's Air Glass window (/airglass?window=1): the page alone, with
  // the room a camera picture needs.
  const pinchWake = voice.detector === "pinch" && voice.handsFree;
  if (airglass && new URLSearchParams(location.search).has("window")) {
    return (
      <Suspense fallback={null}>
        <AirGlass bare pinchWake={pinchWake} />
      </Suspense>
    );
  }

  // The dashboard is three tabs inside one dark shell: Command Center and Agency (the HUD opens when ATLAS is called).
  const handToggle = voice.capabilities?.gestures === true
    ? <HandBadge state={hand.state} lent={airglass}
                 pinch={voice.detector !== "pinch" ? "none" : voice.handsFree ? "live" : "muted"} />
    : null;
  return (
    <WhyContext.Provider value={openWhy}>
    <div className="cmd-shell v2">
      {whyOpen && <WhyPanel overview={overview} onClose={closeWhy} />}
      <CommandHeader overview={overview} waiting={waiting} feed={stream.state} />
      {(approvals.data?.whatsapp_undelivered ?? 0) > 0 && (
        <p className="bn-note" role="status">
          {approvals.data?.whatsapp_undelivered} approval card(s) did not reach your
          WhatsApp. Decide them here.
        </p>
      )}
      <main className="cmd-main">
        <Routes>
          <Route path="/" element={
            <CommandCenter reloadKey={reloadKey} voice={voice} overview={overview}
                           counters={counters} gaze={gaze}
                           agentsRunning={stream.state === "live"
                             ? runningAgents(agentEvents, now.getTime()) : null} />} />
          <Route path="/agency" element={<AgencyTab reloadKey={reloadKey} overview={overview} />} />
          {Object.entries(REDIRECTS).map(([from, to]) => (
            <Route key={from} path={from} element={<Navigate to={to} replace />} />
          ))}
          <Route path="/hud" element={
            <Suspense fallback={<p className="stamp">Loading HUD…</p>}>
              <Hud reloadKey={reloadKey} stream={stream.state} />
            </Suspense>} />
          <Route path="/airglass" element={
            <Suspense fallback={<p className="stamp">Loading Air Glass…</p>}>
              <AirGlass pinchWake={pinchWake} />
            </Suspense>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <CommandFooter
        voice={voice}
        version={version}
        // No check reads the weather and it is not something to watch.
        weather={null}
        network={netWord} networkOk={feedFresh}
        toggles={<><MicBadge voice={voice} />{handToggle}</>} />
      <Suspense fallback={null}>
        <VoiceOverlay voice={voice} onStage={false} />
      </Suspense>
      <HandCursor state={hand.state} cursor={hand.cursor} />
    </div>
    </WhyContext.Provider>
  );
}
