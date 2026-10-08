import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DemoGuide } from "../components/DemoGuide";
import { PersonalView } from "../components/PersonalView";
import { usePreferences } from "../lib/preferences";
import { nextTheme } from "../lib/theme";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { authLogout, me, setActiveTenant } from "../api";
import { useToast } from "../design";
import {
  inboxPayload, livePayload, onboardingState, overviewPayload, requestRun, requestSpeedtest,
  saveOnboarding, startMaintenance, updatesPayload,
} from "../lib/api";
import { roleOf } from "../lib/auth";
import { modLabel, useHotkeys } from "../lib/keys";
import { useLiveFeed } from "../lib/live";
import { audioReady, enableAudio, speak, stopSpeaking } from "../lib/speech";
import { readToolsMode, setToolsMode, subscribeToolsMode } from "../lib/toolsMode";
import type { LiveFeed } from "../lib/live";
import { clearLegacyDismissal, hasLegacyDismissal } from "../lib/onboarding";
import { DnsPrompt } from "./DnsPrompt";
import { useTheme } from "../lib/theme";
import { useTabVisible } from "../lib/visibility";
import type { AlertHandoff, LiveEvent } from "../types";
import { ChatFab } from "./ChatFab";
import { ChatHandoffContext } from "./chat/handoff";
import { ChatPanel } from "./ChatPanel";
import { CommandPalette } from "./CommandPalette";
import type { NavBadges } from "./badges";
import { MobileTabs } from "./MobileTabs";
import { redirectForMode, tabFor } from "./nav";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { Wallboard } from "./Wallboard";
import type { ScanState } from "./TopBar";
import "./shell.css";

const SIDEBAR_KEY = "guardian.sidebar";
/** Open/closed state of the chat popover, per tenant — a superadmin who left
 *  it open on one site should not have it forced open on the next. */
const CHAT_OPEN_KEY = "guardian.chat.open";
/** DOM id the floating button names in `aria-controls`. */
const CHAT_POPOVER_ID = "g-chat-popover";
/** Where the wallboard's "speak new criticals" preference lives — its own
 *  key, deliberately separate from `ChatPanel`'s `SPEAK_PREF_KEY`. They are
 *  two different questions: a wall display in the corridor being told to
 *  announce criticals is not the same decision as this browser reading chat
 *  replies back, and sharing one key would make setting up the display
 *  silently change the desk. */
// The key is exported for the test that proves the preference survives a
// remount; same reason `useLive` above is exported from a component file.
// oxlint-disable-next-line react/only-export-components
export const WALL_SPEAK_KEY = "guardian.wallboard.speak";
/** `/live` is bounded (probe results are cached hub-side for 10s, so a
 *  cache hit costs two cheap DB lookups and nothing else — see
 *  routers/live.py) which is what makes 4s -- the middle of the product's
 *  3-5s "feels live" band -- safe to poll it at. The hub's own ceiling on
 *  `/live` was raised alongside this to 30 calls/min/tenant (routers/live.py
 *  `_RATE_LIMIT`): one tab at 4s uses 15/min, so 30 leaves a full second
 *  tab's worth of headroom on the same tenant, same ratio the old 12/min-at-
 *  15s pairing gave a second tab. */
const LIVE_POLL_MS = 4_000;

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
  } catch {
    // Persistence is a convenience; the in-memory state still toggles.
  }
}

function readWallSpeak(): boolean {
  try {
    return window.localStorage.getItem(WALL_SPEAK_KEY) === "1";
  } catch {
    return false; // private mode / blocked site data: read as off, not broken
  }
}

/** Every path that changes `wallSpeak` comes through here, the click and the
 *  self-disable both. A stored "1" under a speaker icon that has already gone
 *  dark is the preference lying about itself, and it makes the setting look
 *  flaky rather than wrong: restore on, the icon lights, the first critical
 *  cannot play and turns it off, reload, it is lit again. */
