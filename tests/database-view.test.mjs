import test from 'node:test';
import assert from 'node:assert/strict';
import { parseDatabaseViewArgs, formatAllDatabaseRows, splitDatabaseReply } from '../plugins/character-gallery/database-view.js';

test('three requested all forms are separated from search keywords', () => {
  for (const [input, view] of [['-全部', 'summary'], ['角色 -全部', 'character'], ['标签 -全部', 'general']]) {
    const { request } = parseDatabaseViewArgs(input);
    assert.equal(request.all, true); assert.equal(request.view, view); assert.equal(request.query, undefined);
  }
});
test('ordinary summary, pagination and search keep their original meanings', () => {
  assert.equal(parseDatabaseViewArgs('').request.all, false);
  assert.equal(parseDatabaseViewArgs('角色 2').request.page, 2);
  assert.equal(parseDatabaseViewArgs('搜索 丝袜').request.query, '丝袜');
  assert.equal(parseDatabaseViewArgs('搜索 丝袜 -全部').request.query, '丝袜');
});
test('long all output contains every row once with numbered bounded chunks', () => {
  const rows = Array.from({ length: 160 }, (_, i) => ({ zh: `测试标签${i}号`, count: i + 1, sfw: i, nsfw: 1, kind: 'general' }));
  const chunks = formatAllDatabaseRows({ rows, matches: rows.length, asOf: '10-29' }, { view: 'general' });
  assert.ok(chunks.length > 1); assert.ok(chunks.every(chunk => chunk.length < 1450));
  const all = chunks.join('\n');
  for (const row of rows) assert.equal(all.split(row.zh + '：').length - 1, 1);
  assert.match(all, /已输出全部160个标签/); assert.match(chunks[0], /第1\//);
});
test('all summary includes every category and keeps short messages unsplit', () => {
  const rows = ['character', 'general', 'copyright', 'meta'].map(kind => ({ zh: kind, kind, count: 1, sfw: 1, nsfw: 0 }));
  const chunks = formatAllDatabaseRows({ rows, matches: 4, total: 1, mib: 1, sfw: 1, nsfw: 0, unknown: 0, asOf: '10-29' }, { view: 'summary' });
  assert.equal(chunks.length, 1); for (const row of rows) assert.ok(chunks[0].includes(row.zh + '：'));
  assert.deepEqual(splitDatabaseReply('短消息'), ['短消息']);
});
