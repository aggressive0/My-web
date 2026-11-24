/* ============================================================================
 * DashboardAdapters —— 真实数据的唯一解释层（Phase A）
 *
 * 设计约束（来自 docs/REAL_DATA_DASHBOARD_REFACTOR.md）：
 *   1. 纯函数：输入是 RealData 取回的 JSON，输出是视图模型，不发请求、不读 DOM。
 *   2. 缺失即缺失：任何一个字段拿不到，就把对应指标标成 unavailable，
 *      **不用 0 填充、不用示例值兜底**。0 和「没数据」在业务上是两件事。
 *   3. 每个返回值都带口径：来源文件、统计窗口、样本量、更新时间。
 *      没有这些的指标，不允许出现在页面上。
 *
 * 这个文件同时支持浏览器与 Node（测试用 require 直接引用）。
 * ========================================================================== */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.DashboardAdapters = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    function unavailable(reason, extra) {
        var out = { state: 'unavailable', value: null, reason: reason || '数据不可用' };
        if (extra) { for (var k in extra) { if (extra.hasOwnProperty(k)) out[k] = extra[k]; } }
        return out;
    }

    function ready(value, meta) {
        var out = { state: 'ready', value: value };
        if (meta) { for (var k in meta) { if (meta.hasOwnProperty(k)) out[k] = meta[k]; } }
        return out;
    }

    function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }

    function asOf(doc) {
        return (doc && (doc.updated_at || doc.generated_at)) || null;
    }

    /* ---------------- CCU ---------------- */

    /** 单款游戏的最新值与 24h 变化率。变化率算不出就返回 null，不猜。 */
    function ccuDelta(game) {
        var points = (game && Array.isArray(game.points)) ? game.points.slice() : [];
        if (!points.length) return { latest: null, changePct: null, points: [] };
        points.sort(function (a, b) { return String(a.ts).localeCompare(String(b.ts)); });
        var last = points[points.length - 1];
        var latest = num(last && last.p);
        var latestTs = Date.parse(last && last.ts);
        var changePct = null;
        if (latest !== null && isFinite(latestTs)) {
            var target = latestTs - 24 * 3600 * 1000;
            var best = null, bestGap = Infinity;
            for (var i = 0; i < points.length - 1; i++) {
                var t = Date.parse(points[i].ts);
                if (!isFinite(t)) continue;
                var gap = Math.abs(t - target);
                if (gap < bestGap) { bestGap = gap; best = points[i]; }
            }
            // 只接受 24h±6h 内的基准点，否则说明数据有断档，算出来的变化率没有意义
            if (best && bestGap <= 6 * 3600 * 1000 && num(best.p)) {
                changePct = (latest - best.p) / best.p * 100;
            }
        }
        return { latest: latest, changePct: changePct, points: points };
    }

    function ccuSeries(doc, days) {
        if (!doc || !Array.isArray(doc.games) || !doc.games.length) {
            return unavailable('steam-ccu.json 未取到', { source: 'Steam ISteamUserStats' });
        }
        var cutoff = Date.now() - (days || 7) * 86400000;
        var series = doc.games.filter(function (g) { return g && g.enabled !== false; })
            .map(function (g) {
                var delta = ccuDelta(g);
                return {
                    appid: g.appid, name: g.name, color: g.color,
                    points: delta.points.filter(function (p) {
                        return Date.parse(p.ts) >= cutoff;
                    }),
                    latest: delta.latest,
                    changePct: delta.changePct
                };
            })
            .filter(function (g) { return g.points.length; });
        if (!series.length) return unavailable('窗口内没有任何 CCU 采样点');
        return ready(series, {
            source: 'Steam ISteamUserStats', asOf: asOf(doc),
            window: '最近 ' + (days || 7) + ' 天',
            note: 'GitHub data 分支快照，非秒级实时'
        });
    }

    /* ---------------- 风险指数 ---------------- */

    function riskRanking(doc) {
        if (!doc || !Array.isArray(doc.games) || !doc.games.length) {
            return unavailable('risk-daily.json 未取到');
        }
        var rows = doc.games.map(function (g) {
            return {
                appid: g.appid, name: g.name, color: g.color,
                index: num(g.risk_index), status: g.status || 'unknown',
                components: g.components || {},
                used: g.components_used || Object.keys(g.components || {})
                    .filter(function (k) { return g.components[k] &&
                        g.components[k].status !== 'unavailable'; })
            };
        }).filter(function (r) { return r.index !== null; });
        if (!rows.length) return unavailable('风险指数全部为不可用');
        rows.sort(function (a, b) { return b.index - a.index; });
        return ready(rows, {
            asOf: asOf(doc),
            definition: (doc.definition && doc.definition.name) || '风险指数',
            formula: (doc.definition && doc.definition.formula) || '',
            caveat: (doc.definition && doc.definition.caveat) || ''
        });
    }

    /* ---------------- 语言市场 ---------------- */

    function markets(doc) {
        if (!doc || !Array.isArray(doc.markets) || !doc.markets.length) {
            return unavailable('globe-daily.json 未取到');
        }
        var rows = doc.markets.map(function (m) {
            return {
                id: m.id, appid: m.appid, game: m.game, language: m.language,
                name: m.name, nameEn: m.name_en,
                lat: num(m.latitude), lng: num(m.longitude),
                region: m.region, reviewCount: num(m.review_count),
                // 把其余数值字段原样带出，避免适配器猜业务含义
                metrics: Object.keys(m).reduce(function (acc, k) {
                    if (typeof m[k] === 'number' && ['latitude', 'longitude', 'review_count'].indexOf(k) < 0) {
                        acc[k] = m[k];
                    }
                    return acc;
                }, {})
            };
        }).filter(function (m) { return m.lat !== null && m.lng !== null; });
        if (!rows.length) return unavailable('市场点位缺少经纬度');
        return ready(rows, {
            asOf: asOf(doc),
            metric: doc.metric, periodDays: doc.period_days,
            sampleSize: doc.sample_size, minimumReviews: doc.minimum_reviews,
            // 这条必须常驻：语言市场不是玩家所在地
            caveat: '按评论语言划分的市场，不代表玩家真实所在地'
        });
    }

    /* ---------------- 情感 ---------------- */

    function sentimentTotals(sentiment, appid) {
        if (!sentiment || !Array.isArray(sentiment.games) || !sentiment.games.length) {
            return unavailable('sentiment-daily.json 未取到');
        }
        var games = sentiment.games;
        if (appid) {
            games = games.filter(function (g) { return g.appid === appid; });
            if (!games.length) return unavailable('该游戏没有情感数据', { appid: appid });
        }
        var scope = appid ? String(appid) : 'all';
        var n = 0, pos = 0, neg = 0, neu = 0, covered = 0;
        games.forEach(function (g) {
            var t = g.totals || {};
            n += num(t.n) || 0; pos += num(t.pos) || 0;
            neg += num(t.neg) || 0; neu += num(t.neu) || 0;
            if (g.coverage && num(g.coverage.days)) covered += g.coverage.days;
        });
        if (!n) return unavailable('没有带文本的评论样本', { scope: scope });
        return ready({
            scope: scope, games: games.length, n: n, pos: pos, neg: neg, neu: neu,
            posRate: pos / n * 100, negRate: neg / n * 100
        }, {
            asOf: asOf(sentiment),
            model: (sentiment.model && sentiment.model.embedding_model) || '',
            note: appid ? '单款游戏' : '全样本聚合（未按游戏过滤）'
        });
    }

    /* ---------------- 成就 ---------------- */

    function funnels(doc, appid) {
        if (!doc || !Array.isArray(doc.games) || !doc.games.length) {
            return unavailable('steam-funnels.json 未取到');
        }
        var games = appid
            ? doc.games.filter(function (g) { return g.appid === appid; })
            : doc.games;
        if (!games.length) return unavailable('该游戏没有成就数据', { appid: appid });
        return ready(games, {
            asOf: asOf(doc),
            caveat: '成就完成率是玩家行为指标，不是 D1/D7/D30 留存率'
        });
    }

    /* ---------------- 采集状态 ---------------- */

    function freshness(doc, now) {
        if (!doc) return unavailable('collection-status.json 未取到');
        var updated = doc.updated_at;
        var t = Date.parse(updated);
        if (!isFinite(t)) return unavailable('采集状态缺少有效时间戳');
        var hours = ((now || Date.now()) - t) / 3600000;
        var state = hours <= 36 ? 'fresh' : (hours <= 24 * 7 ? 'stale' : 'expired');
        return ready({
            updatedAt: updated, ageHours: hours, state: state,
            games: (doc.games || []).map(function (g) {
                return {
                    appid: g.appid, name: g.name, fetched: num(g.fetched),
                    added: num(g.added), error: g.error || null
                };
            })
        }, { source: '采集工作流' });
    }

    return {
        unavailable: unavailable,
        ready: ready,
        ccuDelta: ccuDelta,
        ccuSeries: ccuSeries,
        riskRanking: riskRanking,
        markets: markets,
        sentimentTotals: sentimentTotals,
        funnels: funnels,
        freshness: freshness
    };
});
