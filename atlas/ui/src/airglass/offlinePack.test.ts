import { describe, expect, it } from "vitest";
import {
  MANIFEST_KEY, normalizeLine, OFFLINE_LINE, OfflinePack, PACK_URL, parseManifest, phraseFor,
  sha256Hex, unreachable, type PackManifest,
} from "./offlinePack";

const bytes = (s: string): ArrayBuffer => new TextEncoder().encode(s).buffer as ArrayBuffer;

async function serverPack(clips: Record<string, [string, string]>): Promise<PackManifest> {
  const phrases: PackManifest["phrases"] = {};
  for (const [id, [text, audio]] of Object.entries(clips)) {
    phrases[id] = { text, url: `/api/voice/clip/${id}?cached_only=true`, sha256: await sha256Hex(bytes(audio)) };
  }
  return { voice: "v1", phrases };
}

describe("a line is matched to a kept Fish clip only when it is the same line", () => {
  const manifest: PackManifest = { voice: "v", phrases: {
    unreachable: { text: "I can't reach ATLAS right now.", url: "/api/voice/clip/unreachable", sha256: "0".repeat(64) },
    listening: { text: "Listening.", url: "/api/voice/clip/listening", sha256: "1".repeat(64) },
  } };

  it("case, curly quotes, spacing and the final full stop do not matter", () => {
    expect(phraseFor(manifest, "I can’t reach ATLAS right now")).toBe("unreachable");
    expect(phraseFor(manifest, "  i can't   reach atlas right now.  ")).toBe("unreachable");
    expect(phraseFor(manifest, "Listening")).toBe("listening");
    expect(normalizeLine("Listening…")).toBe("listening");
  });

  it("anything else is not a pack line: it stays text", () => {
    expect(phraseFor(manifest, "I can't reach ATLAS right now, try later.")).toBeNull();
    expect(phraseFor(manifest, "The store made $40 today.")).toBeNull();
    expect(phraseFor(manifest, "")).toBeNull();
    expect(phraseFor(null, "Listening.")).toBeNull();
  });

  it("only a fetch that never arrived is 'I can't reach ATLAS'", () => {
    expect(unreachable(new TypeError("Failed to fetch"))).toBe(OFFLINE_LINE);
    expect(unreachable(new DOMException("aborted", "AbortError"))).toBeNull();
    expect(unreachable(new Error("bad json"))).toBeNull();
  });
});

describe("the manifest is checked before anything is kept", () => {
  it("drops ids that could name another route, foreign urls and bad hashes", () => {
    const m = parseManifest({ voice: "v", phrases: {
      ok: { text: "Listening.", url: "/api/voice/clip/ok?cached_only=true", sha256: "a".repeat(64) },
      "../x": { text: "x", url: "/api/voice/clip/../x", sha256: "a".repeat(64) },
      evil: { text: "x", url: "https://elsewhere.example/evil.mp3", sha256: "a".repeat(64) },
      short: { text: "x", url: "/api/voice/clip/short", sha256: "abc" },
    } });
    expect(Object.keys(m?.phrases ?? {})).toEqual(["ok"]);
    expect(parseManifest({ detail: "503" })).toBeNull();
    expect(parseManifest(null)).toBeNull();
  });
});

/** Cache Storage, in memory. */
function memoryCaches(): { open: (n: string) => Promise<{
  match: (k: string) => Promise<Response | undefined>; put: (k: string, r: Response) => Promise<void>;
  delete: (k: string) => Promise<boolean>; keys: () => Promise<Array<{ url: string }>>; }>;
  store: Map<string, ArrayBuffer>; } {
  const store = new Map<string, ArrayBuffer>();
  const cache = {
    match: async (k: string) => (store.has(k) ? new Response(store.get(k)!.slice(0)) : undefined),
    put: async (k: string, r: Response) => { store.set(k, await r.arrayBuffer()); },
    delete: async (k: string) => store.delete(k),
    keys: async () => [...store.keys()].map((k) => ({ url: `https://atlas.example.com${k}` })),
  };
  return { open: async () => cache, store };
}

