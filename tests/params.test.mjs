/**
 * URL 参数模块单测（Phase D）。
 * 重点：非法输入必须被丢弃，而不是猜一个"看起来合理"的值。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(
  path.join(here, '..', 'front', 'static_html', 'js', 'url-state.js'), 'utf8');
const sandbox = { exports: {} };
new Function('module', 'exports', 'window', src)(sandbox, sandbox.exports, undefined);
const U = sandbox.exports;

let failed = 0;
const check = (name, fn) => {
  try { fn(); console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + ' — ' + e.message); }
};

check('解析完整参数', () => {
  const s = U.parse('?appid=1203220&range=30d&topic=性能与优化');
  assert.equal(s.appid, '1203220');
  assert.equal(s.range, '30d');
  assert.equal(s.topic, '性能与优化');
});

check('缺少问号也能解析', () => {
  assert.equal(U.parse('appid=5').appid, '5');
});

check('非数字 appid 被丢弃', () => {
  assert.equal(U.parse('?appid=abc').appid, '');
  assert.equal(U.parse('?appid=12abc').appid, '');
});

check('未知 range 被丢弃', () => {
  assert.equal(U.parse('?range=999d').range, '');
  assert.equal(U.parse('?range=7D').range, '');
});

check('空串与 null 返回空状态', () => {
  const a = U.parse('');
  const b = U.parse(null);
  assert.deepEqual(a, { appid: '', range: '', topic: '' });
  assert.deepEqual(b, a);
});

check('build 跳过空值，顺序稳定', () => {
  assert.equal(U.build({ appid: '1', range: '7d', topic: '' }), 'appid=1&range=7d');
  assert.equal(U.build({}), '');
});

check('build 忽略非法值', () => {
  assert.equal(U.build({ appid: 'x', range: '2d' }), '');
});

check('中文主题被正确编码并往返', () => {
  const qs = U.build({ appid: '9', topic: '性能与优化' });
  assert.ok(qs.indexOf('topic=') >= 0);
  assert.equal(U.parse('?' + qs).topic, '性能与优化');
});

check('href 无参数时不加问号', () => {
  assert.equal(U.href('analysis.html', {}), 'analysis.html');
  assert.equal(U.href('analysis.html', { appid: '7' }), 'analysis.html?appid=7');
});

check('主题超长被截断到 40 字符', () => {
  const long = 'x'.repeat(80);
  assert.equal(U.parse('?topic=' + long).topic.length, 40);
});

console.log(failed === 0 ? '\n全部通过' : `\n失败 ${failed} 项`);
process.exit(failed === 0 ? 0 : 1);
