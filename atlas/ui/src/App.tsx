import {
  Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type JSX,
} from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, normaliseHealth } from "./api";
import { useFresh } from "./hooks/useFresh";
import { useStream } from "./hooks/useStream";
import { useHand, toggleHandTracking, type HandState } from "./hooks/useHand";
import { HandCursor } from "./components/HandCursor";
import { Login } from "./components/Login";
import { Activity } from "./pages/Activity";
import { Agency } from "./pages/Agency";
import { Calendar } from "./pages/Calendar";
import { Companion } from "./pages/Companion";
import { Conversations } from "./pages/Conversations";
import { Core } from "./pages/Core";
import { Knowledge } from "./pages/Knowledge";
import { Leads } from "./pages/Leads";
import { Marketing } from "./pages/Marketing";
import { Memory } from "./pages/Memory";
import { Mission } from "./pages/Mission";
import { Pocket } from "./pages/Pocket";
import { Reports } from "./pages/Reports";
import { Status } from "./pages/Status";
import { Tasks } from "./pages/Tasks";
import { Tools } from "./pages/Tools";
import { Workflows } from "./pages/Workflows";
import { useVoice } from "./voice/useVoice";
import { MicBadge } from "./voice/VoiceOverlay";
import { handBadgeTitle } from "./voice/detectorCore";
import { owedAfter, planFocus } from "./voice/focus";
import type { GestureEvent } from "./voice/gestures";
import { Mark } from "./components/Mark";
import { AcademyFooter, AcademyHeader } from "./components/Shell";
import { CommandCenter, CommandFooter, CommandHeader } from "./pages/CommandCenter";
import { runningAgents } from "./pages/commandModel";

// Lazy: the wake detector and its WASM must cost nothing on a page that is
// only being read.
const VoiceOverlay = lazy(() =>
  import("./voice/VoiceOverlay").then((m) => ({ default: m.VoiceOverlay })));
// Lazy too: three.js and the hand model's runtime load only on this page.
const AirGlass = lazy(() =>
  import("./pages/AirGlass").then((m) => ({ default: m.AirGlass })));

// Every screen, in the order they are used rather than the order they were
// built. The nav shows Home and puts the rest under "More" (2026-10-06:
// Home at / and /agency is the one page; the Command Center moved to
// /command and the Agency room to /agency/room). Swipe still walks this
// list in order. The agents map lives on Home; the old Agents tab paths
// (/fleet, /agents) still land on it.
const NAV = [
  { to: "/", label: "Home", icon: "◈", end: true },
  { to: "/command", label: "Command Center", icon: "◈" },
  { to: "/core", label: "AI Core", icon: "◎" },
  { to: "/agency/room", label: "Agency room", icon: "⌘" },
  { to: "/tasks", label: "Tasks", icon: "☑" },
  { to: "/calendar", label: "Calendar", icon: "▦" },
  { to: "/memory", label: "Memory", icon: "⊚" },
  { to: "/conversations", label: "Conversations", icon: "💬" },
  { to: "/knowledge", label: "Knowledge Base", icon: "▤" },
  { to: "/tools", label: "Tools & Skills", icon: "⚙" },
  // Air Glass sits with the tools, but out of the hand-swipe ring (SWIPE
  // below): it takes the camera, so a swipe into it could not swipe out.
  { to: "/airglass", label: "Air Glass", icon: "✋" },
  { to: "/workflows", label: "Workflows", icon: "▷" },
  { to: "/marketing", label: "Marketing", icon: "◭" },
] as const;

// The three older screens. Still reachable, still whole — moved out of the
// first eleven so the sidebar reads as the work rather than as the archive.
const MORE = [
  { to: "/status", label: "Status", icon: "◉" },
  { to: "/activity", label: "Activity", icon: "≡" },
  { to: "/reports", label: "Reports", icon: "▥" },
  { to: "/leads", label: "Leads", icon: "⚑" },
] as const;

const SWIPE = NAV.filter((item) => item.to !== "/airglass");

const STALE_FEED_MS = 60_000;

/** Open-Meteo stamps a reading `2026-09-24T11:00`. The bar has room for the
 *  time, and the full stamp stays in the title — but the time is the part
 *  that answers "is this current?", so it is the part shown. */
