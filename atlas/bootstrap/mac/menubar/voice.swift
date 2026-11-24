// Talking to ATLAS without opening the popover (Miro, 2026-10-06: "I don't
// want to have to open the ATLAS application to use it"; then: "I should
// only press the command button once to be able to talk back and forth with
// ATLAS; make sure the voice is Fish Audio, not the sound of Siri or
// anything else; make sure she responds with voice as well").
//
// Tap ⌘ on its own (cmdtap.swift) once: ATLAS says "Listening" in its Fish
// voice and a conversation is open -- he talks, pauses, ATLAS answers out
// loud (Fish Audio) and on screen, then listens again, back and forth,
// until ⌘ is tapped again, Esc, "stop" / "that's all" / "goodbye", or 20 s
// of quiet. Hold ⌥⇧Space (or tap it, talk, pause) for a single turn.
//
// Apple's speech recognizer hears him -- on the Mac itself whenever the Mac
// supports that -- and only the FINAL transcript is sent, never partial
// words, to POST /api/voice/command with the dashboard's own session cookie.
// That route reads Mac commands in code and runs them through the executor
// (kernel.spoken); anything that is not a Mac command goes to /api/voice/ask.
// The reply is said through /api/voice/speak. A small floating HUD shows his
// live words and ATLAS's reply.
//
// No wake word: nothing listens until a key is pressed. When the Air Glass
// window is open, his words are offered to that page first ("bigger",
// "explain", "give it to ATLAS"); only words it does not take go to ATLAS.
// "Open Air Glass" comes back from /command as window: "airglass".

import AppKit
import AVFoundation
import Speech
import WebKit

let commandURL = atlasURL("/api/voice/command")
let askURL = atlasURL("/api/voice/ask")
let speakURL = atlasURL("/api/voice/speak")
let sessionCookie = "atlas_session"

// MARK: - the HUD

final class HUD {
    private let panel: NSPanel
    private let title = NSTextField(labelWithString: "")
    private let words = NSTextField(wrappingLabelWithString: "")
    private let result = NSTextField(wrappingLabelWithString: "")
    private var hideWork: DispatchWorkItem?
    private let width: CGFloat = 440

    init() {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: width, height: 90),
                        styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
        panel.level = .statusBar
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = true
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary,
                                    .ignoresCycle]

        let blur = NSVisualEffectView()
        blur.material = .hudWindow
        blur.blendingMode = .behindWindow
        blur.state = .active
        blur.wantsLayer = true
        blur.layer?.cornerRadius = 14
        blur.layer?.masksToBounds = true

        title.font = .systemFont(ofSize: 11, weight: .semibold)
        title.textColor = NSColor(red: 0.13, green: 0.83, blue: 0.93, alpha: 1)
        words.font = .systemFont(ofSize: 15, weight: .medium)
        words.textColor = .labelColor
        words.maximumNumberOfLines = 3
        result.font = .systemFont(ofSize: 12)
        result.textColor = .secondaryLabelColor
        result.maximumNumberOfLines = 4
        for label in [words, result] {
            label.preferredMaxLayoutWidth = width - 32
            label.lineBreakMode = .byWordWrapping
        }

        let stack = NSStackView(views: [title, words, result])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 6
        stack.edgeInsets = NSEdgeInsets(top: 12, left: 16, bottom: 14, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false
        blur.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: blur.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: blur.trailingAnchor),
            stack.topAnchor.constraint(equalTo: blur.topAnchor),
            stack.bottomAnchor.constraint(equalTo: blur.bottomAnchor),
            stack.widthAnchor.constraint(equalToConstant: width),
        ])
        panel.contentView = blur
    }

    /// `linger`: seconds before it fades; nil keeps it up (still listening).
    func show(_ heading: String, said: String? = nil, outcome: String? = nil,
              linger: TimeInterval? = nil) {
        hideWork?.cancel()
        title.stringValue = heading.uppercased()
        if let said { words.stringValue = said }
        result.stringValue = outcome ?? ""
        result.isHidden = (outcome ?? "").isEmpty
        words.isHidden = words.stringValue.isEmpty
        layout()
        panel.orderFrontRegardless()
        if let linger {
            let work = DispatchWorkItem { [weak self] in self?.panel.orderOut(nil) }
            hideWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + linger, execute: work)
        }
    }

    func reset() {
        words.stringValue = ""
        result.stringValue = ""
    }

    /// Fade out after `linger` seconds, keeping what is shown.
    func fade(after linger: TimeInterval) {
        hideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.panel.orderOut(nil) }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + linger, execute: work)
    }

    private func layout() {
        guard let view = panel.contentView else { return }
        view.layoutSubtreeIfNeeded()
        let size = NSSize(width: width, height: max(56, view.fittingSize.height))
        // Top centre of the screen the pointer is on, under the menu bar.
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) }
            ?? NSScreen.main
        guard let frame = screen?.visibleFrame else { return }
        panel.setFrame(NSRect(x: frame.midX - size.width / 2,
                              y: frame.maxY - size.height - 14,
                              width: size.width, height: size.height), display: true)
    }
}

