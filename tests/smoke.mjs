/**
 * 冒烟测试 — 「留声」玩家留存中枢静态站
 *
 * 判定标准只有一条：页面「能不能用」，不判「对不对」。
 * 覆盖四层：
 *   L0 资源层  页面与依赖资源返回 200，无失败请求
 *   L1 运行层  无未捕获异常、无 console.error（这是最容易漏、也最容易致命的一层）
 *   L2 结构层  关键锚点存在且非空、图表已实例化、无横向溢出
 *   L3 交互层  时间范围切换、导航互链、AI 面板缺密钥时的降级
 *
 * 用法：
 *   node tests/smoke.mjs                 # 自动起 http.server 于 8099 端口
 *   BASE_URL=http://127.0.0.1:8080 node tests/smoke.mjs   # 复用已在跑的服务
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const SITE_DIR = join(ROOT, 'front', 'static_html');
const PORT = Number(process.env.PORT || 8099);
const BASE = process.env.BASE_URL || `http://127.0.0.1:${PORT}`;

const results = [];
let failures = 0;

function record(page, layer, name, ok, detail = '') {
    results.push({ page, layer, name, ok, detail });
    if (!ok) failures++;
}

/* ---------- 起服务 ---------- */

async function isUp(url) {
    try {
        const res = await fetch(url, { signal: AbortSignal.timeout(800) });
        return res.ok;
    } catch { return false; }
}

async function waitForServer(url, timeoutMs = 20000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
        if (await isUp(url)) return true;
        await new Promise(r => setTimeout(r, 250));
    }
    return false;
}

async function startServer(cmd, port) {
    const child = spawn(cmd, ['-m', 'http.server', String(port), '--bind', '127.0.0.1'], {
        cwd: SITE_DIR, stdio: 'ignore', shell: false
    });
    child.on('error', () => {});
    return child;
}

async function ensureServer() {
    if (await isUp(`${BASE}/index.html`)) {
        console.log(`→ 复用已有服务 ${BASE}`);
        return null;
    }
    const candidates = [process.env.PYTHON, 'python', 'python3', 'py'].filter(Boolean);
    console.log(`→ 启动 http.server：${SITE_DIR} → ${BASE}`);

    for (const cmd of candidates) {
        const child = await startServer(cmd, PORT);
        if (await waitForServer(`${BASE}/index.html`, 8000)) {
            console.log(`   解释器：${cmd}`);
            return child;
        }
        child.kill();
    }
    throw new Error(`无法启动 http.server（尝试过 ${candidates.join(' / ')}），请手动起服务并设置 BASE_URL`);
}

/* ---------- 各页断言配置 ---------- */

const PAGES = [
    {
        file: 'index.html',
        label: '总览',
        canvases: [1, 99],
        // 选择器 → 最少数量；空字符串表示「存在即可」
        anchors: [
            ['#kpiActive', 1], ['#kpiAtRisk', 1], ['#kpiSent', 1], ['#kpiRec', 1],
            ['#warnList .warn', 1],       // 曾因 stage 作用域 bug 恒为空
            ['#regionList > *', 1],
            ['#globeContainer', 1],
            ['#gamePick', 1],             // 游戏切换器由 config/games.yaml 驱动
            ['#chain .chain-step, #chain > *', 1],
        ],
        // 文本非空的容器
        nonEmpty: ['#kpiActive', '#kpiSent', '#warnList', '#regionList', '#kpiSrc'],
    },
    {
        file: 'analysis.html',
        label: '流失分析',
        canvases: [2, 99],
        anchors: [
            ['#trendChart', 1], ['#funnelChart', 1], ['#rangeLabel', 1],
            ['#funnelList .reason-item', 1],
        ],
        nonEmpty: ['#rangeLabel', '#funnelList', '#trendSrc', '#topicSrc'],
    },
    {
        file: 'solution.html',
        label: '召回策略',
        canvases: [0, 99],          // 本页图表为内联 SVG，不引入 ECharts
        anchors: [
            ['#strategyGrid .card', 4],
            ['#stepsTrack .step', 4],
            ['#deployBtn', 1],
            ['#generateStrategy', 1], ['#responseContent', 1],
            ['#riskArc', 1], ['#segDonut circle', 3],
            ['#engineStatus', 1],
        ],
        nonEmpty: ['#planTitle', '#vHigh', '#vPool', '#strategyGrid', '#segLegend'],
    },
    {
        file: 'validation.html',
        label: '留存追踪',
        canvases: [1, 99],
        anchors: [
            ['#retTrendChart', 1], ['#factorChart', 1],
            ['#cohortBody .cohort-row', 1], ['#segRows > *', 1], ['#retKpi', 1],
        ],
        nonEmpty: ['#cohortBody', '#retKpi'],
    },
];

/* ---------- 单页检查 ---------- */

