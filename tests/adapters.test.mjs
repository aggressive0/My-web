/**
 * 适配层单测（Phase A）。
 * 重点不是「算得对」，而是「拿不到数据时绝不编」。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

// front/package.json 声明了 "type": "module"，直接 require 这个 .js 只会得到
// 空的模块命名空间（Node 会按 ESM 解析）。而它在浏览器里是普通 script，
// 所以测试按浏览器的方式执行：读源码 + new Function 提供 module/window。
const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(
  path.join(here, '..', 'front', 'static_html', 'js', 'dashboard-adapters.js'), 'utf8');
const sandboxModule = { exports: {} };
new Function('module', 'exports', 'window', src)(sandboxModule, sandboxModule.exports, undefined);
const A = sandboxModule.exports;

let failed = 0;
function check(name, fn) {
  try { fn(); console.log('  ✓ ' + name); }
  catch (err) { failed++; console.log('  ✗ ' + name + ' — ' + err.message); }
}

const HOUR = 3600 * 1000;
const now = Date.now();
const iso = (offsetHours) => new Date(now - offsetHours * HOUR).toISOString();

console.log('CCU：');

check('无数据返回 unavailable 且 value 为 null', () => {
  const r = A.ccuSeries(null, 7);
  assert.equal(r.state, 'unavailable');
  assert.equal(r.value, null);
});

check('24h 变化率按最近点与 24h 前最近点计算', () => {
  const doc = {
    updated_at: iso(1),
    games: [{ appid: 1, name: 'A', color: '#fff', enabled: true,
              points: [{ ts: iso(30), p: 100 }, { ts: iso(24), p: 200 }, { ts: iso(1), p: 300 }] }]
  };
  const r = A.ccuSeries(doc, 7);
  assert.equal(r.state, 'ready');
  assert.equal(r.value[0].latest, 300);
  assert.equal(Math.round(r.value[0].changePct), 50);
});

check('基准点距 24h 超过 6h 时不计算变化率（数据断档）', () => {
  const doc = {
    games: [{ appid: 1, name: 'A', enabled: true,
              points: [{ ts: iso(60), p: 100 }, { ts: iso(1), p: 300 }] }]
  };
  const r = A.ccuSeries(doc, 7);
  assert.equal(r.value[0].changePct, null, '断档时不能硬算');
});

check('窗口外的点被过滤', () => {
  const doc = {
    games: [{ appid: 1, name: 'A', enabled: true,
              points: [{ ts: iso(24 * 20), p: 1 }, { ts: iso(1), p: 2 }] }]
  };
  const r = A.ccuSeries(doc, 7);
  assert.equal(r.value[0].points.length, 1);
});

console.log('\n风险指数：');

check('空文档返回 unavailable', () => {
  assert.equal(A.riskRanking({}).state, 'unavailable');
});

check('按指数降序，并记录可用分量', () => {
  const doc = {
    updated_at: iso(2),
    definition: { name: '测试指数', formula: 'f', caveat: 'c' },
    games: [
      { appid: 1, name: 'A', risk_index: 30, status: 'ok',
        components: { N: { status: 'unavailable' }, G: { status: 'ok' } } },
      { appid: 2, name: 'B', risk_index: 80, status: 'ok', components: {} }
    ]
  };
  const r = A.riskRanking(doc);
  assert.equal(r.value[0].appid, 2);
  assert.deepEqual(r.value[1].used, ['G']);
  assert.equal(r.caveat, 'c');
});

console.log('\n语言市场：');

check('缺少经纬度的点被丢弃', () => {
  const doc = { markets: [{ id: 'x', latitude: null, longitude: 1 }, { id: 'y', latitude: 1, longitude: 2 }] };
  const r = A.markets(doc);
  assert.equal(r.value.length, 1);
  assert.equal(r.value[0].id, 'y');
});

check('必须带「语言市场≠玩家所在地」的口径', () => {
  const r = A.markets({ markets: [{ id: 'y', latitude: 1, longitude: 2 }] });
  assert.match(r.caveat, /不代表|不是/);
});

console.log('\n情感：');

check('无样本时 unavailable，不用 0 顶替', () => {
  const r = A.sentimentTotals({ games: [{ appid: 1, totals: { n: 0 } }] }, 1);
  assert.equal(r.state, 'unavailable');
});

check('指定 appid 时只聚合该游戏，并标注单款', () => {
  const doc = { updated_at: iso(3), games: [
    { appid: 1, totals: { n: 10, pos: 8, neg: 1, neu: 1 }, coverage: { days: 3 } },
    { appid: 2, totals: { n: 90, pos: 10, neg: 80, neu: 0 }, coverage: { days: 5 } }
  ] };
  const one = A.sentimentTotals(doc, 1);
  assert.equal(one.value.n, 10);
  assert.equal(one.value.posRate, 80);
  assert.match(one.note, /单款/);
  const all = A.sentimentTotals(doc, null);
  assert.equal(all.value.n, 100);
  assert.match(all.note, /聚合/);
});

console.log('\n成就与采集状态：');

check('成就口径必须声明不是留存率', () => {
  const r = A.funnels({ games: [{ appid: 1, achievements: [] }] }, 1);
  assert.match(r.caveat, /不是.*留存/);
});

check('采集状态区分 fresh / stale / expired', () => {
  const mk = (h) => A.freshness({ updated_at: new Date(now - h * HOUR).toISOString(), games: [] }, now);
  assert.equal(mk(2).value.state, 'fresh');
  assert.equal(mk(24 * 3).value.state, 'stale');
  assert.equal(mk(24 * 20).value.state, 'expired');
});

check('采集状态无时间戳时 unavailable', () => {
  assert.equal(A.freshness({ games: [] }).state, 'unavailable');
});

console.log(failed === 0 ? '\n全部通过' : `\n失败 ${failed} 项`);
process.exit(failed === 0 ? 0 : 1);
