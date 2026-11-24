// ATLAS Menu: ATLAS in the Mac menu bar.
//
// A status item that opens a popover holding the dashboard's /pocket page
// (ui/src/pages/Pocket.tsx): talk or type to ATLAS without opening the full
// app. The page does all the work; this file is only the window around it.
// ⌥⇧Space is push to talk without the popover (voice.swift).
//
// Built by build.sh with swiftc; no Xcode project.

import AppKit
import Carbon.HIToolbox
import ServiceManagement
import WebKit

/// Where ATLAS answers: one base for every URL here. Local ATLAS by default
/// (the server atlas.storefront.test was deleted on 2026-10-27); the ATLAS_BASE
/// environment variable or `defaults write Miro.k9.atlasmenu ATLASBase <url>`
/// overrides it.
let defaultAtlasBase = "http://127.0.0.1:8080"
let atlasBase: URL = {
    let env = ProcessInfo.processInfo.environment["ATLAS_BASE"]
    let raw = (env?.isEmpty == false ? env : nil)
        ?? UserDefaults.standard.string(forKey: "ATLASBase") ?? defaultAtlasBase
    let trimmed = raw.hasSuffix("/") ? String(raw.dropLast()) : raw
    guard let url = URL(string: trimmed), let scheme = url.scheme,
          scheme == "http" || scheme == "https", url.host != nil else {
        return URL(string: defaultAtlasBase)!
    }
    return url
}()
let atlasScheme = atlasBase.scheme ?? "http"
let atlasHost = atlasBase.host ?? "127.0.0.1"
let atlasPort = atlasBase.port ?? (atlasScheme == "https" ? 443 : 80)
func atlasURL(_ path: String) -> URL { URL(string: atlasBase.absoluteString + path)! }
/// Same scheme, host and port as the base: ATLAS's own origin.
func isAtlas(_ url: URL?) -> Bool {
    guard let url, url.scheme == atlasScheme, url.host == atlasHost else { return false }
    return (url.port ?? (url.scheme == "https" ? 443 : 80)) == atlasPort
}
let dashboard = atlasURL("/")
let pocketURL = atlasURL("/pocket")
/// The Air Glass page alone (`?window=1`: without the command center around it).
let airGlassURL = atlasURL("/airglass?window=1")
let fullAppBundleID = "com.atlas.desktop"   // /Applications/ATLAS.app
let popoverSize = NSSize(width: 380, height: 560)

// MARK: - log

/// ~/Library/Logs/ATLAS Menu.log -- what loaded and what failed, so a blank
/// popover can be explained without attaching a debugger.
func log(_ message: String) {
    NSLog("ATLAS Menu: %@", message)
    let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/ATLAS Menu.log")
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
    guard let data = line.data(using: .utf8) else { return }
    if let handle = try? FileHandle(forWritingTo: url) {
        handle.seekToEndOfFile()
        handle.write(data)
        try? handle.close()
    } else {
        try? data.write(to: url)
    }
}

// MARK: - the mark

/// The ATLAS mark (ui/src/components/Mark.tsx) drawn as a template image:
/// monochrome, so macOS tints it for light and dark menu bars. The SVG's
/// 32x32 box is y-down; AppKit is y-up, so every y is flipped (32 - y).
func atlasMark(size: CGFloat = 18) -> NSImage {
    let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
        let s = rect.width / 32
        func p(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: x * s, y: (32 - y) * s) }
        NSColor.black.set()

        // The load: the one solid thing in the mark.
        let r: CGFloat = 3.6 * s
        let c = p(16, 7.6)
        NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)).fill()

        // Two legs meeting under the load, and the span that makes it an A.
        let lines = NSBezierPath()
        lines.lineWidth = 2.2 * s
        lines.lineCapStyle = .round
        lines.lineJoinStyle = .round
        lines.move(to: p(4.6, 26.6)); lines.line(to: p(12.4, 12.2))
        lines.move(to: p(27.4, 26.6)); lines.line(to: p(19.6, 12.2))
        lines.move(to: p(8.2, 19.8)); lines.line(to: p(23.8, 19.8))
        lines.stroke()
        return true
    }
    image.isTemplate = true
    image.accessibilityDescription = "ATLAS"
    return image
}

