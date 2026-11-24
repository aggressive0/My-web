/* ============================================================================
 * 流失分析页 Phase C 绑定层
 *
 * 统一筛选：一个 #gamePick 驱动全部面板。切换后每个模块要么显示该 appid 的数据，
 * 要么显示明确空态——不允许「筛选了 A 游戏，图表还是全样本聚合」。
 *
 * 数据来源与口径：
 *   KPI        reviews-daily（评论数/推荐率）+ sentiment-daily（负向）+ risk-daily（风险指数）
 *   情感趋势   sentiment-daily，按 appid 过滤后逐日聚合
 *   成就       steam-funnels，按 appid 过滤；标题已写明「非留存」
 *   时长分层   reviews-daily 的 days[].playtime 分桶
 *   主题       topics-daily，附带 coverage / n_matched / 命中样本
 * ========================================================================== */
(function () {
    'use strict';

    var A = window.DashboardAdapters;
    var RD = window.RealData;
    // 两个依赖缺一不可：没有适配层就没有「缺失即 unavailable」的口径保证
    if (!RD || !A) return;

    var docs = { reviews: null, sentiment: null, risk: null, topics: null, funnels: null };
    // Phase D：筛选状态来自 URL，刷新与分享都不会丢
    var urlState = (window.UrlState && window.UrlState.parse(location.search)) || {};
    var appid = urlState.appid || '';
    var pendingTopic = urlState.topic || '';
    var trendChart = null, funnelChart = null;

    function $(id) { return document.getElementById(id); }
    function esc(t) {
        return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function fmt(n) {
        if (n === null || n === undefined || isNaN(n)) return '—';
        return Number(n).toLocaleString('en-US');
    }

    /* ---------------- 游戏下拉 ---------------- */
    function buildPicker() {
        var sel = $('gamePick');
        if (!sel) return;
        var seen = {}, list = [];
        [docs.sentiment, docs.reviews].forEach(function (d) {
            ((d && d.doc && d.doc.games) || []).forEach(function (g) {
                if (!g || !g.appid || seen[g.appid]) return;
                seen[g.appid] = true;
                list.push({ appid: String(g.appid), name: g.name || String(g.appid) });
            });
        });
        list.sort(function (a, b) { return a.name.localeCompare(b.name); });
        sel.innerHTML = '<option value="">全部游戏</option>' + list.map(function (g) {
            return '<option value="' + g.appid + '">' + esc(g.name) + '</option>';
        }).join('');
        sel.value = appid;

        if (!sel.__bound) {
            sel.__bound = true;
            sel.addEventListener('change', function () {
                appid = sel.value;
                pendingTopic = '';          // 换游戏后原来的主题不再适用
                renderAll();
                if (window.UrlState) window.UrlState.replace(window, { appid: appid });
            });
        }
    }

    function nameOf() {
        if (!appid) return '全部游戏';
        var sel = $('gamePick');
        var opt = sel && sel.options[sel.selectedIndex];
        return opt ? opt.textContent : appid;
    }

    /* ---------------- KPI ---------------- */
    function renderKpi() {
        var rev = docs.reviews && docs.reviews.state === 'ready' ? docs.reviews.doc : null;
        var sen = docs.sentiment && docs.sentiment.state === 'ready' ? docs.sentiment.doc : null;
        var risk = docs.risk && docs.risk.state === 'ready' ? docs.risk.doc : null;

        var reviews = rev ? (rev.games || []).filter(function (g) {
            return !appid || String(g.appid) === appid;
        }) : [];
        var totals = reviews.reduce(function (acc, g) {
            var t = g.totals || {};
            acc.n += t.total || 0; acc.pos += t.positive || 0; acc.neg += t.negative || 0;
            return acc;
        }, { n: 0, pos: 0, neg: 0 });

        var sent = A.sentimentTotals(sen, appid ? Number(appid) : null);
        var rk = null;
        if (risk) {
            var rows = (risk.games || []).filter(function (g) {
                return !appid || String(g.appid) === appid;
            });
            rk = rows.length ? rows.reduce(function (m, g) {
                return (g.risk_index || 0) > (m.risk_index || 0) ? g : m;
            }) : null;
        }

        var set = function (id, value) { var el = $(id); if (el) el.textContent = value; };
        set('kpiReviews', totals.n ? fmt(totals.n) : '—');
        set('kpiPos', totals.n ? (totals.pos / totals.n * 100).toFixed(1) + '%' : '—');
        set('kpiNeg', sent.state === 'ready' ? sent.value.negRate.toFixed(1) + '%' : '—');
        set('kpiRisk', rk && rk.risk_index != null ? Number(rk.risk_index).toFixed(1) : '—');

        var foot = function (id, text) {
            var el = $(id);
            if (el) el.innerHTML = '<span>' + esc(text) + '</span>';
        };
        foot('kpiReviewsFoot', nameOf() + (rev ? '' : ' · 数据不可用'));
        foot('kpiPosFoot', 'voted_up 占比 · n=' + fmt(totals.n));
        foot('kpiNegFoot', sent.state === 'ready' ? '模型判定 · n=' + fmt(sent.value.n) : '情感数据不可用');
        foot('kpiRiskFoot', rk ? (rk.status === 'ok' ? '已计算 · 分量 ' + (rk.components_used || []).length + ' 项' : '部分分量不可用') : '风险数据不可用');
    }

    /* ---------------- 情感趋势（按 appid 过滤） ---------------- */
    function renderTrend() {
        var el = $('trendChart');
        var srcEl = $('trendSrc');
        if (!el) return;
        if (!window.echarts) return;

        var doc = docs.sentiment && docs.sentiment.state === 'ready' ? docs.sentiment.doc : null;
        if (!doc) {
            if (srcEl) srcEl.textContent = '情感数据不可用';
            el.innerHTML = '<div class="rd-state">情感数据不可用</div>';
            return;
        }
        var games = (doc.games || []).filter(function (g) {
            return !appid || String(g.appid) === appid;
        });
        var byDate = {};
        games.forEach(function (g) {
            (g.days || []).forEach(function (d) {
                var a = byDate[d.date] || (byDate[d.date] = { n: 0, pos: 0, neg: 0, neu: 0 });
                a.n += d.n || 0; a.pos += d.pos || 0; a.neg += d.neg || 0; a.neu += d.neu || 0;
            });
        });
        var cutoff = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);
        var keys = Object.keys(byDate).filter(function (k) {
            return k >= cutoff && byDate[k].n >= 5;
        }).sort();

        var old = window.echarts.getInstanceByDom(el);
        if (old) old.dispose();
        trendChart = window.echarts.init(el, null, { renderer: 'canvas' });

        if (!keys.length) {
            el.innerHTML = '<div class="rd-state">该游戏在近 90 天内没有足够样本（每日 ≥5 条）</div>';
            if (srcEl) srcEl.textContent = nameOf() + ' · 样本不足';
            return;
        }

        var pct = function (k, f) { return +(byDate[k][f] / byDate[k].n * 100).toFixed(1); };
        trendChart.setOption({
            grid: { left: 44, right: 20, top: 26, bottom: 30 },
            tooltip: { trigger: 'axis' },
            legend: { right: 0, top: 0, itemWidth: 8, itemHeight: 8, icon: 'circle',
                      textStyle: { color: '#9EB1CA', fontSize: 9 } },
            xAxis: { type: 'category', data: keys.map(function (k) { return k.slice(5); }),
                     axisLabel: { color: '#7B8AA0', fontSize: 9, hideOverlap: true },
                     axisLine: { lineStyle: { color: 'rgba(122,166,255,.18)' } } },
            yAxis: { type: 'value', min: 0, max: 100, name: '%',
                     axisLabel: { color: '#7B8AA0', fontSize: 9 },
                     splitLine: { lineStyle: { color: 'rgba(122,166,255,.10)', type: 'dashed' } } },
            series: [
                { name: '正面', type: 'line', smooth: .3, symbol: 'none',
                  data: keys.map(function (k) { return pct(k, 'pos'); }),
                  lineStyle: { width: 2, color: '#34D399' }, itemStyle: { color: '#34D399' } },
                { name: '中性', type: 'line', smooth: .3, symbol: 'none',
                  data: keys.map(function (k) { return pct(k, 'neu'); }),
                  lineStyle: { width: 1.4, color: '#8A93A5' }, itemStyle: { color: '#8A93A5' } },
                { name: '负面', type: 'line', smooth: .3, symbol: 'none',
                  data: keys.map(function (k) { return pct(k, 'neg'); }),
                  lineStyle: { width: 2, color: '#F0536B' }, itemStyle: { color: '#F0536B' } }
            ]
        }, true);

        if (srcEl) {
            var total = keys.reduce(function (s, k) { return s + byDate[k].n; }, 0);
            srcEl.textContent = nameOf() + ' · 日均样本 ' + Math.round(total / keys.length) +
                ' 条 · 近 90 天 · 仅统计每日 ≥5 条的日期';
        }
    }

    /* ---------------- 成就（非留存） ---------------- */
    function renderFunnels() {
        var doc = docs.funnels && docs.funnels.state === 'ready' ? docs.funnels.doc : null;
        var listEl = $('funnelList');
        var chartEl = $('funnelChart');
        if (!listEl) return;

        if (!doc) {
            listEl.innerHTML = '<div class="reason-item"><span>成就数据不可用</span></div>';
            if (chartEl) chartEl.innerHTML = '<div class="rd-state">成就数据不可用</div>';
            return;
        }
        var games = (doc.games || []).filter(function (g) {
            return !appid || String(g.appid) === appid;
        }).filter(function (g) { return (g.achievements || []).length; });

        if (!games.length) {
            listEl.innerHTML = '<div class="reason-item"><span>该游戏没有成就数据</span></div>';
            if (chartEl) chartEl.innerHTML = '<div class="rd-state">该游戏没有成就数据</div>';
            return;
        }

        var g = games[0];
        // 图表 ~110px、列表 ~108px：取 4 个才既看得清、又不出现「图 5 条列表 4 条」的不一致
        var achs = (g.achievements || []).slice(0, 4);
        listEl.innerHTML = achs.map(function (a) {
            return '<div class="reason-item"><span class="reason-dot" style="--c:' + esc(g.color || '#22D3EE') + '"></span>' +
                '<span>' + esc(a.n) + '</span><span class="val">' + Number(a.p).toFixed(1) + '%</span></div>';
        }).join('') +
            // 说明文字不能塞进 .reason-item：那是多列网格，单行文字会被挤成竖排
            '<p style="grid-column:1/-1;margin:2px 0 0;font-size:9.5px;line-height:1.5;' +
            'white-space:normal;color:#7B8AA0">' +
            '成就解锁比例反映的是推进深度，不是 D1/D7/D30 留存率。</p>';

        if (chartEl && window.echarts) {
            var old = window.echarts.getInstanceByDom(chartEl);
            if (old) old.dispose();
            funnelChart = window.echarts.init(chartEl, null, { renderer: 'canvas' });
            funnelChart.setOption({
                grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
                tooltip: { trigger: 'axis' },
                xAxis: { type: 'value', max: 100, axisLabel: { color: '#7B8AA0', fontSize: 9 },
                         splitLine: { lineStyle: { color: 'rgba(122,166,255,.10)', type: 'dashed' } } },
                yAxis: { type: 'category', inverse: true,
                         data: achs.map(function (a) { return String(a.n).slice(0, 12); }),
                         axisLabel: { color: '#9EB1CA', fontSize: 9 } },
                series: [{ type: 'bar', data: achs.map(function (a) { return Number(a.p); }),
                           barWidth: 9, itemStyle: { color: '#F5A524', borderRadius: [0, 4, 4, 0] } }]
            }, true);
        }
    }

    /* ---------------- 时长分层 ---------------- */
    function renderPlaytime() {
        var rowsEl = $('playtimeRows');
        var srcEl = $('playtimeSrc');
        if (!rowsEl) return;

        var doc = docs.reviews && docs.reviews.state === 'ready' ? docs.reviews.doc : null;
        if (!doc) {
            rowsEl.innerHTML = '<div class="rd-state">评论聚合数据不可用</div>';
            if (srcEl) srcEl.textContent = '数据不可用';
            return;
        }
        var games = (doc.games || []).filter(function (g) {
            return !appid || String(g.appid) === appid;
        });
        var buckets = {}, total = 0;
        games.forEach(function (g) {
            (g.days || []).forEach(function (d) {
                var pt = d.playtime || {};
                Object.keys(pt).forEach(function (k) {
                    buckets[k] = (buckets[k] || 0) + (pt[k] || 0);
                    total += pt[k] || 0;
                });
            });
        });
        var keys = Object.keys(buckets);
        if (!keys.length || !total) {
            rowsEl.innerHTML = '<div class="rd-state">该范围内没有带时长的评论样本</div>';
            if (srcEl) srcEl.textContent = nameOf() + ' · 无样本';
            return;
        }
        var ORDER = ['<1h', '1-10h', '10-50h', '50-100h', '100h+'];
        keys.sort(function (a, b) {
            var ia = ORDER.indexOf(a), ib = ORDER.indexOf(b);
            return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
        });
        var C = ['#22D3EE', '#34D399', '#F5A524', '#F0536B', '#A78BFA'];
        rowsEl.innerHTML = keys.map(function (k, i) {
            var share = buckets[k] / total * 100;
            return '<div class="row"><span class="row-name">' + esc(k) + '</span>' +
                '<span class="row-track"><span class="row-fill" style="--c:' + C[i % C.length] +
                ';width:' + share.toFixed(1) + '%"></span></span>' +
                '<span class="row-num">' + share.toFixed(1) + '%</span></div>';
        }).join('');
        if (srcEl) {
            srcEl.textContent = nameOf() + ' · 样本 ' + fmt(total) + ' 条 · 按评论者游戏时长分桶';
        }
    }

    /* ---------------- 主题 ---------------- */
    function renderTopics() {
        var rowsEl = $('topicRows');
        var srcEl = $('topicSrc');
        if (!rowsEl) return;

        var doc = docs.topics && docs.topics.state === 'ready' ? docs.topics.doc : null;
        if (!doc) {
            rowsEl.innerHTML = '<div class="rd-state">主题数据不可用</div>';
            if (srcEl) srcEl.textContent = '数据不可用';
            return;
        }
        var games = (doc.games || []).filter(function (g) {
            return !appid || String(g.appid) === appid;
        }).filter(function (g) { return (g.topics || []).length; });

        if (!games.length) {
            rowsEl.innerHTML = '<div class="rd-state">' +
                (appid ? '该游戏没有命中任何主题（覆盖率 0）' : '样本中未命中任何主题') + '</div>';
            if (srcEl) srcEl.textContent = nameOf() + ' · 无主题命中';
            return;
        }
        var g = games.sort(function (a, b) { return (b.n_reviews || 0) - (a.n_reviews || 0); })[0];
        if (srcEl) {
            srcEl.textContent = g.name + ' · n=' + fmt(g.n_reviews) + ' · 命中 ' + fmt(g.n_matched) +
                ' · 覆盖率 ' + Math.round((g.coverage || 0) * 100) + '% · 规则词典候选';
        }
        window.__topicEvidence = {};
        // 一屏布局：主题只展示 4 条，其余不渲染（面板内不滚动，避免整页被顶出视口）
        rowsEl.innerHTML = g.topics.slice(0, 4).map(function (t) {
            var lv = t.share >= 20 ? '#F0536B' : (t.share >= 10 ? '#F5A524' : '#22D3EE');
            var ev = t.evidence || t.examples || t.keywords || [];
            var evText = Array.isArray(ev) && ev.length ? ev.slice(0, 3).join(' / ') : '';
            window.__topicEvidence[t.label] = evText;
            return '<div class="corr" data-topic="' + esc(t.label) + '" role="button" tabindex="0"' +
                ' title="' + esc(evText || '该主题没有逐条证据字段') + '">' +
                '<span class="row-name" title="' + esc(t.label) + (t.low_sample ? '（样本不足）' : '') + '">' +
                esc(t.label) + '</span>' +
                '<span class="row-track"><span class="row-fill" style="--c:' + lv +
                ';width:' + Math.min(100, t.share) + '%"></span></span>' +
                '<span class="row-num">' + t.share + '%</span>' +
                '<span class="level" style="--lv:' + lv + '">' +
                (t.share >= 20 ? '高' : (t.share >= 10 ? '中' : '低')) + '</span></div>';
        }).join('');
    }

    /* 主题点击 → 展开该主题的证据（来自 topics-daily 的 evidence/examples/keywords 字段）。
       没有这些字段时如实说明，不编造评论原文。 */
    (function bindTopicClick() {
        var rowsEl = $('topicRows');
        if (!rowsEl || rowsEl.__bound) return;
        rowsEl.__bound = true;
        rowsEl.addEventListener('click', function (ev) {
            var row = ev.target.closest('[data-topic]');
            if (!row) return;
            var box = $('topicEvidenceBox');
            if (!box) {
                box = document.createElement('div');
                box.id = 'topicEvidenceBox';
                box.style.cssText = 'grid-column:1/-1;margin:10px 0 0;font-size:10px;' +
                    'line-height:1.7;color:#9EB1CA;border-left:2px solid rgba(245,165,36,.6);' +
                    'padding-left:8px';
                rowsEl.parentNode.insertBefore(box, rowsEl.nextSibling);
            }
            var label = row.dataset.topic;
            var text = (window.__topicEvidence || {})[label];
            box.textContent = text
                ? '「' + label + '」证据样本：' + text
                : '「' + label + '」在 topics-daily.json 中没有逐条证据字段，只能看到占比';
        });
    })();

    function renderAll() {
        renderKpi();
        renderTrend();
        renderFunnels();
        renderPlaytime();
        renderTopics();
    }

    function load(name, key) {
        return RD.load(name).then(function (r) { docs[key] = r; })
            .catch(function () { docs[key] = { state: 'unavailable' }; });
    }

    /* URL 带来的主题：渲染后定位到该行并展开证据，让「从总览点进来」有落点 */
    function focusTopic() {
        if (!pendingTopic) return;
        var rows = document.querySelectorAll('#topicRows [data-topic]');
        for (var i = 0; i < rows.length; i++) {
            if (rows[i].dataset.topic === pendingTopic) {
                rows[i].click();
                if (rows[i].scrollIntoView) rows[i].scrollIntoView({ block: 'nearest' });
                return;
            }
        }
    }

    Promise.all([
        load('reviews-daily.json', 'reviews'),
        load('sentiment-daily.json', 'sentiment'),
        load('risk-daily.json', 'risk'),
        load('topics-daily.json', 'topics'),
        load('steam-funnels.json', 'funnels')
    ]).then(function () {
        buildPicker();
        renderAll();
        if (pendingTopic) setTimeout(focusTopic, 60);
        // 页面原有脚本也会请求一次 funnels（它是 defer 前执行的，返回时机不确定），
        // 可能在本次渲染之后覆盖成就面板。这里延迟再刷一次，确保 Phase C 的口径最终生效。
        // 页面既有脚本也会重绘趋势图（它按全样本聚合），可能在本次渲染后覆盖回来。
        // 延迟再刷一次，保证面板口径与当前筛选一致。
        setTimeout(function () { renderFunnels(); renderKpi(); renderTrend(); }, 1200);
    });

    window.addEventListener('resize', function () {
        if (trendChart) trendChart.resize();
        if (funnelChart) funnelChart.resize();
    });

    /* ---------------- Phase E：版本公告时间线 ----------------
       数据来自知识库收藏的 Steam 官方公告（/api/knowledge/documents?sort=publish_date）。
       排序用发布时间而不是导入时间，否则 109 篇一次性灌入的记录顺序毫无意义。 */
    (function bindEvents() {
        var btn = $('eventBtn');
        var modal = $('eventModal');
        var listEl = $('eventList');
        if (!btn || !modal || !listEl) return;

        var API = location.protocol + '//' + location.hostname + ':5000';

        function close() { modal.style.display = 'none'; }

        function show() {
            modal.style.display = 'flex';
            listEl.innerHTML = '<div class="rd-state">加载中…</div>';

            var query = '/api/knowledge/documents?source_type=announcement&limit=30&sort=publish_date' +
                (appid ? '&app_id=' + encodeURIComponent(appid) : '');

            fetch(API + query, { cache: 'no-store' })
                .then(function (res) { return res.json(); })
                .then(function (body) {
                    var items = (body.data && body.data.items) || [];
                    var title = $('eventTitle');
                    if (title) {
                        title.textContent = '版本公告时间线 · ' + nameOf() + '（' + items.length + ' 条）';
                    }
                    if (!items.length) {
                        listEl.innerHTML = '<div class="rd-state">没有该游戏的公告记录</div>';
                        return;
                    }
                    listEl.innerHTML = items.map(function (it) {
                        var date = String(it.publish_date || '').slice(0, 10) || '日期未知';
                        var link = it.source_url
                            ? ' <a href="' + esc(it.source_url) + '" target="_blank" rel="noopener"' +
                              ' style="color:#22D3EE;text-decoration:none">原文</a>'
                            : '';
                        return '<div style="display:flex;gap:10px;padding:7px 0;' +
                            'border-bottom:1px dashed rgba(122,166,255,.12);font-size:11.5px">' +
                            '<span style="flex:none;font-family:var(--mono);color:#7B8AA0">' + esc(date) + '</span>' +
                            '<span style="color:#DCE6F5">' + esc(it.title || '(无标题)') + link + '</span></div>';
                    }).join('');
                })
                .catch(function (err) {
                    listEl.innerHTML = '<div class="rd-state">公告读取失败：' + esc(err.message) +
                        '（后端 5000 是否已启动？）</div>';
                });
        }

        btn.addEventListener('click', show);
        $('eventClose').addEventListener('click', close);
        $('eventBackdrop').addEventListener('click', close);
        document.addEventListener('keydown', function (ev) {
            if (ev.key === 'Escape') close();
        });
    })();

    window.AnalysisPhaseC = { render: renderAll };
})();
