import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import { createController, allowedUrl, messageSegments, sourceFromImage, DEFAULTS } from '../plugins/danbooru-archive/index.js';
import { createReceiver, isLoopbackWebSocket } from '../plugins/danbooru-archive/receiver.js';

const delay = (ms = 15) => new Promise((resolve) => setTimeout(resolve, ms));
const fixture = fileURLToPath(new URL('../plugins/danbooru-archive/plugin.json', import.meta.url));

test('manifest declares portable plugin settings and its panel', () => {
  const raw = JSON.parse(readFileSync(fixture, 'utf8'));
  assert.equal(raw.id, 'danbooru-archive');
  assert.equal(raw.apiVersion, 1);
  assert.equal(raw.configSchema.dashboard.panel, 'panel/viewer.html');
  assert.equal(raw.settings.characterThreshold, DEFAULTS.characterThreshold);
  assert.equal(raw.enabledByDefault, false);
});
function harness(options = {}) {
  const sent = [], children = [], qqCalls = [], writes = [];
  const settings = { ...DEFAULTS, ...(options.settings || {}) };
  const api = {
    config: () => settings,
    storage: { get: () => ({}), set: (_k, v) => writes.push(v) }, warn() {}, log() {},
    qqCall: async (action, args) => { qqCalls.push({ action, args }); return options.qqCall?.(action, args) || {}; }
  };
  const spawn = (_python, args, spawnOptions) => {
    const child = new EventEmitter(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.args = args; child.options = spawnOptions; child.killed = false;
    child.kill = () => { child.killed = true; child.emit('exit', 1); return true; };
    child.stdin = new Writable({ write(chunk, _enc, cb) {
      for (const line of chunk.toString().trim().split('\n')) {
        const request = JSON.parse(line); sent.push(request);
        const response = options.reply ? options.reply(request, children.length) : { status: request.status || 'saved', reason: request.reason || 'saved' };
        if (response) queueMicrotask(() => child.stdout.write(JSON.stringify({ ...response, id: request.id }) + '\n'));
      }
      cb();
    }});
    children.push(child);
    if (options.ready !== false) queueMicrotask(() => child.stdout.write(JSON.stringify({ event: 'ready', model: 'test-model', ...(options.readyPayload || {}) }) + '\n'));
    return child;
  };
  const receiver = { start() {}, stop() {} };
  const ctl = createController(api, { spawn, exists: (file) => typeof options.exists === 'function' ? options.exists(file) : options.exists !== false, receiver, startupMs: options.startupMs || 200, taskMs: options.taskMs || 200, retryMs: options.retryMs || 10 });
  return { ctl, sent, children, qqCalls, writes, settings };
}
function event(id = 1, image = { url: 'https://gchat.qpic.cn/gchatpic_new/example' }) {
  return { event: { post_type: 'message', message_type: 'group', group_id: 123, user_id: 456, self_id: 789, message_id: id, message: [{ type: 'image', data: image }] }, chatKey: 'group:123' };
}

test('URL allowlist excludes foreign hosts, credentials, custom ports and local files', () => {
  assert.equal(allowedUrl('https://multimedia.nt.qq.com.cn/x'), true);
  assert.equal(allowedUrl('https://other.qq.com.cn/x'), false);
  assert.equal(allowedUrl('https://multimedia.nt.qq.com.cn.evil.test/x'), false);
  assert.equal(allowedUrl('https://multimedia.nt.qq.com/x?x=1'), true);
  assert.equal(allowedUrl('https://evilqpic.cn/x'), false);
  assert.equal(allowedUrl('https://qpic.cn.evil.test/x'), false);
  assert.equal(allowedUrl('https://u:p@gchat.qpic.cn/x'), false);
  assert.equal(allowedUrl('https://gchat.qpic.cn:3456/x'), false);
  assert.equal(allowedUrl('file:///C:/secret'), false);
});

test('CQ parser decodes parameters and does not interpret ordinary text as images', () => {
  const seg = messageSegments('x[CQ:image,file=abc.jpg,url=https://gchat.qpic.cn/a?x=1&amp;y=2&#44;3]');
  assert.equal(seg.length, 1); assert.equal(seg[0].data.url, 'https://gchat.qpic.cn/a?x=1&y=2,3');
  assert.deepEqual(messageSegments('https://gchat.qpic.cn/something'), []);
});

test('trusted get_image result prefers existing local file; message file paths are rejected', () => {
  assert.deepEqual(sourceFromImage({ url: 'https://gchat.qpic.cn/expired', file: fixture }, true), { path: fixture, trusted: true });
  assert.equal(sourceFromImage({ file: fixture }, false), null);
});

test('hook returns immediately, skips private/self, and deduplicates WS+hook delivery', async () => {
  const h = harness(); h.ctl.activate(); await delay();
  const ev = event(); const ret = h.ctl.onMessage(ev);
  assert.equal(ret, undefined); assert.equal(h.sent.length, 0);
  h.ctl.onMessage(ev);
  const privateEv = event(2); privateEv.event.message_type = 'private'; privateEv.chatKey = 'private:456'; h.ctl.onMessage(privateEv);
  const self = event(3); self.event.user_id = 789; h.ctl.onMessage(self);
  await delay(); assert.equal(h.sent.length, 1); assert.equal(h.ctl.snapshot().counts.saved, 1);
  assert.equal(h.ctl.snapshot().model, 'test-model');
  assert.equal(h.children[0].options.windowsHide, true); assert.equal(h.children[0].options.shell, false);
  h.ctl.dispose(); assert.equal(h.children[0].killed, true);
});

test('untrusted local path only records rejection and never invokes get_image', async () => {
  const h = harness(); h.ctl.activate(); await delay(); h.ctl.onMessage(event(1, { file: fixture })); await delay();
  assert.equal(h.qqCalls.length, 0); assert.equal(h.sent[0].command, 'record'); assert.equal(h.sent[0].reason, 'unsupported_image_source');
  h.ctl.dispose();
});

test('unknown-character settings preserve boolean values and the person score threshold', async () => {
  for (const enabled of [true, false]) {
    const h = harness({ settings: { saveUnknownCharacters: enabled, personThreshold: 0.74 } });
    h.ctl.activate(); await delay(); h.ctl.onMessage(event()); await delay();
    assert.equal(h.sent.length, 1);
    assert.equal(typeof h.sent[0].settings.saveUnknownCharacters, 'boolean');
    assert.equal(h.sent[0].settings.saveUnknownCharacters, enabled);
    assert.equal(h.sent[0].settings.personThreshold, 0.74);
    h.ctl.dispose();
  }
});

test('unidentified person candidate preserves its status without inventing a character tag', async () => {
  const h = harness({ reply: () => ({ status: 'saved', reason: 'accepted_unknown_character_candidate',
    characters: [], characterStatus: 'unidentified', personEvidence: { '1girl': 0.93 },
    files: ['SFW/_unknown_character/image.png'] }) });
  h.ctl.activate(); await delay(); h.ctl.onMessage(event()); await delay();
  const snapshot = h.ctl.snapshot();
  assert.equal(snapshot.counts.saved, 1);
  assert.deepEqual(snapshot.recent[0].characters, []);
  assert.equal(snapshot.recent[0].characterStatus, 'unidentified');
  assert.deepEqual(snapshot.recent[0].personEvidence, { '1girl': 0.93 });
  assert.equal(snapshot.recent[0].reason, 'accepted_unknown_character_candidate');
  assert.equal(snapshot.recent[0].files[0], 'SFW/_unknown_character/image.png');
  h.ctl.dispose();
});

test('GPU provider and matched library image remain visible without counting a new saved file', async () => {
  const sha = 'a'.repeat(64);
  const matched = 'SFW/character/library-image.png';
  const h = harness({ readyPayload: { provider: 'CUDA: NVIDIA GeForce RTX 4070' },
    reply: () => ({ status: 'duplicate', reason: 'near_duplicate', duplicateOf: sha, duplicateFile: matched }) });
  h.ctl.activate(); await delay(); h.ctl.onMessage(event()); await delay();
  const snapshot = h.ctl.snapshot();
  assert.equal(snapshot.provider, 'CUDA: NVIDIA GeForce RTX 4070');
  assert.equal(snapshot.counts.saved, 0);
  assert.equal(snapshot.counts.duplicate, 1);
  assert.equal(snapshot.recent[0].duplicateOf, sha);
  assert.equal(snapshot.recent[0].duplicateFile, matched);
  assert.deepEqual(snapshot.recent[0].files, []);
  h.ctl.dispose();
});

test('expired QQ URL retries once with get_image local cache and no cloud upload', async () => {
  const h = harness({ qqCall: () => ({ url: 'https://gchat.qpic.cn/expired', file: fixture }), reply: (r) => r.source?.url ? { status: 'error', reason: 'download_failed:HTTP_400' } : { status: 'saved', reason: 'saved' } });
  h.ctl.activate(); await delay(); h.ctl.onMessage(event(1, { file: 'ABC123.jpg', url: 'https://gchat.qpic.cn/expired' })); await delay();
  assert.equal(h.qqCalls.length, 1); assert.equal(h.sent.length, 2); assert.equal(h.sent[1].source.path, fixture);
  assert.equal(h.ctl.snapshot().counts.saved, 1); assert.equal(h.ctl.snapshot().counts.error, 0); h.ctl.dispose();
});

test('queue is bounded and disabling clears waiting work', async () => {
  const h = harness({ settings: { maxQueue: 2 }, ready: false }); h.ctl.activate();
  h.ctl.onMessage(event(1)); h.ctl.onMessage(event(2)); h.ctl.onMessage(event(3));
  assert.equal(h.ctl.snapshot().queue, 2); assert.equal(h.ctl.snapshot().counts.dropped, 1);
  h.ctl.deactivate(); h.ctl.onMessage(event(4)); await delay();
  assert.equal(h.ctl.snapshot().queue, 0); assert.equal(h.sent.length, 0); assert.equal(h.children[0].killed, true); h.ctl.dispose();
});

test('worker task timeout kills only its child and automatically recovers', async () => {
  const h = harness({ taskMs: 20, retryMs: 5, reply: (_r, n) => n === 1 ? null : { status: 'saved' } });
  h.ctl.activate(); await delay(); h.ctl.onMessage(event(1)); h.ctl.onMessage(event(2)); await delay(100);
  assert.equal(h.children[0].killed, true); assert.equal(h.children.length, 2);
  assert.equal(h.ctl.snapshot().counts.error, 1); assert.equal(h.ctl.snapshot().counts.saved, 1); h.ctl.dispose();
});

test('missing dependencies report unavailable without spawning', async () => {
  const h = harness({ exists: false }); h.ctl.activate(); await delay(35);
  assert.equal(h.ctl.available().ok, false); assert.equal(h.children.length, 0); h.ctl.dispose();
});

test('PixAI checks its complete local package without requiring an ONNX tag list', () => {
  const checked = [];
  const h = harness({ settings: { modelDir: process.cwd() + '/pixai-tagger-v1.0' }, exists: (file) => { checked.push(file); return !(file.includes('pixai-tagger-v1.0') && file.endsWith('model.onnx')); } });
  assert.equal(h.ctl.available().ok, true);
  for (const name of ['model.safetensors', 'config.json', 'preprocessor_config.json', 'tagger_pipeline.py', 'similarity.py', 'model_runtime.py']) {
    assert.equal(checked.some((file) => file.endsWith(name)), true, name);
  }
  assert.equal(checked.some((file) => file.endsWith('selected_tags.csv')), false);
  h.ctl.dispose();
  const missing = harness({ settings: { modelDir: process.cwd() + '/pixai-tagger-v1.0' }, exists: (file) => !(file.includes('pixai-tagger-v1.0') && file.endsWith('model.onnx')) && !file.endsWith('preprocessor_config.json') });
  assert.equal(missing.ctl.available().ok, false);
  assert.match(missing.ctl.available().reason, /preprocessor_config\.json/);
  missing.ctl.dispose();
});

test('image type settings retain booleans and rejected type details', async () => {
  const h = harness({ settings: { filterImageType: true, rejectScreenshots: false, allow3d: true, minIllustrationScore: .72 }, reply: () => ({ status: 'rejected', reason: 'unsuitable_image_type:not_painting', typeAssessment: { predicted: 'not_painting', accepted: false, scores: { not_painting: .97 } } }) });
  h.ctl.activate(); await delay(); h.ctl.onMessage(event()); await delay();
  assert.equal(h.sent[0].settings.filterImageType, true);
  assert.equal(h.sent[0].settings.rejectScreenshots, false);
  assert.equal(h.sent[0].settings.allow3d, true);
  assert.equal(h.sent[0].settings.minIllustrationScore, .72);
  assert.equal(h.ctl.snapshot().recent[0].typeAssessment.predicted, 'not_painting');
  assert.equal(h.ctl.snapshot().counts.saved, 0); h.ctl.dispose();
});

test('legacy WD model remains available when its ONNX file and tag list exist', () => {
  const checked = [];
  const h = harness({ settings: { modelDir: process.cwd() + '/legacy-wd' }, exists: (file) => { checked.push(file); return !file.endsWith('model.safetensors'); } });
  assert.equal(h.ctl.available().ok, true);
  assert.equal(checked.some((file) => file.endsWith('selected_tags.csv')), true);
  assert.equal(checked.some((file) => file.endsWith('model.safetensors')), false);
  h.ctl.dispose();
});

test('forward images use message_id, preserve serial work and bounded expansion', async () => {
  const h = harness({ qqCall: () => ({ messages: [{ message_id: 25, sender: { user_id: 555 }, content: [{ type: 'image', data: { url: 'https://gchat.qpic.cn/forward' } }] }] }) });
  h.ctl.activate(); await delay(); const ev = event(10); ev.event.message = [{ type: 'forward', data: { id: 'unreliable-res-id' } }]; h.ctl.onMessage(ev); await delay();
  assert.equal(h.qqCalls[0].action, 'get_forward_msg'); assert.deepEqual(h.qqCalls[0].args, { message_id: 10 });
  assert.equal(h.sent.length, 1); assert.equal(h.sent[0].meta.senderId, 555); h.ctl.dispose();
});

test('nested forward uses its resource id when the node message id is not cached', async () => {
  const h = harness({ qqCall: (_action, args) => {
    if (args.message_id === 10) return {messages:[{message_id:999, message:[{type:'forward',data:{id:'nested-resource'}}]}]};
    if (args.id === 'nested-resource') return {data:{messages:[{message_id:25, message:[{type:'image',data:{url:'https://gchat.qpic.cn/nested'}}]}]}};
    throw new Error('payload is empty');
  }});
  h.ctl.activate(); await delay(); const ev=event(10); ev.event.message=[{type:'forward',data:{id:'root-resource'}}]; h.ctl.onMessage(ev); await delay();
  assert.deepEqual(h.qqCalls.map(call=>call.args),[{message_id:10},{id:'nested-resource'}]);
  assert.equal(h.sent.length,1); assert.equal(h.sent[0].meta.forwardMessageId,'10'); assert.equal(h.sent[0].meta.forwardDepth,2);
  h.ctl.dispose();
});

test('forward falls back from missing or empty message cache to resource id, including negative message ids', async () => {
  for (const empty of [true,false]) {
    const h=harness({qqCall:(_action,args)=>{
      if ('message_id' in args) { if (empty) return {messages:[]}; throw new Error('payload is empty'); }
      return [{content:'[CQ:image,file=ok.jpg,url=https://gchat.qpic.cn/fallback]'}];
    }});
    h.ctl.activate(); await delay(); const ev=event(-10); ev.event.message=[{type:'forward',data:{res_id:'root-resource'}}]; h.ctl.onMessage(ev); await delay();
    assert.deepEqual(h.qqCalls.map(call=>call.args),[{message_id:-10},{id:'root-resource'}]);
    assert.equal(h.sent.length,1); assert.equal(h.sent[0].command,'process'); h.ctl.dispose();
  }
});

test('multiple forward cards use their own resource rather than repeating the first card', async () => {
  const h=harness({qqCall:(_action,args)=>({messages:[{content:[{type:'image',data:{url:'https://gchat.qpic.cn/'+args.id}}]}]})});
  h.ctl.activate(); await delay(); const ev=event(10); ev.event.message=[{type:'forward',data:{id:'card-a'}},{type:'forward',data:{id:'card-b'}}]; h.ctl.onMessage(ev); await delay();
  assert.deepEqual(h.qqCalls.map(call=>call.args),[{id:'card-a'},{id:'card-b'}]);
  assert.equal(h.sent.length,2); assert.notEqual(h.sent[0].source.url,h.sent[1].source.url); h.ctl.dispose();
});

test('one original message has a shared 100-image budget across nested nodes', async () => {
  const h=harness({qqCall:(_action,args)=>args.message_id===10
    ? {messages:[{message_id:20,message:[{type:'image',data:{url:'https://gchat.qpic.cn/direct'}},{type:'forward',data:{id:'nested'}}]}]}
    : {messages:Array.from({length:100},(_,i)=>({message:[{type:'image',data:{url:'https://gchat.qpic.cn/'+i}},{type:'image',data:{url:'https://gchat.qpic.cn/other-'+i}}]}))}});
  h.ctl.activate(); await delay(); const ev=event(10); ev.event.message=[{type:'forward',data:{id:'root'}}]; h.ctl.onMessage(ev); await delay(50);
  assert.equal(h.sent.length,100); assert.equal(h.ctl.snapshot().counts.received,100); h.ctl.dispose();
});

test('disabling forward coverage also skips inline node pictures', async () => {
  const h=harness({settings:{includeForwards:false}}); h.ctl.activate(); await delay(); const ev=event(10);
  ev.event.message=[{type:'forward',data:{id:'root'}},{type:'node',data:{content:[{type:'image',data:{url:'https://gchat.qpic.cn/inline'}}]}}]; h.ctl.onMessage(ev); await delay();
  assert.equal(h.qqCalls.length,0); assert.equal(h.sent.length,0); h.ctl.dispose();
});

test('an unavailable forward records an error and continues with the next image', async () => {
  const h=harness({qqCall:()=>{throw new Error('payload is empty');}}); h.ctl.activate(); await delay();
  const ev=event(10); ev.event.message=[{type:'forward',data:{id:'expired'}},{type:'image',data:{url:'https://gchat.qpic.cn/next'}}]; h.ctl.onMessage(ev); await delay();
  assert.equal(h.qqCalls.length,2); assert.equal(h.sent[0].reason,'forward_unavailable'); assert.equal(h.sent[1].command,'process');
  assert.equal(h.ctl.snapshot().counts.saved,1); h.ctl.dispose();
});

test('all-groups receiver only uses loopback and respects deny/global/member blocks', async () => {
  assert.equal(isLoopbackWebSocket('ws://127.0.0.1:10001'), true); assert.equal(isLoopbackWebSocket('ws://example.com'), false);
  const sockets = [], delivered = [], statuses = [];
  const cfg = { snowluma: { wsUrl: 'ws://127.0.0.1:10001', accessToken: 'test-token' }, allow: { groups: ['1'] }, deny: { groups: ['999'] }, blocklist: { '123': ['555'] } };
  class Socket extends EventEmitter {
    constructor(url, options) { super(); this.url = url; this.options = options; this.dead = false; sockets.push(this); queueMicrotask(() => this.emit('open')); }
    terminate() { this.dead = true; this.emit('close'); }
  }
  const receiver = createReceiver({ onEvent: (v) => delivered.push(v), onState: (s) => statuses.push(s), getScope: () => 'all_groups' }, {
    loadDependencies: async () => ({ WebSocket: Socket, getConfig: () => cfg, isGloballyBlocked: (id) => id === '666' }), pollMs: 15, retryBaseMs: 5
  });
  receiver.start(); await delay();
  for (const raw of ['null', '[]', '"text"', '123', '{bad']) assert.doesNotThrow(() => sockets.at(-1).emit('message', Buffer.from(raw)));
  const send = (patch = {}) => sockets.at(-1).emit('message', Buffer.from(JSON.stringify({ ...event().event, ...patch })));
  send(); send({ group_id: 999 }); send({ user_id: 555 }); send({ user_id: 666 }); send({ user_id: 789 }); send({ message_type: 'private' }); send({ post_type: 'message_sent' });
  assert.equal(delivered.length, 1); assert.equal(delivered[0].group_id, 123); // outside host allow list
  assert.equal(sockets[0].url.includes('test-token'), false); assert.equal(JSON.stringify(statuses).includes('test-token'), false);
  cfg.blocklist['123'] = 'not-an-array'; assert.doesNotThrow(() => send()); assert.equal(delivered.length, 2);
  cfg.snowluma.accessToken = 'new-token'; await delay(35); assert.equal(sockets.length, 2); assert.equal(sockets[0].dead, true);
  receiver.stop(); await delay(35); assert.equal(sockets.length, 2); assert.equal(sockets[1].dead, true);
});

test('receiver refuses nonloopback config and never opens a socket', async () => {
  let opened = false;
  const receiver = createReceiver({ onEvent() {}, onState() {}, getScope: () => 'all_groups' }, { loadDependencies: async () => ({ WebSocket: class { constructor() { opened = true; } }, getConfig: () => ({ snowluma: { wsUrl: 'ws://evil.test' } }), isGloballyBlocked: () => false }) });
  receiver.start(); await delay(); assert.equal(opened, false); receiver.stop();
});

test('stopping receiver during dependency loading never creates a late socket', async () => {
  let resolve, opened = 0;
  const promise = new Promise((r) => { resolve = r; });
  const receiver = createReceiver({ onEvent() {}, onState() {}, getScope: () => 'all_groups' }, { loadDependencies: () => promise });
  receiver.start(); receiver.stop();
  resolve({ WebSocket: class { constructor() { opened++; } }, getConfig: () => ({ snowluma: { wsUrl: 'ws://127.0.0.1:10001' } }), isGloballyBlocked: () => false });
  await delay(); assert.equal(opened, 0);
});
