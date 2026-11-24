'use strict';
// Pure rules of the bridge. No network, no Baileys: everything here is unit tested.
// The bridge is a dumb transport (CLAUDE.md invariant 1): ATLAS's executor decides
// what to send; this file only decides what the bridge is willing to carry.
const crypto = require('node:crypto');

const HARD_MAX_PER_HOUR = 20;          // never raised by env, only lowered
const TEXT_MAX = 4000;

function digits(value) {
  return String(value || '').replace(/\D/g, '');
}

function ownerFromEnv(env) {
  const d = digits(env.WHATSAPP_OWNER);
  return d.length >= 8 && d.length <= 15 ? d : '';
}

function capFromEnv(env) {
  const n = parseInt(env.WA_BRIDGE_MAX_PER_HOUR || '', 10);
  if (!Number.isFinite(n) || n < 0) return HARD_MAX_PER_HOUR;
  return Math.min(n, HARD_MAX_PER_HOUR);
}

function secretOk(expected, given) {
  if (!expected || typeof given !== 'string' || !given) return false;
  const a = crypto.createHash('sha256').update(expected).digest();
  const b = crypto.createHash('sha256').update(given).digest();
  return crypto.timingSafeEqual(a, b);
}

// Sliding one-hour window of send timestamps.
class Limiter {
  constructor(cap, now = Date.now) {
    this.cap = cap;
    this.now = now;
    this.stamps = [];
  }
  _trim() {
    const cut = this.now() - 3600 * 1000;
    while (this.stamps.length && this.stamps[0] <= cut) this.stamps.shift();
  }
  count() { this._trim(); return this.stamps.length; }
  allow() { return this.count() < this.cap; }
  record() { this.stamps.push(this.now()); }
}

// The one decision: may this text go to this number now?
// Returns {ok:true} or {ok:false, status, error}.
function checkSend({ to, text }, { owner, limiter, accountNumber }) {
  if (!owner) return { ok: false, status: 503, error: 'WHATSAPP_OWNER is not configured' };
  if (typeof text !== 'string' || !text.trim()) {
    return { ok: false, status: 400, error: 'text is required' };
  }
  if (text.length > TEXT_MAX) return { ok: false, status: 400, error: 'text is too long' };
  if (digits(to) !== owner) {
    return { ok: false, status: 403, error: 'recipient is not the allowed number' };
  }
  if (accountNumber && accountNumber !== owner) {
    return { ok: false, status: 409, error: 'linked account is not the owner number' };
  }
  if (!limiter.allow()) {
    return { ok: false, status: 429, error: 'hourly send cap reached' };
  }
  return { ok: true };
}

// "/reply <message id> <text>": parsed to DATA only. Nothing in the bridge acts on it;
// a later executor action may, after its own checks (see docs/whatsapp-link.md).
const REPLY = /^\/reply\s+(\d{1,9})\s+([\s\S]{1,1000})$/;
function parseCommand(text) {
  const m = REPLY.exec(String(text || '').trim());
  return m ? { type: 'reply', message_id: parseInt(m[1], 10), text: m[2].trim() } : null;
}

// Baileys status numbers -> words ATLAS understands.
function ackName(n) {
  return { 0: 'error', 1: 'pending', 2: 'server_ack', 3: 'delivered', 4: 'read', 5: 'read' }[n]
    || 'unknown';
}

module.exports = {
  HARD_MAX_PER_HOUR, TEXT_MAX, digits, ownerFromEnv, capFromEnv, secretOk, Limiter,
  checkSend, parseCommand, ackName,
};
