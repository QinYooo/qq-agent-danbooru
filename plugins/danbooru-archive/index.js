import { homedir } from 'node:os';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { createReceiver } from './receiver.js';

const ID = 'danbooru-archive';
const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULTS = Object.freeze({
  pythonPath: 'python',
  modelDir: '',
  outputRoot: path.join(homedir(), 'Pictures', 'Danbooru'),
  minSide: 768, minPixels: 786432, characterThreshold: 0.85,
  saveUnknownCharacters: true, personThreshold: 0.7,
  generalThreshold: 0.35, filterThreshold: 0.6, nsfwThreshold: 0.6,
  ratingConfidence: 0.6, maxBytes: 26214400, maxPixels: 40000000,
  maxAspect: 2.5, nearDuplicateDistance: 4, minEntropy: 3, minSharpness: 12,
  maxQueue: 300, includeForwards: true, scope: 'all_groups',
  imageTypeModelDir: '',
  filterImageType: true, minIllustrationScore: 0.65, allow3d: false,
  rejectScreenshots: true, screenshotThreshold: 0.35, explicitTagThreshold: 0.6
});
const FILTER_KEYS = ['minSide', 'minPixels', 'characterThreshold', 'generalThreshold', 'filterThreshold',
  'nsfwThreshold', 'ratingConfidence', 'maxBytes', 'maxPixels', 'maxAspect',
  'nearDuplicateDistance', 'minEntropy', 'minSharpness', 'personThreshold',
  'minIllustrationScore', 'screenshotThreshold', 'explicitTagThreshold'];
const MAX_MEMORY = 64 * 1024 * 1024;
const MAX_ITEMS_PER_MESSAGE = 100;
const text = (value, max = 500) => String(value ?? '').slice(0, max);
const cleanError = (value) => text(value?.message ?? value, 700).replace(/https?:\/\/\S+/gi, '[图片地址已隐藏]');
const unescapeCQ = (s) => String(s).replace(/&#44;/g, ',').replace(/&#91;/g, '[').replace(/&#93;/g, ']').replace(/&amp;/g, '&');

export function allowedUrl(value) {
  try {
    const u = new URL(value);
    return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password &&
      (!u.port || ['80', '443'].includes(u.port)) &&
      (u.hostname === 'multimedia.nt.qq.com.cn' || ['qpic.cn', 'qq.com', 'gtimg.cn'].some((h) => u.hostname === h || u.hostname.endsWith('.' + h)));
  } catch { return false; }
}

export function messageSegments(message) {
  if (Array.isArray(message)) return message.slice(0, 500);
  if (typeof message !== 'string') return [];
  const out = [];
  for (const m of message.matchAll(/\[CQ:(image|forward),([^\]]*)\]/g)) {
    const data = {};
    for (const part of m[2].split(',')) {
      const p = part.indexOf('=');
      if (p > 0) data[part.slice(0, p)] = unescapeCQ(part.slice(p + 1));
    }
    out.push({ type: m[1], data });
    if (out.length >= 500) break;
  }
  return out;
}

export function sourceFromImage(data, trusted = false, maxBytes = DEFAULTS.maxBytes) {
  if (trusted) {
    for (const v of [data?.file, data?.path, data?.file_path]) {
      if (typeof v !== 'string') continue;
      try {
        const local = v.startsWith('file://') ? fileURLToPath(v) : v;
        if (path.isAbsolute(local) && existsSync(local)) return { path: local, trusted: true };
      } catch { /* try the next returned source */ }
    }
  }
  for (const v of [data?.url, data?.file, data?.path, data?.file_path]) {
    if (typeof v !== 'string' || !v) continue;
    if (allowedUrl(v)) return { url: v };
    if (v.startsWith('base64://')) {
      const value = v.slice(9);
      if (value.length <= Math.ceil(maxBytes * 4 / 3) + 8 && /^[A-Za-z0-9+/=\r\n]+$/.test(value)) return { base64: value };
    }
  }
  return null;
}

