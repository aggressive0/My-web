/**
 * 无浏览器条件下的 DOM 冒烟：桩化 document/window/fetch，执行 solution.html 的内联脚本，
 * 断言「游戏下拉被填充」「最近一次策略自动载入」「点击生成后方案卡被真实数据覆盖」。
 * 注：历史策略表与证据链模块已按需求移除，相关断言同步删除。
 */
import fs from 'node:fs';

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PAGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)),
                          '..', 'front', 'static_html', 'solution.html');
const html = fs.readFileSync(PAGE, 'utf8');
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)];
const code = scripts.map(m => m[1]).sort((a, b) => b.length - a.length)[0];

/* ---------------- 极简 DOM ---------------- */
const ids = [...new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]))];
const listeners = new Map();
const elements = new Map();

function makeEl(id) {
  const el = {
    id, textContent: '', innerHTML: '', value: '', disabled: false, hidden: false,
    dataset: {}, style: {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
      toggle(c, on) { on === undefined ? (this._set.has(c) ? this._set.delete(c) : this._set.add(c))
                                       : (on ? this._set.add(c) : this._set.delete(c)); },
      contains(c) { return this._set.has(c); },
    },
    children: [],
    addEventListener(type, fn) {
      const key = id + ':' + type;
      listeners.set(key, fn);
    },
    removeAttribute(name) { delete el.dataset[name]; },
    querySelectorAll() { return []; },
    remove() { el._removed = true; },
    // 对话渲染会查 .chat-text，桩里必须返回一个元素而不是 null
    querySelector(sel) {
      if (!el._q) el._q = {};
      if (!el._q[sel]) el._q[sel] = makeEl(sel);
      return el._q[sel];
    },
    appendChild(c) { el.children.push(c); return c; },
    setAttribute() {},
    getAttribute() { return null; },
    focus() {},
  };
  return el;
}

for (const id of ids) elements.set(id, makeEl(id));

const document = {
  getElementById: (id) => elements.get(id) || null,
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: (tag) => makeEl(tag),
  addEventListener: (type, fn) => listeners.set('document:' + type, fn),
  body: makeEl('body'),
  documentElement: makeEl('html'),
};

const window = {
  addEventListener: (type, fn) => listeners.set('window:' + type, fn),
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  location: { protocol: 'http:', hostname: '127.0.0.1' },
  prompt: () => '',
  localStorage: { getItem: () => null, setItem: () => {} },
};

/* ---------------- fetch 桩 ---------------- */
const RETRIEVED = {
  query: '版本更新 维护 修复 trios',
  hits: [{ chunk_id: 'c1', document_id: 'd1', title: 'NARAKA Update – Sep 16',
           score: 0.6123, publish_date: '2026-09-16',
           excerpt: 'Newly added Quick Matchmaking · Trios mode.' }],
};

const STRATEGY = {
  app_id: 1203220, execution_status: 'suggested',
  summary: '在线人数下降 3.28%，建议先沟通再评估活动',
  risk_analysis: { risk_level: 'medium', observed_signals: [], possible_causes: [] },
  data_evidence: [{ name: 'ccu_latest', value: 37338 }, { name: 'ccu_change_24h_pct', value: -3.28 }],
  knowledge_evidence: [{ chunk_id: 'c1', document_id: 'd1',
                         title: 'NARAKA Update – Sep 16', claim: '新增 Trios 模式' }],
  recall_actions: [{ name: '优化 Trios 匹配', priority: 'high', objective: 'o', target_group: 'g',
                     action_type: 't', description: 'd', expected_effect: 'e', measurement: [] },
                   { name: '调整特效性能', priority: 'medium', objective: 'o', target_group: 'g',
                     action_type: 't', description: 'd', expected_effect: 'e', measurement: [] },
                   { name: '补充匹配说明', priority: 'low', objective: 'o', target_group: 'g',
                     action_type: 't', description: 'd', expected_effect: 'e', measurement: [] }],
  limitations: ['仅群体级建议'],
};

const calls = [];
const fetch = async (url, opts) => {
  calls.push({ url, method: (opts && opts.method) || 'GET' });
  let payload;
  if (url.includes('/api/games')) {
    payload = { data: [{ appid: 1203220, name: '永劫无间' },
                       { appid: 1091500, name: '赛博朋克 2077' }] };
  } else if (url.includes('/api/recall/history')) {
    payload = { data: { total: 1, items: [{ strategy_id: 's1', summary: '历史摘要',
                                            created_at: '2026-11-18T12:51:42+00:00',
                                            action_count: 3, risk_level: 'medium' }] } };
  } else if (url.includes('/api/knowledge/answer')) {
    payload = { data: { status: 'answered', intent: 'kb_factual_qa',
                        answer: '11 月 5 日更新调整了 Vanquisher’s Trials。',
                        citations: [{ label: 'E1', title: 'NARAKA Update – Nov 5',
                                      chunk_id: 'doc_a#0000' }],
                        evidence: { missing_elements: [] }, reason: '' } };
  } else if (url.includes('/api/recall/generate')) {
    payload = { data: { status: 'generated', strategy_id: 's2', strategy: STRATEGY,
                        knowledge_query: RETRIEVED.query,
                        retrieved_evidence: RETRIEVED.hits } };
  } else if (/\/api\/recall\/s/.test(url)) {
    payload = { data: { strategy_id: 's1', created_at: '2026-11-18T12:51:42+00:00',
                        strategy: STRATEGY, retrieved: RETRIEVED } };
  } else {
    payload = { data: {} };
  }
  return { ok: true, status: 200, json: async () => payload };
};