// MARK: - rules shared by both web views

/// Navigation stays on ATLAS. A link marked to open elsewhere ("Full ›")
/// goes to the full app; anything else opens in the default browser.
func atlasNavigation(_ action: WKNavigationAction) -> WKNavigationActionPolicy {
    guard let url = action.request.url else { return .cancel }
    let scheme = url.scheme ?? ""
    if scheme == "about" || scheme == "blob" || scheme == "data" || isAtlas(url) {
        if action.targetFrame == nil {
            openFullAtlas(at: url)
            return .cancel
        }
        return .allow
    }
    NSWorkspace.shared.open(url)
    return .cancel
}

/// The camera and microphone, decided here and nowhere else (Miro,
/// 2026-10-06: "allow camera, built inside ATLAS").
///
///   microphone            ATLAS's own origin (atlasBase: scheme, host, port).
///   camera, camera + mic  ATLAS's own origin AND the requesting frame is the
///                         Air Glass page (its path starts with /airglass).
///                         For the main frame the web view's current URL must
///                         say so too, so a page that has moved on in-app
///                         (history.pushState) away from Air Glass is refused.
///   anything else         denied.
///
/// Every decision is logged. WKFrameInfo's request is the frame's document
/// request, which an in-app route change does not update: Air Glass therefore
/// gets the camera when it was LOADED at /airglass (the Air Glass window
/// does exactly that), not after navigating to it from another screen.
///
/// A request the origin rules allow is still only granted once the listener
/// on ATLAS's port has proved it is ATLAS (`verifyAtlasListener`): any local
/// process could bind 127.0.0.1:8080 while ATLAS is down. Until then, deny.
/// `decide` is called exactly once, on the main queue.
func mediaDecision(origin: WKSecurityOrigin, frame: WKFrameInfo, type: WKMediaCaptureType,
                   webView: WKWebView, decide: @escaping (WKPermissionDecision) -> Void) {
    let (allowed, what) = mediaPolicy(origin: origin, frame: frame, type: type, webView: webView)
    guard allowed else {
        log("media capture \(what): deny")
        decide(.deny)
        return
    }
    verifyAtlasListener { [weak webView] verified, why in
        // The page may have moved on during the probe: ask the rules again.
        guard let webView else {
            log("media capture \(what): deny (web view gone)")
            decide(.deny)
            return
        }
        let still = mediaPolicy(origin: origin, frame: frame, type: type, webView: webView).0
        let ok = verified && still
        log("media capture \(what): \(ok ? "grant" : "deny") (\(why)\(still ? "" : ", page moved"))")
        decide(ok ? .grant : .deny)
    }
}

// MARK: - is the listener really ATLAS?

/// What /healthz must carry (atlas/api/main.py SERVICE_NAME).
let atlasServiceName = "atlas-agency"
let atlasVerifyTTL: TimeInterval = 60

/// Redirects are refused: the 3xx itself comes back and fails the 200 check.
final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

/// One long-lived ephemeral session: no cookies sent or kept, no cache,
/// two seconds for the whole exchange.
let atlasProbeSession: URLSession = {
    let config = URLSessionConfiguration.ephemeral
    config.timeoutIntervalForRequest = 2
    config.timeoutIntervalForResource = 2
    config.httpCookieAcceptPolicy = .never
    config.httpShouldSetCookies = false
    config.httpCookieStorage = nil
    config.urlCache = nil
    config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
    return URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
}()

/// Main queue only. Set on a positive answer; a negative one is never cached.
var atlasVerifiedAt: Date?

