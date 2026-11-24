// ATLAS's offline lines in its own Fish voice, kept in this browser.
//
// Miro wants ATLAS to speak only in his chosen Fish Audio voice, including
// when ATLAS is out of reach. While ATLAS answers, the page copies the small
// pack from GET /api/voice/offline-pack into Cache Storage, checking every
// clip against its sha256. When ATLAS cannot be reached, a line that is in
// the pack plays from there. A line that is not in the pack is shown, not
// spoken. Nothing falls back to speechSynthesis. Every storage call is in a
// try/catch: a browser that refuses storage just has no offline voice.

export const PACK_URL = "/api/voice/offline-pack";
export const PACK_CACHE = "atlas-voice-pack-v1";
/** The manifest's own key in the cache (never a real route). */
export const MANIFEST_KEY = "/__atlas-voice-pack/manifest.json";

export interface PackEntry { text: string; url: string; sha256: string }
export interface PackManifest { voice: string; phrases: Record<string, PackEntry> }

/** A line as heard, not as typed: case, curly quotes, spacing and the final
 * full stop do not make two lines different. */
export function normalizeLine(text: string): string {
  return text.normalize("NFKC").toLowerCase()
    .replace(/[‘’′]/g, "'").replace(/[“”]/g, '"')
    .replace(/[–—]/g, "-")
    .replace(/\s+/g, " ").trim()
    .replace(/[\s.!…]+$/u, "");
}

/** The pack id whose text is `text`, or null. Exact lines only: a line that
 * merely resembles one in the pack is not said with that one's words. */
export function phraseFor(manifest: PackManifest | null, text: string): string | null {
  if (!manifest) return null;
  const want = normalizeLine(text);
  if (!want) return null;
  for (const [id, entry] of Object.entries(manifest.phrases)) {
    if (normalizeLine(entry.text) === want) return id;
  }
  return null;
}

/** Air Glass's line for a request that never reached ATLAS. */
export const OFFLINE_LINE = "airglass_offline";

/** The pack line to say for a failed request to ATLAS: OFFLINE_LINE when
 * fetch itself failed (a TypeError: no network, no host, refused), so
 * "I can't reach ATLAS" is true. Anything else -- an abort, a bad answer --
 * says nothing from the pack. */
export function unreachable(e: unknown): string | null {
  return e instanceof TypeError ? OFFLINE_LINE : null;
}

const ID = /^[a-z_]{1,40}$/;
const SHA = /^[0-9a-f]{64}$/;

/** The server's manifest, checked; anything malformed is left out. Clip
 * URLs must be ATLAS's own /api/voice/clip route. */
export function parseManifest(raw: unknown): PackManifest | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { voice?: unknown; phrases?: unknown };
  if (typeof r.voice !== "string" || !r.phrases || typeof r.phrases !== "object") return null;
  const phrases: Record<string, PackEntry> = {};
  for (const [id, e] of Object.entries(r.phrases as Record<string, unknown>)) {
    if (!ID.test(id) || !e || typeof e !== "object") continue;
    const { text, url, sha256 } = e as Record<string, unknown>;
    if (typeof text !== "string" || typeof url !== "string" || typeof sha256 !== "string") continue;
    if (!url.startsWith(`/api/voice/clip/${id}`) || !SHA.test(sha256)) continue;
    phrases[id] = { text, url, sha256 };
  }
  return Object.keys(phrases).length ? { voice: r.voice, phrases } : null;
}

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;
interface CacheLike {
  match(key: string): Promise<Response | undefined>;
  put(key: string, r: Response): Promise<void>;
  delete(key: string): Promise<boolean>;
  keys(): Promise<ReadonlyArray<{ url: string }>>;
}
interface CachesLike { open(name: string): Promise<CacheLike> }

function browserCaches(): CachesLike | null {
  try {
    return typeof caches === "undefined" ? null : (caches as unknown as CachesLike);
  } catch {
    return null;
  }
}

export class OfflinePack {
  manifest: PackManifest | null = null;
  private refreshing: Promise<void> | null = null;

  constructor(private readonly fetchImpl: FetchLike = (u, i) => fetch(u, i),
              private readonly store: CachesLike | null = browserCaches()) {}

  private async cache(): Promise<CacheLike | null> {
    try {
      return this.store ? await this.store.open(PACK_CACHE) : null;
    } catch {
      return null;
    }
  }

  /** The pack kept from an earlier visit, if any. */
  async load(): Promise<PackManifest | null> {
    try {
      const c = await this.cache();
      const r = await c?.match(MANIFEST_KEY);
      if (r) this.manifest = parseManifest(await r.json());
    } catch {
      this.manifest = null;
    }
    return this.manifest;
  }

  /** Copy the pack from ATLAS. A clip is fetched only when its hash changed;
   * one that does not match is dropped (that line is text until next time).
   * A 503 (no Fish voice set, or Fish down with a line missing) or no
   * network keeps the pack already here. */
  refresh(): Promise<void> {
    this.refreshing ??= this.doRefresh().finally(() => { this.refreshing = null; });
    return this.refreshing;
  }

  private async doRefresh(): Promise<void> {
    const c = await this.cache();
    if (!c) return;
    let fresh: PackManifest | null;
    try {
      const r = await this.fetchImpl(PACK_URL, { credentials: "same-origin" });
      if (!r.ok) return;
      fresh = parseManifest(await r.json());
    } catch {
      return;
    }
    if (!fresh) return;
    const wanted = new Set<string>();
    for (const entry of Object.values(fresh.phrases)) {
      wanted.add(entry.url);
      try {
        if (await this.verified(c, entry)) continue;
        await c.delete(entry.url);
        const r = await this.fetchImpl(entry.url, { credentials: "same-origin" });
        if (!r.ok) continue;
        const buf = await r.arrayBuffer();
        if ((await sha256Hex(buf)) !== entry.sha256) continue;
        await c.put(entry.url, new Response(buf, { headers: { "Content-Type": "audio/mpeg" } }));
      } catch {
        /* that line stays text */
      }
    }
    try {
      for (const req of await c.keys()) {
        const u = new URL(req.url, "https://atlas.invalid");
        const path = u.pathname + u.search;
        if (path !== MANIFEST_KEY && !wanted.has(path)) await c.delete(req.url);
      }
      await c.put(MANIFEST_KEY, new Response(JSON.stringify(fresh),
        { headers: { "Content-Type": "application/json" } }));
      this.manifest = fresh;
    } catch {
      /* the old manifest stays */
    }
  }

  private async verified(c: CacheLike, entry: PackEntry): Promise<Blob | null> {
    const r = await c.match(entry.url);
    if (!r) return null;
    const buf = await r.arrayBuffer();
    if ((await sha256Hex(buf)) !== entry.sha256) return null;
    return new Blob([buf], { type: "audio/mpeg" });
  }

  /** A kept clip by pack id, only if its bytes match the manifest. Never
   * the network. */
  async clip(id: string): Promise<Blob | null> {
    const entry = this.manifest?.phrases[id];
    if (!entry) return null;
    try {
      const c = await this.cache();
      return c ? await this.verified(c, entry) : null;
    } catch {
      return null;
    }
  }

  /** The kept clip whose text is exactly `text`, or null. */
  async clipFor(text: string): Promise<Blob | null> {
    const id = phraseFor(this.manifest, text);
    return id ? this.clip(id) : null;
  }
}
