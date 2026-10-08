// Tap ⌘ to talk (Miro, 2026-10-06: "make it simpler with the Command button
// only: press it once and the voice control appears and says listening").
//
// A tap is either Command key pressed and released on its own within
// `tapWindow`, with no other key, modifier or mouse click in between, so ⌘C,
// ⌘-Tab and ⌘-click never count. Esc is passed on so a listening HUD can be
// cancelled.
//
// Seeing keys while another app is in front needs the Accessibility
// permission (System Settings › Privacy & Security › Accessibility › ATLAS
// Menu). Without it only taps made while ATLAS Menu itself is in front (its
// Air Glass window, the popover) are seen; `trusted` says which, and the app
// says so once instead of failing silently.

import AppKit
import ApplicationServices

final class CommandTap {
    static let enabledKey = "tapCommandToTalk"
    static let noteShownKey = "tapCommandNoteShown"

    var onTap: (() -> Void)?
    /// Esc while something is listening; true when it was used.
    var onEscape: (() -> Bool)?

    private var monitors: [Any] = []
    private var commandDown = false
    private var spoiled = false
    private var downAt = Date.distantPast
    private var trustTimer: Timer?
    private let tapWindow: TimeInterval = 0.35

    var enabled: Bool {
        get { UserDefaults.standard.object(forKey: CommandTap.enabledKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: CommandTap.enabledKey) }
    }

    /// Accessibility trust, asked without a prompt.
    var trusted: Bool {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: false] as CFDictionary
        return AXIsProcessTrustedWithOptions(opts)
    }

    /// Install the monitors and report the permission. `note` is shown once
    /// when the permission is missing (where to allow it).
    func start(note: (String, String) -> Void) {
        install()
        let ok = trusted
        log("tap ⌘ to talk: \(enabled ? "on" : "off"), accessibility \(ok ? "granted" : "not granted")")
        guard !ok else { return }
        if enabled && !UserDefaults.standard.bool(forKey: CommandTap.noteShownKey) {
            UserDefaults.standard.set(true, forKey: CommandTap.noteShownKey)
            note("Tap ⌘ to talk needs one permission",
                 "System Settings › Privacy & Security › Accessibility › turn on ATLAS Menu. "
                 + "Until then ⌘ works only while an ATLAS Menu window is in front; ⌥⇧Space works everywhere.")
            // Puts ATLAS Menu in that list, so it is one switch to turn on.
            let ask = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(ask)
        }
        // A global monitor installed before the permission was given does not
        // start seeing keys by itself: watch for it and install again.
        trustTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] timer in
            guard let self, self.trusted else { return }
            timer.invalidate()
            log("accessibility granted: ⌘ taps are now seen in every app")
            self.install()
        }
    }

    private func install() {
        for m in monitors { NSEvent.removeMonitor(m) }
        monitors.removeAll()
        let mask: NSEvent.EventTypeMask = [.flagsChanged, .keyDown, .leftMouseDown,
                                           .rightMouseDown, .otherMouseDown]
        if let g = NSEvent.addGlobalMonitorForEvents(matching: mask, handler: { [weak self] e in
            self?.handle(e)
        }) { monitors.append(g) }
        if let l = NSEvent.addLocalMonitorForEvents(matching: mask, handler: { [weak self] e in
            self?.handle(e)
            return e
        }) { monitors.append(l) }
    }

    private func handle(_ event: NSEvent) {
        switch event.type {
        case .flagsChanged:
            let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            let command = flags.contains(.command)
            let others = !flags.intersection([.shift, .option, .control, .function]).isEmpty
            if command && !commandDown {
                commandDown = true
                spoiled = others
                downAt = Date()
            } else if command && others {
                spoiled = true
            } else if !command && commandDown {
                commandDown = false
                let quick = Date().timeIntervalSince(downAt) <= tapWindow
                if quick && !spoiled && enabled { onTap?() }
            }
        case .keyDown:
            if commandDown { spoiled = true }
            if event.keyCode == 53 { _ = onEscape?() }   // Esc
        default:
            if commandDown { spoiled = true }            // ⌘-click
        }
    }
}
