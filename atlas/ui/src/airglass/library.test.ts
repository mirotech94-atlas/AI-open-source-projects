// The free library's answer is data: kept only where it has the right shape,
// files only from ATLAS's own routes, sizes as the source gave them.
import { describe, expect, it } from "vitest";
import { baseName, explainLibrary, externalUris, formatSize, parseLibrary, sourcesDown } from "./library";

const GOOD = {
  id: "polyhaven:ArmChair_01", name: "Arm Chair 01", source: "Poly Haven",
  license: "CC0-1.0", license_name: "CC0 1.0", attribution: "by Kirill · Poly Haven · CC0",
  credit_required: false, description: "A vintage armchair.",
  thumb: "/api/airglass/library/thumb?id=polyhaven:ArmChair_01",
  url: "/api/airglass/library/file?id=polyhaven:ArmChair_01",
  main: "ArmChair_01_1k.gltf", size_bytes: 766548,
};

describe("parseLibrary", () => {
  it("keeps a well-formed result with its source's figures", () => {
    const r = parseLibrary({ query: "chair", results: [GOOD], read_at: "2026-10-07T17:30:00+00:00",
      sources: { polyhaven: "ok", khronos: "ok" } });
    expect(r.models).toHaveLength(1);
    expect(r.models[0]).toMatchObject({ id: GOOD.id, licenseName: "CC0 1.0", sizeBytes: 766548,
      creditRequired: false, main: "ArmChair_01_1k.gltf" });
    expect(r.readAt?.toISOString()).toBe("2026-10-07T17:30:00.000Z");
    expect(sourcesDown(r)).toBe("");
  });

  it("drops a model whose file is not on ATLAS's own route, and a picture that is not", () => {
    const r = parseLibrary({ results: [
      { ...GOOD, url: "https://dl.polyhaven.org/x.gltf" },
      { ...GOOD, url: "/api/airglass/library//evil.example/x" },
      { ...GOOD, main: "page.html" },
      { ...GOOD, id: "khronos:ToyCar", thumb: "https://evil.example/t.png", size_bytes: null },
      "nonsense", null,
    ] });
    expect(r.models.map((m) => m.id)).toEqual(["khronos:ToyCar"]);
    expect(r.models[0]?.thumb).toBeNull();
    expect(r.models[0]?.sizeBytes).toBeNull();
  });

  it("says which source did not answer, and never invents a time", () => {
    const r = parseLibrary({ results: [], read_at: "garbage",
      sources: { polyhaven: "the library at api.polyhaven.com did not answer", khronos: "ok" } });
    expect(sourcesDown(r)).toBe("the library at api.polyhaven.com did not answer");
    expect(r.readAt).toBeNull();
    expect(parseLibrary(undefined).models).toEqual([]);
  });
});

describe("the files a .gltf names", () => {
  it("lists buffers and images once, skipping data: URIs", () => {
    expect(externalUris({
      buffers: [{ uri: "ArmChair_01.bin" }, { uri: "data:application/octet-stream;base64,AAAA" }],
      images: [{ uri: "textures/a.jpg" }, { uri: "textures/a.jpg" }, { bufferView: 3 }, { uri: "b.png" }],
    })).toEqual(["ArmChair_01.bin", "textures/a.jpg", "b.png"]);
    expect(externalUris(null)).toEqual([]);
    expect(externalUris({ buffers: "x" })).toEqual([]);
  });

  it("keys them by base name, as the loader looks them up", () => {
    expect(baseName("textures/Armchair_01_diff_1k.jpg")).toBe("Armchair_01_diff_1k.jpg");
    expect(baseName("a%20b.png")).toBe("a b.png");
  });
});

describe("words about a library model", () => {
  it("formats a size, or says it is not listed", () => {
    expect(formatSize(null)).toBe("size not listed");
    expect(formatSize(766548)).toBe("749 KB");
    expect(formatSize(2349276)).toBe("2.2 MB");
  });

  it("explains it in the source's own words, with the credit when one is owed", () => {
    const [m] = parseLibrary({ results: [GOOD] }).models;
    expect(m && explainLibrary(m)).toBe("Arm Chair 01: from Poly Haven, CC0 1.0. A vintage armchair.");
    const [by] = parseLibrary({ results: [{ ...GOOD, credit_required: true, license_name: "CC BY 4.0",
      attribution: "© 2017 Someone (CC BY 4.0)", description: "" }] }).models;
    expect(by && explainLibrary(by)).toBe("Arm Chair 01: from Poly Haven, CC BY 4.0. Credit: © 2017 Someone (CC BY 4.0).");
  });
});
