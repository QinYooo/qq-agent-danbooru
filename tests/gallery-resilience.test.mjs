import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createController, DEFAULTS } from '../plugins/character-gallery/gallery.js';

const root = await fs.mkdtemp(path.join(os.tmpdir(), 'qq-gallery-resilience-'));
const items = [];
for (let i = 0; i < 5; i++) {
  const file = path.join(root, `image-${i}.png`);
  await fs.writeFile(file, `inert-fixture-${i}`);
  items.push({ key: `image-${i}`, file });
}
const tick = () => new Promise(resolve => setTimeout(resolve, 5));
async function until(check) {
  const deadline = performance.now() + 1000;
  while (!check()) {
    if (performance.now() > deadline) throw new Error('test observation did not arrive within 1s');
    await tick();
  }
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function harness(options = {}) {
  const state = new Map(), notices = [], warnings = [], snapshots = [];
  const api = {
    config: () => ({ ...DEFAULTS, outputRoot: root, cooldownSeconds: 0 }),
    storage: { get: (key, fallback) => state.get(key) ?? fallback,
      set: (key, value) => { state.set(key, value); if (key === 'lastResult') snapshots.push(value); } },
    log() {}, warn: (...args) => warnings.push(args)
  };
  const controller = createController(api, { records: {}, titles: new Map() }, {
    retrieve: async query => ({ items, tags: ['fixture'], name: query }), pause: async () => {}, ...options
  });
  const context = (group, id, query, count, sendMedia) => ({
    chatKey: `group:${group}`, onebot: { selfId: '100' },
    event: { post_type: 'message', message_type: 'group', self_id: 100, user_id: 200,
      group_id: group, message_id: id,
      message: [{ type: 'at', data: { qq: '100' } }, { type: 'text', data: { text: `来${count}张${query}` } }] },
    sender: { sendMedia }, send: async text => { notices.push({ group, id, text }); }
  });
  controller.start();
  return { controller, context, state, notices, warnings, snapshots };
}

test('one slow group does not block a second group; each group is serial and active groups never exceed two', async t => {
  const h = harness(), gates = new Map(), started = [], ended = [], activeGroups = new Set();
  let peak = 0;
  t.after(async () => { h.controller.stop(); for (const gate of gates.values()) gate.resolve(); await h.controller.idle(); });
  function enqueue(group, id, label) {
    const gate = deferred(); gates.set(label, gate);
    h.controller.handle(h.context(group, id, label, 1, async () => {
      assert.equal(activeGroups.has(group), false, 'same-group sends must not overlap');
      activeGroups.add(group); peak = Math.max(peak, activeGroups.size); started.push(label);
      await gate.promise;
      activeGroups.delete(group); ended.push(label);
    }));
  }
  enqueue(300, 1, 'slow-first'); enqueue(300, 2, 'same-group-second');
  enqueue(301, 3, 'independent'); enqueue(302, 4, 'third-group');
  await until(() => started.length === 2);
  assert.deepEqual(new Set(started), new Set(['slow-first', 'independent']));
  await tick(); assert.equal(started.length, 2, 'third group must wait for a concurrency slot');
  gates.get('independent').resolve();
  await until(() => started.includes('third-group'));
  assert.equal(started.includes('same-group-second'), false, 'same group must wait for its own first request');
  gates.get('third-group').resolve(); gates.get('slow-first').resolve();
  await until(() => started.includes('same-group-second'));
  assert.ok(ended.includes('slow-first'));
  gates.get('same-group-second').resolve();
  await h.controller.idle();
  assert.equal(peak, 2); assert.equal(started.length, 4); assert.equal(ended.length, 4);
});

test('the send budget waits for the in-flight receipt, never retries it, then leaves remaining images unsent', async t => {
  let time = 0, attempts = 0;
  const receipt = deferred(), h = harness({ monotonicNow: () => time, sendBudgetMs: 1000 });
  t.after(async () => { h.controller.stop(); receipt.resolve(); await h.controller.idle(); });
  h.controller.handle(h.context(300, 10, 'budget', 3, async () => { attempts++; await receipt.promise; }));
  await until(() => attempts === 1);
  time = 5000;
  await tick(); await tick();
  assert.equal(attempts, 1); assert.equal(h.notices.length, 0);
  assert.equal(h.state.get('lastResult').status, 'uploading');
  assert.equal(h.state.get('lastResult').count, 0, 'no receipt must not be counted as sent');
  receipt.resolve(); await h.controller.idle();
  const result = h.state.get('lastResult');
  assert.equal(attempts, 1); assert.equal(result.status, 'partial');
  assert.equal(result.count, 1); assert.equal(result.notAttempted, 2); assert.equal(result.stoppedReason, 'time_budget');
  assert.equal(result.uploadRetries, 0); assert.equal(result.unconfirmedKeys.length, 0);
  assert.match(h.notices[0].text, /已确认发出1张.*剩余2张/);
});

test('two consecutive missing receipts stop only the remainder and preserve truthful counts', async t => {
  const h = harness(); let attempts = 0;
  t.after(() => h.controller.stop());
  h.controller.handle(h.context(300, 20, 'receipts', 5, async () => {
    if (++attempts > 1) throw new Error('QQ returned no message receipt');
  }));
  await h.controller.idle();
  const result = h.state.get('lastResult');
  assert.equal(attempts, 3); assert.equal(result.count, 1); assert.equal(result.selected, 5);
  assert.equal(result.unconfirmedKeys.length, 2); assert.equal(result.notAttempted, 2);
  assert.equal(result.uploadFailedKeys.length, 0); assert.equal(result.stoppedReason, 'consecutive_failures');
  assert.equal(Object.keys(h.state.get('deliverySkips')).length, 2);
  assert.equal(h.notices.length, 1); assert.match(h.notices[0].text, /已确认发出1张.*另外2张.*剩余2张/);
});

test('one image with a repeated upload transport failure is skipped without quarantine and later images succeed', async t => {
  const h = harness(); let attempts = 0;
  const references = [];
  t.after(() => h.controller.stop());
  h.controller.handle(h.context(300, 30, 'transport', 3, async (_chat, segments) => {
    references.push(segments.find(segment => segment.type === 'image').data.file);
    if (++attempts <= 2) throw new Error('highway upload transport failed after 3 attempts: ECONNRESET');
  }));
  await h.controller.idle();
  const result = h.state.get('lastResult');
  assert.equal(attempts, 4); assert.equal(references[0], references[1], 'bounded retry keeps exactly the same bytes');
  assert.equal(new Set(references.slice(1)).size, 3, 'later attempts are different images');
  assert.equal(result.count, 2); assert.equal(result.uploadFailedKeys.length, 1); assert.equal(result.uploadRetries, 1);
  assert.equal(result.notAttempted, 0); assert.equal(result.unconfirmedKeys.length, 0);
  assert.equal(h.state.has('deliverySkips'), false); assert.equal(result.status, 'partial');
  assert.equal(h.notices.length, 1); assert.match(h.notices[0].text, /已确认发出2张.*1张遇到上传连接故障/);
});

test('persistent upload failure stops after two images with one retry each and does not quarantine them', async t => {
  const h = harness(); let attempts = 0;
  t.after(() => h.controller.stop());
  h.controller.handle(h.context(300, 40, 'offline', 5, async () => {
    attempts++; throw new Error('highway upload transport failed after 3 attempts: ECONNRESET');
  }));
  await h.controller.idle();
  const result = h.state.get('lastResult');
  assert.equal(attempts, 4); assert.equal(result.count, 0); assert.equal(result.uploadRetries, 2);
  assert.equal(result.uploadFailedKeys.length, 2); assert.equal(result.notAttempted, 3);
  assert.equal(result.stoppedReason, 'consecutive_failures'); assert.equal(h.state.has('deliverySkips'), false);
  assert.match(h.notices[0].text, /已确认发出0张.*2张遇到上传连接故障.*剩余3张/);
});

test('idle has a short bounded timeout without treating the pending send as completed', async t => {
  const gate = deferred(), h = harness(); let attempts = 0;
  t.after(async () => { h.controller.stop(); gate.resolve(); await h.controller.idle(); });
  h.controller.handle(h.context(300, 50, 'waiting', 1, async () => { attempts++; await gate.promise; }));
  await until(() => attempts === 1);
  const started = performance.now();
  await assert.rejects(h.controller.idle({ timeoutMs: 20 }), /timeout|超时/i);
  assert.ok(performance.now() - started < 500);
  assert.equal(attempts, 1); assert.equal(h.state.get('lastResult').status, 'uploading');
  gate.resolve(); await h.controller.idle();
  assert.equal(h.state.get('lastResult').count, 1);
});

test('reload/disable during a send records cancellation and cannot continue with later images',async t=>{
  const gate=deferred(),h=harness();let attempts=0;
  t.after(async()=>{h.controller.stop();gate.resolve();await h.controller.idle();});
  h.controller.handle(h.context(300,60,'interruption',3,async()=>{attempts++;await gate.promise;}));
  await until(()=>attempts===1);
  h.controller.stop();
  assert.equal(h.state.get('lastResult').status,'cancelled');
  assert.equal(h.state.get('lastResult').reason,'plugin_deactivated_or_reloaded');
  gate.resolve();await h.controller.idle();
  assert.equal(attempts,1);assert.equal(h.state.get('lastResult').status,'cancelled');
});

test.after(async () => {
  assert.ok(path.resolve(root).startsWith(path.resolve(os.tmpdir()) + path.sep));
  assert.ok(path.basename(root).startsWith('qq-gallery-resilience-'));
  await fs.rm(root, { recursive: true, force: true });
});
