'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { makeApi } = require('../server');

const OWNER = '13412033334';
const SECRET = 'unit-secret-value';

function fakeLink(over = {}) {
  const sent = [];
  return {
    sent,
    state: () => ({ connected: true, number: OWNER, last_seen: '2026-11-14T00:00:00Z', ...over }),
    send: async (to, text) => { sent.push([to, text]); return 'ID' + sent.length; },
    ackOf: (id) => (id === 'ID1' ? 'delivered' : undefined),
    inbound: () => [],
  };
}

async function call(server, method, path, body, headers = { 'x-bridge-secret': SECRET }) {
  const r = await fetch(`http://127.0.0.1:${server.address().port}${path}`, {
    method, headers: { 'content-type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: r.status, body: await r.json() };
}

async function boot(link, cap = 2) {
  const s = makeApi({ link, secret: SECRET, owner: OWNER, cap });
  await new Promise((r) => s.listen(0, '127.0.0.1', r));
  return s;
}

test('secret header required on every route', async () => {
  const s = await boot(fakeLink());
  for (const [m, p, b] of [['GET', '/status'], ['POST', '/send', { to: OWNER, text: 'x' }], ['GET', '/inbound']]) {
    assert.equal((await call(s, m, p, b, {})).status, 401);
    assert.equal((await call(s, m, p, b, { 'x-bridge-secret': 'wrong' })).status, 401);
  }
  s.close();
});

test('send reaches only the owner and reports the message id', async () => {
  const link = fakeLink(); const s = await boot(link);
  const bad = await call(s, 'POST', '/send', { to: '+19175550100', text: 'hi' });
  assert.equal(bad.status, 403); assert.equal(link.sent.length, 0);
  const ok = await call(s, 'POST', '/send', { to: '+1 555 010 0100', text: 'hi' });
  assert.equal(ok.status, 200); assert.equal(ok.body.id, 'ID1');
  assert.deepEqual(link.sent, [[OWNER, 'hi']]);
  assert.equal((await call(s, 'GET', '/message/ID1')).body.status, 'delivered');
  s.close();
});

test('hourly cap answers 429 and sends nothing more', async () => {
  const link = fakeLink(); const s = await boot(link, 2);
  await call(s, 'POST', '/send', { to: OWNER, text: 'a' });
  await call(s, 'POST', '/send', { to: OWNER, text: 'b' });
  assert.equal((await call(s, 'POST', '/send', { to: OWNER, text: 'c' })).status, 429);
  assert.equal(link.sent.length, 2);
  s.close();
});

test('disconnected is 503, not sent', async () => {
  const link = fakeLink({ connected: false }); const s = await boot(link);
  const r = await call(s, 'POST', '/send', { to: OWNER, text: 'a' });
  assert.equal(r.status, 503); assert.equal(link.sent.length, 0);
  const st = await call(s, 'GET', '/status');
  assert.equal(st.body.connected, false);
  s.close();
});

test('wrong account linked: status says owner_match false, send refused', async () => {
  const link = fakeLink({ number: '19175550100' }); const s = await boot(link);
  assert.equal((await call(s, 'GET', '/status')).body.owner_match, false);
  assert.equal((await call(s, 'POST', '/send', { to: OWNER, text: 'a' })).status, 409);
  s.close();
});

test('responses never contain the secret', async () => {
  const s = await boot(fakeLink());
  const r = await call(s, 'GET', '/nope');
  assert.ok(!JSON.stringify(r).includes(SECRET));
  s.close();
});