function rememberWallSpeak(on: boolean): void {
  try {
    window.localStorage.setItem(WALL_SPEAK_KEY, on ? "1" : "0");
  } catch {
    // Persistence is a convenience; the in-memory state still toggles.
  }
}

/** `fallback` is what a tenant with nothing stored yet inherits — the state
 *  the popover is in right now. Switching sites resets the transcript (the
 *  `key` on `ChatPanel`) but should not slam the panel shut on a user who was
 *  mid-conversation. */
function readChatOpen(tenantId: string, fallback: boolean): boolean {
  try {
    const raw = window.localStorage.getItem(`${CHAT_OPEN_KEY}.${tenantId}`);
    return raw === null ? fallback : raw === "1";
  } catch {
    return fallback;
  }
}

function writeChatOpen(tenantId: string, open: boolean): void {
  try {
    window.localStorage.setItem(`${CHAT_OPEN_KEY}.${tenantId}`, open ? "1" : "0");
  } catch {
    // Persistence is a convenience; the in-memory state still toggles.
  }
}

/** Advanced is the DEFAULT, as it is in the reference (`localStorage
 *  ['nexus.mode'] || 'advanced'`). It has to be: the flag now decides which
 *  nav rows exist, so a browser with nothing stored — every existing user, on
 *  the first load after this change — must keep the whole tree. Absent and
 *  `"0"` are therefore no longer the same answer; only an explicit `"0"`,
 *  written by someone who actually chose simple, means simple. */
/** Exposes the one `useLiveFeed()` instance `Shell` holds to every page
 *  underneath it, so a tab can subscribe to a frame type without opening a
 *  second socket. */
const LiveContext = createContext<LiveFeed | null>(null);

// The context and its hook belong in one file next to `Shell`, which is the
// only thing that ever provides it — same tradeoff as `design/Toast.tsx`.
// oxlint-disable-next-line react/only-export-components
export function useLive(): LiveFeed {
  const feed = useContext(LiveContext);
  if (feed === null) {
    throw new Error("useLive must be used inside <Shell>");
  }
  return feed;
}

/** The current route's tab key, sent to the hub as `ChatRequest.tab` so
 *  Vault's context blob promotes that tab's section to the front. */
function tabKeyFor(pathname: string): string {
  return tabFor(pathname)?.key ?? "overview";
}