function observedClock(stamp: string): string {
  const [, clock] = stamp.split("T");
  return clock ? clock.slice(0, 5) : stamp;
}

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
  const [search, setSearch] = useState("");
  // Not a clock: this exists only so the network cell stops saying
  // "Excellent" on its own once the last event ages past a minute. Five
  // seconds is well inside that boundary and a twelfth of the re-renders.
  const [now, setNow] = useState(() => new Date());
  const location = useLocation();
  const navigate = useNavigate();
  // Only the Command Center reads `search`. A box that accepts typing and
  // changes nothing is worse than no box, and Tools already has its own.
  const onMission = location.pathname === "/command";
  // Home is drawn at both paths; it is the stage for the figure there.
  const onHome = location.pathname === "/" || location.pathname === "/agency";

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

  const navIndex = useMemo(
    () => Math.max(0, SWIPE.findIndex((item) => item.to === location.pathname)),
    [location.pathname],
  );

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
      const delta = event.dir === "left" ? 1 : -1;
      const next = SWIPE[(navIndex + delta + SWIPE.length) % SWIPE.length];
      if (next) navigate(next.to);
    } else if (event.kind === "dismiss") {
      voice.dismiss();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navIndex, navigate]);

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
  // and the indicator still says so rather than quietly looking fine.
  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setInterval(refresh, 30_000);
    return (): void => window.clearInterval(timer);
  }, [session?.authenticated, refresh]);

  const overview = useFresh(
    () => (session?.authenticated ? api.overview() : Promise.resolve(null)),
    [reloadKey, session?.authenticated],
  );
  const counters = useFresh(
    () => (session?.authenticated ? api.status() : Promise.resolve(null)),
    [reloadKey, session?.authenticated],
  );
  const ambient = useFresh(
    () => (session?.authenticated ? api.ambient() : Promise.resolve(null)),
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
        <Mark size={44} />
        <span className="brand">ATLAS</span>
        <p>Connecting…</p>
      </div>,
    );
  }
  if (!session.authenticated) {
    return gate(<Login configured={session.configured} onSignedIn={loadSession} />);
  }

  // A failed refresh leaves the old read in `overview.data`; the pill must
  // not keep saying "All clear" from it.
  const watched = (overview.error ? [] : overview.data?.modules ?? [])
    .filter((m) => m.enabled && !m.setup);
  const rank: Record<string, number> = { ok: 0, unknown: 1, degraded: 2, failing: 3 };
  const worst = watched.reduce<string>((acc, m) => {
    const h = normaliseHealth(m.health);
    return (rank[h] ?? 1) > (rank[acc] ?? 0) ? h : acc;
  }, watched.length ? "ok" : "unknown");

  // "System failing" was read as "ATLAS is broken" -- it never meant that.
  // ATLAS was working perfectly; two of the things it watches were failing,
  // which is the pill doing its job. The word "system" was the problem: this
  // pill describes the estate, not the software drawing it. Name what is
  // wrong and how many, so it cannot be mistaken for a self-report.
  const hurt = watched.filter((m) => {
    const h = normaliseHealth(m.health);
    return h === "failing" || h === "degraded";
  });
  const blind = watched.filter((m) => normaliseHealth(m.health) === "unknown");
  const pill =
    worst === "ok" ? "All clear"
      : watched.length === 0 ? "Health unknown"
      : worst === "unknown"
        ? `${blind.length} unmeasured`
        : `${hurt.length} need${hurt.length === 1 ? "s" : ""} attention`;
  const pillWhy =
    worst === "ok" ? "Everything ATLAS watches is healthy"
      : worst === "unknown"
        ? "Nothing could be measured, so nothing is known"
        : `${hurt.map((m) => m.title || m.module).join(", ")} — ATLAS itself is fine`;
  const waiting = counters.error ? null : counters.data?.waiting_on_you ?? null;

  // The feed is only "Excellent" while it is both connected and recent. A
  // socket that is open and silent for a minute is a dead feed wearing a live
  // badge, which is the one thing this indicator exists to prevent.
  const lastEvent = stream.lastEventAt ?? lastFetch;
  const feedFresh = stream.state === "live" && lastEvent !== null
    && now.getTime() - lastEvent.getTime() < STALE_FEED_MS;
  const netWord = feedFresh ? "Excellent"
    : stream.state === "connecting" ? "Connecting" : "Disconnected";

  const weather = ambient.data?.weather ?? null;
  // The one reason the server gave, shortened only where it is the literal
  // "no coordinates" case — everything else is passed through as written,
  // because an unreachable service is a different problem from an unset
  // variable and the bar is the only place either one surfaces.
  const reason = ambient.data?.reason ?? null;
  const weatherWhy = reason === null
    ? "unavailable"
    : reason === "ATLAS_LAT/ATLAS_LON unset"
      ? "not configured"
      : `unreachable — ${reason}`;

  // The "More" menu: every screen but Home, in NAV order, then the archive.
  const more = [...NAV.slice(1), ...MORE];
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

  // Home is ATLAS · Command Center (Miro, 2026-10-06): its own dark chrome,
  // header to talk bar. The other pages keep the Academy shell.
  if (onHome) {
    const handToggle = voice.capabilities?.gestures === true
      ? <HandBadge state={hand.state}
                   pinch={voice.detector !== "pinch" ? "none" : voice.handsFree ? "live" : "muted"} />
      : null;
    return (
      <div className="cmd-shell">
        <CommandHeader items={more} overview={overview} waiting={waiting}
                       feed={stream.state} voice={voice}
                       onSignOut={() => void api.logout().then(loadSession)} />
        <main className="cmd-main">
          <CommandCenter reloadKey={reloadKey} voice={voice} overview={overview}
                         counters={counters} gaze={gaze}
                         agentsRunning={stream.state === "live"
                           ? runningAgents(agentEvents, now.getTime()) : null} />
        </main>
        <CommandFooter
          voice={voice}
          version={version}
          // No check reads the weather (/api/ambient asks Open-Meteo per
          // request), and it is not something to watch: left off this bar.
          weather={null}
          network={netWord} networkOk={feedFresh}
          toggles={<><MicBadge voice={voice} />{handToggle}</>} />
        <Suspense fallback={null}>
          <VoiceOverlay voice={voice} onStage={false} />
        </Suspense>
        <HandCursor state={hand.state} cursor={hand.cursor} />
      </div>
    );
  }

  return (
    <div className="ac-shell">
      <AcademyHeader
        items={more} health={worst} pill={pill} pillWhy={pillWhy}
        feed={stream.state} updated={lastEvent} waiting={waiting}
        onSignOut={() => void api.logout().then(loadSession)}
        search={onMission ? (
          <input className="ac-search" value={search} placeholder="Search the feed"
                 aria-label="Filter the live intelligence feed"
                 onChange={(e) => setSearch(e.target.value)} />
        ) : undefined}
        badges={<>
          {/* Home has its own, larger talk button beside the figure. */}
          {!onHome && (
            <button type="button" className="ac-btn ac-btn-accent ac-btn-sm"
                    onClick={voice.tap} title="Talk to ATLAS">
              Talk
            </button>
          )}
          <MicBadge voice={voice} />
          {voice.capabilities?.gestures === true
            && <HandBadge state={hand.state} lent={airglass}
                          pinch={voice.detector !== "pinch" ? "none"
                            : voice.handsFree ? "live" : "muted"} />}
        </>} />

      <main className={onHome ? "ac-main" : "cc-main hud-zone"}>
        <Routes>
          <Route path="/" element={
            <CommandCenter reloadKey={reloadKey} voice={voice} overview={overview} agentsRunning={null}
                  counters={counters} gaze={gaze} />} />
          <Route path="/command" element={
            <Mission reloadKey={reloadKey} voice={voice} search={search} />} />
          <Route path="/core" element={
            <Core reloadKey={reloadKey} voice={voice}
                  // The same cursor `HandCursor` draws, and on the same
                  // condition -- only once a hand is actually steering it,
                  // never merely while the camera is watching.
                  gaze={gaze} />} />
          <Route path="/tasks" element={<Tasks reloadKey={reloadKey} />} />
          <Route path="/calendar" element={<Calendar reloadKey={reloadKey} />} />
          <Route path="/conversations" element={<Conversations reloadKey={reloadKey} />} />
          <Route path="/knowledge" element={<Knowledge reloadKey={reloadKey} />} />
          <Route path="/tools" element={<Tools reloadKey={reloadKey} />} />
          <Route path="/workflows" element={<Workflows reloadKey={reloadKey} />} />
          <Route path="/marketing" element={<Marketing reloadKey={reloadKey} />} />
          <Route path="/status" element={<Status reloadKey={reloadKey} />} />
          <Route path="/activity" element={<Activity reloadKey={reloadKey} />} />
          <Route path="/reports" element={<Reports reloadKey={reloadKey} />} />
          {/* The agents map is on Home now; old links and a spoken "open the
              fleet" (voice focus /fleet) still land on it. */}
          <Route path="/fleet" element={<Navigate to="/agency" replace />} />
          <Route path="/agents" element={<Navigate to="/agency" replace />} />
          <Route path="/agency" element={
            <CommandCenter reloadKey={reloadKey} voice={voice} overview={overview} agentsRunning={null}
                  counters={counters} gaze={gaze} />} />
          <Route path="/agency/room" element={<Agency reloadKey={reloadKey} />} />
          <Route path="/memory" element={<Memory reloadKey={reloadKey} />} />
          <Route path="/airglass" element={
            <Suspense fallback={<p className="stamp">Loading Air Glass…</p>}>
              <AirGlass pinchWake={pinchWake} />
            </Suspense>} />
          <Route path="/leads" element={<Leads reloadKey={reloadKey} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <AcademyFooter
        location={ambient.data?.location ?? "not set"}
        networkOk={feedFresh} network={netWord}
        weather={
          /* Never a plausible temperature, and never one vague word for every
             way of not having one. "Not configured" and "the service returned
             503" are different facts, and the second one is actionable. */
          weather ? (
            <>
              {`${Math.round(weather.temperature_c)}°C ${weather.description}`}
              {weather.observed_at && (
                <span className="t" title={`Open-Meteo reading taken ${weather.observed_at}`}>
                  {" "}· read {observedClock(weather.observed_at)}
                </span>
              )}
            </>
          ) : <span data-health="unknown">{ambient.data ? weatherWhy : "not read yet"}</span>
        } />

      {/* Above the screens, never instead of them: they stay mounted so the
          live stream and scroll position survive a conversation. */}
      <Suspense fallback={null}>
        <VoiceOverlay voice={voice} onStage={location.pathname === "/core"} />
      </Suspense>
      <HandCursor state={hand.state} cursor={hand.cursor} />
    </div>
  );
}
