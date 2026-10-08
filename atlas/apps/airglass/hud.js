// The HUD: right-side panel (VAULT / PROPS / KINETICS), status line, hand
// skeleton overlay, the catcher on the right edge, captions, the camera
// message and the optional ATLAS status orb. DOM only; no maths.
import { LM } from './gestures.js';

const $ = (sel) => document.querySelector(sel);
const BONES = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10],
  [10, 11], [11, 12], [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17],
  [17, 18], [18, 19], [19, 20],
];
const clock = (d) => d.toLocaleTimeString([], { hour12: false });

export class Hud {
  constructor(handlers) {
    this.h = handlers;
    this.panel = $('#panel');
    this.canvas = $('#hands');
    this.ctx = this.canvas.getContext('2d');
    this.menuOpen = false;
    this.hovered = null;

    $('#panel-tab').addEventListener('click', () => this.setMenu(!this.menuOpen));
    $('#open-file').addEventListener('click', () => $('#file').click());
    $('#file').addEventListener('change', (e) => {
      if (e.target.files.length) this.h.onFiles(e.target.files);
      e.target.value = '';
    });
    for (const btn of document.querySelectorAll('[data-toggle]')) {
      btn.addEventListener('click', () => this.h.onToggle(btn.dataset.toggle));
    }
    $('#msg-demo').addEventListener('click', () => this.h.onToggle('demo'));
    $('#msg-retry').addEventListener('click', () => this.h.onRetryCamera());
    this.panel.addEventListener('click', (e) => {
      const item = e.target.closest('[data-pick]');
      if (item) this.h.onPick(item.dataset.pick);
    });

    // Drag and drop anywhere.
    const drop = $('#drop');
    let depth = 0;
    window.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; drop.hidden = false; });
    window.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; drop.hidden = true; } });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => {
      e.preventDefault(); depth = 0; drop.hidden = true;
      if (e.dataTransfer?.files?.length) this.h.onFiles(e.dataTransfer.files);
    });
    this.resize();
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio, 2);
    this.canvas.width = window.innerWidth * dpr;
    this.canvas.height = window.innerHeight * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // ----------------------------------------------------------------- panel

  setProps(props, custom = []) {
    const ul = $('#prop-list');
    ul.replaceChildren(...[...props, ...custom].map((p, i) => {
      const li = document.createElement('li');
      li.dataset.pick = `prop:${p.id}`;
      li.innerHTML = `<span class="idx">${String(i + 1).padStart(2, '0')}</span>`;
      li.append(p.name);
      return li;
    }));
  }

  setVault(items) {
    const ul = $('#vault-list');
    if (!items.length) {
      ul.innerHTML = '<li class="empty">Nothing caught yet. Toss a prop to the right.</li>';
      return;
    }
    ul.replaceChildren(...items.map((it, i) => {
      const li = document.createElement('li');
      li.dataset.pick = `vault:${i}`;
      li.append(it.name);
      const t = document.createElement('time');
      t.textContent = clock(it.at);
      li.append(t);
      return li;
    }));
  }

  setActive(pickKey) {
    for (const el of this.panel.querySelectorAll('[data-pick]')) {
      el.classList.toggle('active', el.dataset.pick === pickKey);
    }
  }

  setMenu(open) {
    this.menuOpen = open;
    this.panel.classList.toggle('open', open);
    document.body.classList.toggle('menu-open', open);
    $('#panel-tab').setAttribute('aria-expanded', String(open));
  }

  /** Is this viewport point over the open panel? */
  overMenu(x, y) {
    if (!this.menuOpen) return false;
    const r = this.panel.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  /** The pickable item under a viewport point (hands canvas ignores pointers). */
  itemAt(x, y) {
    if (!this.overMenu(x, y)) return null;
    return document.elementFromPoint(x, y)?.closest('[data-pick]') ?? null;
  }

  hover(points) {
    const el = points.map((p) => this.itemAt(p.x, p.y)).find(Boolean) ?? null;
    if (el === this.hovered) return;
    this.hovered?.classList.remove('hover');
    el?.classList.add('hover');
    this.hovered = el;
  }

  /** Centre of a pickable item, for the demo hand to aim at. */
  pointOf(pickKey) {
    const el = this.panel.querySelector(`[data-pick="${pickKey}"]`);
    if (!el || !this.menuOpen) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + Math.min(r.width / 2, 90), y: r.top + r.height / 2 };
  }

  /** x (px) beyond which a tossed prop is caught by ATLAS. */
  catchLineX() {
    return this.menuOpen ? this.panel.getBoundingClientRect().left : window.innerWidth - 24;
  }

  // ------------------------------------------------------------- readouts

  status({ hands, fps, gesture, source }) {
    $('#st-hands').textContent = String(hands);
    $('#st-fps').textContent = fps ? fps.toFixed(0) : '–';
    $('#st-gesture').textContent = gesture;
    $('#st-source').textContent = source;
  }

  kinetics({ pos, rot, scale, speed }) {
    const f = (v) => (v >= 0 ? ' ' : '') + v.toFixed(2);
    $('#k-pos').textContent = `${f(pos.x)} ${f(pos.y)}`;
    $('#k-rot').textContent = rot.map((d) => `${Math.round(d)}°`).join(' ');
    $('#k-scale').textContent = `×${scale.toFixed(2)}`;
    $('#k-vel').textContent = speed.toFixed(2);
  }

  setToggle(name, on, label = null) {
    const btn = document.querySelector(`[data-toggle="${name}"]`);
    if (!btn) return;
    btn.setAttribute('aria-pressed', String(on));
    if (label !== null) btn.querySelector('b').textContent = label;
  }

  caption(text, who = 'ATLAS') {
    const el = $('#caption');
    el.innerHTML = `<span class="who">${who}</span>`;
    el.append(text);
    el.classList.remove('show');
    void el.offsetWidth;
    el.classList.add('show');
    clearTimeout(this.capTimer);
    this.capTimer = setTimeout(() => el.classList.remove('show'), 3200);
  }

  speakHint(show) { $('#speak-hint').hidden = !show; }

  demoCaption(text) {
    const el = $('#demo-caption');
    el.hidden = !text;
    el.textContent = text ?? '';
  }

  catchFlash() {
    const c = $('#catcher');
    c.classList.remove('catch');
    void c.offsetWidth;
    c.classList.add('catch');
  }

  showMessage(title, body) {
    $('#msg-title').textContent = title;
    $('#msg-body').textContent = body ?? '';
    $('#message').hidden = false;
  }

  hideMessage() { $('#message').hidden = true; }

  /** state: OK | STALE | UNKNOWN | OFF. Each figure carries the time it was read. */
  orb(state, detail = '', at = null) {
    const el = $('#orb');
    el.hidden = state === 'OFF';
    el.dataset.state = state;
    $('#orb-word').textContent = state;
    $('#orb-detail').textContent = at ? `${detail} · read ${clock(at)}` : detail;
  }

  // -------------------------------------------------------------- overlay

  drawHands(hands, { palmProgress = 0, mode = 'IDLE' } = {}) {
    const g = this.ctx;
    g.clearRect(0, 0, window.innerWidth, window.innerHeight);
    for (const h of hands) {
      const lm = h.lm;
      const pinching = h.pinchState?.active;
      g.lineWidth = 1.5;
      g.strokeStyle = pinching ? 'rgba(255, 214, 102, 0.85)' : 'rgba(120, 240, 255, 0.7)';
      g.beginPath();
      for (const [a, b] of BONES) { g.moveTo(lm[a].x, lm[a].y); g.lineTo(lm[b].x, lm[b].y); }
      g.stroke();
      g.fillStyle = 'rgba(200, 252, 255, 0.95)';
      for (const p of lm) { g.beginPath(); g.arc(p.x, p.y, 2.4, 0, Math.PI * 2); g.fill(); }
      // pinch reticle
      const p = h.point;
      g.strokeStyle = pinching ? 'rgba(255, 214, 102, 1)' : 'rgba(120, 240, 255, 0.55)';
      g.lineWidth = pinching ? 2.5 : 1;
      g.beginPath(); g.arc(p.x, p.y, pinching ? 14 : 10, 0, Math.PI * 2); g.stroke();
      // open-palm hold progress around the wrist-to-middle-MCP centre
      if (palmProgress > 0 && mode === 'IDLE') {
        const c = lm[LM.MIDDLE_MCP];
        g.strokeStyle = 'rgba(120, 240, 255, 0.95)';
        g.lineWidth = 3;
        g.beginPath();
        g.arc(c.x, c.y, 34, -Math.PI / 2, -Math.PI / 2 + palmProgress * Math.PI * 2);
        g.stroke();
      }
    }
  }
}
