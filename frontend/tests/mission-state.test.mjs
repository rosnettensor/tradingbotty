import test from 'node:test';
import assert from 'node:assert/strict';
import { missionState, countdown } from '../src/missionState.js';
const now = 1000;
const base = () => ({ ts: now, execution_allowed: true, wallet_fresh: true, lanes: [{ id: 'daily', enabled: true, status: 'armed', reasons: [] }], architecture: { journal: [] }, order_trace: [] });
const state = { nodes: [] };
test('ready is waiting for a signal, never an invented active order', () => {
  const m = missionState(base(), state, true, '', now);
  assert.equal(m.phase, 'waiting'); assert.deepEqual(m.channels, [false, false, false]);
});
test('disconnect, stale data and read failures suppress all activity', () => {
  for (const [ts, connected, error] of [[now, false, ''], [800, true, ''], [now, true, 'HTTP 500']]) {
    const m = missionState({ ...base(), ts, order_trace: [{ state: 'submitting' }] }, state, connected, error, now);
    assert.equal(m.phase, 'offline'); assert.deepEqual(m.channels, [false, false, false]);
  }
});
test('uncertainty takes precedence over a pending transmission', () => {
  const ops = base(); ops.architecture.journal = [{ state: 'uncertain', count: 1 }]; ops.order_trace = [{ state: 'submitting' }];
  assert.equal(missionState(ops, state, true, '', now).phase, 'blocked');
});
test('recorded submission is explicitly unconfirmed', () => {
  const ops = base(); ops.order_trace = [{ state: 'submitting', side: 'BUY', symbol: 'BTC' }];
  const m = missionState(ops, state, true, '', now);
  assert.equal(m.phase, 'submitting'); assert.match(m.detail, /noch nicht bestätigt/);
});
test('pause and evidence blocks are distinct', () => {
  assert.equal(missionState({ ...base(), execution_allowed: false }, state, true, '', now).phase, 'paused');
  const ops = base(); ops.lanes[0].status = 'blocked'; ops.lanes[0].reasons = ['Evidence fehlt'];
  const m = missionState(ops, state, true, '', now); assert.equal(m.phase, 'blocked'); assert.equal(m.detail, 'Evidence fehlt');
});
test('old running flags and a stale wallet do not imply current readiness', () => {
  assert.equal(missionState(base(), { nodes: [{ id: 'brain', status: 'running', last_run: 500 }] }, true, '', now).phase, 'waiting');
  assert.equal(missionState({ ...base(), wallet_fresh: false }, state, true, '', now).phase, 'offline');
});
test('unknown or due schedule does not fabricate a countdown', () => {
  assert.equal(countdown(null, now), 'wird ermittelt'); assert.equal(countdown(900, now), 'Prüfung fällig'); assert.equal(countdown(4660, now), '1h 01m');
});
