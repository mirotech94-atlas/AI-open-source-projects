# ATLAS Menu

ATLAS in the Mac menu bar. Click the ATLAS mark to talk or type to ATLAS without opening
the full app. The popover is a web view on `https://atlas.example.com/pocket`
(`ui/src/pages/Pocket.tsx`), so everything it does runs through the same doors as the
dashboard.

## Build and install

```sh
./build.sh             # build/ATLAS Menu.app (swiftc, no Xcode project)
./build.sh --install   # also copies it to ~/Applications/ATLAS Menu.app and launches it
```

The build needs the Xcode command line tools and macOS 13 or later. It is ad-hoc signed
(`codesign -s -`) with no sandbox and no hardened runtime.

## Use

- **Left click** or **⌥⇧A** opens and closes the popover. Clicking anywhere else also
  closes it.
- **Right click** (or control-click) shows a menu: Open full ATLAS
  (`/Applications/ATLAS.app` if it is installed, otherwise the dashboard), **Air Glass**,
  Reload, **Tap ⌘ to talk**, **Say “Listening”**, Launch at login, and Quit.
- The first time you use it, sign in with the dashboard password. The session cookie is
  kept in the app's persistent web data, so signing in lasts across restarts for the
  cookie's 30 days.
- The first tap on the mic brings up the macOS microphone prompt. The prompt takes
  focus, so the popover closes. Reopen it: if the status says Listening, just talk;
  otherwise tap the mic. The web views grant the microphone only to
  `https://atlas.example.com`, and the camera only to the Air Glass page (below). If the
  microphone is ever blocked, the entry to turn on in System Settings → Privacy &
  Security → Microphone is **ATLAS Menu** (the in-app message says "your browser").
- **Launch at login** is off until you turn it on in the right-click menu. Nobody else
  can switch it on for you: it is your click. macOS may then ask you to approve it in
  System Settings → General → Login Items.

## Tap ⌘ to talk

The simplest way in: **tap the Command key once on its own** (either ⌘, pressed and let
go within about a third of a second, with no other key or click in between).

- **One tap is a conversation.** The panel appears and ATLAS says "Listening" in her
  Fish Audio voice. Say what you want. After about 0.7 s of quiet it is sent, and
  ATLAS's reply is said out loud and shown in the panel. Then she listens again, back
  and forth, with no second tap.
- The conversation ends when you **tap ⌘ again** (anything you have just said is sent
  first), press **Esc**, say **"stop"**, **"that's all"** or **"goodbye"**, or say
  nothing for 20 seconds.
- **Talk over her** and she stops and listens. The mic uses macOS echo cancellation
  where the Mac supports it, and what ATLAS says is never fed to the speech recognizer,
  so she doesn't hear herself.
- Mac commands ("open Notes", "search for …", "new note …") still run during the
  conversation. Everything else goes to ATLAS.
- **Her voice is Fish Audio and nothing else.** Replies are sent to Fish one sentence
  at a time, and the first sentence plays while the rest are still being made. The
  fixed lines ("Listening", "I couldn't reach my voice") are Fish clips that the server
  renders once and keeps, and ATLAS Menu keeps copies in `~/Library/Caches/ATLAS Menu`.
  If Fish can't speak, she plays the kept "I couldn't reach my voice" clip and the
  reply stays in the panel as text. There is never a system voice.