/// GET <atlasBase>/healthz and require 200 + JSON {"ok": true,
/// "service": "atlas-agency"}. Calls back on the main queue with the result
/// and a short reason for the log.
func verifyAtlasListener(then: @escaping (Bool, String) -> Void) {
    dispatchPrecondition(condition: .onQueue(.main))
    if let at = atlasVerifiedAt, Date().timeIntervalSince(at) < atlasVerifyTTL {
        then(true, "listener verified \(Int(Date().timeIntervalSince(at)))s ago")
        return
    }
    let url = atlasURL("/healthz")
    var req = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
                         timeoutInterval: 2)
    req.httpShouldHandleCookies = false
    req.setValue("application/json", forHTTPHeaderField: "Accept")
    atlasProbeSession.dataTask(with: req) { data, response, error in
        let verdict: (Bool, String)
        if let error {
            verdict = (false, "healthz unreachable: \(error.localizedDescription)")
        } else if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            verdict = (false, "healthz answered \(http.statusCode)")
        } else if !isAtlas(response?.url) {
            verdict = (false, "healthz answered from another origin")
        } else if let data,
                  let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            let okIsTrue = (body["ok"] as? NSNumber).map {
                CFGetTypeID($0) == CFBooleanGetTypeID() && $0.boolValue
            } ?? false
            let service = body["service"] as? String
            if okIsTrue && service == atlasServiceName {
                verdict = (true, "listener verified")
            } else {
                verdict = (false, "healthz is not ATLAS (service=\(service ?? "missing"))")
            }
        } else {
            verdict = (false, "healthz body is not JSON")
        }
        DispatchQueue.main.async {
            if verdict.0 { atlasVerifiedAt = Date() }
            log("listener check: \(verdict.1)")
            then(verdict.0, verdict.1)
        }
    }.resume()
}

/// The origin and frame rules alone; says nothing about who is listening.
func mediaPolicy(origin: WKSecurityOrigin, frame: WKFrameInfo, type: WKMediaCaptureType,
                 webView: WKWebView) -> (Bool, String) {
    let originPort = origin.port != 0 ? origin.port : (origin.protocol == "https" ? 443 : 80)
    let atlas = origin.protocol == atlasScheme && origin.host == atlasHost
        && originPort == atlasPort
    let frameURL = frame.request.url
    let framePath = frameURL?.path ?? ""
    let frameOnAtlas = isAtlas(frameURL)
    let pagePath = frame.isMainFrame ? (webView.url?.path ?? "") : framePath
    let airGlass = frameOnAtlas && framePath.hasPrefix("/airglass") && pagePath.hasPrefix("/airglass")
    let ok: Bool
    let kind: String
    switch type {
    case .microphone:
        ok = atlas
        kind = "microphone"
    case .camera:
        ok = atlas && airGlass
        kind = "camera"
    case .cameraAndMicrophone:
        ok = atlas && airGlass
        kind = "camera+microphone"
    @unknown default:
        ok = false
        kind = "unknown(\(type.rawValue))"
    }
    let what = "\(kind) for \(origin.protocol)://\(origin.host) frame \(framePath.isEmpty ? "?" : framePath) page \(pagePath.isEmpty ? "?" : pagePath)"
    return (ok, what)
}

// MARK: - the web view

final class PocketController: NSViewController, WKNavigationDelegate, WKUIDelegate {
    let webView: WKWebView

    init() {
        let config = WKWebViewConfiguration()
        // The default, persistent store: the 30-day session cookie from the
        // login survives quitting and relaunching.
        config.websiteDataStore = .default()
        // ATLAS's answers are spoken through Fish TTS blobs; a spoken answer
        // to a typed question starts without a click on the audio itself.
        config.mediaTypesRequiringUserActionForPlayback = []
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: NSRect(origin: .zero, size: popoverSize), configuration: config)
        webView.setValue(false, forKey: "drawsBackground")   // no white flash before the HUD paints
        super.init(nibName: nil, bundle: nil)
        webView.navigationDelegate = self
        webView.uiDelegate = self
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func loadView() {
        let host = NSView(frame: NSRect(origin: .zero, size: popoverSize))
        host.wantsLayer = true
        host.layer?.backgroundColor = NSColor(red: 0.012, green: 0.027, blue: 0.043, alpha: 1).cgColor
        webView.autoresizingMask = [.width, .height]
        webView.frame = host.bounds
        host.addSubview(webView)
        view = host
        preferredContentSize = popoverSize
    }

    /// Always the pocket URL itself, never `reload()`: a page that was
    /// redirected elsewhere (an older bundle sends /pocket to /) comes back
    /// to the pocket.
    func loadPocket() {
        log("load \(pocketURL.absoluteString)")
        webView.load(URLRequest(url: pocketURL))
    }

