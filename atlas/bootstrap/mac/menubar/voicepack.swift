// ATLAS's offline lines, kept on this Mac in its own Fish voice.
//
// Miro wants ATLAS to speak only in his chosen Fish Audio voice, including
// when ATLAS is out of reach. Whenever ATLAS answers, ATLAS Menu fetches the
// small pack from GET /api/voice/offline-pack ("Listening.", "I can't reach
// ATLAS right now.", "Sorry, I didn't catch that." and a few more), checks
// every clip against its sha256, and keeps it in
// ~/Library/Application Support/ATLAS Menu/voice-pack. Offline, the matching
// kept clip plays. A line with no kept clip is not spoken: its text is in
// the HUD. There is no system voice.
import CryptoKit
import Foundation

/// GET with the dashboard's session cookie; calls back on the main queue
/// with (status, body, error). Status -1 is "not signed in".
typealias PackFetch = (URL, @escaping (Int, Data?, Error?) -> Void) -> Void

final class VoicePack {
    /// How often a good pack is checked again, and how soon after a failed try.
    private let freshFor: TimeInterval = 6 * 3600
    private let retryAfter: TimeInterval = 10 * 60
    private var lastGood = Date.distantPast
    private var lastTry = Date.distantPast
    private var refreshing = false
    /// Called after a refresh changed what is kept (clears in-memory copies).
    var onChange: (() -> Void)?

    let dir: URL?

    /// `dir` is for a check run against a scratch folder; the app keeps the
    /// pack in Application Support.
    init(dir: URL? = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("ATLAS Menu/voice-pack", isDirectory: true)) {
        self.dir = dir
    }

    private var manifestFile: URL? { dir?.appendingPathComponent("manifest.json") }

    /// Pack ids are the server's line names: lowercase words and underscores.
    /// Anything else (a path, a dot) is refused before it names a file.
    static func validID(_ id: String) -> Bool {
        !id.isEmpty && id.count <= 40
            && id.unicodeScalars.allSatisfy { CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz_").contains($0) }
    }

    static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// The kept manifest's phrases: id -> (sha256, text).
    private func kept() -> [String: (sha: String, text: String)] {
        guard let file = manifestFile, let data = try? Data(contentsOf: file) else { return [:] }
        return Self.phrases(in: data)
    }

    static func phrases(in data: Data) -> [String: (sha: String, text: String)] {
        guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let phrases = body["phrases"] as? [String: Any] else { return [:] }
        var out: [String: (sha: String, text: String)] = [:]
        for (id, raw) in phrases {
            guard validID(id), let entry = raw as? [String: Any],
                  let sha = entry["sha256"] as? String, sha.count == 64,
                  let text = entry["text"] as? String else { continue }
            out[id] = (sha.lowercased(), text)
        }
        return out
    }

    /// A kept clip, only if it is in the kept manifest and its bytes still
    /// match the hash ATLAS gave for it. Local only: never the network.
    func clip(_ id: String) -> Data? {
        guard Self.validID(id), let dir, let want = kept()[id]?.sha else { return nil }
        guard let data = try? Data(contentsOf: dir.appendingPathComponent("\(id).mp3")),
              !data.isEmpty, Self.sha256(data) == want else { return nil }
        return data
    }

    /// Fetch the pack when it is due (or `force`). Each clip is downloaded
    /// only when its hash changed, checked, then written whole. A 503 (no
    /// voice set, Fish down with lines missing) keeps the pack already here.
    func refresh(fetch: @escaping PackFetch, force: Bool = false) {
        let now = Date()
        guard !refreshing else { return }
        if !force && (now.timeIntervalSince(lastGood) < freshFor
                      || now.timeIntervalSince(lastTry) < retryAfter) { return }
        guard let dir, let manifestURL = URL(string: "https://\(atlasHost)/api/voice/offline-pack")
        else { return }
        refreshing = true
        lastTry = now
        fetch(manifestURL) { [weak self] status, data, error in
            guard let self else { return }
            guard status == 200, let data else {
                self.refreshing = false
                let why = status == -1 ? "not signed in"
                    : error?.localizedDescription ?? Self.detail(data) ?? "HTTP \(status)"
                log("voice pack: not refreshed (\(why)); keeping what is here")
                return
            }
            let phrases = Self.phrases(in: data)
            guard !phrases.isEmpty else {
                self.refreshing = false
                log("voice pack: the manifest couldn't be read; keeping what is here")
                return
            }
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            var todo = phrases.keys.sorted().filter { id in
                guard let have = try? Data(contentsOf: dir.appendingPathComponent("\(id).mp3"))
                else { return true }
                return Self.sha256(have) != phrases[id]?.sha
            }
            var fetched = 0
            var missing: [String] = []
            func next() {
                guard let id = todo.first else {
                    self.finish(dir: dir, manifest: data, phrases: phrases,
                                fetched: fetched, missing: missing)
                    return
                }
                todo.removeFirst()
                let file = dir.appendingPathComponent("\(id).mp3")
                var parts = URLComponents(string: "https://\(atlasHost)/api/voice/clip/\(id)")!
                parts.queryItems = [URLQueryItem(name: "cached_only", value: "true")]
                fetch(parts.url!) { status, clip, _ in
                    if status == 200, let clip, Self.sha256(clip) == phrases[id]?.sha {
                        do {
                            try clip.write(to: file, options: .atomic)
                            fetched += 1
                        } catch {
                            missing.append(id)
                        }
                    } else {
                        // A stale clip (another voice, older words) is never
                        // played: gone, and that line is text until next time.
                        try? FileManager.default.removeItem(at: file)
                        missing.append(id)
                    }
                    next()
                }
            }
            next()
        }
    }

    private func finish(dir: URL, manifest: Data, phrases: [String: (sha: String, text: String)],
                        fetched: Int, missing: [String]) {
        // Clips for lines ATLAS no longer lists are dropped.
        if let files = try? FileManager.default.contentsOfDirectory(atPath: dir.path) {
            for name in files where name.hasSuffix(".mp3") {
                let id = String(name.dropLast(4))
                if phrases[id] == nil {
                    try? FileManager.default.removeItem(at: dir.appendingPathComponent(name))
                }
            }
        }
        if let file = manifestFile { try? manifest.write(to: file, options: .atomic) }
        refreshing = false
        if missing.isEmpty { lastGood = Date() }
        log("voice pack: \(phrases.count - missing.count)/\(phrases.count) lines kept"
            + (fetched > 0 ? ", \(fetched) new" : "")
            + (missing.isEmpty ? "" : "; text only for \(missing.joined(separator: ", "))"))
        onChange?()
    }

    private static func detail(_ data: Data?) -> String? {
        guard let data, let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return body["detail"] as? String
    }
}
