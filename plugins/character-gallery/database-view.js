import { readDatabase } from './retrieval.js?v=1.4.3';

const CATEGORIES = { 角色: 'character', 标签: 'general', 作品: 'copyright', 来源: 'meta' };
const LABELS = { character: '角色', general: '画面标签', copyright: '作品', meta: '来源 / 规格', search: '搜索结果' };

export function parseDatabaseViewArgs(argument) {
  const raw = String(argument || '').trim();
  const all = /(?:^|\s)-全部(?=\s|$)/u.test(raw);
  const input = raw.replace(/(?:^|\s)-全部(?=\s|$)/gu, ' ').trim();
  const category = /^(角色|标签|作品|来源)\s*(\d{1,5})?$/.exec(input);
  const search = /^搜索\s*(.+?)(?:\s+第?(\d{1,5})页)?$/.exec(input);
  const request = { mode: 'overview', view: 'summary', page: 1, all };
  if (category) { request.view = CATEGORIES[category[1]]; request.page = Number(category[2] || 1); }
  else if (input) { request.view = 'search'; request.query = search ? search[1] : input; request.page = Number(search?.[2] || 1); }
  return { request, category };
}

export function splitDatabaseReply(text, limit = 1400) {
  const parts = [];
  let current = '';
  for (let line of text.split('\n')) {
    if (current && current.length + 1 + line.length > limit) { parts.push(current); current = ''; }
    while (line.length > limit) { parts.push(line.slice(0, limit)); line = line.slice(limit); }
    current += (current ? '\n' : '') + line;
  }
  if (current) parts.push(current);
  return parts.length > 1 ? parts.map((part, i) => `【数据库 · 第${i + 1}/${parts.length}段】\n${part}`) : parts;
}

export function formatAllDatabaseRows(report, request) {
  const line = row => `${row.zh}：${row.count}张（SFW ${row.sfw} / NSFW ${row.nsfw}）`;
  const parts = [];
  if (request.view === 'summary') {
    parts.push(`图库全部明细（${report.asOf}）`, `共${report.total}张 · ${report.mib} MiB`,
      `SFW ${report.sfw}张 / NSFW ${report.nsfw}张 · 角色未识别${report.unknown}张`);
    for (const kind of ['character', 'general', 'copyright', 'meta']) {
      const rows = report.rows.filter(row => row.kind === kind);
      parts.push('', `【${LABELS[kind]} · 全部${rows.length}个】`, ...rows.map(line));
    }
  } else {
    parts.push(`${LABELS[request.view]}${request.query ? `「${request.query}」` : ''} · 全部${report.matches}个（${report.asOf}）`,
      ...report.rows.map(line));
  }
  parts.push('', `已输出全部${report.matches}个标签。一张图的多个标签会分别计数。`);
  return splitDatabaseReply(parts.join('\n'));
}

export function createDatabaseView() {
  return async (argument, cfg, { signal } = {}) => {
    const { request, category } = parseDatabaseViewArgs(argument);
    const report = await readDatabase(cfg, signal, request);
    if (request.all) return formatAllDatabaseRows(report, request);
    if (request.view === 'summary') {
      const parts = [
        `图库账本来啦（${report.asOf}）`,
        `共 ${report.total} 张 · ${report.mib} MiB`,
        `SFW ${report.sfw} 张 / NSFW ${report.nsfw} 张`,
        `角色未识别 ${report.unknown} 张；文件缺失 ${report.missing} 张`,
        `角色标签 ${report.distinct.character} 个 / 画面标签 ${report.distinct.general} 个 / 作品标签 ${report.distinct.copyright} 个`,
        '', '【角色 · 图片数】', ...report.topCharacters.map(row => `${row.zh}：${row.count}`),
        '', '【常见画面标签 · 图片数】', ...report.topTags.map(row => `${row.zh}：${row.count}`),
        '', '继续看：查看数据库 角色 / 标签 / 作品',
        '翻页：查看数据库 角色 2',
        '搜索：查看数据库 搜索 丝袜',
        '全部输出：查看数据库 -全部 / 角色 -全部 / 标签 -全部',
        '一张图可有多个标签，各标签数量会重叠。'
      ];
      return parts.join('\n');
    }
    const heading = `${LABELS[request.view]}${request.query ? `「${request.query}」` : ''} · ${report.matches} 个 · 第 ${report.page}/${report.pages} 页`;
    const lines = report.rows.map(row => `${row.zh}：${row.count}张（SFW ${row.sfw} / NSFW ${row.nsfw}）`);
    const next = report.page < report.pages
      ? `下一页：查看数据库 ${category ? category[1] + ' ' + (report.page + 1) : '搜索 ' + request.query + ' 第' + (report.page + 1) + '页'}`
      : '已经到底啦。';
    return [heading, ...(lines.length ? lines : ['没有找到匹配的标签，试试中文简称或英文tag吧。']), '', next].join('\n');
  };
}