// MARK: - talking

/// One ⌘ tap is a conversation (Miro, 2026-10-06: "I should only press the
/// command button once to be able to talk back and forth with ATLAS; make
/// sure the voice is Fish Audio, not the sound of Siri or anything else;
/// make sure she responds with voice as well").
///
///   ⌘ tap      "Listening" (a Fish clip), then it listens; after a pause his
///              words are sent; ATLAS's reply is said in Fish Audio and shown;
///              then it listens again -- until ⌘ is tapped again, Esc, "stop"
///              / "that's all" / "goodbye", or 20 s with nothing said.
///   ⌥⇧Space    one turn: hold and talk (or tap, talk, pause); the reply is
///              said the same way, then it stops.
///
/// The only voice is Fish Audio: replies through /api/voice/speak, fixed
/// lines from the offline pack kept in Application Support (voicepack.swift)
/// or /api/voice/clip (rendered once by Fish, kept by the server and here in
/// ~/Library/Caches). If Fish cannot speak and nothing is kept, the reply is
/// text in the HUD -- never a system voice.
///
/// ATLAS never hears itself: the recognizer is fed only while it is
/// listening, never while a clip or a reply plays, and the mic runs through
/// Apple's voice processing (echo cancellation) when the Mac allows it.
/// Barge-in: talking over a reply (a sustained level well above the room's
/// quiet floor) stops it and starts his turn.
final class PushToTalk: NSObject {
    enum State { case idle, cue, listening, finishing, sending, replying }
    static let sayListeningKey = "sayListening"

    /// Offered the final words first (the Air Glass window): calls back true
    /// when it took them.
    var localHandler: ((String, @escaping (Bool) -> Void) -> Void)?
    /// A window ATLAS asked the menu bar to open ("airglass").
    var onWindow: ((String) -> Void)?

    var sayListening: Bool {
        get { UserDefaults.standard.object(forKey: PushToTalk.sayListeningKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: PushToTalk.sayListeningKey) }
    }

    private let hud = HUD()
    private var hint = "Talk, then pause — or tap ⌥⇧Space again to send."
    private var state = State.idle
    /// A ⌘-tap conversation is open: after each reply it listens again.
    private var conversation = false
    /// End the conversation once the reply in flight has been said.
    private var endAfterReply = false
    private var latest = ""                 // the newest partial, shown only
    private var keyDown = false
    private var pressedAt = Date.distantPast
    private var tapMode = false             // tapped, not held: ends on a pause
    private var pauseTimer: Timer?
    private var finalTimer: Timer?
    private var capTimer: Timer?
    private var silenceTimer: Timer?
    private var failedStarts = 0

    // Audio: one engine for the mic and for ATLAS's voice, so Apple's echo
    // cancellation knows what ATLAS is saying.
    private let engine = AVAudioEngine()
    private let playerNode = AVAudioPlayerNode()
    private var engineReady = false
    private var echoCancelled = false
    private var playToken = 0
    private var speakRun = 0
    private var recognizer: SFSpeechRecognizer?
    private var task: SFSpeechRecognitionTask?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    // Read on the audio thread: guarded by `lock`.
    private let lock = NSLock()
    private var feeding: SFSpeechAudioBufferRecognitionRequest?

    // Barge-in.
    private var floorDB: Float = -60
    private var loudSince: Date?
    private var voiceDown = false

    /// Fixed Fish lines already fetched ("listening", "no_voice", "bye").
    private var clips: [String: Data] = [:]
    /// The offline lines kept on this Mac (voicepack.swift).
    private let pack = VoicePack()

    /// Held at least this long, letting go sends; shorter is a tap.
    private let holdThreshold: TimeInterval = 0.35
    /// In tap mode, this long without a new word ends the request.
    // 1.3 s felt like ATLAS not answering (2026-10-06); 0.7 s of quiet ends
    // his turn, and the final words are waited on at most `finalWait`.
    private let pauseToSend: TimeInterval = 0.7
    private let finalWait: TimeInterval = 0.4
    /// When his turn ended and when the reply was asked for: the timings
    /// logged per turn (STT final, ATLAS's answer, Fish first audio).
    private var finishedAt = Date.distantPast
    private var sentAt = Date.distantPast
    private let maxListen: TimeInterval = 30
    /// A conversation with nothing said for this long ends on its own.
    private let quietEnds: TimeInterval = 20
    /// Talking over a reply: this many dB over the quiet floor, this long.
    private var bargeMarginDB: Float { echoCancelled ? 18 : 28 }
    private let bargeHold: TimeInterval = 0.3

