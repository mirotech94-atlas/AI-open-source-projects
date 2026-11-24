'use strict';
// The HTTP face. `link` is the Baileys adapter (index.js) or a fake in tests:
//   link.state() -> {connected, number, last_seen}
//   link.send(jidNumber, text) -> Promise<messageId>
//   link.ackOf(id) -> string | undefined
//   link.inbound(since) -> array
const http = require('node:http');
const { checkSend, secretOk, Limiter, digits } = require('./policy');

function json(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) });
  res.end(data);
}

function readBody(req, max = 16384) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > max) { reject(new Error('too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function makeApi({ link, secret, owner, cap, delay = async () => {}, now = Date.now }) {
  const limiter = new Limiter(cap, now);
  let sending = Promise.resolve();      // sends are serialised: one at a time

  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://x');
      if (!secretOk(secret, req.headers['x-bridge-secret'])) {
        return json(res, 401, { error: 'unauthorized' });
      }
      if (req.method === 'GET' && url.pathname === '/status') {
        const s = link.state();
        const number = s.number || '';
        return json(res, 200, {
          connected: Boolean(s.connected), number, last_seen: s.last_seen || null,
          owner_match: Boolean(number && number === owner),
          sent_last_hour: limiter.count(), cap_per_hour: cap,
        });
      }
      if (req.method === 'GET' && url.pathname.startsWith('/message/')) {
        const ack = link.ackOf(decodeURIComponent(url.pathname.slice(9)));
        return json(res, 200, { status: ack || 'unknown' });
      }
      if (req.method === 'GET' && url.pathname === '/inbound') {
        return json(res, 200, { events: link.inbound(parseInt(url.searchParams.get('since') || '0', 10)) });
      }
      if (req.method === 'POST' && url.pathname === '/send') {
        let body;
        try { body = JSON.parse(await readBody(req)); } catch { return json(res, 400, { error: 'bad json' }); }
        const state = link.state();
        const verdict = checkSend(body || {}, { owner, limiter, accountNumber: state.number });
        if (!verdict.ok) return json(res, verdict.status, { error: verdict.error });
        if (!state.connected) return json(res, 503, { error: 'WhatsApp is not connected' });
        // Reserve the slot first so a burst cannot overshoot the cap while delays run.
        limiter.record();
        const job = sending.then(async () => {
          await delay();
          return link.send(digits(body.to), body.text);
        });
        sending = job.catch(() => {});
        try {
          const id = await job;
          return json(res, 200, { ok: true, id });
        } catch (exc) {
          limiter.stamps.pop();
          return json(res, 502, { error: 'send failed' });
        }
      }
      return json(res, 404, { error: 'not found' });
    } catch (exc) {
      return json(res, 500, { error: 'internal error' });   // never echo the exception
    }
  });
}

module.exports = { makeApi };