async function checkPage(browser, cfg) {
    const failuresAtStart = failures;
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    const consoleErrs = [];
    const failedReq = [];

    page.on('pageerror', e => errors.push((e.message || String(e)).split('\n')[0]));
    page.on('console', m => { if (m.type() === 'error') consoleErrs.push(m.text().slice(0, 200)); });
    page.on('requestfailed', r => {
        // 用户取消/中断不算缺陷；仅记录真正的加载失败
        const t = (r.failure() || {}).errorText || '';
        if (!/ERR_ABORTED/.test(t)) failedReq.push(`${r.url().split('/').pop()} (${t})`);
    });

    const url = `${BASE}/${cfg.file}`;
    const res = await page.goto(url, { waitUntil: 'load' }).catch(e => { errors.push('NAV: ' + e.message); return null; });
    record(cfg.label, 'L0', '页面返回 200', !!res && res.status() === 200, res ? `HTTP ${res.status()}` : 'no response');

    // 给 WebGL / ECharts / 首次渲染留时间
    await page.waitForTimeout(4500);

    record(cfg.label, 'L1', '无未捕获异常', errors.length === 0, errors.join(' | '));
    record(cfg.label, 'L1', '无 console.error', consoleErrs.length === 0, consoleErrs.join(' | '));
    record(cfg.label, 'L1', '无失败请求', failedReq.length === 0, failedReq.join(' | '));

    const dom = await page.evaluate(() => ({
        title: document.title.trim(),
        canvases: document.querySelectorAll('canvas').length,
        links: Array.from(document.querySelectorAll('a[href$=".html"]')).map(a => a.getAttribute('href')),
        overflowX: document.documentElement.scrollWidth - window.innerWidth,
        hasData: typeof window.DashboardData === 'object' && window.DashboardData !== null,
        cloudLayer: (() => {
            const globe = window.GlobeStage && window.GlobeStage.instance && window.GlobeStage.instance();
            if (!globe || !globe.scene) return false;
            let found = false;
            globe.scene().traverse(obj => { if (obj && obj.userData && obj.userData.globeCloudLayer) found = true; });
            return found;
        })(),
        counts: {}, texts: {}
    }));

    record(cfg.label, 'L2', '标题非空', dom.title.length > 0, dom.title);
    record(cfg.label, 'L2', `canvas 数 ${dom.canvases} 在 [${cfg.canvases[0]},∞)`, dom.canvases >= cfg.canvases[0], `实际 ${dom.canvases}`);
    record(cfg.label, 'L2', '无横向溢出', dom.overflowX <= 1, `溢出 ${dom.overflowX}px`);
    record(cfg.label, 'L2', '数据层已挂载', dom.hasData, dom.hasData ? '' : 'window.DashboardData 缺失');
    if (cfg.file === 'index.html') {
        record(cfg.label, 'L2', '地球云层纹理已挂载', dom.cloudLayer, dom.cloudLayer ? '' : 'cloud mesh 未进入 globe scene');
    }
    record(cfg.label, 'L2', '导航互链 4 条', new Set(dom.links).size >= 4, `实际 ${new Set(dom.links).size} 条`);

    for (const [sel, min] of cfg.anchors) {
        const n = await page.evaluate(s => document.querySelectorAll(s).length, sel);
        record(cfg.label, 'L2', `锚点 ${sel} ≥ ${min}`, n >= min, `实际 ${n}`);
    }
    for (const sel of cfg.nonEmpty) {
        const len = await page.evaluate(s => {
            const el = document.querySelector(s);
            return el ? (el.innerText || el.textContent || '').trim().length : -1;
        }, sel);
        record(cfg.label, 'L2', `${sel} 有内容`, len > 0, len === -1 ? '元素不存在' : `文本长度 ${len}`);
    }

    /* L3 交互 —— 首页：时间范围切换 */
    if (cfg.file === 'index.html') {
        const before = await page.evaluate(() => document.getElementById('rangeLabel').textContent.trim());
        const hasSeg = await page.evaluate(() => document.querySelectorAll('.seg-btn').length);
        if (hasSeg >= 2) {
            for (const range of ['7d', '90d']) {
                const errBefore = errors.length;
                await page.evaluate(r => {
                    const b = Array.from(document.querySelectorAll('.seg-btn')).find(x => x.dataset.range === r);
                    if (b) b.click();
                }, range);
                await page.waitForTimeout(700);
                const state = await page.evaluate(() => ({
                    label: document.getElementById('rangeLabel').textContent.trim(),
                    warns: document.querySelectorAll('#warnList .warn').length,
                    kpi: document.getElementById('kpiActive').textContent.trim()
                }));
                record(cfg.label, 'L3', `切到 ${range} 文案更新`, state.label !== before, `${before} → ${state.label}`);
                record(cfg.label, 'L3', `切到 ${range} 后无新增异常`, errors.length === errBefore, errors.slice(errBefore).join(' | '));
                record(cfg.label, 'L3', `切到 ${range} 后 KPI 有值`, state.kpi.length > 0, state.kpi.slice(0, 30));
            }
        } else {
            record(cfg.label, 'L3', '时间范围分段控件存在', false, `找到 ${hasSeg} 个 .seg-btn`);
        }

        /* 游戏切换器：选项来自 config/games.yaml（经 data 分支同步），不在页面里写死。
           无真实数据时切换器是隐藏的，这是符合预期的一种状态，不算失败。 */
        const pick = await page.evaluate(() => {
            const s = document.getElementById('gamePick');
            if (!s) return null;
            return {
                hidden: s.hidden,
                n: s.options.length,
                names: Array.from(s.options).map(o => o.textContent),
            };
        });
        const pickOk = !!pick && (pick.hidden || (pick.n >= 5 && pick.names[0] === '指数对比'));
        record(cfg.label, 'L3', '游戏切换器由配置生成', pickOk,
            !pick ? '元素缺失'
                : pick.hidden ? '无真实数据，切换器隐藏（预期之一）'
                    : `${pick.n} 项：${pick.names.slice(1, 4).join(' / ')}`);
    }

    /* L3 交互 —— 留存页：KPI 与时长分布必须来自真实评论聚合 */
    if (cfg.file === 'validation.html') {
        const v = await page.evaluate(() => ({
            cards: document.querySelectorAll('#retKpi .kpi').length,
            rows: document.querySelectorAll('#cohortBody .cohort-row').length,
            first: ((document.querySelector('#cohortBody .cohort-row') || {}).innerText || '').replace(/\s+/g, ' ')
        }));
        record(cfg.label, 'L3', '留存页 KPI 为真实四卡', v.cards === 4, `${v.cards} 张`);
        record(cfg.label, 'L3', '时长分布表已渲染且首行为游戏名',
            v.rows >= 6 && /[A-Za-z\u4e00-\u9fa5]/.test(v.first), `${v.rows} 行：${v.first.slice(0, 30)}`);
    }

    /* L3 交互 —— 分析页：主题热度必须落到真实主题或显式空态（不能停在「加载中」） */
    if (cfg.file === 'analysis.html') {
        const topic = await page.evaluate(() => {
            const rows = document.querySelectorAll('#topicRows .corr').length;
            const st = document.querySelector('#topicRows .rd-state');
            return { rows, state: st ? st.textContent.trim() : '' };
        });
        record(cfg.label, 'L3', '主题热度已落到真实数据或空态',
            topic.rows > 0 || !!topic.state,
            topic.rows > 0 ? topic.rows + ' 个主题' : ('空态：' + topic.state));
    }

    /* L3 交互 —— 策略页：缺密钥降级、方案联动、部署状态 */
    if (cfg.file === 'solution.html') {
        const hint = await page.evaluate(async () => {
            const fold = document.querySelector('.ai-fold');
            if (fold) fold.open = true;
            window.prompt = () => null;                 // 模拟用户取消输入
            document.getElementById('modelPrompt').value = '冒烟测试：北美玩家流失';
            document.getElementById('generateStrategy').click();
            await new Promise(r => setTimeout(r, 900));
            const el = document.getElementById('responseContent');
            return (el && el.textContent || '').trim();
        });
        record(cfg.label, 'L3', '缺密钥时给出可读提示', hint.length > 0, hint.slice(0, 60) || '面板无反馈');

        const switched = await page.evaluate(async () => {
            const before = document.getElementById('planTitle').textContent;
            const btn = document.querySelector('#strategyGrid [data-view="emotion"]');
            if (!btn) return 'no-card';
            btn.click();
            await new Promise(r => setTimeout(r, 250));
            return document.getElementById('planTitle').textContent === before ? 'no-change' : 'changed';
        });
        record(cfg.label, 'L3', '「查看方案」联动右侧方案', switched === 'changed', switched);

        const deployed = await page.evaluate(async () => {
            document.getElementById('deployBtn').click();
            await new Promise(r => setTimeout(r, 300));
            return (document.getElementById('deployLabel').textContent || '').trim();
        });
        record(cfg.label, 'L3', '部署按钮状态可切换', deployed.indexOf('部署队列') >= 0, deployed);
    }

    /* 截图留档：仅本页有失败时写盘，供排查 */
    if (failures > failuresAtStart) {
        const shot = join(HERE, 'artifacts', cfg.file.replace('.html', '.png'));
        await page.screenshot({ path: shot }).catch(() => {});
    }

    await page.close();
}

/* ---------- 主流程 ---------- */

const server = await ensureServer().catch(e => { console.error(e.message); process.exit(1); });
const browser = await chromium.launch();

try {
    for (const cfg of PAGES) await checkPage(browser, cfg);
} finally {
    await browser.close();
    if (server) server.kill();
}

/* ---------- 报告 ---------- */

const pad = s => String(s).padEnd(6, ' ');
console.log('\n' + '─'.repeat(78));
for (const r of results) {
    const mark = r.ok ? 'PASS' : 'FAIL';
    const line = `${mark}  ${pad(r.layer)} ${pad(r.page)} ${r.name}`;
    console.log(r.ok ? line : `${line}\n        ↳ ${r.detail || '断言失败'}`);
}
console.log('─'.repeat(78));
console.log(`共 ${results.length} 项，失败 ${failures} 项`);
process.exit(failures === 0 ? 0 : 1);