function server(manifest: () => PackManifest | null, audio: Record<string, string>, log: string[]) {
  return async (url: string): Promise<Response> => {
    log.push(url);
    if (url === PACK_URL) {
      const m = manifest();
      return m ? new Response(JSON.stringify(m)) : new Response(JSON.stringify({ detail: "no voice" }), { status: 503 });
    }
    const id = /\/api\/voice\/clip\/([a-z_]+)/.exec(url)?.[1] ?? "";
    return id in audio ? new Response(bytes(audio[id] ?? "")) : new Response("", { status: 404 });
  };
}

describe("the pack kept in this browser", () => {
  it("keeps every clip, plays the matching one offline, and says nothing for others", async () => {
    const audio = { unreachable: "ID3away", listening: "ID3listen" };
    const m = await serverPack({ unreachable: ["I can't reach ATLAS right now.", audio.unreachable],
      listening: ["Listening.", audio.listening] });
    const caches = memoryCaches();
    const log: string[] = [];
    const pack = new OfflinePack(server(() => m, audio, log), caches);
    await pack.refresh();
    expect(caches.store.has(MANIFEST_KEY)).toBe(true);

    // A later visit with ATLAS gone: everything comes from the cache.
    const offline = new OfflinePack(async () => { throw new TypeError("Failed to fetch"); }, caches);
    await offline.load();
    await offline.refresh();                         // fails quietly, keeps the pack
    const blob = await offline.clipFor("I can't reach ATLAS right now.");
    expect(blob && new TextDecoder().decode(await blob.arrayBuffer())).toBe("ID3away");
    expect(await offline.clip("listening")).not.toBeNull();
    expect(await offline.clipFor("Something ATLAS never kept.")).toBeNull();
    expect(await offline.clip("bye")).toBeNull();
  });

  it("downloads a clip again only when its hash changed, and never plays a bad one", async () => {
    const audio: Record<string, string> = { listening: "ID3listen" };
    let m = await serverPack({ listening: ["Listening.", "ID3listen"] });
    const caches = memoryCaches();
    const log: string[] = [];
    const pack = new OfflinePack(server(() => m, audio, log), caches);
    await pack.refresh();
    log.length = 0;
    await pack.refresh();
    expect(log).toEqual([PACK_URL]);                  // unchanged: manifest only

    // A new voice whose download comes back wrong: dropped, the line is text.
    m = await serverPack({ listening: ["Listening.", "ID3newvoice"] });
    await pack.refresh();
    expect(await pack.clip("listening")).toBeNull();
    audio.listening = "ID3newvoice";
    await pack.refresh();
    const blob = await pack.clip("listening");
    expect(blob && new TextDecoder().decode(await blob.arrayBuffer())).toBe("ID3newvoice");
  });

  it("a 503 (no Fish voice set) keeps the pack already here", async () => {
    const audio = { listening: "ID3listen" };
    const good = await serverPack({ listening: ["Listening.", "ID3listen"] });
    let m: PackManifest | null = good;
    const caches = memoryCaches();
    const pack = new OfflinePack(server(() => m, audio, []), caches);
    await pack.refresh();
    m = null;
    await pack.refresh();
    expect(await pack.clip("listening")).not.toBeNull();
  });

  it("no Cache Storage at all: no offline voice, and nothing throws", async () => {
    const pack = new OfflinePack(async () => new Response("{}"), null);
    expect(await pack.load()).toBeNull();
    await pack.refresh();
    expect(await pack.clipFor("Listening.")).toBeNull();
    const broken = new OfflinePack(async () => new Response("{}"),
      { open: async () => { throw new Error("SecurityError"); } });
    await broken.refresh();
    expect(await broken.load()).toBeNull();
  });
});
