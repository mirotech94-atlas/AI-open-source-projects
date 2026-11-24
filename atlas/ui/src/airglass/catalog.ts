// What Air Glass can show, and the fixed words ATLAS says about each.
//
// "Explain it" never invents a fact: the built-in models are described by
// the fixed lines below, written here; the ATLAS Academy products by their
// own catalog entry (title, kind, summary), bundled at build time; a model
// added from disk by nothing but its file name; the data panels by the
// numbers they just read (data.ts).
//
// ATLAS Academy: the store's catalog (https://agency.storefront.test/catalog.json)
// is cross-origin and the dashboard's CSP is connect-src 'self', and no
// same-origin endpoint serves it. So the catalog and the covers are bundled
// from the repository at build time (atlas/modules/academy/catalog.json and
// assets/covers/, copied into the Dockerfile's UI stage like _tokens.css):
// the covers become content-hashed /assets/ files, same-origin. A cover
// changes on screen only after the next build.
import catalogJson from "../../../atlas/modules/academy/catalog.json";

export type Source = "model" | "data" | "academy" | "library";

/** Fixed words for the built-in models. */
export const MODEL_ABOUT: Readonly<Record<string, string>> = {
  globe: "A wireframe globe: fifteen-degree lines of latitude and longitude around a solid core, "
    + "with a tilted ring. It is drawn in code here, not a map of anywhere real.",
  knot: "A torus knot: one tube that winds round a ring and through its own middle, "
    + "drawn in code from a formula.",
  crystal: "A crystal: an icosahedron, twenty triangular faces, stretched upward, with a small "
    + "octahedron inside. Drawn in code.",
  atlas: "The ATLAS mark: the five letters built from bars inside an oval ring. It is my name, "
    + "drawn in code.",
};

interface RawProduct {
  slug?: unknown; title?: unknown; kind?: unknown; summary?: unknown;
  cover?: unknown; cover_alt?: unknown;
}

export interface Product {
  slug: string;
  title: string;
  kind: string;
  summary: string;
  /** Same-origin URL of the bundled cover, or null when the product has none. */
  cover: string | null;
  coverAlt: string;
}

const covers = import.meta.glob<string>(
  "../../../atlas/modules/academy/assets/covers/*.{jpg,jpeg,png,webp}",
  { eager: true, query: "?url", import: "default" },
);

const coverUrl = (rel: unknown): string | null => {
  if (typeof rel !== "string" || !rel) return null;
  const name = rel.split("/").pop() ?? "";
  const hit = Object.entries(covers).find(([path]) => path.endsWith(`/${name}`));
  return hit ? hit[1] : null;
};

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** The products as the catalog lists them; anything malformed is skipped. */
export const PRODUCTS: readonly Product[] = ((catalogJson as { products?: RawProduct[] }).products ?? [])
  .filter((p) => str(p.slug) && str(p.title))
  .map((p) => ({
    slug: str(p.slug), title: str(p.title), kind: str(p.kind), summary: str(p.summary),
    cover: coverUrl(p.cover), coverAlt: str(p.cover_alt),
  }));

/** The words for "explain" on a product card: the catalog's own, nothing added. */
export function explainProduct(p: Product): string {
  const kind = p.kind ? ` ${/^[aeiou]/i.test(p.kind) ? "An" : "A"} ${p.kind.toLowerCase()} from ATLAS Academy.` : "";
  return `${p.title}.${kind} ${p.summary}`.trim();
}

/** A model added from disk: only what is known, its file name. */
export const explainAdded = (name: string): string =>
  `${name}: a model you added from this Mac. All I know about it is its file name.`;