/* ---------------- 执行 ---------------- */
const run = new Function('window', 'document', 'fetch', 'location', 'localStorage',
                         'setTimeout', 'console', code);
run(window, document, fetch, window.location, window.localStorage,
    (fn) => setTimeout(fn, 0), console);

await new Promise(r => setTimeout(r, 60));

const $ = (id) => elements.get(id);
let failed = 0;
const check = (label, ok, extra) => {
  console.log((ok ? '  ✓ ' : '  ✗ ') + label + (extra === undefined ? '' : '  ' + extra));
  if (!ok) failed++;
};

console.log('页面加载阶段：');
check('游戏下拉已填充', ($('aiGame').innerHTML || '').includes('永劫无间'));
check('默认选中 1203220', String($('aiGame').value) === '1203220', $('aiGame').value);
// 刷新后必须自动恢复最近一次结果，否则用户以为丢了会重复生成
check('自动载入最近策略', ($('planTitle').textContent || '').includes('在线人数下降 3.28%'));
check('自动载入后标记为真实', String($('aiSourceTag').dataset.kind) === 'real');
check('知识库命中已渲染', ($('aiRetrieved').innerHTML || '').includes('NARAKA Update'));
check('检索查询词已显示', ($('hitsQuery').textContent || '').includes('版本更新'));
check('调用了 /api/games', calls.some(c => c.url.includes('/api/games')));

console.log('\n点击「生成策略」：');
const click = listeners.get('aiGenerate:click');
check('已注册点击处理器', typeof click === 'function');
await click.call($('aiGenerate'));
await new Promise(r => setTimeout(r, 60));

check('方案卡标题 = 真实摘要', ($('planTitle').textContent || '').includes('在线人数下降 3.28%'));
check('方案 ID 带 strategy_id', ($('planId').textContent || '').includes('s2'));
check('策略详情 3 条措施', ($('stepsTrack').innerHTML.match(/data-action="1"/g) || []).length === 3);
check('知识证据已渲染', ($('aiEvidence').innerHTML || '').includes('Trios'));
check('数据指标已渲染', ($('aiMetrics').innerHTML || '').includes('ccu_latest'));
check('限制说明已渲染', ($('aiLimits').innerHTML || '').includes('群体级'));
check('结果区块已显示', $('aiResult').hidden === false);
check('来源标记 = 本地模型生成', String($('aiSourceTag').dataset.kind) === 'real');
check('状态行标记为成功', String($('aiStatus').dataset.state) === 'ok', $('aiStatus').textContent);
check('调用了 generate 接口', calls.some(c => c.url.includes('/api/recall/generate')));

console.log('\n对话模块：');
$('chatInput').value = '最近一次更新改了什么？';
const submit = listeners.get('chatForm:submit');
check('已注册提交处理器', typeof submit === 'function');
await submit({ preventDefault() {} });
await new Promise(r => setTimeout(r, 80));
check('用户消息已入列', $('chatLog').children.length >= 1, 'children=' + $('chatLog').children.length);
check('AI 回复已入列', $('chatLog').children.length >= 2);
check('调用了 /api/knowledge/answer', calls.some(c => c.url.includes('/api/knowledge/answer')));

console.log('\n生成失败路径：');
const failFetch = async (url, opts) => {
  if (url.includes('/api/recall/generate')) {
    return { ok: false, status: 422, json: async () => ({ data: { status: 'generation_failed',
             reason: 'data_evidence_mismatch', detail: 'value_mismatch:ccu_latest' } }) };
  }
  return fetch(url, opts);
};
const run2 = new Function('window', 'document', 'fetch', 'location', 'localStorage',
                          'setTimeout', 'console', code);
run2(window, document, failFetch, window.location, window.localStorage,
     (fn) => setTimeout(fn, 0), console);
await new Promise(r => setTimeout(r, 40));
const click2 = listeners.get('aiGenerate:click');
await click2.call($('aiGenerate'));
await new Promise(r => setTimeout(r, 40));
check('失败时状态为 err', String($('aiStatus').dataset.state) === 'err');
check('失败原因被如实展示', ($('aiStatus').textContent || '').includes('data_evidence_mismatch'));

console.log(failed === 0 ? '\n全部通过' : `\n失败 ${failed} 项`);
process.exit(failed === 0 ? 0 : 1);