function imageToken(data) {
  const file = data?.file;
  // Never ask OneBot to resolve an arbitrary local path supplied in a message.
  return typeof file === 'string' && /^[A-Za-z0-9_{}=.-]{1,300}$/.test(file) && !file.includes('..') ? file : null;
}

export function forwardResourceId(data) {
  for (const value of [data?.id, data?.res_id, data?.forward_id, data?.data_id]) {
    if (typeof value === 'string' && /^[A-Za-z0-9_+/=-]{1,1024}$/.test(value)) return value;
  }
  return null;
}

function forwardNodes(response) {
  const nodes = Array.isArray(response) ? response : response?.messages ?? response?.data?.messages ?? response?.data;
  return Array.isArray(nodes) ? nodes.filter((node) => node && typeof node === 'object') : [];
}

export function createController(api, overrides = {}) {
  const spawnChild = overrides.spawn || spawn;
  const fileExists = overrides.exists || existsSync;
  const workerPath = overrides.workerPath || path.join(HERE, 'worker.py');
  const startupMs = overrides.startupMs || 120000;
  const taskMs = overrides.taskMs || 120000;
  const retryMs = overrides.retryMs || 5000;
  let enabled = false, disposed = false, child = null, reader = null, ready = false;
  let queue = [], queueBytes = 0, pumping = false, seq = 0, generation = 0;
  let current = null, startTimer = null, retryTimer = null, flushTimer = null;
  let failures = 0, runtimeKey = '', stderrTail = '';
  const seen = new Map();
  let old = {};
  try { old = api.storage?.get('snapshot', {}) || {}; } catch { /* recover fresh */ }
  const state = {
    status: 'disabled', message: '已关闭', model: '', provider: '', lastError: '', updatedAt: '',
    receiverStatus: 'disabled', receiverMessage: '群图片接收通道已关闭',
    counts: { received: 0, saved: 0, rejected: 0, duplicate: 0, error: 0, dropped: 0, ...(old.counts || {}) },
    recent: Array.isArray(old.recent) ? old.recent.slice(0, 20) : []
  };
  function config() { return { ...DEFAULTS, ...(api.config?.() || {}) }; }
  function snapshot() { return { ...state, counts: { ...state.counts }, recent: [...state.recent], queue: queue.length, processing: pumping, outputRoot: config().outputRoot, scope: config().scope === 'all_groups' ? '所有群聊；保留群黑名单、全局与群成员屏蔽规则；不含私聊和机器人自己发的图' : 'QQ Agent 聊天白名单内的群' }; }
  function flush() {
    clearTimeout(flushTimer); flushTimer = null;
    state.updatedAt = new Date().toISOString();
    try { api.storage?.set('snapshot', snapshot()); } catch (e) { api.warn?.('状态保存失败：', cleanError(e)); }
  }
  function changed() {
    if (!flushTimer) { flushTimer = setTimeout(flush, 1500); flushTimer.unref?.(); }
  }
  function setState(status, message) { state.status = status; state.message = message; changed(); }
  function result(value, meta = {}) {
    const status = ['saved', 'rejected', 'duplicate', 'error', 'dropped'].includes(value.status) ? value.status : 'error';
    state.counts[status]++;
    const item = {
      at: new Date().toISOString(), status, reason: text(value.reason, 250), error: cleanError(value.error || ''),
      groupId: text(meta.groupId, 30), messageId: text(meta.messageId, 100),
      characters: Array.isArray(value.characters) ? value.characters.slice(0, 20).map((c) => typeof c === 'string' ? text(c, 150) : text(c?.name || c?.tag, 150)) : [],
      characterStatus: value.characterStatus === 'unidentified' ? 'unidentified' : value.characterStatus === 'identified' ? 'identified' : undefined,
      personEvidence: value.personEvidence && typeof value.personEvidence === 'object'
        ? Object.fromEntries(Object.entries(value.personEvidence).filter(([, score]) => typeof score === 'number' && Number.isFinite(score)).slice(0, 20).map(([tag, score]) => [text(tag, 100), score])) : {},
      duplicateOf: text(value.duplicateOf, 64), duplicateFile: text(value.duplicateFile, 500),
      typeAssessment: value.typeAssessment && typeof value.typeAssessment === 'object'
        ? { predicted: text(value.typeAssessment.predicted, 40), accepted: value.typeAssessment.accepted === true,
            scores: value.typeAssessment.scores } : undefined,
      screenshotEvidence: value.screenshotEvidence || {}, explicitEvidence: value.explicitEvidence || {},
      rating: value.rating, files: Array.isArray(value.files) ? value.files.slice(0, 10).map((f) => text(f, 500)) : []
    };
    state.recent.unshift(item); state.recent.length = Math.min(state.recent.length, 20);
    if (status === 'error') { state.lastError = item.error || item.reason; api.warn?.('图片归档失败：', item.reason, item.error); }
    changed();
  }
  function dependencies() {
    const c = config();
    const required = [[c.pythonPath, 'Python 环境'], [workerPath, '分类程序'],
      [path.join(HERE, 'similarity.py'), '相似图片比较程序'], [path.join(HERE, 'model_runtime.py'), '模型运行程序'],
      [path.join(HERE, 'image_quality.py'), '画面类型筛选程序'], [path.join(c.imageTypeModelDir, 'model.onnx'), '画面类型筛选模型']];
    if (fileExists(path.join(c.modelDir, 'model.onnx'))) {
      required.push([path.join(c.modelDir, 'selected_tags.csv'), 'WD 标签表']);
    } else {
      required.push(...['model.safetensors', 'config.json', 'preprocessor_config.json', 'tagger_pipeline.py'].map((f) => [path.join(c.modelDir, f), `PixAI 模型文件 ${f}`]));
    }
    for (const [f, label] of required) {
      if (!fileExists(f)) return { ok: false, reason: `${label}不存在：${f}` };
    }
    return { ok: true };
  }
  function rejectCurrent(error) {
    const pending = current; current = null;
    if (pending) { clearTimeout(pending.timer); pending.reject(error); }
  }
  function killWorker() {
    clearTimeout(startTimer); startTimer = null;
    ready = false;
    const oldChild = child; child = null;
    reader?.close(); reader = null;
    if (oldChild) { try { oldChild.stdin.destroy(); oldChild.kill(); } catch { /* already exited */ } }
    rejectCurrent(new Error('worker_stopped'));
  }
  function scheduleRestart() {
    if (!enabled || disposed || retryTimer) return;
    const delay = Math.min(60000, retryMs * Math.max(1, failures));
    retryTimer = setTimeout(() => { retryTimer = null; start(); }, delay);
    retryTimer.unref?.();
  }
  function failWorker(proc, error) {
    if (child !== proc) return;
    failures++;
    state.lastError = cleanError(error);
    api.warn?.('本地分类进程异常，将自动重试：', state.lastError);
    killWorker();
    if (enabled) { setState('retrying', '本地分类进程异常，等待自动重试'); scheduleRestart(); }
  }
  function start() {
    if (!enabled || disposed || child) return;
    const dep = dependencies();
    if (!dep.ok) { failures++; state.lastError = dep.reason; setState('error', dep.reason); scheduleRestart(); return; }
    const c = config();
    runtimeKey = [c.pythonPath, c.modelDir, c.outputRoot, c.imageTypeModelDir].join('\n');
    setState('loading', '正在载入本地图片标签模型');
    stderrTail = '';
    let proc;
    try {
      proc = spawnChild(c.pythonPath, ['-B', '-u', workerPath, '--model-dir', c.modelDir, '--output-root', c.outputRoot,
        '--image-type-model-dir', c.imageTypeModelDir], {
        windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'], cwd: HERE
      });
    } catch (e) { failures++; state.lastError = cleanError(e); setState('error', '分类进程启动失败'); scheduleRestart(); return; }
    child = proc;
    proc.stdin.on('error', (e) => failWorker(proc, e));
    proc.on('error', (e) => failWorker(proc, e));
    proc.on('exit', (code, signal) => failWorker(proc, new Error(`分类进程退出（${code ?? signal ?? 'unknown'}）${stderrTail ? '：' + stderrTail : ''}`)));
    proc.stderr.setEncoding('utf8');
    proc.stderr.on('data', (chunk) => { stderrTail = cleanError((stderrTail + chunk).slice(-1500)); });
    reader = createInterface({ input: proc.stdout });
    reader.on('line', (line) => {
      if (child !== proc) return;
      if (line.length > 1024 * 1024) { failWorker(proc, new Error('分类进程返回过大的响应')); return; }
      let value;
      try { value = JSON.parse(line); } catch { return; }
      if (value.event === 'ready') {
        clearTimeout(startTimer); startTimer = null; ready = true; failures = 0;
        state.model = typeof value.model === 'string' ? value.model : 'PixAI Tagger v1.0';
        state.provider = typeof value.provider === 'string' ? text(value.provider, 150) : '';
        state.lastError = ''; setState('ready', '已就绪，自动筛选所有群聊图片'); flush(); pump();
      } else if (value.event === 'error' || value.event === 'fatal') {
        failWorker(proc, new Error(value.error || value.message || value.reason || '本地模型初始化失败'));
      } else if (current && String(value.id) === current.id) {
        const pending = current; current = null; clearTimeout(pending.timer); pending.resolve(value);
      }
    });
    startTimer = setTimeout(() => failWorker(proc, new Error('本地模型载入超时')), startupMs);
    startTimer.unref?.();
  }
  function request(payload) {
    return new Promise((resolve, reject) => {
      if (!child || !ready || current) { reject(new Error('worker_not_ready')); return; }
      const proc = child;
      const id = `${Date.now()}-${++seq}`;
      const timer = setTimeout(() => failWorker(proc, new Error('单张图片处理超时')), taskMs);
      timer.unref?.();
      current = { id, resolve, reject, timer };
      try { proc.stdin.write(JSON.stringify({ ...payload, id }) + '\n'); }
      catch (e) { failWorker(proc, e); }
    });
  }
  async function qqImage(data) {
    const file = imageToken(data);
    if (!file) return null;
    const r = await api.qqCall('get_image', { file });
    const d = r?.data && typeof r.data === 'object' ? r.data : r;
    return sourceFromImage(d, true, Number(config().maxBytes));
  }
  function isCurrent(g) { return enabled && !disposed && g === generation; }
  function enqueue(job, front = false) {
    const max = Math.max(1, Math.min(1000, Number(config().maxQueue) || DEFAULTS.maxQueue));
    const bytes = job.source?.base64?.length || 2048;
    if (queue.length >= max || queueBytes + bytes > MAX_MEMORY) {
      result({ status: 'dropped', reason: 'queue_full', error: '图片队列已满，本张未保存' }, job.meta);
      api.warn?.('图片队列已满，已跳过一项（未保存原图）');
      return;
    }
    job.bytes = bytes;
    if (front) queue.unshift(job); else queue.push(job);
    queueBytes += bytes; changed();
  }
  function jobsFromSegments(segments, meta, depth = 0, context = { remaining: MAX_ITEMS_PER_MESSAGE, seen: new Set(), rootMessageId: meta.messageId }) {
    const jobs = [];
    const c = config();
    const parsed = messageSegments(segments);
    const multipleForwards = parsed.filter((segment) => segment?.type === 'forward').length > 1;
    for (const segment of parsed) {
      if (jobs.length >= MAX_ITEMS_PER_MESSAGE || context.remaining <= 0) break;
      const data = segment?.data || {};
      if (segment?.type === 'image') {
        context.remaining--;
        state.counts.received++;
        const source = sourceFromImage(data, false, Number(c.maxBytes));
        jobs.push({ type: 'image', source, file: imageToken(data), meta });
      } else if (segment?.type === 'forward' && c.includeForwards && depth < 2) {
        jobs.push({ type: 'forward', messageId: meta.messageId, forwardId: forwardResourceId(data),
          preferResource: depth > 0 || multipleForwards, allowMessageId: !multipleForwards,
          meta, depth: depth + 1, context });
      } else if (segment?.type === 'node' && c.includeForwards && depth < 2) {
        jobs.push(...jobsFromSegments(data.content || data.message || [], { ...meta,
          messageId: data.message_id ?? data.id ?? meta.messageId,
          senderId: data.sender?.user_id ?? data.user_id ?? data.uin ?? meta.senderId,
          forwardMessageId: context.rootMessageId, forwardDepth: depth + 1 }, depth + 1, context).slice(0, MAX_ITEMS_PER_MESSAGE - jobs.length));
      }
    }
    return jobs;
  }
  async function expandForward(job, g) {
    if (!job.context.remaining) return null;
    const key = job.forwardId ? 'resource:' + job.forwardId : 'message:' + job.messageId;
    if (job.context.seen.has(key)) return null;
    job.context.seen.add(key);
    const attempts = [];
    if (job.allowMessageId && /^-?\d+$/.test(String(job.messageId))) {
      const numeric = Number(job.messageId);
      attempts.push({ message_id: Number.isSafeInteger(numeric) ? numeric : String(job.messageId) });
    }
    if (job.forwardId) {
      if (job.preferResource) attempts.unshift({ id: job.forwardId });
      else attempts.push({ id: job.forwardId });
    }
    if (!attempts.length) return request({ command: 'record', status: 'rejected', reason: 'forward_missing_reference', meta: job.meta });
    let nodes = [];
    for (const args of attempts) {
      try { nodes = forwardNodes(await api.qqCall('get_forward_msg', args)); }
      catch (e) { api.warn?.('读取合并转发失败，将尝试可用的另一种编号：', cleanError(e)); }
      if (!isCurrent(g)) return null;
      if (nodes.length) break;
    }
    if (!nodes.length) return request({ command: 'record', status: 'error', reason: 'forward_unavailable', meta: job.meta });
    let jobs = [];
    for (const node of nodes.slice(0, 100)) {
      const content = node.content || node.message || node.data?.content || node.data?.message || node.raw_message || [];
      const meta = { ...job.meta, messageId: node.message_id ?? node.id ?? node.data?.id ?? '',
        senderId: node.sender?.user_id ?? node.user_id ?? node.data?.uin ?? job.meta.senderId,
        forwardMessageId: job.context.rootMessageId, forwardDepth: job.depth };
      jobs.push(...jobsFromSegments(content, meta, job.depth, job.context));
      if (jobs.length >= MAX_ITEMS_PER_MESSAGE) { jobs = jobs.slice(0, MAX_ITEMS_PER_MESSAGE); break; }
    }
    for (const next of jobs.reverse()) enqueue(next, true);
    return null;
  }
  async function processJob(job, g) {
    if (job.type === 'forward') return expandForward(job, g);
    let source = job.source;
    if (!source && job.file) source = await qqImage({ file: job.file });
    if (!isCurrent(g)) return null;
    if (!source) return request({ command: 'record', status: 'rejected', reason: 'unsupported_image_source', meta: job.meta });
    const settings = Object.fromEntries(FILTER_KEYS.map((k) => [k, Number(config()[k])]));
    settings.saveUnknownCharacters = config().saveUnknownCharacters !== false;
    settings.filterImageType = config().filterImageType !== false;
    settings.rejectScreenshots = config().rejectScreenshots !== false;
    settings.allow3d = config().allow3d === true;
    let value = await request({ command: 'process', source, meta: job.meta, settings });
    if (isCurrent(g) && value.status === 'error' && String(value.reason).startsWith('download_failed') && job.file && source.url) {
      const fresh = await qqImage({ file: job.file });
      if (!isCurrent(g)) return null;
      if (fresh) value = await request({ command: 'process', source: fresh, meta: job.meta, settings });
    }
    return value;
  }
  async function pump() {
    if (pumping || !enabled || !ready || disposed) return;
    pumping = true;
    const g = generation;
    try {
      while (isCurrent(g) && ready && queue.length) {
        const c = config();
        if (runtimeKey !== [c.pythonPath, c.modelDir, c.outputRoot, c.imageTypeModelDir].join('\n')) { killWorker(); start(); break; }
        const job = queue.shift(); queueBytes -= job.bytes; changed();
        try { const value = await processJob(job, g); if (isCurrent(g) && value) result(value, job.meta); }
        catch (e) { if (isCurrent(g)) result({ status: 'error', reason: 'processing_failed', error: cleanError(e) }, job.meta); }
      }
    } finally {
      pumping = false; changed();
      if (enabled && ready && queue.length) queueMicrotask(pump);
    }
  }
  function onMessage(ctx) {
    if (!enabled || disposed) return;
    const event = ctx?.event;
    if (!event || (event.message_type !== 'group' && !String(ctx.chatKey || '').startsWith('group:'))) return;
    if (event.post_type && event.post_type !== 'message') return;
    if (event.self_id != null && String(event.self_id) === String(event.user_id ?? ctx.senderId)) return;
    const groupId = String(event.group_id ?? String(ctx.chatKey || '').split(':')[1] ?? '');
    if (!/^\d+$/.test(groupId)) return;
    const messageId = text(event.message_id, 100);
    const key = `${groupId}:${messageId}`;
    if (messageId && seen.has(key)) return;
    if (messageId) { seen.set(key, Date.now()); if (seen.size > 2000) seen.delete(seen.keys().next().value); }
    const meta = { groupId, messageId, senderId: text(event.user_id ?? ctx.senderId, 30) };
    for (const job of jobsFromSegments(event.message || event.raw_message || [], meta)) enqueue(job);
    start();
    // All OneBot calls and image work start on a later turn, never inside the hook.
    queueMicrotask(pump);
  }
  function activate() {
    if (disposed || enabled) return;
    enabled = true; generation++;
    receiver.start();
    setState('loading', '正在启动本地分类'); start(); flush();
  }
  function deactivate() {
    if (!enabled && !child && !retryTimer) return;
    enabled = false; generation++;
    receiver.stop();
    clearTimeout(retryTimer); retryTimer = null;
    state.counts.dropped += queue.length;
    queue = []; queueBytes = 0; seen.clear();
    killWorker(); setState('disabled', '已关闭，已停止分类并清空等待队列'); flush();
  }
  function dispose() { deactivate(); disposed = true; clearTimeout(flushTimer); flushTimer = null; }
  function available() {
    const dep = dependencies();
    if (!dep.ok) return dep;
    if (state.status === 'error') return { ok: false, reason: state.lastError || state.message };
    return { ok: true };
  }
  const receiver = overrides.receiver || createReceiver({
    onEvent: (event) => onMessage({ event, chatKey: `group:${event.group_id}`, senderId: event.user_id }),
    onState: (r) => { state.receiverStatus = r.status; state.receiverMessage = r.message; changed(); },
    getScope: () => config().scope
  });
  return { activate, deactivate, dispose, available, onMessage, snapshot };
}

let controller;
export function setup(api) {
  controller = createController(api);
}
export function activate() { controller?.activate(); }
export function deactivate() { controller?.deactivate(); }
export function dispose() { controller?.dispose(); }
export function available() { return controller?.available() || { ok: false, reason: '插件尚未初始化' }; }
export const hooks = { onMessage(ctx) { controller?.onMessage(ctx); } };
