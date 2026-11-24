/* ============================================================================
 * 总览页 Phase B 绑定层
 *
 * 职责：把 RealData 取回的 JSON 经 DashboardAdapters 解释后，写入四个面板：
 *   1. 游戏舆情排行（risk-daily.json）—— 风险指数降序，不再是虚构的「区域流失率」
 *   2. 规则预警 —— 阈值触发，非模型生成；每条都带触发它的字段与数值
 *   3. 下一步动作 —— 由最高优先级预警推导，不承诺任何无法估算的效果
 *   4. 决策链 —— 用真实指标填，算不出来的写「不可估计」
 *
 * 原则：任何一处拿不到数据就写不可用，绝不回落到演示值。
 * 分级阈值（40 / 60）是本层显式声明的口径，不是 risk-daily.json 的字段。
 * ========================================================================== */
(function () {
    'use strict';

    var A = window.DashboardAdapters;
    var RD = window.RealData;
    if (!A || !RD) return;

    var HIGH = 60, MID = 40;          // 分级阈值，写进来源条，避免被当成文档字段
    var data = { risk: null, sent: null, ccu: null, collected: null };

    function $(id) { return document.getElementById(id); }

    function esc(t) {
        return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function sourceBar(el, file, asOf, extra) {
        if (!el) return;
        var when = asOf ? String(asOf).replace('T', ' ').slice(0, 16) : '未知时间';
        el.textContent = [file, '更新 ' + when, extra].filter(Boolean).join(' · ');
    }

    function level(index) {
        if (index >= HIGH) return { key: 'high', label: '高' };
        if (index >= MID) return { key: 'medium', label: '中' };
        return { key: 'low', label: '低' };
    }

    /* ---------------- 2. 规则预警 ---------------- */
    function buildAlerts() {
        var alerts = [];
        var risk = A.riskRanking(data.risk && data.risk.doc);
        if (risk.state === 'ready') {
            risk.value.forEach(function (row) {
                if (row.index < MID) return;
                var lv = level(row.index);
                alerts.push({
                    id: 'risk-' + row.appid,
                    level: lv.key,
                    title: row.name + ' 风险指数 ' + row.index.toFixed(1) + '（' + lv.label + '）',
                    detail: '来自 risk-daily.json；可用分量 ' + (row.used.join(' / ') || '无') +
                        '，共 ' + row.used.length + ' 项。阈值：高 ≥' + HIGH + '，中 ≥' + MID + '。',
                    evidence: 'risk_index=' + row.index.toFixed(1)
                });
            });
        }

        var ccu = A.ccuSeries(data.ccu && data.ccu.doc, 7);
        if (ccu.state === 'ready') {
            ccu.value.forEach(function (g) {
                if (g.changePct === null || g.changePct > -5) return;
                alerts.push({
                    id: 'ccu-' + g.appid,
                    level: 'medium',
                    title: g.name + ' 同时在线 24h 下降 ' + g.changePct.toFixed(1) + '%',
                    detail: '来自 steam-ccu.json；当前 ' + Math.round(g.latest) +
                        ' 人。阈值：24h 跌幅 ≤ -5%。',
                    evidence: 'ccu_change_24h=' + g.changePct.toFixed(2) + '%'
                });
            });
        }

        var sent = A.sentimentTotals(data.sent && data.sent.doc, null);
        if (sent.state === 'ready' && sent.value.negRate >= 25) {
            alerts.push({
                id: 'sent-all',
                level: 'medium',
                title: '全样本负向评论占比 ' + sent.value.negRate.toFixed(1) + '%',
                detail: '来自 sentiment-daily.json；样本 ' + sent.value.n +
                    ' 条（' + sent.value.games + ' 款游戏聚合）。阈值：负向 ≥ 25%。',
                evidence: 'neg=' + sent.value.neg + '/' + sent.value.n
            });
        }
        return alerts;
    }

    var ALERT_TONE = { high: '#FF6B75', medium: '#FFB45F', low: '#35D9F2' };

    function renderAlerts() {
        var host = $('warnList');
        if (!host) return;
        var alerts = buildAlerts();
        if (!alerts.length) {
            var missing = !data.risk && !data.ccu && !data.sent;
            host.innerHTML = '<div class="empty-state">' +
                (missing ? '指标数据不可用（未取到 risk-daily / steam-ccu / sentiment-daily）'
                         : '当前没有触发阈值的指标') + '</div>';
            return;
        }
        host.innerHTML = alerts.slice(0, 6).map(function (a) {
            return '<div class="warn" role="button" tabindex="0" aria-expanded="false"' +
                ' data-alert-id="' + esc(a.id) + '"' +
                ' aria-label="' + esc(a.title) + '，点击展开计算依据">' +
                '<span class="warn-ico" style="--c:' + (ALERT_TONE[a.level] || '#FFB45F') + '"></span>' +
                '<div class="warn-txt"><b>' + esc(a.title) + '</b>' +
                '<span>' + esc(a.evidence) + '</span></div>' +
                // 完整依据默认折叠：列表要能扫，细节要能查
                '<div class="warn-detail" hidden style="grid-column:1/-1;margin-top:6px;' +
                'font-size:10px;line-height:1.7;color:#9EB1CA">' + esc(a.detail) + '</div></div>';
        }).join('');

        if (!host.__bound) {
            host.__bound = true;
            host.addEventListener('click', function (ev) {
                var item = ev.target.closest('.warn');
                if (!item) return;
                var detail = item.querySelector('.warn-detail');
                if (!detail) return;
                detail.hidden = !detail.hidden;
                item.setAttribute('aria-expanded', detail.hidden ? 'false' : 'true');
            });
            host.addEventListener('keydown', function (ev) {
                if (ev.key !== 'Enter' && ev.key !== ' ') return;
                var item = ev.target.closest('.warn');
                if (!item) return;
                ev.preventDefault();
                item.click();
            });
        }
        var meta = $('warnSrc');
        if (meta) meta.textContent = alerts.length + ' 条 · 阈值触发 · 非模型生成';
    }

    /* ---------------- 3. 下一步动作（有证据） ---------------- */
    function renderAdvice() {
        var title = $('adviceTitle');
        if (!title) return;
        var alerts = buildAlerts();
        if (!alerts.length) {
            title.textContent = '暂无建议：没有触发阈值的指标';
            $('adviceSub').textContent = '规则不会在数据不足时给出动作，避免误导';
            $('adviceFoot').textContent = '无数据 · 不给效果承诺';
            return;
        }
        var top = alerts[0];
        title.textContent = '优先处理：' + top.title;
        $('adviceSub').textContent = '依据 ' + top.detail;
        $('adAudience').textContent = '该指标对应的全部玩家（群体级，未做玩家级识别）';
        $('adChannel').textContent = '社区公告 / 版本更新说明 / 客服话术';
        $('adReason').textContent = top.evidence;
        $('adviceFoot').textContent = '效果无法从公开数据估算，不给具体百分比';

        var btn = $('adviceEvidence');
        if (btn && !btn.__bound) {
            btn.__bound = true;
            btn.addEventListener('click', function () {
                var detail = $('actionDetail');
                if (detail) detail.hidden = !detail.hidden;
            });
        }
    }

    /* ---------------- 4. 决策链 ---------------- */
    function renderChain() {
        var risk = A.riskRanking(data.risk && data.risk.doc);
        var sent = A.sentimentTotals(data.sent && data.sent.doc, null);
        var ccu = A.ccuSeries(data.ccu && data.ccu.doc, 7);
        var top = risk.state === 'ready' ? risk.value[0] : null;

        function put(id, text) { var el = $(id); if (el) el.textContent = text; }

        put('chainHotspot', top ? top.name : '数据不可用');
        put('chainHotspotHeat', top ? '指数 ' + top.index.toFixed(1) : '—');
        put('chainNeg', sent.state === 'ready' ? '负向 ' + sent.value.negRate.toFixed(1) + '%' : '—');
        put('chainNegChg', sent.state === 'ready' ? '样本 ' + sent.value.n + ' 条' : '—');
        put('chainAffected', risk.state === 'ready' ? '覆盖 ' + risk.value.length + ' 款游戏' : '—');
        put('chainRiskLevel', top ? '风险 ' + level(top.index).label : '—');
        put('chainStrategy', top ? '核查 ' + top.used.slice(0, 2).join(' / ') + ' 相关信号' : '—');
        put('chainRecovery', '效果不可估计');
    }

    /* ---------------- 载入 ---------------- */
    /* ---------------- 数据新鲜度诊断 ----------------
       每个源单独报「更新时间 + 是否可能过期」，而不是笼统说一句「数据已更新」。
       阈值与 adapters.freshness 一致：36 小时内视为新鲜。 */
    function renderDiagnostics() {
        var host = $('ticker');
        if (!host) return;
        // refresh 会被范围切换重复调用，先清掉上一轮追加的诊断块，避免越堆越多
        [].slice.call(host.querySelectorAll('[data-diag]')).forEach(function (n) { n.remove(); });
        var pairs = [['CCU', data.ccu], ['情感', data.sent], ['风险', data.risk], ['采集', data.collected]];
        pairs.forEach(function (pair) {
            var el = document.createElement('span');
            el.className = 'tk-item on';
            el.dataset.diag = '1';
            var rec = pair[1];
            if (!rec || rec.state !== 'ready') {
                el.textContent = pair[0] + ' 不可用';
                el.style.color = '#FFB45F';
            } else {
                var stamp = rec.doc && rec.doc.updated_at;
                var hours = window.RealData.ageHours(stamp);
                var stale = hours !== null && hours > 36;
                el.textContent = pair[0] + ' ' + String(stamp || '').replace('T', ' ').slice(5, 16) +
                    (stale ? ' · 可能过期' : '');
                if (stale) el.style.color = '#FFB45F';
            }
            host.appendChild(el);
        });
    }

    /* ---------------- 风险排行 → 分析页 ----------------
       点一款游戏直接带着 appid 跳到分析页，筛选状态用 URL 传，刷新也不丢。 */
    (function bindRankJump() {
        var list = $('regionList');
        if (!list || list.__jumpBound) return;
        list.__jumpBound = true;
        list.addEventListener('click', function (ev) {
            var row = ev.target.closest('.region[data-appid]');
            if (!row || !window.UrlState) return;
            location.href = window.UrlState.href('analysis.html', { appid: row.dataset.appid });
        });
    })();

    function refresh() {
        // 注意：不碰 #regionList —— 那里已有一套基于真实评论的实现（带 n= 样本条）。
        // 两套写同一个容器只会互相覆盖，谁后跑谁赢，等于随机。
        renderAlerts();
        renderAdvice();
        renderChain();
        renderDiagnostics();
    }

    function load(name, key) {
        return RD.load(name).then(function (r) {
            data[key] = (r && r.state === 'ready') ? r : null;
        }).catch(function () { data[key] = null; });
    }

    Promise.all([
        load('risk-daily.json', 'risk'),
        load('sentiment-daily.json', 'sent'),
        load('steam-ccu.json', 'ccu'),
        load('collection-status.json', 'collected')
    ]).then(refresh);

    // 页面原有的范围切换会把排行重绘成空态（它依赖已停用的区域数据），
    // 这里在事件之后再覆盖回真实排行。
    document.addEventListener('click', function (ev) {
        if (ev.target.closest('[data-range]')) setTimeout(refresh, 260);
    }, true);

    window.PhaseB = { refresh: refresh, alerts: buildAlerts };
})();