    private static let stopWords = try? NSRegularExpression(
        pattern: "^\\s*(?:ok(?:ay)?[,\\s]+)?(?:stop|that'?s all|that is all|good ?bye|bye|"
            + "never ?mind|thanks,? that'?s all|that'?s it)\\b[\\s.!]*$",
        options: [.caseInsensitive])

    // MARK: the keys

    func pressed() {
        if keyDown { return }               // key repeat
        keyDown = true
        pressedAt = Date()
        switch state {
        case .idle:
            hint = "Talk, then pause — or tap ⌥⇧Space again to send."
            startTurn(conversation: false, cue: false)
        case .listening where tapMode && !conversation: finish(reason: "second tap")
        case .replying where !conversation:
            // Pressing again while ATLAS answers: it stops, and he talks.
            stopPlaying()
            state = .idle
            startTurn(conversation: false, cue: false)
        default: break
        }
    }

    func released() {
        keyDown = false
        guard state == .listening, !conversation else { return }
        if Date().timeIntervalSince(pressedAt) >= holdThreshold && !tapMode {
            finish(reason: "released")
        } else if !tapMode {
            tapMode = true
            hud.show("Listening…", said: latest.isEmpty ? nil : latest, outcome: hint)
            armPauseTimer()
        }
    }

    /// A tap of ⌘ on its own: open a conversation, or end the one that is
    /// open (sending what he has said first, if anything).
    func commandTapped() {
        if state == .idle {
            hint = "Talk — I'll answer and listen again. Tap ⌘ or Esc to end."
            startTurn(conversation: true, cue: sayListening)
            return
        }
        if state == .listening && !latest.isEmpty {
            endAfterReply = true
            finish(reason: "⌘ tap")
            return
        }
        endConversation(heading: "Talk later", note: nil)
    }

    /// Esc: stop listening and talking, send nothing. True when there was
    /// something to stop.
    func cancel() -> Bool {
        guard state != .idle else { return false }
        log("cancelled (Esc)")
        endConversation(heading: "Cancelled", note: "Nothing more was sent.")
        return true
    }

    /// A one-off message in the HUD (the Accessibility note).
    func note(_ heading: String, _ text: String, linger: TimeInterval = 14) {
        hud.show(heading, said: "", outcome: text, linger: linger)
    }

    // MARK: a turn

    private func startTurn(conversation open: Bool, cue: Bool) {
        // A kept line still playing from the last turn (endSaying) stops
        // first: the recognizer must never be fed ATLAS's own voice.
        stopPlaying()
        conversation = open
        // A conversation ends each turn on a pause; ⌥⇧Space held ends on
        // letting go (released() turns a short press into a tap).
        tapMode = open
        endAfterReply = false
        failedStarts = 0
        hud.reset()
        hud.show("Listening…", outcome: hint)
        guard permitted() else { conversation = false; return }
        guard prepareEngine() else {
            conversation = false
            hud.show("Can't listen", outcome: "The microphone didn't start.", linger: 6)
            return
        }
        if cue {
            // Said BEFORE the recognizer is fed, so it never hears "Listening".
            state = .cue
            clip("listening", cachedOnly: false) { [weak self] data in
                guard let self, self.state == .cue else { return }
                guard let data else { self.listen(); return }
                self.play(data) { [weak self] _ in
                    guard let self, self.state == .cue else { return }
                    self.listen()
                }
            }
        } else {
            listen()
        }
    }