    // Navigation stays on ATLAS. Anything else opens in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(atlasNavigation(action))
    }

    // target=_blank and window.open: never a second web view.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if isAtlas(url) { openFullAtlas(at: url) } else { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    // The microphone for ATLAS's own origin; the camera only for Air Glass
    // (which the popover never shows). See `mediaDecision`.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        mediaDecision(origin: origin, frame: frame, type: type, webView: webView,
                      decide: decisionHandler)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        log("loaded \(webView.url?.absoluteString ?? "?") title=\(webView.title ?? "")")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        log("navigation failed: \(error.localizedDescription)")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        log("could not reach ATLAS: \(error.localizedDescription)")
        let html = """
        <html><body style="background:#03070b;color:#9fb3bf;font:12px ui-monospace,Menlo,monospace;
        padding:24px"><p style="color:#22d3ee;letter-spacing:.3em">ATLAS</p>
        <p>Could not reach ATLAS at \(atlasBase.absoluteString).</p><p style="color:#f59e0b">\(error.localizedDescription)</p>
        <p>Is the local stack up (docker compose ... up -d)? Right-click the
        menu-bar icon, then Reload.</p></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        log("web content process ended; reloading")
        loadPocket()
    }
}

// MARK: - the Air Glass window

/// Air Glass in its own resizable window: larger than the popover, with its
/// own web view on the SAME persistent data store, so the sign-in carries
/// over. Closing it releases the camera.
final class AirGlassWindow: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?

    var isOpen: Bool { window?.isVisible ?? false }

    func show() {
        if window == nil { build() }
        guard let window, let webView else { return }
        if webView.url?.path.hasPrefix("/airglass") != true {
            log("air glass: load \(airGlassURL.absoluteString)")
            webView.load(URLRequest(url: airGlassURL))
        }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    private func build() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()          // the popover's store: same session
        // ATLAS speaks (speechSynthesis, Fish TTS blobs) without a click first.
        config.mediaTypesRequiringUserActionForPlayback = []
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        let frame = NSRect(x: 0, y: 0, width: 1280, height: 800)
        let web = WKWebView(frame: frame, configuration: config)
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
        web.uiDelegate = self
        web.autoresizingMask = [.width, .height]

        let win = NSWindow(contentRect: frame,
                           styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: false)
        win.title = "Air Glass — ATLAS"
        win.minSize = NSSize(width: 900, height: 600)
        win.isReleasedWhenClosed = false
        win.backgroundColor = NSColor(red: 0.012, green: 0.035, blue: 0.05, alpha: 1)
        win.contentView = web
        win.center()
        win.setFrameAutosaveName("ATLASAirGlass")
        win.delegate = self
        window = win
        webView = web
    }

    /// Words the menu-bar voice heard, offered to the page first. True when
    /// Air Glass took them as one of its commands.
    func offer(_ text: String, _ done: @escaping (Bool) -> Void) {
        guard isOpen, let webView else { return done(false) }
        webView.callAsyncJavaScript(
            "return window.__airglassCommand ? window.__airglassCommand(t) : false",
            arguments: ["t": text], in: nil, in: .page) { result in
                let took = (try? result.get()) as? Bool ?? false
                log("air glass: offered \"\(text.prefix(60))\": \(took ? "taken" : "not a command")")
                done(took)
            }
    }

    // Closing the window lets go of the camera at once, and unloads the page.
    func windowWillClose(_ notification: Notification) {
        log("air glass: window closed, camera released")
        webView?.setCameraCaptureState(.none, completionHandler: nil)
        webView?.load(URLRequest(url: URL(string: "about:blank")!))
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(atlasNavigation(action))
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if isAtlas(url) { openFullAtlas(at: url) } else { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        mediaDecision(origin: origin, frame: frame, type: type, webView: webView,
                      decide: decisionHandler)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        log("air glass: loaded \(webView.url?.absoluteString ?? "?")")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        log("air glass: could not reach ATLAS: \(error.localizedDescription)")
        webView.loadHTMLString("""
        <html><body style="background:#03070b;color:#9fb3bf;font:13px ui-monospace,Menlo,monospace;
        padding:32px"><p style="color:#22d3ee;letter-spacing:.3em">AIR GLASS</p>
        <p>Could not reach ATLAS at \(atlasBase.absoluteString).</p><p style="color:#f59e0b">\(error.localizedDescription)</p>
        <p>Is the local stack up? Then reopen this window.</p></body></html>
        """, baseURL: nil)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        log("air glass: web content process ended; reloading")
        webView.load(URLRequest(url: airGlassURL))
    }
}

// MARK: - opening the full app

func openFullAtlas(at url: URL = dashboard) {
    if url == dashboard || url.path == "/" || url.path.isEmpty,
       let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: fullAppBundleID) {
        NSWorkspace.shared.openApplication(at: app, configuration: .init()) { _, error in
            if let error { log("could not open ATLAS.app: \(error.localizedDescription)") }
        }
        return
    }
    NSWorkspace.shared.open(url)
}

// MARK: - the app

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    let popover = NSPopover()
    let pocket = PocketController()
    let talker = PushToTalk()
    let airGlass = AirGlassWindow()
    let commandTap = CommandTap()
    var hotKey: EventHotKeyRef?
    var talkHotKey: EventHotKeyRef?
    var packTimer: Timer?

    func applicationDidFinishLaunching(_ note: Notification) {
        log("launch \(Bundle.main.bundlePath)")
        installEditMenu()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            button.image = atlasMark()
            button.toolTip = "ATLAS  (⌥⇧A)  ·  tap ⌘ or hold ⌥⇧Space to talk"
            button.target = self
            button.action = #selector(clicked(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        popover.behavior = .transient
        popover.animates = false
        popover.contentSize = popoverSize
        popover.contentViewController = pocket
        _ = pocket.view
        pocket.loadPocket()

        registerHotKey()

        // The voice: words go to the Air Glass window first when it is open,
        // and "open Air Glass" opens it.
        talker.localHandler = { [weak self] text, done in
            guard let self, self.airGlass.isOpen else { return done(false) }
            self.airGlass.offer(text, done)
        }
        talker.onWindow = { [weak self] name in
            if name == "airglass" { self?.airGlass.show() }
        }
        commandTap.onTap = { [weak self] in self?.talker.commandTapped() }
        commandTap.onEscape = { [weak self] in self?.talker.cancel() ?? false }
        commandTap.start { [weak self] title, text in self?.talker.note(title, text) }
        // The offline lines in ATLAS's Fish voice (voicepack.swift): fetched
        // soon after launch, then checked hourly; VoicePack decides when one
        // is actually due, and keeps the last good pack when ATLAS is away.
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            self?.talker.refreshPack()
        }
        packTimer = Timer.scheduledTimer(withTimeInterval: 3600, repeats: true) { [weak self] _ in
            self?.talker.refreshPack()
        }

        if CommandLine.arguments.contains("--airglass") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.airGlass.show() }
        }
        if CommandLine.arguments.contains("--show") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.showPopover() }
        }
    }

    // An LSUIElement app has no menu bar of its own, so without this hidden
    // Edit menu ⌘C / ⌘V / ⌘A / ⌘Z do nothing inside the web view.
    func installEditMenu() {
        let main = NSMenu()
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        NSApp.mainMenu = main
    }

    @objc func clicked(_ sender: NSStatusBarButton) {
        let event = NSApp.currentEvent
        let right = event?.type == .rightMouseUp
            || (event?.modifierFlags.contains(.control) ?? false)
        if right { showMenu() } else { togglePopover() }
    }

    func togglePopover() {
        if popover.isShown { popover.performClose(nil) } else { showPopover() }
    }

    func showPopover() {
        guard let button = statusItem.button else { return }
        // Without activating, the popover shows but the text box never gets
        // a keystroke: the app is a background agent.
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        popover.contentViewController?.view.window?.makeFirstResponder(pocket.webView)
    }

    // MARK: right-click menu

    func showMenu() {
        let menu = NSMenu()
        menu.addItem(withTitle: "Open full ATLAS", action: #selector(openFull), keyEquivalent: "")
        menu.addItem(withTitle: "Air Glass", action: #selector(openAirGlass), keyEquivalent: "")
        menu.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        menu.addItem(.separator())
        let tap = NSMenuItem(title: "Tap ⌘ to talk", action: #selector(toggleCommandTap),
                             keyEquivalent: "")
        tap.state = commandTap.enabled ? .on : .off
        menu.addItem(tap)
        let say = NSMenuItem(title: "Say “Listening”", action: #selector(toggleSayListening),
                             keyEquivalent: "")
        say.state = talker.sayListening ? .on : .off
        menu.addItem(say)
        menu.addItem(.separator())
        let login = NSMenuItem(title: "Launch at login", action: #selector(toggleLogin),
                               keyEquivalent: "")
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit ATLAS Menu", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items { item.target = self }
        menu.delegate = self
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
    }

    // The menu is attached only for the right click; detached again so the
    // next left click opens the popover instead of the menu.
    func menuDidClose(_ menu: NSMenu) {
        statusItem.menu = nil
    }

    @objc func openFull() { openFullAtlas() }

    @objc func openAirGlass() { airGlass.show() }

    @objc func toggleCommandTap() {
        commandTap.enabled.toggle()
        log("tap ⌘ to talk: \(commandTap.enabled ? "on" : "off")")
    }

    @objc func toggleSayListening() {
        talker.sayListening.toggle()
        log("say listening: \(talker.sayListening ? "on" : "off")")
    }

    @objc func reload() { pocket.loadPocket() }

    @objc func toggleLogin() {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled {
                try service.unregister()
                log("launch at login: off")
            } else {
                try service.register()
                log("launch at login: on (\(service.status.rawValue))")
                if service.status == .requiresApproval {
                    SMAppService.openSystemSettingsLoginItems()
                }
            }
        } catch {
            log("launch at login failed: \(error.localizedDescription)")
            let alert = NSAlert()
            alert.messageText = "Launch at login could not be changed"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }

    @objc func quit() { NSApp.terminate(nil) }

    // MARK: ⌥⇧A (the popover) and ⌥⇧Space (push to talk)

    static let popoverKey: UInt32 = 1
    static let talkKey: UInt32 = 2

    /// Carbon's RegisterEventHotKey: global shortcuts that need no
    /// Accessibility permission (an NSEvent global monitor would). One
    /// handler for both keys, pressed and released, told apart by id.
    func registerHotKey() {
        var specs = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                          eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                          eventKind: UInt32(kEventHotKeyReleased)),
        ]
        let me = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, event, userData in
            guard let userData, let event else { return noErr }
            var key = EventHotKeyID()
            let read = GetEventParameter(event, EventParamName(kEventParamDirectObject),
                                         EventParamType(typeEventHotKeyID), nil,
                                         MemoryLayout<EventHotKeyID>.size, nil, &key)
            guard read == noErr else { return read }
            let down = GetEventKind(event) == UInt32(kEventHotKeyPressed)
            let delegate = Unmanaged<AppDelegate>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { delegate.hotKey(key.id, down: down) }
            return noErr
        }, specs.count, &specs, me, nil)

        let signature = OSType(0x41544C53)   // 'ATLS'
        let popover = RegisterEventHotKey(UInt32(kVK_ANSI_A), UInt32(optionKey | shiftKey),
                                          EventHotKeyID(signature: signature,
                                                        id: AppDelegate.popoverKey),
                                          GetApplicationEventTarget(), 0, &hotKey)
        log(popover == noErr ? "hotkey ⌥⇧A registered" : "hotkey ⌥⇧A unavailable (\(popover))")
        let talk = RegisterEventHotKey(UInt32(kVK_Space), UInt32(optionKey | shiftKey),
                                       EventHotKeyID(signature: signature,
                                                     id: AppDelegate.talkKey),
                                       GetApplicationEventTarget(), 0, &talkHotKey)
        log(talk == noErr ? "hotkey ⌥⇧Space registered"
                          : "hotkey ⌥⇧Space unavailable (\(talk))")
    }

    func hotKey(_ id: UInt32, down: Bool) {
        switch (id, down) {
        case (AppDelegate.popoverKey, true): togglePopover()
        case (AppDelegate.talkKey, true): talker.pressed()
        case (AppDelegate.talkKey, false): talker.released()
        default: break
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
