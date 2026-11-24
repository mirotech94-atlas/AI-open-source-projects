'use strict';
// ATLAS WhatsApp bridge: ONE linked device (WhatsApp Web style), a dumb transport.
//   :8090  API (compose-internal only, never published): /status /send /message/:id /inbound
//   :8091  pairing page, published on the host's 127.0.0.1 only: /pair
// Auth state lives in AUTH_DIR (a docker volume). It is never printed or logged.
// Rules: send only to WHATSAPP_OWNER (policy.js), low hourly cap, human-like delays,
// no bulk, no cold messages. Inbound is stored as untrusted data and acted on by nothing.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const pino = require('pino');
const QRCode = require('qrcode');
const baileys = require('@whiskeysockets/baileys');
const { makeApi } = require('./server');
const P = require('./policy');

const makeWASocket = baileys.default || baileys.makeWASocket;
const { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion,
  Browsers } = baileys;

const AUTH_DIR = process.env.AUTH_DIR || '/data/auth';
const INBOUND_FILE = process.env.INBOUND_FILE || '/data/inbound.jsonl';
const SECRET = process.env.WA_BRIDGE_SECRET || '';
const OWNER = P.ownerFromEnv(process.env);
const CAP = P.capFromEnv(process.env);
const log = pino({ level: 'info', redact: ['*.secret', 'secret'] });

if (!SECRET || SECRET.length < 16) { log.fatal('WA_BRIDGE_SECRET missing or too short'); process.exit(1); }
if (!OWNER) { log.fatal('WHATSAPP_OWNER missing or invalid'); process.exit(1); }

const state = { connected: false, number: '', last_seen: null, qr: null, since: null, attempts: 0 };
const acks = new Map();           // message id -> ack name (last 300)
const sentIds = new Set();        // ids we sent: their echoes are not inbound
let sock = null;
let inboundSeq = 0;

function remember(id, ack) {
  acks.set(id, ack);
  if (acks.size > 300) acks.delete(acks.keys().next().value);
}

function readInbound() {
  try {
    return fs.readFileSync(INBOUND_FILE, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  } catch { return []; }
}
inboundSeq = readInbound().reduce((m, e) => Math.max(m, e.seq || 0), 0);

function storeInbound(text, id) {
  const event = {
    seq: ++inboundSeq, id, at: new Date().toISOString(),
    untrusted: true,                       // every ingested byte is untrusted (invariant 4)
    text: String(text).slice(0, 2000), command: P.parseCommand(text),
  };
  fs.appendFileSync(INBOUND_FILE, JSON.stringify(event) + '\n', { mode: 0o600 });
  log.info({ seq: event.seq }, 'inbound stored');   // never the text
}

function ownerJids() {
  const own = new Set([`${OWNER}@s.whatsapp.net`]);
  if (sock && sock.user && sock.user.lid) own.add(sock.user.lid.replace(/:\d+@/, '@'));
  return own;
}

async function connect() {
  const { state: auth, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  let version;
  try { ({ version } = await fetchLatestBaileysVersion()); } catch { /* library default */ }
  sock = makeWASocket({
    auth, version, logger: pino({ level: 'warn' }),
    browser: Browsers.macOS('Desktop'), markOnlineOnConnect: false,
    syncFullHistory: false, printQRInTerminal: false,
  });
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', (u) => {
    if (u.qr) state.qr = u.qr;
    if (u.connection === 'open') {
      state.connected = true; state.qr = null; state.attempts = 0; state.since = Date.now();
      state.number = P.digits((sock.user && sock.user.id || '').split(':')[0].split('@')[0]);
      state.last_seen = new Date().toISOString();
      log.info({ owner_match: state.number === OWNER }, 'connected');
    }
    if (u.connection === 'close') {
      state.connected = false;
      const code = u.lastDisconnect && u.lastDisconnect.error && u.lastDisconnect.error.output
        && u.lastDisconnect.error.output.statusCode;
      if (code === DisconnectReason.loggedOut) {
        log.error('logged out from the phone: wipe the auth volume and pair again');
        state.number = '';
        return;                                  // never auto-retry a logout
      }
      const wait = Math.min(300000, 2000 * 2 ** Math.min(state.attempts++, 8)) + Math.random() * 1000;
      log.warn({ code, wait: Math.round(wait) }, 'disconnected, retrying');
      setTimeout(() => connect().catch((e) => log.error({ err: e.message }, 'reconnect failed')), wait);
    }
  });
  sock.ev.on('messages.update', (updates) => {
    for (const u of updates) {
      if (u.key && u.key.id && u.update && u.update.status !== undefined) {
        remember(u.key.id, P.ackName(u.update.status));
      }
    }
  });
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    state.last_seen = new Date().toISOString();
    if (type !== 'notify') return;
    for (const m of messages) {
      const id = m.key && m.key.id;
      if (!id || sentIds.has(id) || !ownerJids().has(m.key.remoteJid)) continue;   // owner chat only
      const text = m.message && (m.message.conversation
        || (m.message.extendedTextMessage && m.message.extendedTextMessage.text));
      if (text) storeInbound(text, id);
    }
  });
}

const link = {
  state: () => ({ connected: state.connected, number: state.number, last_seen: state.last_seen }),
  async send(number, text) {
    const jid = `${number}@s.whatsapp.net`;
    await sock.presenceSubscribe(jid).catch(() => {});
    await sock.sendPresenceUpdate('composing', jid).catch(() => {});
    await new Promise((r) => setTimeout(r, 800 + Math.min(text.length * 15, 3000)));
    await sock.sendPresenceUpdate('paused', jid).catch(() => {});
    const res = await sock.sendMessage(jid, { text });
    sentIds.add(res.key.id);
    if (sentIds.size > 300) sentIds.delete(sentIds.values().next().value);
    remember(res.key.id, 'pending');
    return res.key.id;
  },
  ackOf: (id) => acks.get(id),
  inbound: (since) => readInbound().filter((e) => e.seq > (since || 0)),
};

// Human-like pacing between sends: 3-8 s on top of the typing delay above.
const delay = () => new Promise((r) => setTimeout(r, 3000 + Math.random() * 5000));
makeApi({ link, secret: SECRET, owner: OWNER, cap: CAP, delay })
  .listen(8090, '0.0.0.0', () => log.info('api on :8090 (compose network only)'));

// Pairing page: no secret on it, so it is published on the host's loopback only.
http.createServer(async (req, res) => {
  if (!req.url.startsWith('/pair')) { res.writeHead(404); return res.end(); }
  let body;
  if (state.connected) {
    body = `<h2>Linked</h2><p>Connected as +${state.number}${state.number === OWNER ? '' : ' (NOT the owner number: sends are refused)'}.</p>`;
  } else if (state.qr) {
    const svg = await QRCode.toString(state.qr, { type: 'svg', margin: 2, width: 320 });
    body = `<h2>Scan with WhatsApp</h2><p>Phone: Settings &gt; Linked devices &gt; Link a device.</p>${svg}<p>The code refreshes about every 20 s; this page reloads itself.</p>`;
  } else {
    body = '<h2>Waiting for a QR code...</h2>';
  }
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
    'x-frame-options': 'DENY' });
  res.end(`<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5><title>ATLAS WhatsApp link</title><body style="font-family:system-ui;text-align:center;margin:3rem">${body}</body>`);
}).listen(8091, '0.0.0.0', () => log.info('pairing page on :8091 (host loopback only)'));

fs.mkdirSync(path.dirname(INBOUND_FILE), { recursive: true });
connect().catch((e) => log.error({ err: e.message }, 'first connect failed'));
