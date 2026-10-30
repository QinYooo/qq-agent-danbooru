import { homedir } from 'node:os';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash, randomInt } from 'node:crypto';

export const DEFAULTS = Object.freeze({
  outputRoot: path.join(homedir(), 'Pictures', 'Danbooru'), requireMention: true, defaultCount: 3,
  maxCount: 9, cooldownSeconds: 8, batchGapSeconds: 0.5, singleImageMessages: true, lightweightDelivery: true, receiptPixelRetry: true,
  recentImageHours: 24,
  aliases: '莉音=rio_(blue_archive)\n莉央=rio_(blue_archive)',
  pythonPath: 'python'
});
const IMAGE = /\.(?:png|jpe?g|webp|gif|bmp|avif)$/i;
const COUNTS = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
const contained = (root, target) => {
  const rel = path.relative(root, target);
  return rel !== '' && !rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel);
};
export const normalize = value => String(value ?? '').normalize('NFKC').toLowerCase()
  .replace(/[\s_·・.\-']/gu, '').replace(/[（]/g, '(').replace(/[）]/g, ')');

function decodeCQ(text) {
  return String(text).replace(/&#91;/g, '[').replace(/&#93;/g, ']').replace(/&#44;/g, ',').replace(/&amp;/g, '&');
}

export function parseCommand(event, fallbackSelfId, cfg = DEFAULTS) {
  if (!event || event.post_type !== 'message' || event.message_type !== 'group') return null;
  const self = String(event.self_id ?? fallbackSelfId ?? '');
  const sender = String(event.user_id ?? event.sender?.user_id ?? '');
  if (!/^\d+$/.test(self) || sender === self || !/^\d+$/.test(sender)) return null;
  let text = '', mentioned = false, otherMention = false;
  if (Array.isArray(event.message)) {
    for (const seg of event.message) {
      if (seg.type === 'text') text += String(seg.data?.text || '');
      else if (seg.type === 'at') {
        if (String(seg.data?.qq) === self) mentioned = true;
        else otherMention = true;
      } else if (seg.type !== 'reply') return null;
    }
  } else {
    const raw = String(event.raw_message ?? event.message ?? '');
    text = raw.replace(/\[CQ:([^,\]]+)([^\]]*)\]/g, (_, type, params) => {
      if (type === 'at') {
        const qq = /(?:^|,)qq=([^,]*)/.exec(params)?.[1];
        if (qq === self) mentioned = true; else otherMention = true;
      } else if (type !== 'reply') otherMention = true;
      return '';
    });
    text = decodeCQ(text);
  }
  if (otherMention || (cfg.requireMention !== false && !mentioned)) return null;
  text = text.normalize('NFKC').trim().replace(/^[\s:：,，]+/, '').replace(/[。.!！?？~～\s]+$/, '');
  const database = /^查看数据库(?:\s*(.{0,120}))?$/.exec(text);
  if (database) return { query: database[1]?.trim() || '', overview: true };
  // Only a complete request is actionable. Quoted, forwarded and conversational
  // mentions cannot accidentally send the library to a group.
  const match = /^(?:请|麻烦)?(?:帮我|给我)?(?:随机)?(?:来|发|发来|给)(?:我)?\s*(几|[1-9]\d?|[一二两三四五六七八九十])\s*张\s*(.{1,100}?)$/.exec(text);
  if (!match) return null;
  const wanted = match[1] === '几' ? Number(cfg.defaultCount) || 3 : COUNTS[match[1]] || Number(match[1]);
  const maximum = Math.max(1, Math.min(9, Number(cfg.maxCount) || 9));
  const count = Math.max(1, Math.min(maximum, wanted));
  if (/^(?:随机)?(?:图片|照片|插画|图)$/.test(match[2].trim())) return { query: '全库随机', count, all: true };
  let query = match[2].trim().replace(/(?:的)?(?:图片|照片|插画|图)$/u, '').trim();
  if (!query || /[\r\n\[\]<>\\/]/.test(query) || query.includes('..')) return null;
  return { query, count };
}

export function buildIndex(data) {
  const records = data.characters || {}, aliases = new Map(), titles = new Map();
  const add = (value, tag) => {
    const key = normalize(value);
    if (!key || key.length > 120) return;
    if (!aliases.has(key)) aliases.set(key, new Set());
    aliases.get(key).add(tag);
  };
  for (const [tag, row] of Object.entries(records)) {
    titles.set(tag, String(row.name || tag));
    add(tag, tag);
    add(tag.replace(/_\([^)]*\)/g, ''), tag);
    for (const name of row.aliases || []) add(name, tag);
  }
  return { records, aliases, titles, add };
}

