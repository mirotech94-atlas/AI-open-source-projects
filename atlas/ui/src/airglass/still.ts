// The one still ATLAS is shown when he asks it to look. Taken from the live
// <video> at that moment, un-mirrored (the picture as the camera saw it, so
// writing on what he holds reads the right way round), downscaled so its
// longer side is at most 768 px, and JPEG-encoded -- all in this tab. It is
// never kept: the canvas is emptied as soon as the JPEG exists, and the
// bytes live only as long as the one request that carries them.

export const STILL_MAX_SIDE = 768;
export const STILL_QUALITY = 0.8;

/** Fit (w, h) inside a max side, keeping the aspect; never upscales. */
export function fitWithin(w: number, h: number, max = STILL_MAX_SIDE): { w: number; h: number } {
  if (w <= 0 || h <= 0) return { w: 0, h: 0 };
  const k = Math.min(1, max / Math.max(w, h));
  return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)) };
}

/** Base64 of some bytes, in chunks (no blob: URL, which the CSP refuses). */
export function toBase64(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    out += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(out);
}

/** One JPEG of what the camera sees now, or null when there is no picture. */
export async function captureStill(video: HTMLVideoElement): Promise<{ b64: string; bytes: number } | null> {
  const size = fitWithin(video.videoWidth, video.videoHeight);
  if (!size.w || video.readyState < 2) return null;
  const canvas = document.createElement("canvas");
  canvas.width = size.w;
  canvas.height = size.h;
  const g = canvas.getContext("2d");
  if (!g) return null;
  g.drawImage(video, 0, 0, size.w, size.h);
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", STILL_QUALITY));
  canvas.width = 0;   // let go of the pixels
  canvas.height = 0;
  if (!blob) return null;
  const bytes = new Uint8Array(await blob.arrayBuffer());
  return { b64: toBase64(bytes), bytes: bytes.length };
}