- **Offline she still speaks in her Fish voice.** Soon after launch, and after a reply
  when the pack is more than 6 hours old, ATLAS Menu fetches `/api/voice/offline-pack`.
  This is a short set of lines ("Listening.", "I can't reach ATLAS right now.", "Sorry,
  I didn't catch that.", "Talk soon.", …). It checks each clip against its sha256 and
  keeps it in `~/Library/Application Support/ATLAS Menu/voice-pack`. When ATLAS can't be
  reached (no network, no route, no host; a timeout doesn't count, because the words may
  have arrived), she says the kept "I can't reach ATLAS right now." A line with no kept
  clip isn't spoken; its text is in the panel.
- `~/Library/Logs/ATLAS Menu.log` records each turn's timings: when the words were
  final, how long ATLAS took to answer, and how long until the first audio.
- ⌘C, ⌘-Tab, ⌘-click and other shortcuts never count as a tap.
- Turn it off with **Tap ⌘ to talk** in the right-click menu if it ever fires by
  accident; turn the spoken word off with **Say “Listening”**. Both are remembered.
- ⌥⇧Space (below) still works exactly as before.
- Say **"open Air Glass"** and the Air Glass window opens. While that window is open,
  what you say goes to Air Glass first ("bigger", "spin", "stop", "explain", "health",
  "give it to ATLAS"); anything it doesn't take goes to ATLAS as usual.

**The permission it needs.** To see the ⌘ key while another app is in front, macOS
needs **Accessibility** for ATLAS Menu: System Settings › Privacy & Security ›
Accessibility › turn on **ATLAS Menu**. The first launch without it shows a one-time
note in the panel and adds ATLAS Menu to that list, so it is one switch. Until it is on,
⌘ taps work only while an ATLAS Menu window is in front, and ⌥⇧Space works everywhere.
The log records whether the permission is on. Every rebuild changes the ad-hoc
signature, so macOS may need the switch turned off and on again after one.

## Air Glass window

**Air Glass** in the right-click menu (or saying "open Air Glass") opens
`https://atlas.example.com/airglass` in its own resizable window, larger than the
popover, with the same sign-in. It is the hand-tracking HUD: pick a hologram, pinch to
grab it, move to spin it, drop it on ATLAS (see `apps/airglass/README.md`).

- **Camera.** macOS asks once ("ATLAS Menu uses the camera only on the Air Glass page,
  for hand tracking that runs on this Mac."). The web view grants the camera only when
  the page is ATLAS's own (https, atlas.example.com) and the frame asking is the Air Glass
  page (its path starts with `/airglass`); everything else is denied, and every
  decision is written to the log. The rule reads the URL the page was loaded at, so the
  window always loads `/airglass` directly.
- Closing the window releases the camera straight away and unloads the page.
- ATLAS speaks in that window without a click first.

## Push to talk: ⌥⇧Space

Talk to your Mac without opening the popover or the full app.

- **Hold ⌥⇧Space**, say what you want, and let go. Or **tap ⌥⇧Space** once, talk, and
  it sends after a short pause (tap again to send straight away).
- A small panel at the top of the screen shows "Listening…" and your words as you say
  them, then one line saying what ran, the one question ATLAS needs answered, or why
  nothing was sent. It fades on its own.
- The reply is said out loud in ATLAS's Fish Audio voice, as in a ⌘ conversation, but
  this is one turn only. Press the key again to stop her and talk. Rebuild with
  `./build.sh --install` to get this.
- Only your finished sentence is sent, never half-heard words. Apple's speech
  recognizer turns it into text on the Mac itself whenever the Mac supports that.
- It uses the same sign-in as the popover. If you're signed out, the panel says so:
  press ⌥⇧A and sign in once.

What it does straight away (each one through ATLAS's executor, as you asking):

| Say | Does |
| --- | --- |
| "open Notes", "launch Spotify" | opens the app |
| "close Safari", "quit Slack" | quits the app (an app with unsaved work asks you first) |
| "open x.com", "go to x dot com", "go to YouTube" | opens the site in your default browser |
| "search for flights to Lisbon", "google best pizza", "search YouTube for lofi" | a search in your browser |
| "new note groceries", "create a note called hello", "take a note that says buy milk" | a new note in Notes |
| "…create a new note, make the title say hello" | sets the title of the note made in the same breath |
| "open the browser and google the weather" | one search (the browser opens with it) |

Chain them in one breath, up to six steps: "open Notes, once you're there create a new
note and make the title say hello". The steps run in order and stop at the first one
that fails; the panel says what ran and where it stopped. "No wait" or "never mind"
cancels what you said just before it ("open Safari… no wait, Chrome" opens Chrome).

It asks instead of guessing: "close it", "search" with nothing after it, "open Notes or
Safari", or a title with no new note to put it on gets one question, and nothing runs.
Anything that isn't a Mac command ("how's FlyCheap doing?") goes to ATLAS like a question
in the popover. It never types into whichever app is in front, never opens Terminal,
Photo Booth or FaceTime, and never deletes or edits a note that already exists. Sending
an iMessage or running a Shortcut is still a card you approve in Slack.

### What macOS asks the first time

- **Microphone** for ATLAS Menu, the first time you press ⌥⇧Space.
- **Speech Recognition** for ATLAS Menu, right after it. Allow both, then press ⌥⇧Space
  again. If either is ever off: System Settings → Privacy & Security → Microphone /
  Speech Recognition → ATLAS Menu.
- **Automation**: the first new note asks whether the ATLAS agent may control
  **Notes**. The prompt names the program the agent runs as (python3 or osascript, run by
  launchd as com.atlas.mac-agent), not ATLAS Menu. Sites and searches open with macOS's
  `open`, so your browser does not prompt.
- Every rebuild changes the ad-hoc signature, so macOS may ask for the Microphone and
  Speech Recognition again after one.

### The Mac agent must be current

New notes need the updated agent on the Mac. An older agent refuses the new operation by
name and runs nothing; the panel then says to update it:

```sh
bootstrap/mac/install-agent.sh
```

It copies `bootstrap/mac/mac_agent.py` to `~/.config/atlas/mac_agent.py` (keeping the
old copy as `.bak`) and restarts it with
`launchctl kickstart -k gui/$(id -u)/com.atlas.mac-agent`. Opening apps, sites and
searches already work with the agent installed before this change.

### Not in this version

No always-on wake word: nothing listens until you press or tap the key. An opt-in "Hey ATLAS"
that listens only on the Mac (on-device recognition, nothing sent until it hears the
phrase) is a possible follow-up.

## Notes

- ATLAS is served only on the NetBird mesh or the home network. When it can't be
  reached, the popover says so; use Reload once the connection is back.
- Every rebuild changes the ad-hoc signature, so macOS may ask for the microphone
  again after one.
- On a crowded menu bar, macOS can put the icon behind the notch or in the overflow
  («). It also appears on the menu bar of every external display.
- The log is at `~/Library/Logs/ATLAS Menu.log`. It records each page load, each
  failure, each microphone grant or denial, and for ⌥⇧Space whether recognition ran on
  the Mac and what came back.
- `--show` (`open "ATLAS Menu.app" --args --show`) opens the popover at launch, which
  is useful for checking a build. `--airglass` opens the Air Glass window at launch.