    private func permitted() -> Bool {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: break
        case .notDetermined:
            hud.show("Speech recognition",
                     outcome: "macOS is asking once — allow it, then tap ⌘ again.", linger: 6)
            SFSpeechRecognizer.requestAuthorization { status in
                log("speech recognition: \(status.rawValue)")
            }
            return false
        default:
            hud.show("Can't listen", outcome: "Speech Recognition is off for ATLAS Menu: System Settings → Privacy & Security → Speech Recognition.",
                     linger: 7)
            return false
        }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined:
            hud.show("Microphone", outcome: "macOS is asking once — allow it, then tap ⌘ again.",
                     linger: 6)
            AVCaptureDevice.requestAccess(for: .audio) { ok in log("microphone: \(ok)") }
            return false
        default:
            hud.show("Can't listen", outcome: "The microphone is off for ATLAS Menu: System Settings → Privacy & Security → Microphone.",
                     linger: 7)
            return false
        }
    }

    private func listen() {
        let rec = SFSpeechRecognizer(locale: Locale.current) ?? SFSpeechRecognizer(
            locale: Locale(identifier: "en-US"))
        guard let rec, rec.isAvailable else {
            endConversation(heading: "Can't listen",
                            note: "Speech recognition isn't available right now.")
            return
        }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.addsPunctuation = true          // commas help split "open Notes, then…"
        if rec.supportsOnDeviceRecognition {
            req.requiresOnDeviceRecognition = true
        }
        recognizer = rec
        request = req
        latest = ""
        loudSince = nil
        floorDB = -60
        state = .listening
        lock.lock(); feeding = req; lock.unlock()
        log("listening (on device: \(rec.supportsOnDeviceRecognition), "
            + "echo cancelled: \(echoCancelled), conversation: \(conversation))")
        hud.show("Listening…", said: "", outcome: hint)
        capTimer?.invalidate()
        capTimer = Timer.scheduledTimer(withTimeInterval: maxListen, repeats: false) {
            [weak self] _ in self?.finish(reason: "time limit")
        }
        silenceTimer?.invalidate()
        if conversation {
            silenceTimer = Timer.scheduledTimer(withTimeInterval: quietEnds, repeats: false) {
                [weak self] _ in
                guard let self, self.state == .listening, self.latest.isEmpty else { return }
                self.endConversation(heading: "Talk later", note: "Nothing said for a while.")
            }
        }
        task = rec.recognitionTask(with: req) { [weak self] result, error in
            DispatchQueue.main.async { self?.heard(result, error) }
        }
    }

    private func heard(_ result: SFSpeechRecognitionResult?, _ error: Error?) {
        if let result {
            let text = result.bestTranscription.formattedString
            if result.isFinal {
                send(final: text)
                return
            }
            if state == .listening && text != latest {
                latest = text
                failedStarts = 0
                hud.show("Listening…", said: text, outcome: hint)
                if tapMode { armPauseTimer() }
            }
        } else if let error, state == .listening || state == .finishing {
            log("recognition ended: \(error.localizedDescription)")
            stopFeeding()
            if conversation && failedStarts < 3 {
                // Silence ends a recognition task; the conversation goes on
                // until its own quiet limit.
                failedStarts += 1
                listen()
                return
            }
            endSaying("didnt_catch", heading: "Didn't catch that", note: "Nothing was sent.")
        }
    }

    private func armPauseTimer() {
        pauseTimer?.invalidate()
        guard !latest.isEmpty else { return }   // wait for a first word
        pauseTimer = Timer.scheduledTimer(withTimeInterval: pauseToSend, repeats: false) {
            [weak self] _ in self?.finish(reason: "pause")
        }
    }

    /// Stop feeding the recognizer and wait for its final words -- briefly
    /// (Miro, 2026-10-06: "should answer once I stop speaking"). On-device
    /// the final result follows within a few hundred milliseconds; if it has
    /// not come by `finalWait`, the words heard by then, which the pause has
    /// already settled, are sent.
    private func finish(reason: String) {
        guard state == .listening else { return }
        state = .finishing
        finishedAt = Date()
        log("finishing (\(reason))")
        pauseTimer?.invalidate()
        capTimer?.invalidate()
        silenceTimer?.invalidate()
        stopFeeding()
        request?.endAudio()
        hud.show("Working…", said: latest)
        finalTimer = Timer.scheduledTimer(withTimeInterval: finalWait, repeats: false) {
            [weak self] _ in
            guard let self, self.state == .finishing else { return }
            if !self.latest.isEmpty {
                log("timing: no final result in \(Int(self.finalWait * 1000)) ms; sending the settled words")
                let words = self.latest
                self.task?.cancel()
                self.send(final: words)
                return
            }
            self.task?.cancel()
            if self.conversation && !self.endAfterReply {
                self.hud.show("Didn't catch that", said: self.latest,
                              outcome: "The words never settled. Listening again.")
                self.listen()
            } else {
                self.endSaying("didnt_catch", heading: "Didn't catch that",
                               note: "The words never settled, so nothing was sent.")
            }
        }
    }

    private func stopFeeding() {
        lock.lock(); feeding = nil; lock.unlock()
    }

    // MARK: sending

    private func send(final text: String) {
        finalTimer?.invalidate()
        pauseTimer?.invalidate()
        capTimer?.invalidate()
        silenceTimer?.invalidate()
        stopFeeding()
        task = nil
        request = nil
        let said = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard state == .listening || state == .finishing else { return }
        sentAt = Date()
        if state == .finishing {
            log("timing: words final \(Self.ms(since: finishedAt)) ms after he stopped")
        } else {
            finishedAt = sentAt
        }
        guard !said.isEmpty else {
            if conversation && !endAfterReply { listen() } else {
                endSaying("didnt_catch", heading: "Didn't catch that", note: "Nothing was sent.")
            }
            return
        }
        if conversation, let stop = Self.stopWords,
           stop.firstMatch(in: said, range: NSRange(said.startIndex..., in: said)) != nil {
            log("conversation ended by: \(said)")
            state = .replying
            hud.show("Talk later", said: said)
            clip("bye", cachedOnly: false) { [weak self] data in
                guard let self else { return }
                guard let data else { self.endConversation(heading: "Talk later", note: nil); return }
                self.play(data) { [weak self] _ in
                    self?.endConversation(heading: "Talk later", note: nil)
                }
            }
            return
        }
        state = .sending
        hud.show("Working…", said: said)
        // The Air Glass window, when open, takes its own commands first: the
        // words stay on this Mac and nothing is sent.
        guard let local = localHandler else { sendToAtlas(said); return }
        local(said) { [weak self] took in
            guard let self else { return }
            if took { self.reply(heading: "Air Glass", said: said, shown: "Done.", say: nil) }
            else { self.sendToAtlas(said) }
        }
    }

    private func sendToAtlas(_ said: String) {
        post(commandURL, said) { [weak self] reply in
            guard let self, self.state == .sending else { return }
            switch reply {
            case .failure(let why):
                self.endSaying(why.unreachable ? "unreachable" : nil,
                               heading: "Not sent", note: why.description, said: said)
            case .success(let body):
                self.refreshPack()              // ATLAS is reachable: keep the pack fresh
                if (body["handled"] as? Bool) == false {
                    self.hud.show("Asking ATLAS…", said: said)
                    self.post(askURL, said) { [weak self] reply in
                        guard let self, self.state == .sending else { return }
                        switch reply {
                        case .failure(let why):
                            self.endSaying(why.unreachable ? "unreachable" : nil,
                                           heading: "Not sent", note: why.description,
                                           said: said)
                        case .success(let body):
                            log("timing: ATLAS answered \(Self.ms(since: self.sentAt)) ms "
                                + "after the words were sent (server: \(body["ms"] ?? "?") ms)")
                            let answer = (body["answer"] as? String)
                                ?? (body["error"] as? String) ?? "No answer."
                            // Said as well as shown: the server's spoken line,
                            // the "no model" line included.
                            self.reply(heading: "ATLAS", said: said, shown: answer,
                                       say: (body["spoken"] as? String) ?? answer)
                        }
                    }
                    return
                }
                let line = (body["answer"] as? String) ?? "Done."
                if let window = body["window"] as? String { self.onWindow?(window) }
                let asked = body["ask"] as? String
                let ran = (body["ran"] as? [[String: Any]]) ?? []
                let failed = ran.contains { ($0["ok"] as? Bool) == false }
                self.reply(heading: asked != nil ? "One question" : (failed ? "Stopped" : "Done"),
                           said: said, shown: asked ?? line, say: asked ?? line)
            }
        }
    }

    /// Show ATLAS's reply, say it in Fish Audio, then listen again (in a
    /// conversation) or stop.
    private func reply(heading: String, said: String, shown: String, say: String?) {
        log("\(heading): \(shown)")
        state = .replying
        hud.show(heading, said: said, outcome: shown)
        guard let say, !say.isEmpty else { afterReply(); return }
        speak(say) { [weak self] voiced in
            guard let self, self.state == .replying else { return }
            if !voiced {
                self.hud.show(heading, said: said,
                              outcome: shown + "\n(I couldn't reach my voice, so here it is in text.)")
            }
            self.afterReply()
        }
    }

    private func afterReply() {
        if conversation && !endAfterReply {
            listen()
        } else {
            state = .idle
            conversation = false
            endAfterReply = false
            shutEngine()
            hud.fade(after: 9)
        }
    }

    private func endConversation(heading: String, note: String?, said: String? = nil) {
        pauseTimer?.invalidate()
        capTimer?.invalidate()
        finalTimer?.invalidate()
        silenceTimer?.invalidate()
        stopFeeding()
        task?.cancel()
        task = nil
        request = nil
        stopPlaying()
        shutEngine()
        conversation = false
        endAfterReply = false
        state = .idle
        log("\(heading)\(note.map { ": \($0)" } ?? "")")
        hud.show(heading, said: said ?? latest, outcome: note, linger: note == nil ? 3 : 7)
    }

    /// End the turn and say one kept offline line (`line`, a pack id) in
    /// ATLAS's Fish voice. Only a clip already on this Mac is played -- no
    /// network wait while ATLAS is out of reach -- and with none kept the
    /// text in the HUD is all there is.
    private func endSaying(_ line: String?, heading: String, note: String?,
                           said: String? = nil) {
        endConversation(heading: heading, note: note, said: said)
        guard let line, let data = localClip(line) else { return }
        play(data) { [weak self] _ in
            // A new turn started meanwhile owns the engine; leave it be.
            guard let self, self.state == .idle else { return }
            self.shutEngine()
        }
    }

    /// Fetch the offline pack when it is due (VoicePack throttles).
    func refreshPack(force: Bool = false) {
        pack.onChange = { [weak self] in self?.clips.removeAll() }
        pack.refresh(fetch: { [weak self] url, then in
            guard let self else { return }
            self.call(url, method: "GET", json: nil, timeout: 20, then: then)
        }, force: force)
    }

    // MARK: the voice: Fish Audio only

    /// Say `text` in ATLAS's Fish voice, sentence by sentence: every
    /// sentence is sent to Fish Audio at once and the first plays as soon as
    /// its audio is back, while the rest are still being made -- the first
    /// words are heard after one short sentence's synthesis, not the whole
    /// reply's. When Fish cannot voice the first sentence (not 200, no
    /// audio, 15 s), ATLAS's kept Fish clip "I couldn't reach my voice"
    /// plays if there is one; `voiced` is false and the reply stays as
    /// text. There is no system voice.
    private func speak(_ text: String, then: @escaping (Bool) -> Void) {
        let parts = PushToTalk.sentences(
            String(text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(1400)))
        guard !parts.isEmpty else { then(false); return }
        speakRun += 1
        let run = speakRun
        var audio = [Data?](repeating: nil, count: parts.count)
        var arrived = [Bool](repeating: false, count: parts.count)
        var next = 0
        var busy = false
        var voiced = false
        let asked = Date()
        func pump() {
            guard run == speakRun, !busy else { return }
            guard next < parts.count else { then(voiced); return }
            guard arrived[next] else { return }
            guard let data = audio[next] else {
                if next == 0 {
                    // Not even the first sentence: say so in Fish, once.
                    busy = true
                    clip("no_voice", cachedOnly: true) { [weak self] clip in
                        guard let self, run == self.speakRun else { return }
                        guard let clip else { then(false); return }
                        self.play(clip) { _ in then(false) }
                    }
                    return
                }
                next += 1                       // that sentence is on screen
                pump()
                return
            }
            if next == 0 {
                log("timing: first audio \(Self.ms(since: asked)) ms after asking Fish, "
                    + "\(Self.ms(since: finishedAt)) ms after he stopped talking")
            }
            busy = true
            voiced = true
            play(data) { finished in
                busy = false
                guard run == self.speakRun else { return }
                guard finished else { then(voiced); return }
                next += 1
                pump()
            }
        }
        for (i, part) in parts.enumerated() {
            call(speakURL, method: "POST", json: ["text": part], timeout: 15) {
                [weak self] status, data, error in
                guard let self, run == self.speakRun else { return }
                if status == 200, let data, !data.isEmpty {
                    audio[i] = data
                    if self.voiceDown { self.voiceDown = false; log("Fish Audio is speaking again") }
                } else if !self.voiceDown {
                    self.voiceDown = true
                    let why = error?.localizedDescription ?? "HTTP \(status)"
                    log("Fish Audio couldn't speak (\(why)); text only")
                }
                arrived[i] = true
                pump()
            }
        }
    }

    /// A reply in sentences for speaking: at most four, a short one joined
    /// to the next so Fish is never asked for a single word.
    static func sentences(_ text: String) -> [String] {
        guard !text.isEmpty else { return [] }
        let pattern = try? NSRegularExpression(pattern: "(?<=[.!?])\\s+")
        let range = NSRange(text.startIndex..., in: text)
        var cuts: [String] = []
        var start = text.startIndex
        pattern?.enumerateMatches(in: text, range: range) { match, _, _ in
            guard let match, let r = Range(match.range, in: text) else { return }
            cuts.append(String(text[start..<r.lowerBound]))
            start = r.upperBound
        }
        cuts.append(String(text[start...]))
        var out: [String] = []
        for cut in cuts.map({ $0.trimmingCharacters(in: .whitespaces) }) where !cut.isEmpty {
            if let last = out.last, last.count < 24 {
                out[out.count - 1] = last + " " + cut
            } else {
                out.append(cut)
            }
        }
        if out.count > 4 {
            out = Array(out.prefix(3)) + [out.dropFirst(3).joined(separator: " ")]
        }
        return out
    }

    static func ms(since date: Date) -> Int { Int(Date().timeIntervalSince(date) * 1000) }

    /// A fixed line in ATLAS's Fish voice kept on this Mac: from memory,
    /// the offline pack (checked against its hash), or
    /// ~/Library/Caches/ATLAS Menu. Never the network.
    private func localClip(_ name: String) -> Data? {
        if let data = clips[name] { return data }
        if let data = pack.clip(name) {
            clips[name] = data
            return data
        }
        let file = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent("ATLAS Menu/\(name).mp3")
        if let file, let data = try? Data(contentsOf: file), !data.isEmpty {
            clips[name] = data
            return data
        }
        return nil
    }

    /// A fixed line in ATLAS's Fish voice: kept on this Mac (localClip), or
    /// from /api/voice/clip (then kept in ~/Library/Caches/ATLAS Menu).
    private func clip(_ name: String, cachedOnly: Bool, then: @escaping (Data?) -> Void) {
        if let data = localClip(name) { then(data); return }
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent("ATLAS Menu", isDirectory: true)
        let file = dir?.appendingPathComponent("\(name).mp3")
        var parts = URLComponents(url: atlasURL("/api/voice/clip/\(name)"), resolvingAgainstBaseURL: false)!
        if cachedOnly { parts.queryItems = [URLQueryItem(name: "cached_only", value: "true")] }
        call(parts.url!, method: "GET", json: nil, timeout: 10) { [weak self] status, data, _ in
            guard status == 200, let data, !data.isEmpty else { then(nil); return }
            self?.clips[name] = data
            if let dir, let file {
                try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                try? data.write(to: file)
            }
            then(data)
        }
    }

    // MARK: audio

    /// The mic and ATLAS's voice on one engine. Measured on this Mac
    /// (2026-10-06, a headless run of this sequence): with voice processing
    /// enabled before the engine has built its output, `start()` fails with
    /// -10875; touching the output and mixer first, it starts, the mic
    /// arrives as 9 deinterleaved channels (channel 0 is the processed
    /// voice), and a 44.1 kHz clip re-linked to the running mixer plays to
    /// the end. If it still will not start with echo cancellation, it is
    /// tried once without.
    private func prepareEngine() -> Bool {
        if engineReady { return true }
        if startEngine(echoCancellation: true) { return true }
        log("audio engine would not start with echo cancellation; trying without")
        return startEngine(echoCancellation: false)
    }

    private func startEngine(echoCancellation: Bool) -> Bool {
        _ = engine.outputNode               // build the I/O before voice processing
        _ = engine.mainMixerNode
        let input = engine.inputNode
        do {
            try input.setVoiceProcessingEnabled(echoCancellation)
            echoCancelled = echoCancellation
        } catch {
            echoCancelled = false
            log("echo cancellation unavailable: \(error.localizedDescription)")
        }
        if engine.attachedNodes.contains(playerNode) == false { engine.attach(playerNode) }
        engine.connect(playerNode, to: engine.mainMixerNode, format: nil)
        let format = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.heardAudio(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            log("audio engine failed (echo cancellation \(echoCancellation)): "
                + error.localizedDescription)
            input.removeTap(onBus: 0)
            if engine.isRunning { engine.stop() }
            return false
        }
        log("audio engine started: mic \(format.channelCount) ch at \(Int(format.sampleRate)) Hz, "
            + "echo cancelled: \(echoCancelled)")
        engineReady = true
        return true
    }

    /// The recognizer is given one channel: with voice processing the mic
    /// arrives as several, and channel 0 is the processed voice.
    private static func mono(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        guard buffer.format.channelCount > 1, let source = buffer.floatChannelData,
              let format = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                         sampleRate: buffer.format.sampleRate,
                                         channels: 1, interleaved: false),
              let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: buffer.frameLength),
              let target = out.floatChannelData else { return buffer }
        out.frameLength = buffer.frameLength
        target[0].update(from: source[0], count: Int(buffer.frameLength))
        return out
    }

    private func shutEngine() {
        stopPlaying()
        if engine.isRunning { engine.stop() }
        engine.inputNode.removeTap(onBus: 0)
        engineReady = false
    }

    /// Play Fish audio (mp3) through the engine, so the echo canceller hears
    /// it as ATLAS's own. `then(true)` when it played to the end.
    private func play(_ data: Data, then: @escaping (Bool) -> Void) {
        guard prepareEngine() else { then(false); return }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("atlas-voice-\(UUID().uuidString).mp3")
        let file: AVAudioFile
        do {
            try data.write(to: url)
            file = try AVAudioFile(forReading: url)
        } catch {
            log("could not play ATLAS's voice: \(error.localizedDescription)")
            try? FileManager.default.removeItem(at: url)
            then(false)
            return
        }
        playToken += 1
        let token = playToken
        playerNode.stop()
        engine.connect(playerNode, to: engine.mainMixerNode, format: file.processingFormat)
        playerNode.scheduleFile(file, at: nil, completionCallbackType: .dataPlayedBack) {
            [weak self] _ in
            DispatchQueue.main.async {
                try? FileManager.default.removeItem(at: url)
                guard let self, self.playToken == token else { return }
                then(true)
            }
        }
        playerNode.play()
    }

    private func stopPlaying() {
        playToken += 1                       // a stopped clip never calls back
        speakRun += 1                        // nor do sentences still arriving
        playerNode.stop()
    }

    /// On the audio thread: feed the recognizer while listening, and report
    /// the level for the quiet floor and barge-in.
    private func heardAudio(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        let req = feeding
        lock.unlock()
        if let req { req.append(PushToTalk.mono(buffer)) }
        let db = PushToTalk.level(buffer)
        DispatchQueue.main.async { [weak self] in self?.level(db) }
    }

    private static func level(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let samples = buffer.floatChannelData?[0], buffer.frameLength > 0 else {
            return -100
        }
        var sum: Float = 0
        for i in 0..<Int(buffer.frameLength) { sum += samples[i] * samples[i] }
        let rms = (sum / Float(buffer.frameLength)).squareRoot()
        return rms > 0 ? 20 * log10(rms) : -100
    }

    private func level(_ db: Float) {
        if state == .listening {
            // The room's quiet floor: the quietest it has been this turn.
            floorDB = max(-75, min(floorDB, db))
            return
        }
        guard state == .replying, conversation else { loudSince = nil; return }
        if db > max(floorDB + bargeMarginDB, -45) {
            if loudSince == nil { loudSince = Date() }
            if let since = loudSince, Date().timeIntervalSince(since) >= bargeHold {
                log("barge-in: he talked over the reply")
                loudSince = nil
                stopPlaying()
                listen()
            }
        } else {
            loudSince = nil
        }
    }

    // MARK: the network

    /// POST {"transcript": …} with the dashboard's session cookie.
    private func post(_ url: URL, _ transcript: String,
                      _ then: @escaping (Result<[String: Any], Problem>) -> Void) {
        // A chain waits up to 30 s per step on the Mac, and /ask can take its
        // time: generous, and a timeout is "unknown", not failed.
        call(url, method: "POST", json: ["transcript": transcript], timeout: 120) {
            status, data, error in
            let reply: Result<[String: Any], Problem>
            if status == -1 {
                reply = .failure(Problem("Not signed in: open the popover (⌥⇧A) and sign in once."))
            } else if let error = error as? URLError, error.code == .timedOut {
                reply = .failure(Problem("No answer in time; whether it ran is unknown. Check the Mac."))
            } else if let error {
                reply = .failure(Problem("Couldn't reach ATLAS (\(error.localizedDescription)). It answers only on the mesh or at home.",
                                         unreachable: Problem.neverArrived(error)))
            } else if status == 404 && url == commandURL {
                reply = .failure(Problem("This ATLAS server doesn't have push to talk yet; it needs the new deploy. Nothing ran."))
            } else if status == 401 {
                reply = .failure(Problem("Signed out: open the popover (⌥⇧A) and sign in."))
            } else if status != 200 {
                reply = .failure(Problem("ATLAS answered \(status); nothing more is known."))
            } else if let data,
                      let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                reply = .success(body)
            } else {
                reply = .failure(Problem("ATLAS's reply couldn't be read."))
            }
            then(reply)
        }
    }

    /// One request to ATLAS with the popover's session cookie, read from its
    /// own web data store -- no other credential exists here. Calls back on
    /// the main queue; status -1 means not signed in (nothing was sent).
    private func call(_ url: URL, method: String, json: [String: Any]?, timeout: TimeInterval,
                      then: @escaping (Int, Data?, Error?) -> Void) {
        WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
            // ATLAS's exact host (no suffix match: "evil-127.0.0.1" or a
            // parent-domain cookie never qualifies) and, when the cookie is
            // port-scoped, ATLAS's exact port.
            let mine = cookies.filter { cookie in
                cookie.domain == atlasHost
                    && (cookie.portList?.contains(NSNumber(value: atlasPort)) ?? true)
            }
            guard mine.contains(where: { $0.name == sessionCookie }) else {
                DispatchQueue.main.async { then(-1, nil, nil) }
                return
            }
            var req = URLRequest(url: url, timeoutInterval: timeout)
            req.httpMethod = method
            // The cookie copied from WebKit is the only one sent; a
            // Set-Cookie in the reply never lands in URLSession's own store.
            req.httpShouldHandleCookies = false
            for (key, value) in HTTPCookie.requestHeaderFields(with: mine) {
                req.setValue(value, forHTTPHeaderField: key)
            }
            if let json {
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                req.httpBody = try? JSONSerialization.data(withJSONObject: json)
            }
            URLSession.shared.dataTask(with: req) { data, response, error in
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                DispatchQueue.main.async { then(status, data, error) }
            }.resume()
        }
    }
}

struct Problem: Error, CustomStringConvertible {
    let description: String
    /// The request never reached ATLAS (no network, no route, no host), so
    /// "I can't reach ATLAS" is known to be true. A timeout is not this: the
    /// words may have arrived and run.
    let unreachable: Bool
    init(_ text: String, unreachable: Bool = false) {
        description = text
        self.unreachable = unreachable
    }

    static func neverArrived(_ error: Error) -> Bool {
        guard let error = error as? URLError else { return false }
        return [.notConnectedToInternet, .cannotFindHost, .cannotConnectToHost,
                .dnsLookupFailed, .internationalRoamingOff, .dataNotAllowed]
            .contains(error.code)
    }
}