function customAliases(value) {
  const result = new Map();
  const source = String(value || '').replace(/＝/g, '=').replace(/^\s*#.*$/gm, '');
  for (const pair of source.matchAll(/([^=\s;；,，]+)\s*=\s*([^\s=;；,，]+)/gu)) {
    const name = normalize(pair[1]), tag = pair[2];
    if (name && tag && !/[\\/\x00-\x1f]/.test(tag) && tag !== '..' && tag.length <= 150) {
      if (!result.has(name)) result.set(name, new Set());
      result.get(name).add(tag);
    }
  }
  return result;
}

// Same Windows-safe naming rule as the archive, including long-tag hash suffixes.
export function folderName(tag) {
  let clean = tag.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[ .]+$/, '');
  if (/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(clean)) clean = '_' + clean;
  if (!clean || clean.length > 90 || clean !== tag) clean = (clean.slice(0, 80) || 'character') + '_' + createHash('sha256').update(tag).digest('hex').slice(0, 8);
  return clean;
}

export async function listCharacters(root, { includeUnknown = false } = {}) {
  const absolute = path.resolve(String(root));
  const rootStat = await fs.lstat(absolute);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error('图库目录不可用或是链接目录');
  const rootReal = await fs.realpath(absolute), folders = new Map();
  for (const category of ['SFW', 'NSFW']) {
    const parent = path.join(absolute, category);
    let stat;
    try { stat = await fs.lstat(parent); } catch (e) { if (e.code === 'ENOENT') continue; throw e; }
    if (!stat.isDirectory() || stat.isSymbolicLink()) continue;
    const parentReal = await fs.realpath(parent);
    if (!contained(rootReal, parentReal)) continue;
    const entries = await fs.readdir(parent, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.isSymbolicLink() || (entry.name.startsWith('_') && !(includeUnknown && entry.name === '_unknown_character'))) continue;
      const file = path.join(parent, entry.name);
      const real = await fs.realpath(file).catch(() => '');
      if (!real || !contained(parentReal, real)) continue;
      if (!folders.has(entry.name)) folders.set(entry.name, []);
      folders.get(entry.name).push({ dir: file, real, rootReal });
    }
  }
  return folders;
}

export function resolveCharacter(query, index, folders, overrideText = '') {
  const key = normalize(query), overrides = customAliases(overrideText);
  // A full folder/tag always wins over nickname matching, including tags absent
  // from the downloaded dictionary. Never synthesize a path from the query.
  if (folders.has(query)) return { tags: [query], name: index.titles.get(query) || query };
  let candidates = overrides.get(key) || index.aliases.get(key);
  if (!candidates) {
    const direct = [...folders.keys()].filter(tag => normalize(tag) === key);
    if (!direct.length) return { error: 'unknown', query };
    candidates = new Set(direct);
  }
  const tags = [...candidates];
  if (tags.length > 1) return { error: 'ambiguous', query, candidates: tags.slice(0, 8) };
  const tag = tags[0];
  return { tags: [tag], name: index.titles.get(tag) || query };
}

export async function collectImages(folders, tags) {
  const result = new Map();
  for (const tag of tags) {
    for (const folder of folders.get(folderName(tag)) || []) {
      for (const entry of await fs.readdir(folder.dir, { withFileTypes: true })) {
        if (!entry.isFile() || entry.isSymbolicLink() || !IMAGE.test(entry.name)) continue;
        const file = path.join(folder.dir, entry.name);
        const stat = await fs.lstat(file).catch(() => null);
        if (!stat?.isFile() || stat.isSymbolicLink() || stat.size <= 0 || stat.size > 25 * 1024 * 1024) continue;
        const real = await fs.realpath(file).catch(() => '');
        if (!real || !contained(folder.real, real) || !contained(folder.rootReal, real)) continue;
        // The archive filename is its SHA256, even for a restored file. Copies
        // across rating folders therefore cannot appear twice in one response.
        const key = /^([a-f\d]{64})(?:[_.]|$)/i.exec(path.parse(entry.name).name)?.[1]?.toLowerCase() || real;
        if (!result.has(key)) result.set(key, { key, file: real });
      }
    }
  }
  return [...result.values()];
}

