// Draws a data panel or a product card face onto a 2D canvas, which the
// scene uses as a texture (a CanvasTexture needs no URL, so the CSP is not
// involved). UNKNOWN is grey, unlit, dashed and spelled out: it never shares
// OK's green (CLAUDE.md invariant 7). Every panel prints its read time.
import type { PanelData, RowState } from "./data";

export const PANEL_W = 1024;
export const PANEL_H = 640;

const INK: Record<RowState, string> = {
  ok: "#6dffa8", warn: "#ffcf66", fail: "#ff6b6b", unknown: "#9aa3a8", plain: "#e9fdff",
};

function fit(g: CanvasRenderingContext2D, text: string, max: number): string {
  if (g.measureText(text).width <= max) return text;
  let t = text;
  while (t.length > 1 && g.measureText(`${t}…`).width > max) t = t.slice(0, -1);
  return `${t}…`;
}

const clock = (d: Date): string => d.toLocaleTimeString([], { hour12: false });

export function drawPanel(canvas: HTMLCanvasElement, p: PanelData | null): void {
  canvas.width = PANEL_W;
  canvas.height = PANEL_H;
  const g = canvas.getContext("2d");
  if (!g) return;
  g.clearRect(0, 0, PANEL_W, PANEL_H);
  g.fillStyle = "rgba(6, 26, 36, 0.86)";
  g.fillRect(0, 0, PANEL_W, PANEL_H);
  g.strokeStyle = "rgba(88, 240, 255, 0.85)";
  g.lineWidth = 4;
  g.strokeRect(6, 6, PANEL_W - 12, PANEL_H - 12);

  const mono = "ui-monospace, 'JetBrains Mono', Menlo, monospace";
  g.textBaseline = "top";
  g.fillStyle = "#58f0ff";
  g.font = `700 46px ${mono}`;
  g.fillText((p?.title ?? "Reading…").toUpperCase(), 40, 34);

  if (!p) return;
  let y = 104;
  if (p.error) {
    g.fillStyle = INK.unknown;
    g.font = `600 34px ${mono}`;
    g.fillText("UNKNOWN", 40, y);
    g.font = `28px ${mono}`;
    g.fillText(fit(g, p.error, PANEL_W - 80), 40, y + 52);
  } else {
    g.fillStyle = "#e9fdff";
    g.font = `30px ${mono}`;
    g.fillText(fit(g, p.summary, PANEL_W - 80), 40, y);
    y += 60;
    g.font = `28px ${mono}`;
    for (const r of p.rows.slice(0, 8)) {
      g.fillStyle = "rgba(233, 253, 255, 0.82)";
      g.fillText(fit(g, r.label, 430), 40, y);
      const value = fit(g, r.value, 470);
      const x = PANEL_W - 40 - g.measureText(value).width;
      if (r.state === "unknown") {
        g.save();
        g.setLineDash([8, 6]);
        g.strokeStyle = INK.unknown;
        g.lineWidth = 2;
        g.strokeRect(x - 10, y - 6, g.measureText(value).width + 20, 42);
        g.restore();
      }
      g.fillStyle = INK[r.state];
      g.fillText(value, x, y);
      y += 52;
    }
  }
  g.fillStyle = "rgba(233, 253, 255, 0.6)";
  g.font = `24px ${mono}`;
  g.fillText(p.readAt ? `read ${clock(p.readAt)}` : "not read", 40, PANEL_H - 56);
}

/** A product with no cover: its title on a plain card face. */
export function drawCardFace(canvas: HTMLCanvasElement, title: string, kind: string): void {
  canvas.width = 512;
  canvas.height = 720;
  const g = canvas.getContext("2d");
  if (!g) return;
  g.fillStyle = "#1d1336";
  g.fillRect(0, 0, 512, 720);
  g.strokeStyle = "#f4b740";
  g.lineWidth = 6;
  g.strokeRect(18, 18, 476, 684);
  g.fillStyle = "#ffffff";
  g.font = "700 48px ui-sans-serif, system-ui, sans-serif";
  g.textBaseline = "top";
  const words = title.split(/\s+/);
  let line = "", y = 120;
  for (const w of words) {
    const next = line ? `${line} ${w}` : w;
    if (g.measureText(next).width > 420 && line) { g.fillText(line, 46, y); y += 60; line = w; } else line = next;
  }
  if (line) g.fillText(line, 46, y);
  g.fillStyle = "#f4b740";
  g.font = "600 30px ui-sans-serif, system-ui, sans-serif";
  g.fillText(kind.toUpperCase(), 46, 620);
}