export function Shell() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const { theme } = useTheme();
  const preferences = usePreferences();
  const cycleTheme = () => preferences.save({...preferences.value, theme: nextTheme(theme)});

  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [advanced, setAdvanced] = useState(readToolsMode);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [wallboardOpen, setWallboardOpen] = useState(false);
  // The wallboard's speaker. It lives here rather than in `Wallboard` because
  // the speaking happens in the alert subscription below, which already owns
  // the `seenCrit` dedup; a second subscription inside the wallboard would
  // announce every critical twice.
  //
  // Off by default and remembered, the same shape as `ChatPanel`'s speaker
  // (`SPEAK_PREF_KEY` there): two toggles that look alike on the same screen
  // must not answer "does this survive a reload" differently. The case is
  // sharper here than in the panel -- a wallboard is a display somebody sets
  // up once and walks away from, and a kiosk that reboots at 3am would
  // otherwise come back with a dark speaker in an empty room and swallow the
  // next critical.
  //
  // What a restored "on" is and is not: an `AudioContext` created outside a
  // user gesture starts suspended, so reading "1" out of storage records an
  // intention, not a working speaker. Nothing here has resumed the context
  // yet. That is why `openWallboard` calls `enableAudio()` when this is
  // already true -- opening the wallboard is a real click, and on a wall
  // display it is the only gesture the thing reliably gets, so it is the one
  // that has to convert the intention into sound.
  //
  // The irreducible case, so the next reader does not chase it as a bug: a
  // display that reboots and is never touched at all offers no gesture, so
  // the first critical after it is SILENT and flips this toggle back off
  // rather than being announced. It is not recoverable after the fact
  // either -- the alert handler adds the key to `seenCrit` ABOVE the speak
  // gate (it is the toast's dedup too, and replaying toasts on every
  // reconnect would be worse), so re-enabling the speaker later will not
  // read out the critical that turned it off. Persisting is still right: it
  // is the only way the display remembers what it was set to. Do not add a
  // document-level one-shot gesture listener to work around the untouched
  // case.
  const [wallSpeak, setWallSpeak] = useState(readWallSpeak);
  // `wallSpeak` is read directly rather than through a `setWallSpeak`
  // functional updater, matching `ChatPanel.toggleSpeak` (the sibling
  // toggle, same pair of calls): `enableAudio()` and `stopSpeaking()` are
  // side effects, and a functional updater buys stale-closure safety this
  // handler doesn't need (it isn't racing another update to `wallSpeak`
  // between render and click). Running side effects inside an updater relies
  // on them being safe to call more than once — true today (both are
  // idempotent by their own docstrings), but that safety lives in another
  // file and shouldn't be a precondition for this handler being correct.
  // Doing the effects once, here, and only then setting state, removes the
  // question rather than depending on the answer staying true.
  const toggleWallSpeak = useCallback(() => {
    const now = !wallSpeak;
    // `enableAudio` must run inside this handler, not in an effect: the
    // AudioContext gesture rule is about where the call happens, and an
    // effect is not a gesture. See lib/speech.ts.
    if (now && !enableAudio()) return;
    // Turning the speaker off has to cut whatever is playing right now, not
    // just stop new criticals from being read: `wallSpeakRef` (below) is
    // what the alert handler checks, so without this a sentence already in
    // flight finishes over a wall display whose icon has already gone dark
    // -- the same "control lying about what's audible" failure this whole
    // module exists to prevent. `lib/speech.ts`'s module docstring names
    // this call out: "the chat header's speaker toggle and the wallboard's
    // both call this expecting exactly that: once silenced, nothing arrives
    // late." `ChatPanel`'s toggle does the same thing for the same reason.
    if (!now) stopSpeaking();
    rememberWallSpeak(now);
    setWallSpeak(now);
  }, [wallSpeak]);
  const [chatOpen, setChatOpen] = useState(false);
  // The alert hand-off the chat panel should open with, if any. Set by any
  // page under this shell through `ChatHandoffContext` (the alerts list, when a
  // remedy needs the owner) and handed to `ChatPanel`, which seeds it into the
  // transcript. Kept after the panel closes so re-opening still shows the
  // conversation it started.
  const [handoff, setHandoff] = useState<AlertHandoff | null>(null);
  // A question to put to Vault, from the Help section's "Ask Vault about
  // this". The `nonce` is what makes a SECOND click land: `ChatPanel` is keyed
  // on the tenant, so it survives navigating between articles, and a
  // question-only value would dedupe against itself when two articles ask
  // something similar — or when the reader returns to the same article.
  const [ask, setAsk] = useState<{ question: string; nonce: number } | null>(null);
  const [scanState, setScanState] = useState<ScanState>("idle");
  const chatFabRef = useRef<HTMLButtonElement | null>(null);

  const meQuery = useQuery({ queryKey: ["me"], queryFn: me });
  const overviewQuery = useQuery({
    queryKey: ["overview"],
    queryFn: overviewPayload,
    refetchInterval: 10_000,
  });
  /** Whether this hub can hear and speak at all (`OverviewPayload.voice`).
   *  Every voice control on this screen is gated on it, including the
   *  wallboard's announcement below. */
  const voice = overviewQuery.data?.voice ?? false;
  const inboxQuery = useQuery({ queryKey: ["inboxPayload", "alerts"], queryFn: () => inboxPayload("alerts") });
  // Backs the sub-header's latency strip and the maintenance readout. A
  // failure here must never blank the shell, so the strip simply renders
  // nothing until it succeeds. `enabled: tabVisible` matters more now than
  // it did at the old 15s cadence -- at 4s a backgrounded tab left open all
  // day would otherwise burn a meaningful slice of the tenant's `/live`
  // budget for a readout nobody is looking at.
  const tabVisible = useTabVisible();
  const liveQuery = useQuery({
    queryKey: ["live"],
    queryFn: livePayload,
    refetchInterval: LIVE_POLL_MS,
    enabled: tabVisible,
  });
  const updatesQuery = useQuery({ queryKey: ["updates"], queryFn: updatesPayload });

  const switchTenant = useMutation({
    mutationFn: (tenantId: string) => setActiveTenant(tenantId),
    onSuccess: () => {
      void queryClient.invalidateQueries();
    },
  });

  const live = useLiveFeed();

  const tenantId = meQuery.data?.active_tenant?.id ?? null;

  // The first-run wizard fires for every USER who has not been through it —
  // not for every tenant that has no scan yet, which is what this gate used to
  // do. That old reading meant the second person to join an established tenant
  // never saw the wizard, and they are exactly the person who needs it. The
  // hub answers "should this show?" per (user, tenant) and picks the
  // walkthrough (`setup` installs, `tour` orients) — see
  // `hub/guardian_hub/onboarding.py`.
  //
  // `/welcome` is deliberately outside this `<Shell>`'s route tree
  // (`App.tsx`) — the wizard has no sidebar/top bar of its own — so this is a
  // real navigation, not a swap of `<Outlet>`'s content, and it only ever
  // fires once: once `navigate` runs, `Shell` itself unmounts.
  const onboardingQuery = useQuery({
    queryKey: ["onboarding"],
    queryFn: onboardingState,
    enabled: tenantId !== null,
    staleTime: Infinity,
    retry: false,
  });

  // Reconciles the LEGACY per-tenant `localStorage` dismissal into the hub,
  // exactly once per tenant: a tenant that had already skipped the wizard on
  // this browser must not be dragged back into it by the new per-user gate. It
  // is migrated as "skipped" for whoever is signed in now (the only person it
  // can possibly have been) and the key is deleted, so there is one authority
  // afterward rather than two. A second user on the same browser never
  // inherits it — the key is gone by then, and the hub's row is not theirs.
  const migratedFor = useRef<string | null>(null);
  // Set synchronously by the migration effect and read by the redirect effect
  // below it, which is what keeps the two in the right order: the flag is
  // already true by the time the redirect effect runs in the same commit, so
  // the user is never bounced to `/welcome` in the tick before the migrating
  // POST lands. Cleared on either outcome — a failed migration must not
  // suppress the wizard forever.
  const migrating = useRef(false);
  const legacyMigration = useMutation({
    mutationFn: () => saveOnboarding({ skipped: true }),
    onSuccess: (state) => {
      migrating.current = false;
      queryClient.setQueryData(["onboarding"], state);
    },
    onError: () => {
      migrating.current = false;
      void queryClient.invalidateQueries({ queryKey: ["onboarding"] });
    },
  });
  const migrate = legacyMigration.mutate;
  useEffect(() => {
    const state = onboardingQuery.data;
    if (tenantId === null || state === undefined || migratedFor.current === tenantId) return;
    migratedFor.current = tenantId;
    if (!hasLegacyDismissal(tenantId)) return;
    clearLegacyDismissal(tenantId);
    if (!state.show) return;      // the hub already knows; nothing to migrate
    migrating.current = true;
    migrate();
  }, [tenantId, onboardingQuery.data, migrate]);

  // A superadmin is a member of every tenant on the hub (`deps.memberships`),
  // so a per-(user, tenant) gate would walk them through a tour on every site
  // they switch into. They are exempt from the *tour* only: a genuinely
  // unconfigured tenant still gets the installing walkthrough, which is the
  // one that does real work, and Settings → Sensors' "Re-run setup" reaches
  // the tour on demand.
  const isSuperadmin = meQuery.data?.user?.is_superadmin === true;
  useEffect(() => {
    const state = onboardingQuery.data;
    if (state === undefined || !state.show) return;
    if (isSuperadmin && state.path === "tour") return;
    if (migrating.current) return;   // a legacy dismissal is being migrated; see above
    if (location.pathname !== "/welcome") navigate("/welcome", { replace: true });
  }, [onboardingQuery.data, isSuperadmin, tenantId, navigate, location.pathname]);

  // The persisted open/closed state can only be read once the active tenant
  // is known, so it is hydrated in an effect rather than in `useState`'s
  // initialiser — at first render `tenantId` is still `null` and a lazy
  // initialiser would have latched onto the wrong key for the session. The
  // ref makes it once per tenant, so a later toggle is never overwritten.
  const chatHydratedFor = useRef<string | null>(null);
  useEffect(() => {
    if (tenantId === null || chatHydratedFor.current === tenantId) return;
    chatHydratedFor.current = tenantId;
    setChatOpen((prev) => readChatOpen(tenantId, prev));
  }, [tenantId]);

  // Every seen CRIT key gets one toast, ever — a reconnect must not replay
  // toasts for alerts the user already saw before the socket dropped.
  const seenCrit = useRef(new Set<string>());

  // Read through refs rather than adding these to the effect's deps: that
  // effect owns the live-feed subscription, and re-creating it on every
  // toggle would drop any alert that arrived in the gap. `seenCrit` is
  // already a ref for the same reason.
  const wallSpeakRef = useRef(false);
  const wallboardOpenRef = useRef(false);
  const voiceRef = useRef(false);
  useEffect(() => { wallSpeakRef.current = wallSpeak; }, [wallSpeak]);
  useEffect(() => { wallboardOpenRef.current = wallboardOpen; }, [wallboardOpen]);
  useEffect(() => { voiceRef.current = voice; }, [voice]);

  useEffect(() => {
    const offScan = live.on("scan", () => {
      // A finished scan is the end of whatever the button queued.
      setScanState((prev) => (prev === "idle" ? prev : "done"));
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["inboxPayload"] });
      void queryClient.invalidateQueries({ queryKey: ["updates"] });
      void queryClient.invalidateQueries({ queryKey: ["live"] });
    });
    // The owner adjusted this account's plan or trial from the console.
    const offBilling = live.on("billing", () => {
      void queryClient.invalidateQueries({ queryKey: ["billing"] });
    });
    const offAlert = live.on("alert", (event: LiveEvent) => {
      void queryClient.invalidateQueries({ queryKey: ["inboxPayload"] });
      const sev = event.data?.sev;
      const key = typeof event.data?.key === "string" ? event.data.key : event.ts;
      if (sev === "CRIT" && !seenCrit.current.has(key)) {
        seenCrit.current.add(key);
        const msg = typeof event.data?.msg === "string" ? event.data.msg : "New critical alert";
        toast.show(msg, "alert");
        // `voiceRef` as well as the other two: the key can be pulled from the
        // hub at any time, after which the next `/overview` poll drops
        // `voice` to false and the wallboard's speaker button disappears --
        // but `wallSpeak` stays true, so without this check the display keeps
        // making round trips that 503, on a control the customer can no
        // longer see in order to turn it off. `ChatPanel`'s speak effect
        // checks `voice` for the same reason.
        if (voiceRef.current && wallSpeakRef.current && wallboardOpenRef.current) {
          void speak(msg).then((played) => {
            if (!played && !audioReady()) {
              // Persisted as well as set, so the stored preference and the
              // icon agree -- see `readWallSpeak`.
              setWallSpeak(false);
              rememberWallSpeak(false);
            }
          });
        }
      }
    });
    return () => {
      offScan();
      offBilling();
      offAlert();
    };
  }, [live, queryClient, toast]);

  // Clears the transient "Scan finished" label a few seconds after the feed
  // reported it, so the button returns to being an action.
  useEffect(() => {
    if (scanState !== "done") return;
    const id = window.setTimeout(() => setScanState("idle"), 4000);
    return () => window.clearTimeout(id);
  }, [scanState]);

  // F11: a cold load straight into an advanced-only URL — pasted, bookmarked,
  // or typed — must bounce to Overview in simple mode exactly the way
  // toggling out of advanced already does off a route it can no longer show.
  // This runs once, on mount, and never again: `toggleAdvanced` below already
  // owns every subsequent mode flip, and a link or palette hit taken WHILE
  // already in simple mode still has to land on the advanced route it named
  // (`redirectForMode`'s own doc, and the reference's own behaviour) — the
  // gap this closes is only the very first paint of a session that starts
  // simple already sitting on a URL simple mode does not have.
  const coldLoadChecked = useRef(false);
  useEffect(() => {
    if (coldLoadChecked.current) return;
    coldLoadChecked.current = true;
    const to = redirectForMode(location.pathname, location.search, advanced);
    if (to !== null) navigate(to, { replace: true });
    // Deliberately mount-only: `advanced`/`location` are read once here, not
    // tracked, so a later toggle or navigation is untouched by this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Flips the mode, persists it, and — the part the reference does and
   *  Vault did not — moves off a route the new mode cannot show. Fired only
   *  on the toggle, never on same-session navigation: a link or a palette hit
   *  taken while already sitting in simple mode still lands on the advanced
   *  route it named, exactly as the reference's own `toggleMode()` redirect
   *  only ever fires from the toggle itself. A *cold* load of an advanced
   *  URL — a pasted link, a bookmark, a typed address — is a different case,
   *  handled once above by the mount-only effect: that one is indistinguishable
   *  from "switched to simple mode while already here", which already bounces. */
  function toggleAdvanced() {
    setToolsMode(!advanced);
  }

  /* The mode now lives in Settings -> Appearance, which is not in this
   * component's tree, so the change arrives on the window rather than through
   * props. Everything that used to happen inside the toggle happens here
   * instead, so it happens however the mode was changed -- including from
   * another tab. */
  useEffect(() => {
    return subscribeToolsMode((next) => {
      setAdvanced(next);
      const to = redirectForMode(location.pathname, location.search, next);
      if (to !== null) navigate(to);
    });
  }, [location.pathname, location.search, navigate]);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      writeCollapsed(next);
      return next;
    });
  }

  async function handleSignOut() {
    try {
      await authLogout();
    } finally {
      queryClient.clear();
      navigate("/login", { replace: true });
    }
  }

  async function handleRunScan() {
    setScanState("queued");
    try {
      const ack = await requestRun("scan", "");
      toast.show(ack.started === true ? "Scan started" : "Scan queued", "good");
      setScanState(ack.started === true ? "running" : "queued");
      await queryClient.cancelQueries({ queryKey: ["live"] });
      void queryClient.invalidateQueries({ queryKey: ["live"] });
    } catch {
      setScanState("idle");
      toast.show("Could not queue a scan", "alert");
    }
  }

  /** Refresh means "re-read everything visible", not a page reload — silent
   *  refresh is the product rule. */
  async function handleRefresh() {
    await queryClient.cancelQueries();
    await queryClient.invalidateQueries();
  }

  async function handleMaintenance(hours: number) {
    try {
      await startMaintenance(hours);
      toast.show(hours === 0 ? "Maintenance ended" : `Non-critical alerts paused for ${hours}h`, "good");
      await queryClient.cancelQueries({ queryKey: ["live"] });
      void queryClient.invalidateQueries({ queryKey: ["live"] });
    } catch {
      toast.show(hours === 0 ? "Could not end maintenance" : "Could not start maintenance", "alert");
    }
  }

  async function handleSpeedtest() {
    try {
      await requestSpeedtest();
      toast.show("Speed test queued", "good");
    } catch {
      toast.show("Could not queue a speed test", "alert");
    }
  }

  const me_ = meQuery.data ?? null;
  const updatesWarn = Boolean(updatesQuery.data?.hosts.some((h) => h.reboot_required));
  const alerts = overviewQuery.data?.alerts ?? null;
  const inboxCounts = inboxQuery.data?.counts ?? null;
  // One object for every nav surface: the sidebar's parents, its dropdown
  // children, and the mobile bar (which has no disclosure control, so a
  // child's count can only reach a phone rolled up onto its parent).
  const navBadges: NavBadges = useMemo(
    () => ({
      inboxUnread: inboxCounts?.unread ?? 0,
      alertsOpen: inboxCounts?.alerts ?? 0,
      alertsCrit: alerts?.crit ?? 0,
      alertsWarn: alerts?.warn ?? 0,
      incidents: inboxCounts?.incidents ?? 0,
      recs: inboxCounts?.recs ?? 0,
      securityCrit: alerts?.crit ?? 0,
      securityWarn: alerts?.warn ?? 0,
      updatesWarn,
    }),
    [inboxCounts, alerts, updatesWarn],
  );

  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  // The gesture that makes a restored `wallSpeak` real. `wallboardOpen` is
  // not itself persisted, so after a kiosk reboot this click is the first
  // moment a stored "on" can reach anything -- and without the
  // `enableAudio()` here it never would: the context is still suspended, so
  // the operator who opens the wallboard gets a LIT speaker over a silent
  // room, and the first critical turns the toggle back off in front of them.
  // Unconditional `setWallboardOpen` on purpose: unlike `toggleWallSpeak`,
  // where a refused context means the toggle must not claim to be on, a
  // refused context here is no reason to withhold the wallboard. The alert
  // handler already owns the self-disable if sound really is dead.
  const openWallboard = useCallback(() => {
    if (wallSpeak) enableAudio();
    setWallboardOpen(true);
  }, [wallSpeak]);
  const closeWallboard = useCallback(() => setWallboardOpen(false), []);
  const setChatOpenPersisted = useCallback(
    (next: boolean) => {
      setChatOpen(next);
      if (tenantId !== null) writeChatOpen(tenantId, next);
    },
    [tenantId],
  );
  const toggleChat = useCallback(() => setChatOpenPersisted(!chatOpen), [chatOpen, setChatOpenPersisted]);
  const openHandoff = useCallback(
    (next: AlertHandoff) => {
      setHandoff(next);
      setChatOpenPersisted(true);
    },
    [setChatOpenPersisted],
  );
  const closeChat = useCallback(() => setChatOpenPersisted(false), [setChatOpenPersisted]);
  const askNonce = useRef(0);
  const askGuardian = useCallback(
    (question: string) => {
      askNonce.current += 1;
      setAsk({ question, nonce: askNonce.current });
      setChatOpenPersisted(true);
    },
    [setChatOpenPersisted],
  );
  // One object for the context, memoised so every page under the shell does
  // not re-render each time `Shell` does.
  const chatHandoff = useMemo(
    () => ({ openAlert: openHandoff, askGuardian }),
    [openHandoff, askGuardian],
  );

  // "mod+/" toggles the chat panel; ⌘K/Ctrl-K for the palette is bound
  // inside `CommandPalette` itself (it must fire even while typing).
  useHotkeys({ "mod+/": toggleChat });

  return (
    <LiveContext.Provider value={live}>
     <ChatHandoffContext.Provider value={chatHandoff}>
      <div
        className="g-shell"
        data-mode={advanced ? "advanced" : "simple"}
      >
        <a className="g-skip-link" href="#g-shell-main-content">
          Skip to main content
        </a>
        <Sidebar
          collapsed={collapsed}
          onToggleCollapsed={toggleCollapsed}
          badges={navBadges}
          me={me_}
          onSwitchTenant={(tenantId) => switchTenant.mutate(tenantId)}
          advanced={advanced}
        />

        <div className="g-shell-main">
          <TopBar
            me={me_}
            role={roleOf(meQuery.data)}
            overview={overviewQuery.data ?? null}
            overviewFetchedAt={overviewQuery.dataUpdatedAt || null}
            live={liveQuery.data ?? null}
            scanState={scanState}
            maintenanceUntil={liveQuery.data?.maintenance_until ?? null}
            advanced={advanced}
            onToggleAdvanced={toggleAdvanced}
            theme={theme}
            onCycleTheme={cycleTheme}
            onOpenPalette={openPalette}
            onOpenWallboard={openWallboard}
            onRefresh={() => void handleRefresh()}
            onScanNow={() => void handleRunScan()}
            onSpeedtest={() => void handleSpeedtest()}
            onMaintenance={(hours) => void handleMaintenance(hours)}
            onSignOut={handleSignOut}
          />

          {me_?.active_tenant?.slug === "demo" && <DemoGuide />}
          {/* Reads the onboarding state the Shell already has -- `networks[].dns`
              is the hub's live "is traffic flowing through the resolver" signal,
              so this needs no query of its own and cannot disagree with the
              wizard about whether filtering is on. */}
          <DnsPrompt state={onboardingQuery.data} />
          <PersonalView />
          <div className="g-shell-body">
            <main className="g-shell-content" id="g-shell-main-content" tabIndex={-1}>
              <Outlet />
            </main>
          </div>

          {/* The reference's footer rail — only bindings that are actually
              wired: `useHotkeys({"mod+/": toggleChat})` above and ⌘K bound
              inside `CommandPalette` itself. Escape closing the palette/chat/
              drawers is each of those components' own handler, not a global
              one, but it is real everywhere it is listed. */}
          <footer className="g-key-legend" aria-label="keyboard shortcuts">
            <span><kbd>{modLabel()}K</kbd>search</span>
            <span><kbd>{modLabel()}/</kbd>chat</span>
            <span><kbd>Esc</kbd>close</span>
          </footer>
        </div>

        {/* The popover and its button are fixed to the viewport, so they are
            siblings of the shell's columns rather than living inside the
            scrolling content area. */}
        <ChatPanel
          // Keyed on the active tenant: a superadmin switching tenants must
          // not carry the previous tenant's transcript, thread id, or a
          // pending tier-3 confirm token across — a fresh key remounts
          // `ChatPanel` (and the `useChat` state it owns) clean rather than
          // needing an explicit reset effect.
          key={me_?.active_tenant?.id ?? "no-tenant"}
          id={CHAT_POPOVER_ID}
          open={chatOpen}
          onClose={closeChat}
          tab={tabKeyFor(location.pathname)}
          handoff={handoff}
          ask={ask}
          providers={me_?.chat_providers ?? []}
          exampleIp={overviewQuery.data?.chat_example_ip ?? ""}
          voice={voice}
          live={live}
          returnFocusTo={chatFabRef}
        />
        <ChatFab
          open={chatOpen}
          onToggle={toggleChat}
          critCount={alerts?.crit ?? 0}
          controls={CHAT_POPOVER_ID}
          buttonRef={chatFabRef}
        />

        <MobileTabs badges={navBadges} advanced={advanced} />

        {/* Not a route: a wallboard with a sidebar and a top bar around it is
            just the dashboard again. It overlays the shell and draws off the
            same two queries, so it keeps refreshing on their cadence with no
            interval of its own. */}
        {wallboardOpen && (
          <Wallboard
            overview={overviewQuery.data ?? null}
            overviewFetchedAt={overviewQuery.dataUpdatedAt || null}
            live={liveQuery.data ?? null}
            rows={inboxQuery.data?.rows ?? []}
            maintenanceUntil={liveQuery.data?.maintenance_until ?? null}
            onClose={closeWallboard}
            voice={voice}
            speakOn={wallSpeak}
            onToggleSpeak={toggleWallSpeak}
          />
        )}

        <CommandPalette
          open={paletteOpen}
          onOpen={openPalette}
          onClose={closePalette}
          advanced={advanced}
          onCycleTheme={cycleTheme}
          onOpenChat={() => {
            setChatOpenPersisted(true);
            closePalette();
          }}
          onRunScan={handleRunScan}
          onSpeedtest={handleSpeedtest}
        />
      </div>
     </ChatHandoffContext.Provider>
    </LiveContext.Provider>
  );
}

export default Shell;
