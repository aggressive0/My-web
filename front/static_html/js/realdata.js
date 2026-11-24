/* ============================================================================
 * RealData —— 四页共用的真实数据层
 *
 * 职责只有三件：
 *   1. 按「远程 data 分支 → 仓库内离线快照 → 不可用」的顺序取数，并如实报告来源；
 *   2. 提供统一的四态渲染（loading / ready / empty / error）与状态条；
 *   3. 绝不返回演示数据填充 —— 这是 AGENTS.md §4.3 的硬要求。
 *
 * 为什么不复用 dashboard-data.js 的 grabJson：
 *   那个函数把「远程失败 → 快照失败 → 演示序列」串成一条静默回退链（审计 P1）。
 *   本文件只允许前两级，第三级必须是显式空态。
 *
 * 用法：
 *   RealData.load('sentiment-daily.json').then(function (r) {
 *     // r.state: 'ready' | 'unavailable'
 *     // r.doc / r.source: 'remote' | 'snapshot' / r.updatedAt
 *   });
 * ========================================================================== */
window.RealData = (function () {
    'use strict';

    // 仓库已重命名为 liusheng-player-retention；data 分支在其上持续更新
    // （2026-11-24 核对：collection-status.json updated_at = 2026-11-24T04:51Z）。
    // 旧地址 My-web 只是 GitHub 的重定向目标，不应继续作为数据源。
    var REMOTE = 'https://raw.githubusercontent.com/aggressive0/liusheng-player-retention/data/';
    var SNAPSHOT = 'json/';

    /* 每个数据文件的「人话」说明，用于状态条与 aria-label */
    var META = {
        'steam-ccu.json': { label: '同时在线', src: 'Steam ISteamUserStats' },
        'steam-funnels.json': { label: '成就完成率', src: 'Steam 成就接口' },
        'reviews-daily.json': { label: '评论聚合', src: 'Steam 评论接口' },
        'sentiment-daily.json': { label: '评论情感', src: 'MiniLM + 逻辑回归' },
        'topics-daily.json': { label: '主题候选', src: '规则词典 v1' },
        'risk-daily.json': { label: '风险指数', src: '公开信号合成' },
        'collection-status.json': { label: '采集状态', src: '采集日志' },
        'globe-daily.json': { label: '语言市场舆情热点', src: 'Steam 评论样本聚合' }
    };

    var cache = {};

    function fetchJson(url, timeoutMs) {
        return new Promise(function (resolve) {
            var ctrl = window.AbortController ? new AbortController() : null;
            var timer = setTimeout(function () {
                if (ctrl) ctrl.abort();
            }, timeoutMs || 5000);

            fetch(url, ctrl ? { signal: ctrl.signal, cache: 'no-store' } : { cache: 'no-store' })
                .then(function (res) {
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    return res.json();
                })
                .then(function (doc) { clearTimeout(timer); resolve(doc); })
                .catch(function () { clearTimeout(timer); resolve(null); });
        });
    }

    function ok(doc, source, updatedAt) {
        return { state: 'ready', doc: doc, source: source, updatedAt: updatedAt || null };
    }

    function load(name, opts) {
        opts = opts || {};
        if (cache[name]) return Promise.resolve(cache[name]);

        var remote = (opts.remote || REMOTE) + name;
        var snapshot = (opts.snapshot || SNAPSHOT) + name;

        return fetchJson(remote, opts.timeout).then(function (doc) {
            if (doc) {
                var r = ok(doc, 'remote', doc.updated_at);
                cache[name] = r;
                return r;
            }
            return fetchJson(snapshot, opts.timeout).then(function (sdoc) {
                if (sdoc) {
                    var r2 = ok(sdoc, 'snapshot', sdoc.updated_at);
                    cache[name] = r2;
                    return r2;
                }
                return { state: 'unavailable', doc: null, source: null, updatedAt: null };
            });
        });
    }

    /* ---------- 展示辅助 ---------- */

    function hhmm(iso) {
        if (!iso) return '时间未知';
        var d = new Date(iso);
        if (isNaN(d)) return '时间未知';
        function p(n) { return n < 10 ? '0' + n : '' + n; }
        return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    function sourceText(r) {
        if (!r || r.state !== 'ready') return '数据不可用';
        return (r.source === 'remote' ? '实时' : '离线快照') + ' · 更新于 ' + hhmm(r.updatedAt);
    }

    /* 状态条：把「来源 + 更新时间 + 数据类型」渲染到指定容器。
       没有容器就跳过——不强求每个面板都有，但有关键数字的面板必须有。 */
    function stamp(el, r, extra) {
        if (!el) return;
        if (!r || r.state !== 'ready') {
            el.innerHTML = '<span class="rd-chip is-bad">数据不可用</span>';
            el.setAttribute('title', '上游与本地快照均未取到数据，未使用任何模拟值填充');
            return;
        }
        var stale = extra && extra.stale;
        el.innerHTML =
            '<span class="rd-chip' + (r.source === 'remote' ? '' : ' is-warn') + '">' +
            (r.source === 'remote' ? 'LIVE' : 'SNAPSHOT') + '</span>' +
            '<span class="rd-time">' + hhmm(r.updatedAt) + '</span>' +
            (extra && extra.note ? '<span class="rd-note">' + extra.note + '</span>' : '') +
            (stale ? '<span class="rd-chip is-warn">可能过期</span>' : '');
    }

    /* 四态：把 loading/empty/error 文案塞进容器，ready 时清空 */
    function state(el, kind, text) {
        if (!el) return;
        if (kind === 'ready') {
            el.innerHTML = '';
            el.removeAttribute('data-state');
            return;
        }
        el.setAttribute('data-state', kind);
        el.innerHTML = '<div class="rd-state rd-' + kind + '">' + (text || '') + '</div>';
    }

    function ageHours(iso) {
        if (!iso) return null;
        var t = new Date(iso).getTime();
        if (isNaN(t)) return null;
        return (Date.now() - t) / 3600000;
    }

    return {
        load: load,
        stamp: stamp,
        state: state,
        sourceText: sourceText,
        ageHours: ageHours,
        meta: META
    };
})();
