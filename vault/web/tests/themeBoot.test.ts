import { describe, expect, it } from "vitest";
import { applyPersistedTheme } from "../src/lib/themeBoot";
import type { StorageLike, ThemeRootLike } from "../src/lib/themeBoot";

// This exercises the exact function `index.html`'s inline pre-paint script
// duplicates by hand (see that file's comment, and `themeBoot.ts`'s header).
// The inline copy itself is not covered here — a plain <script> in
// index.html isn't reachable from a vitest/jsdom test — but the two are
// kept byte-for-byte in sync by convention, and this is the logic that
// matters: read the persisted key, apply it before paint, never throw.

function fakeStorage(value: string | null): StorageLike {
  return { getItem: () => value };
}

function fakeRoot(): ThemeRootLike {
  return { dataset: {} };
}

describe("applyPersistedTheme", () => {
  it("applies a persisted light choice", () => {
    const root = fakeRoot();
    applyPersistedTheme(fakeStorage("light"), root);
    expect(root.dataset.theme).toBe("light");
  });

  it("applies a persisted dark choice", () => {
    const root = fakeRoot();
    applyPersistedTheme(fakeStorage("dark"), root);
    expect(root.dataset.theme).toBe("dark");
  });

  it("leaves data-theme unset for a persisted 'system' choice", () => {
    const root = fakeRoot();
    applyPersistedTheme(fakeStorage("system"), root);
    expect(root.dataset.theme).toBeUndefined();
  });

  it("leaves data-theme unset when nothing is persisted", () => {
    const root = fakeRoot();
    applyPersistedTheme(fakeStorage(null), root);
    expect(root.dataset.theme).toBeUndefined();
  });

  it("leaves data-theme unset for garbage stored under the key", () => {
    const root = fakeRoot();
    applyPersistedTheme(fakeStorage("purple"), root);
    expect(root.dataset.theme).toBeUndefined();
  });

  it("never throws when storage access throws (private window / blocked site data)", () => {
    const root = fakeRoot();
    const throwing: StorageLike = {
      getItem: () => {
        throw new Error("SecurityError");
      },
    };
    expect(() => applyPersistedTheme(throwing, root)).not.toThrow();
    expect(root.dataset.theme).toBeUndefined();
  });
});
