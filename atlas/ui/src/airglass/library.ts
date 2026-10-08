// The free 3D library on Air Glass's fourth shelf: Poly Haven (CC0) and the
// openly licensed Khronos glTF sample models, searched by ATLAS
// (GET /api/airglass/library) and fetched through it (/library/file): the
// dashboard's CSP lets this page talk to ATLAS only, so it never fetches the
// sources itself. A model's files are fetched as bytes and parsed by the same
// no-URL loader as a model from disk (scene.ts::parseModel).
//
// Everything a source says (names, credits, descriptions) is shown as text,
// never followed. A size is the source's own figure, or "size not listed".

/** One model as the server lists it. */
export interface LibraryModel {
  id: string;
  name: string;
  source: string;
  license: string;
  licenseName: string;
  attribution: string;
  creditRequired: boolean;
  description: string;
  thumb: string | null;
  url: string;
  main: string;
  sizeBytes: number | null;
}

export interface LibraryResult {
  query: string;
  models: LibraryModel[];
  /** Per source: "ok", or the words for why it did not answer. */
  sources: Record<string, string>;
  readAt: Date | null;
}

/** The only path a library file or picture may come from (same-origin). */
const OWN = "/api/airglass/library/";
export const MAX_MODEL_BYTES = 60 * 1024 * 1024;   // all files of one model, here

const str = (v: unknown, cap = 300): string => (typeof v === "string" ? v.slice(0, cap) : "");
const own = (v: unknown): string | null =>
  typeof v === "string" && v.startsWith(OWN) && !v.includes("//") ? v : null;

/** The server's answer, kept only where it has the shape it should. */
export function parseLibrary(raw: unknown): LibraryResult {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const models: LibraryModel[] = [];
  for (const x of Array.isArray(r.results) ? r.results : []) {
    if (!x || typeof x !== "object") continue;
    const m = x as Record<string, unknown>;
    const id = str(m.id, 120), url = own(m.url), main = str(m.main, 120);
    if (!id || !url || !/\.(glb|gltf)$/i.test(main)) continue;
    const size = typeof m.size_bytes === "number" && Number.isFinite(m.size_bytes) && m.size_bytes >= 0
      ? m.size_bytes : null;
    models.push({
      id, url, main, name: str(m.name, 80) || id, source: str(m.source, 60),
      license: str(m.license, 30), licenseName: str(m.license_name, 30) || str(m.license, 30),
      attribution: str(m.attribution, 300), creditRequired: m.credit_required === true,
      description: str(m.description, 300), thumb: own(m.thumb), sizeBytes: size,
    });
  }
  const sources: Record<string, string> = {};
  if (r.sources && typeof r.sources === "object") {
    for (const [k, v] of Object.entries(r.sources as Record<string, unknown>)) sources[k] = str(v, 200);
  }
  const at = typeof r.read_at === "string" ? new Date(r.read_at) : null;
  return { query: str(r.query, 80), models, sources,
    readAt: at && !Number.isNaN(at.getTime()) ? at : null };
}

/** The sources that did not answer, in words, or "" when all did. */
export function sourcesDown(res: LibraryResult): string {
  return Object.values(res.sources).filter((v) => v && v !== "ok").join("; ");
}

/** "2.3 MB", or the honest "size not listed". */
export function formatSize(bytes: number | null): string {
  if (bytes === null) return "size not listed";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The files a .gltf names besides itself (buffers and images; data: URIs
 * are inside it already). Order kept, duplicates once. */
export function externalUris(gltf: unknown): string[] {
  const j = (gltf && typeof gltf === "object" ? gltf : {}) as { buffers?: unknown; images?: unknown };
  const out: string[] = [];
  for (const list of [j.buffers, j.images]) {
    if (!Array.isArray(list)) continue;
    for (const item of list) {
      const uri = (item as { uri?: unknown } | null)?.uri;
      if (typeof uri === "string" && uri && !uri.startsWith("data:") && !out.includes(uri)) out.push(uri);
    }
  }
  return out;
}

/** The loader looks files up by base name (scene.ts), so two parts with the
 * same base name would be ambiguous: that is refused, not guessed. */
export function baseName(uri: string): string {
  const last = uri.split(/[\\/]/).pop() ?? uri;
  try { return decodeURIComponent(last); } catch { return last; }
}

/** What ATLAS says about a library model: the source's own words, its
 * license and credit; nothing added. */
export function explainLibrary(m: LibraryModel): string {
  const about = m.description ? ` ${m.description}` : "";
  const credit = m.creditRequired && m.attribution ? ` Credit: ${m.attribution}.` : "";
  return `${m.name}: from ${m.source}, ${m.licenseName}.${about}${credit}`;
}

async function detail(r: Response, fallback: string): Promise<string> {
  const body = (await r.json().catch(() => ({}))) as { detail?: unknown };
  return typeof body.detail === "string" ? body.detail : `${fallback} (${r.status})`;
}

/** Search; throws with the server's own words when the library did not answer. */
export async function searchLibrary(q: string): Promise<LibraryResult> {
  const r = await fetch(`${OWN.slice(0, -1)}?q=${encodeURIComponent(q)}`,
    { credentials: "same-origin" });
  if (!r.ok) throw new Error(await detail(r, "the library search failed"));
  return parseLibrary(await r.json());
}

/** Every file of one model, through ATLAS, keyed by base name for the loader. */
export async function fetchModelFiles(m: LibraryModel): Promise<Map<string, ArrayBuffer>> {
  const files = new Map<string, ArrayBuffer>();
  let total = 0;
  const get = async (part: string | null): Promise<ArrayBuffer> => {
    const r = await fetch(part === null ? m.url : `${m.url}&part=${encodeURIComponent(part)}`,
      { credentials: "same-origin" });
    if (!r.ok) throw new Error(await detail(r, "the model could not be fetched"));
    const buf = await r.arrayBuffer();
    total += buf.byteLength;
    if (total > MAX_MODEL_BYTES) throw new Error("that model is larger than 60 MB in all");
    return buf;
  };
  const main = await get(null);
  files.set(m.main, main);
  if (/\.gltf$/i.test(m.main)) {
    let json: unknown;
    try { json = JSON.parse(new TextDecoder().decode(main)); } catch { throw new Error("the library sent a broken .gltf"); }
    for (const uri of externalUris(json)) {
      const name = baseName(uri);
      if (files.has(name)) throw new Error(`two files of that model are both called ${name}`);
      files.set(name, await get(uri));
    }
  }
  return files;
}
