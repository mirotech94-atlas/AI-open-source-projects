'use strict';
const test = require('node:test');
const assert = require('node:assert');
const p = require('../policy');

const OWNER = '+1 555 010 0142';
const mk = (cap = 3, t = { v: 1e9 }) => ({ owner: OWNER, limiter: new p.Limiter(cap, () => t.v), accountNumber: OWNER });

test('owner number only, any formatting', () => {
  assert.ok(p.checkSend({ to: '+1 555 010 0142', text: 'hi' }, mk()).ok);
  const bad = p.checkSend({ to: '+15550000001', text: 'hi' }, mk());
  assert.equal(bad.status, 403);
});
test('no owner configured refuses', () => {
  assert.equal(p.checkSend({ to: OWNER, text: 'hi' }, { ...mk(), owner: '' }).status, 503);
});
test('empty or huge text refused', () => {
  assert.equal(p.checkSend({ to: OWNER, text: '  ' }, mk()).status, 400);
  assert.equal(p.checkSend({ to: OWNER, text: 'x'.repeat(4001) }, mk()).status, 400);
});
test('rate cap slides over an hour and never exceeds 20', () => {
  const t = { v: 1e9 }; const ctx = mk(2, t);
  ctx.limiter.record(); ctx.limiter.record();
  assert.equal(p.checkSend({ to: OWNER, text: 'a' }, ctx).status, 429);
  t.v += 3600 * 1000 + 1;
  assert.ok(p.checkSend({ to: OWNER, text: 'a' }, ctx).ok);
  assert.equal(p.capFromEnv({ WA_BRIDGE_MAX_PER_HOUR: '500' }), 20);
  assert.equal(p.capFromEnv({ WA_BRIDGE_MAX_PER_HOUR: '5' }), 5);
  assert.equal(p.capFromEnv({}), 20);
});
test('account that is not the owner refuses', () => {
  const ctx = { ...mk(), accountNumber: '19175550100' };
  assert.equal(p.checkSend({ to: OWNER, text: 'a' }, ctx).status, 409);
});
test('secret compare', () => {
  assert.ok(p.secretOk('s3cret', 's3cret'));
  assert.ok(!p.secretOk('s3cret', 'nope'));
  assert.ok(!p.secretOk('s3cret', undefined));
  assert.ok(!p.secretOk('', ''));
});
test('reply command parses to data only', () => {
  assert.deepEqual(p.parseCommand('/reply 42 Thanks, call you at 5'),
    { type: 'reply', message_id: 42, text: 'Thanks, call you at 5' });
  assert.equal(p.parseCommand('reply 42 hi'), null);
  assert.equal(p.parseCommand('/reply x hi'), null);
});