export function selectRandom(items, count, recent = [], rng = randomInt) {
  const old = new Set(recent), fresh = items.filter(x => !old.has(x.key)), previous = items.filter(x => old.has(x.key));
  const shuffle = list => {
    const copy = [...list];
    for (let i = copy.length - 1; i > 0; i--) {
      const j = rng(i + 1); [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  };
  return [...shuffle(fresh), ...shuffle(previous)].slice(0, count);
}

export function createSentImageHistory(storage, {now = Date.now, warn = () => {}} = {}) {
  const entries = new Map();
  try {
    for (const [key, time] of Object.entries(storage?.get?.('recentGroupImages', {}) || {})) {
      if (/^group:\d+\|[a-f0-9]{64}$/.test(key) && Number.isFinite(time) && time >= 0) entries.set(key,time);
    }
  } catch { /* Optional history must not prevent plugin loading. */ }
  const identity = key => /^[a-f0-9]{64}$/i.test(String(key)) ? String(key).toLowerCase()
    : createHash('sha256').update(String(key)).digest('hex');
  function prune(windowMs) {
    const cutoff = now() - windowMs;
    for (const [key,time] of entries) if (time <= cutoff) entries.delete(key);
    if (entries.size > 6000) {
      const oldest = [...entries].sort((a,b)=>a[1]-b[1]);
      for (let i=0;i<oldest.length-6000;i++) entries.delete(oldest[i][0]);
    }
  }
  return {
    keysFor(chatKey, items, windowMs) {
      if (windowMs <= 0) return [];
      prune(windowMs);
      return items.filter(item=>entries.has(chatKey+'|'+identity(item.key))).map(item=>item.key);
    },
    remember(chatKey, items, windowMs) {
      if (windowMs <= 0 || !items.length) return;
      const at = now();
      for (const item of items) entries.set(chatKey+'|'+identity(item.key),at);
      prune(windowMs);
      try { storage?.set('recentGroupImages',Object.fromEntries(entries)); }
      catch(error) { warn('本群近期发图记录保存失败',String(error.message)); }
    }
  };
}

async function splitImageBatches(items, cfg) {
  const mib = 1024 * 1024, batches = [];
  const maxImages = cfg.singleImageMessages !== false ? 1 : 3;
  let current = [], bytes = 0;
  const flush = () => { if (current.length) batches.push(current); current = []; bytes = 0; };
  for (const item of items) {
    const stat = await fs.stat(item.file);
    if (!stat.isFile() || stat.size <= 0 || stat.size > 25 * mib) throw new Error('图片文件已变化或超过发送大小限制');
    // Large originals go alone; smaller images stay below both batch ceilings.
    const sized = { ...item, bytes: stat.size };
    if (stat.size >= 4 * mib) { flush(); batches.push([sized]); continue; }
    if (current.length >= maxImages || bytes + stat.size > 6 * mib) flush();
    current.push(sized); bytes += stat.size;
  }
  flush();
  return batches;
}

function pauseBetweenBatches(ms, signal) {
  return new Promise(resolve => {
    if (signal.aborted) return resolve();
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, ms);
    signal.addEventListener('abort', done, { once: true });
  });
}

async function imageSegments(items, inline) {
  const images = [];
  for (const item of items) {
    let reference = pathToFileURL(item.file).href;
    if (inline && item.bytes <= 15 * 1024**2) {
      const bytes = await fs.readFile(item.file);
      if (bytes.length !== item.bytes) throw new Error('图片文件在发送前发生变化');
      reference = 'base64://' + bytes.toString('base64');
    }
    images.push({type:'image',data:{file:reference}});
  }
  return images;
}

export function buildDatabaseForwardNodes(parts, ctx) {
  const selfId = String(ctx.event?.self_id ?? ctx.onebot?.selfId ?? '');
  if (!/^[1-9]\d*$/.test(selfId)) throw new Error('无法确定机器人自己的QQ号，暂时不能发送合并转发');
  const name = String(ctx.onebot?.selfNickname || '图库账本').slice(0, 80);
  const time = Math.floor(Date.now() / 1000);
  // SnowLuma recognizes an exclusive array of node segments as a merged
  // forward in send_group_msg, so the normal SendQueue remains in charge.
  return parts.map(text => ({ type: 'node', data: {
    uin: Number(selfId), name, time,
    title: '图库数据库完整明细', source: '图库账本', summary: `完整明细，共${parts.length}段`,
    content: [{ type: 'text', data: { text: String(text) } }]
  } }));
}

export function createController(api, index, options = {}) {
  const config = () => ({ ...DEFAULTS, ...(api.config() || {}) });
  const now = options.now || Date.now;
  let active = false, epoch = 0;
  let abortController = new AbortController();
  const queue = [], seen = new Map(), cooldowns = new Map();
  const running = new Map();
  const maxActiveGroups = 2;
  const sendBudgetMs = options.sendBudgetMs ?? 120000;
  const clock = options.monotonicNow || (() => performance.now());
  const sentHistory = createSentImageHistory(api.storage,{now,warn:(...args)=>api.warn?.(...args)});
  const deliverySkips = new Map();
  let results = [];
  try { results = api.storage?.get?.('recentResults', []) || []; } catch { /* optional diagnostics */ }
  if (!Array.isArray(results)) results = [];
  results = results.slice(-20);
  try {
    for (const [key, entry] of Object.entries(api.storage?.get?.('deliverySkips', {}) || {})) {
      if (entry && Number(entry.until) > now()) deliverySkips.set(key, entry);
    }
  } catch { /* missing or old optional state must not prevent loading */ }
  function temporarilySkip(chatKey, item) {
    const key = chatKey + '|' + item.key;
    deliverySkips.delete(key);
    deliverySkips.set(key, { until: now() + 24 * 60 * 60 * 1000, reason: 'single_image_delivery_unconfirmed' });
    for (const [oldKey, entry] of deliverySkips) if (Number(entry.until) <= now()) deliverySkips.delete(oldKey);
    while (deliverySkips.size > 256) deliverySkips.delete(deliverySkips.keys().next().value);
    try { api.storage?.set('deliverySkips', Object.fromEntries(deliverySkips)); }
    catch (error) { api.warn?.(`发送避让记录保存失败：${error.message}`); }
  }
  function snapshot(value) {
    try {
      const result = { ...value, at: new Date(now()).toISOString() };
      api.storage?.set('lastResult', result);
      if (!['retrieving', 'uploading', 'sending', 'sending_with_skips', 'preparing_receipt_retry', 'uploading_receipt_retry'].includes(value.status)) {
        results.push(result); results = results.slice(-20);
        api.storage?.set('recentResults', results);
      }
    }
    catch (e) { api.warn?.('点图状态记录失败', String(e.message)); }
  }
  async function process(ctx, command, ownEpoch) {
    const stillActive = () => active && ownEpoch === epoch;
    const cfg = config();
    snapshot({ status: 'retrieving', query: command.query, chatKey: ctx.chatKey });
    if (command.overview) {
      let text;
      try {
        text = await options.overview(command.query, cfg, { signal: abortController.signal });
      } catch (error) {
        if (stillActive()) await ctx.send('这次没读到图库账本，稍后再试一下吧。');
        throw error;
      }
      if (Array.isArray(text)) {
        if (!stillActive()) return;
        try {
          const nodes = buildDatabaseForwardNodes(text, ctx);
          await ctx.sender.sendMedia(ctx.chatKey, nodes, {
            label: `[图库数据库完整明细 · ${text.length}段]`,
            dedupeKey: `database-forward:${ctx.chatKey}:${ctx.event?.message_id ?? Date.now()}`
          });
        } catch (error) {
          if (stillActive()) await ctx.send('数据库明细已经整理好，但合并转发这次没能确认发出。先不拆开发了，免得刷屏；可以稍后重试，或用普通分页查看。', { dedupe: false });
          throw error;
        }
      } else {
        if (!stillActive()) return;
        const result = await ctx.send(text, { dedupe: false });
        if (result?.failed?.length) throw new Error('数据库明细部分内容未送达，停止继续输出');
      }
      snapshot({ status: 'database_overview', query: command.query });
      return;
    }
    let resolved, items, criteria, retrievalMetrics;
    if (command.all && options.listAll) {
      items = await options.listAll(cfg, { signal: abortController.signal });
      resolved = { tags: [], name: '全库随机' };
    } else if (!command.all && options.retrieve) {
      let result;
      try { result = await options.retrieve(command.query, cfg, { signal: abortController.signal }); }
      catch (error) {
        if (stillActive()) await ctx.send('这次图库检索没成功，稍后再叫我找一下吧。');
        throw error;
      }
      if (!stillActive()) return;
      if (result.message) {
        await ctx.send(result.message);
        snapshot({ status: 'query_feedback', query: command.query, message: result.message }); return;
      }
      resolved = { tags: result.tags, name: result.name };
      items = result.items; criteria = result.criteria; retrievalMetrics = result.metrics;
      api.log?.('图库检索耗时 '+JSON.stringify(retrievalMetrics || {}));
    } else {
      let folders;
      try { folders = await listCharacters(cfg.outputRoot, { includeUnknown: command.all === true }); }
      catch (e) {
        if (stillActive()) await ctx.send('图库目录暂时无法读取，请检查“角色图库点图”的图库目录设置。');
        throw e;
      }
      resolved = command.all
        ? { tags: [...folders.keys()], name: '全库随机' }
        : resolveCharacter(command.query, index, folders, cfg.aliases);
      if (!resolved.error) items = await collectImages(folders, resolved.tags);
    }
    if (!stillActive()) return;
    if (resolved.error) {
      const text = resolved.error === 'ambiguous'
        ? `“${command.query}”有重名，请使用完整角色标签：\n${resolved.candidates.join('\n')}`
        : `没有找到“${command.query}”的角色映射。可直接使用完整 Danbooru 角色标签，或在“角色图库点图”设置中补充中文别名。`;
      await ctx.send(text);
      snapshot({ status: resolved.error, query: command.query }); return;
    }
    if (!items.length) {
      await ctx.send(command.all ? '图库暂时没有可发送的已保存图片。' : `图库暂时没有“${resolved.name}”的已保存图片（${resolved.tags.join('、')}）。`);
      snapshot({ status: 'empty', query: command.query, tags: resolved.tags }); return;
    }
    const availableBeforeSkips = items.length;
    items = items.filter(item => Number(deliverySkips.get(ctx.chatKey + '|' + item.key)?.until || 0) <= now());
    if (!items.length) {
      await ctx.send('找到了符合条件的图，不过这些图最近在本群发送时没拿到成功确认，我先暂时避开，免得反复发不出去。24小时后会自动恢复。');
      snapshot({ status: 'delivery_skipped', query: command.query, availableBeforeSkips }); return;
    }
    const configuredHours = Number(cfg.recentImageHours);
    const historyWindowMs = Math.max(0,Math.min(168,Number.isFinite(configuredHours)?configuredHours:24))*3600000;
    let selected = selectRandom(items, command.count, sentHistory.keysFor(ctx.chatKey,items,historyWindowMs));
    if (options.validate) selected = await options.validate(selected, cfg);
    if (!selected.length) { if (stillActive()) await ctx.send('选中的图片文件暂时找不到了，稍后再试一下吧。'); return; }
    if (!stillActive()) return;
    const label = `[图库点图：${resolved.name}，${selected.length}张]`;
    const lines = ['来了，接图～', `喏，给你来${selected.length}张。`, `这${selected.length}张给你，收好～`, '图来啦～'];
    const caption = items.length < command.count
      ? `暂时只有${selected.length}张，先给你这些～`
      : lines[randomInt(lines.length)];
    const batches = await splitImageBatches(selected, cfg);
    if (typeof ctx.sender?.sendMedia !== 'function') throw new Error('当前 QQ Agent 缺少统一多图发送接口');
    const delivered = [];
    const unconfirmed = [];
    const uploadFailed = [];
    const receiptPixelRetries = [];
    let stoppedReason = '', consecutiveFailures = 0;
    const failures = [];
    const sendingStarted = clock();
    let originalBytesTotal = 0, uploadBytesTotal = 0, uploadRetries = 0;
    for (let i = 0; i < batches.length; i++) {
      // Check the budget only between completed sends. A Promise.race here
      // would leave QQ uploading in the background and risk duplicate sends.
      if (i > 0 && clock() - sendingStarted >= sendBudgetMs) { stoppedReason = 'time_budget'; break; }
      if (i > 0) await (options.pause || pauseBetweenBatches)(Math.max(0.5, Math.min(30, Number(cfg.batchGapSeconds) || 0.5)) * 1000, abortController.signal);
      if (!stillActive()) return;
      const batch = batches[i];
      let sending = batch;
      if (cfg.lightweightDelivery !== false && options.prepare && batch.some(item => item.bytes > 2 * 1024**2)) {
        try { sending = await options.prepare(batch, cfg, { signal: abortController.signal }); }
        catch (error) {
          if (!stillActive()) return;
          snapshot({status:'preparation_error',query:command.query,count:delivered.length,error:String(error.message)});
          await ctx.send('这张大图的本地传输副本没准备好，这次先停下了；归档原图还在图库里。'); return;
        }
      }
      if (!stillActive()) return;
      const batchBytes = sending.reduce((sum, item) => sum + item.bytes, 0);
      originalBytesTotal += batch.reduce((sum,item)=>sum+item.bytes,0); uploadBytesTotal += batchBytes;
      api.log?.(`图库准备发送第${i + 1}/${batches.length}批：${batch.length}张，${(batchBytes / 1024**2).toFixed(2)}MiB`);
      // Inline originals prevent the host from retrying a path on any error.
      const images = await imageSegments(sending,cfg.singleImageMessages !== false);
      const segments = [
        ...(i === 0 ? [{ type: 'text', data: { text: caption + (batches.length > 1 ? ` 我分${batches.length}次发～` : '') + '\n' } }] : []),
        ...images
      ];
      if (!stillActive()) return;
      const sendStarted = performance.now();
      snapshot({ status: 'uploading', query: command.query, chatKey: ctx.chatKey, count: delivered.length,
        selected: selected.length, batch: i + 1, batches: batches.length, batchBytes, retrievalMetrics });
      try {
        const send = (outgoing = segments, suffix = '') => ctx.sender.sendMedia(ctx.chatKey, outgoing, {
            label: batches.length > 1 ? `${label}（第${i + 1}/${batches.length}批）` : label,
            dedupeKey: 'gallery:' + batch.map(x => x.key).sort().join('|') + suffix });
        const originalSend = async () => {
          try { await send(); }
          catch (error) {
            // A definite upload failure is still safe to retry as before.
            if (!/highway upload transport failed/i.test(String(error.message))) throw error;
            if (clock() - sendingStarted >= sendBudgetMs) throw error;
            await (options.pause || pauseBetweenBatches)(1000, abortController.signal);
            if (!stillActive()) return;
            uploadRetries++; await send();
          }
        };
        try { await originalSend(); }
        catch (originalError) {
          const noReceipt = /no message receipt|delivery was not confirmed|sequence\s*=\s*null/i.test(String(originalError.message));
          if (!noReceipt || batch.length !== 1 || cfg.receiptPixelRetry === false || !options.prepareReceiptRetry
            || clock() - sendingStarted >= sendBudgetMs || !stillActive()) throw originalError;
          const retry = {key:batch[0].key,status:'preparing',originalError:String(originalError.message).slice(0,300)};
          receiptPixelRetries.push(retry);
          try {
            snapshot({status:'preparing_receipt_retry',query:command.query,chatKey:ctx.chatKey,batch:i+1,count:delivered.length});
            const variant = await options.prepareReceiptRetry(sending[0],cfg,{signal:abortController.signal});
            const outgoing = await imageSegments([variant],true);
            await (options.pause || pauseBetweenBatches)(Math.max(0.5,Math.min(30,Number(cfg.batchGapSeconds)||0.5))*1000,abortController.signal);
            if (!stillActive()) return;
            snapshot({status:'uploading_receipt_retry',query:command.query,chatKey:ctx.chatKey,batch:i+1,count:delivered.length});
            uploadBytesTotal += variant.bytes;
            retry.status='sending';
            // Exactly one extra submission. Its errors cannot re-enter this
            // branch or receive the original upload-transport retry.
            await send(outgoing,':tl25-minus10-v1');
            retry.status='sent';retry.bytes=variant.bytes;
          } catch (retryError) {
            retry.status='failed';retry.error=String(retryError.message).slice(0,300);
            throw new Error(String(originalError.message)+'；25像素补发失败：'+retry.error);
          }
        }
      } catch (error) {
        if (!stillActive()) return;
        failures.push({batch:i+1,elapsedMs:Math.round(performance.now()-sendStarted),bytes:batchBytes,
          error:String(error.message).slice(0,300)});
        api.warn?.(`图库发送失败 ${JSON.stringify({chatKey:ctx.chatKey,batch:i+1,
          elapsedMs:Math.round(performance.now()-sendStarted),bytes:batchBytes,error:String(error.message).slice(0,300)})}`);
        if (batch.length === 1 && /no message receipt|delivery was not confirmed|sequence\s*=\s*null/i.test(String(error.message))) {
          unconfirmed.push(batch[0]);
          temporarilySkip(ctx.chatKey, batch[0]);
          snapshot({ status: 'sending_with_skips', query: command.query, criteria, count: delivered.length,
            unconfirmedKeys: unconfirmed.map(item => item.key), selected: selected.length, batch: i + 1, batches: batches.length });
          api.warn?.(`单张图片QQ未确认送达，本群暂避24小时并继续其他图片：${batch[0].key}`);
          if (++consecutiveFailures >= 2) { stoppedReason = 'consecutive_failures'; break; }
          continue;
        }
        if (/highway upload transport failed/i.test(String(error.message))) {
          // The protocol failed before submitting this message. Do not
          // quarantine its image as a content/receipt problem. Move on only
          // after the bounded retry; stop if the connection keeps failing.
          uploadFailed.push(...batch);
          if (++consecutiveFailures >= 2) { stoppedReason = 'consecutive_failures'; break; }
          continue;
        }
        snapshot({ status: 'delivery_unconfirmed', query: command.query, chatKey:ctx.chatKey, criteria, count: delivered.length,
          selected: selected.length, batch: i + 1, batches: batches.length, batchBytes,
          unconfirmedKeys: batch.map(item => item.key), error: String(error.message).slice(0, 300),failures,
          retrievalMetrics,sendTotalMs:Math.round(clock()-sendingStarted) });
        api.warn?.(`图库第${i + 1}/${batches.length}批未确认送达，停止后续批次：${error.message}`);
        // Receipt failures can still have delivered the batch. Do not repeat
        // confirmed batches or start the remaining batches after an error.
        try { await ctx.send(`这批图没收到发送成功的确认，我先停下了。${delivered.length ? `前面已确认发出${delivered.length}张。` : ''}你先看看群里有没有收到，免得重复。`, { dedupe: false }); }
        catch (noticeError) { api.warn?.(`发图异常提示未送达：${noticeError.message}`); }
        return;
      }
      delivered.push(...batch);
      sentHistory.remember(ctx.chatKey,batch,historyWindowMs);
      consecutiveFailures = 0;
      if (!stillActive()) return;
      snapshot({ status: 'sending', query: command.query,
        tags: command.all ? [] : resolved.tags, criteria, count: delivered.length, available: items.length,
        batch: i + 1, batches: batches.length, retrievalMetrics, sendMs: Math.round(performance.now()-sendStarted),
        originalBytes: batch.reduce((sum,item)=>sum+item.bytes,0), uploadBytes: batchBytes });
    }
    const notAttempted = selected.length - delivered.length - unconfirmed.length - uploadFailed.length;
    snapshot({ status: unconfirmed.length || uploadFailed.length || notAttempted ? 'partial' : 'sent', query: command.query, chatKey:ctx.chatKey, criteria,
      tags: command.all ? [] : resolved.tags, available: items.length,
      count: delivered.length, selected: selected.length, unconfirmedKeys: unconfirmed.map(item => item.key),
      retrievalMetrics, originalBytesTotal, uploadBytesTotal, uploadRetries,
      uploadFailedKeys: uploadFailed.map(item => item.key), notAttempted, stoppedReason, failures, receiptPixelRetries,
      sendTotalMs: Math.round(clock() - sendingStarted) });
    if ((unconfirmed.length || uploadFailed.length || notAttempted) && stillActive()) {
      const details = [`已确认发出${delivered.length}张。`];
      if (unconfirmed.length) details.push(`另外${unconfirmed.length}张QQ没确认送达，已跳过并在本群暂避24小时。`);
      if (uploadFailed.length) details.push(`${uploadFailed.length}张遇到上传连接故障，先跳过了。`);
      if (notAttempted) details.push(`${stoppedReason === 'time_budget' ? '这次发送等得太久' : '连续发送失败'}，剩余${notAttempted}张先不发了，免得一直堵着。`);
      await ctx.send(details.join(''), { dedupe: false });
    }
    api.log?.(`图库发送结束：确认${delivered.length}张，未确认${unconfirmed.length}张，上传失败${uploadFailed.length}张，未尝试${notAttempted}张`);
  }
  function drain() {
    while (active && running.size < maxActiveGroups) {
      const position = queue.findIndex(item => !running.has(item.ctx.chatKey));
      if (position < 0) return;
      const { ctx, command, ownEpoch, enqueuedAt } = queue.splice(position, 1)[0];
      if (ownEpoch !== epoch) continue;
      const task = (async () => {
        try {
          // Do not deliver a stale request several minutes after the user
          // asked. Notices also retain the host's per-chat send queue.
          if (clock() - enqueuedAt > 120000) {
            snapshot({status:'queue_expired',query:command.query,chatKey:ctx.chatKey});
            await ctx.send('刚才那次点图排队太久，已经取消了；还想看的话再叫我一声。', { dedupe:false });
          } else await process(ctx, command, ownEpoch);
        }
        catch (e) {
          api.warn?.('角色图库点图失败', String(e.message));
          if (active && ownEpoch === epoch) snapshot({ status: 'error', query: command.query, chatKey:ctx.chatKey, error: String(e.message).slice(0, 300) });
          // A timeout can mean the upload succeeded. Do not send a misleading
          // second response or automatically resend a batch.
        }
      })();
      running.set(ctx.chatKey, task);
      void task.finally(() => { running.delete(ctx.chatKey); drain(); });
    }
  }
  return {
    start() { if (!active) { active = true; epoch++; abortController = new AbortController(); } },
    stop() {
      if (active && (running.size || queue.length)) snapshot({status:'cancelled',
        reason:'plugin_deactivated_or_reloaded',chatKeys:[...running.keys()],queued:queue.length});
      active = false; epoch++; abortController.abort(); queue.length = 0; seen.clear(); cooldowns.clear();
    },
    // The host consults route providers immediately after onMessage. Claim at
    // enqueue time so the asynchronous QQ upload cannot wake the model first.
    handled({ chatKey, mid } = {}) {
      const at = seen.get(String(chatKey) + '|' + String(mid));
      return active && mid != null && at !== undefined && now() - at <= 120000;
    },
    handle(ctx) {
      if (!active) return false;
      const event = ctx.event, cfg = config();
      const command = parseCommand(event, ctx.onebot?.selfId, cfg);
      if (!command || ctx.chatKey !== `group:${event.group_id}`) return false;
      const t = now(), key = ctx.chatKey + '|' + String(event.message_id ?? '');
      if (event.message_id != null && seen.has(key)) return true;
      if (event.message_id != null) seen.set(key, t);
      for (const [k, at] of seen) if (t - at > 120000) seen.delete(k);
      while (seen.size > 1000) seen.delete(seen.keys().next().value);
      const userKey = ctx.chatKey + '|' + String(event.user_id ?? event.sender?.user_id) + (command.overview ? '|database' : '|images');
      const delay = command.overview ? 1000 : Math.max(0, Number(cfg.cooldownSeconds) || 0) * 1000;
      if (cooldowns.has(userKey) && t - cooldowns.get(userKey) < delay) return true;
      if (queue.length >= 10) { api.warn?.('点图队列已满，跳过本条指令'); return true; }
      cooldowns.delete(userKey); cooldowns.set(userKey, t);
      while (cooldowns.size > 1000) cooldowns.delete(cooldowns.keys().next().value);
      queue.push({ ctx, command, ownEpoch: epoch, enqueuedAt: clock() });
      // Never block the 5-second hook on disk scans or QQ's 90-second upload.
      void drain(); return true;
    },
    async idle({ timeoutMs = 30000 } = {}) {
      const deadline = performance.now() + timeoutMs;
      while (running.size || queue.length) {
        if (performance.now() >= deadline) throw new Error('图库等待空闲超时');
        await new Promise(resolve => setTimeout(resolve, 5));
      }
    }
  };
}

export async function loadCharacters() {
  const file = fileURLToPath(new URL('./characters.json', import.meta.url));
  return buildIndex(JSON.parse(await fs.readFile(file, 'utf8').catch(error => { if(error.code==='ENOENT') return fs.readFile(new URL('./characters.seed.json', import.meta.url),'utf8'); throw error; })));
}
