/* =========================================================================
   dashboard-data.js — 首页数据适配层（Mock）
   规范：
   1. 所有数值为演示数据，meta.dataSource 明确标记 mock；
   2. 时间范围（7/30/90 天）与区域筛选统一从这里出口，组件不自行造数；
   3. 真实接口就绪后，只需替换本文件的取数函数，UI 层无需改动。
   ========================================================================= */
(function (global) {
    'use strict';

    var END_DATE = new Date(2026, 10, 14);       // 数据截止日
    var RANGE_DAYS = { '7d': 7, '30d': 30, '90d': 90 };

    /* 区域对情感/流失的偏移因子（用于生成区域视图，非真实统计） */
    var REGION_FACTOR = {
        na: { sent: -0.16, churn: 1.28 },
        eu: { sent: -0.05, churn: 1.05 },
        ap: { sent: 0.12, churn: 0.82 },
        sa: { sent: 0.02, churn: 0.90 },
        me: { sent: 0.06, churn: 0.75 }
    };

    var META = {
        dataSource: 'mock',
        generatedAt: '2026-11-14T15:20:00+08:00',
        timezone: 'Asia/Shanghai'
    };

    function pad(n) { return String(n).padStart(2, '0'); }
    function fmt(d) { return (d.getMonth() + 1) + '.' + pad(d.getDate()); }
    function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }

    /** 时间窗口：返回给头部显示，也用于 API 查询参数 */
    function windowOf(range) {
        var days = RANGE_DAYS[range] || 30;
        var start = new Date(END_DATE.getTime() - (days - 1) * 86400000);
        return { start: iso(start), end: iso(END_DATE), days: days, label: fmt(start) + ' – ' + fmt(END_DATE) };
    }

    /* ---------------- 区域 ---------------- */
    /* 区域按时间范围缩放：保证排行、信息卡、地球读同一份数 */
    var RANGE_SCALE = {
        '7d':  { churn: 0.94, atRisk: 0.94 },
        '30d': { churn: 1.00, atRisk: 1.00 },
        '90d': { churn: 1.08, atRisk: 1.15 }
    };
    var activeRange = '30d';
    var regionCache = {};

    var REGIONS = [
        { id: 'na', name: '北美', churnRate: 7.8, change: 1.2,  atRisk: 3120, lat: 40,  lng: -100, risk: 'high' },
        { id: 'eu', name: '欧洲', churnRate: 6.1, change: 0.8,  atRisk: 2340, lat: 50,  lng: 12,   risk: 'medium' },
        { id: 'ap', name: '亚太', churnRate: 4.9, change: -0.3, atRisk: 1860, lat: 26,  lng: 116,  risk: 'low' },
        { id: 'sa', name: '南美', churnRate: 4.7, change: -0.5, atRisk: 940,  lat: -14, lng: -56,  risk: 'low' },
        { id: 'me', name: '中东', churnRate: 3.2, change: -0.1, atRisk: 520,  lat: 25,  lng: 45,   risk: 'low' }
    ];

    /* ---------------- 玩家分布点（真实经纬度） ---------------- */
    var PLAYER_POINTS = [
        { id: 'p1',  lat: 40.7,  lng: -74.0,  players: 128000, risk: 'high' },
        { id: 'p2',  lat: 34.0,  lng: -118.2, players: 96000,  risk: 'medium' },
        { id: 'p3',  lat: 41.9,  lng: -87.6,  players: 61000,  risk: 'medium' },
        { id: 'p4',  lat: 51.5,  lng: -0.1,   players: 88000,  risk: 'medium' },
        { id: 'p5',  lat: 48.9,  lng: 2.35,   players: 54000,  risk: 'low' },
        { id: 'p6',  lat: 55.8,  lng: 37.6,   players: 47000,  risk: 'medium' },
        { id: 'p7',  lat: 31.2,  lng: 121.5,  players: 142000, risk: 'low' },
        { id: 'p8',  lat: 39.9,  lng: 116.4,  players: 118000, risk: 'low' },
        { id: 'p9',  lat: 22.3,  lng: 114.2,  players: 76000,  risk: 'low' },
        { id: 'p10', lat: 35.7,  lng: 139.7,  players: 83000,  risk: 'low' },
        { id: 'p11', lat: 37.6,  lng: 127.0,  players: 64000,  risk: 'low' },
        { id: 'p12', lat: -23.5, lng: -46.6,  players: 58000,  risk: 'medium' },
        { id: 'p13', lat: -34.6, lng: -58.4,  players: 32000,  risk: 'low' },
        { id: 'p14', lat: 25.2,  lng: 55.3,   players: 41000,  risk: 'low' },
        { id: 'p15', lat: 19.1,  lng: 72.9,   players: 52000,  risk: 'low' },
        { id: 'p16', lat: -33.9, lng: 151.2,  players: 27000,  risk: 'low' }
    ];

    /* ---------------- 热点 ---------------- */
    var HOTSPOTS = [
        {
            id: 'h1', name: '竞技平衡性争议', regionId: 'na', lat: 40, lng: -100,
            heat: 320, sentiment: -0.42, relatedPlayers: 18200, category: '版本内容',
            negativeRate: 68, negativeChange: 12.4, affectedPlayers: 8420, risk: 'high',
            response: { name: '定向回流活动', expectedRecoveryRate: 18 }
        },
        {
            id: 'h2', name: '活动奖励调整', regionId: 'eu', lat: 50, lng: 12,
            heat: 186, sentiment: -0.18, relatedPlayers: 9600, category: '运营活动',
            negativeRate: 54, negativeChange: 6.1, affectedPlayers: 3100, risk: 'medium',
            response: { name: '补偿礼包 + 预告说明', expectedRecoveryRate: 12 }
        },
        {
            id: 'h3', name: '新赛季玩法讨论', regionId: 'ap', lat: 26, lng: 116,
            heat: 500, sentiment: 0.36, relatedPlayers: 24600, category: '版本内容',
            negativeRate: 22, negativeChange: -8.3, affectedPlayers: 1200, risk: 'low',
            response: { name: 'UGC 内容共创', expectedRecoveryRate: 8 }
        }
    ];

    /* ---------------- 迁移弧线 ---------------- */
    var CHURN_ARCS = [
        { id: 'a1', from: 'na', to: 'eu', count: 1240, risk: 'high' },
        { id: 'a2', from: 'na', to: 'sa', count: 860,  risk: 'medium' },
        { id: 'a3', from: 'eu', to: 'me', count: 620,  risk: 'medium' },
        { id: 'a4', from: 'ap', to: 'sa', count: 410,  risk: 'low' }
    ];

    /* ---------------- KPI（按时间范围给出不同口径，全部为演示数据） ---------------- */
    var KPI_BASE = {
        '7d':  { active: 128.6, atRisk: 8254,  sentiment: 0.68,  recovery: 23.4, dActive: 3.1,  dAtRisk: -6.4,  dSent: 0.03, dRec: 0.9 },
        '30d': { active: 131.2, atRisk: 8780,  sentiment: 0.64,  recovery: 22.6, dActive: 6.2,  dAtRisk: -12.5, dSent: 0.07, dRec: 2.1 },
        '90d': { active: 124.4, atRisk: 10097, sentiment: 0.58,  recovery: 20.8, dActive: 11.8, dAtRisk: -18.2, dSent: 0.14, dRec: 4.6 }
    };

    function sparkOf(range, key) {
        var full = {
            active: [98, 101, 104, 99, 106, 110, 108, 112, 109, 115, 118, 114, 117, 121, 119, 123, 120, 124, 122, 126, 123, 127, 125, 128, 126, 129, 127, 130, 128, 131.2],
            atRisk: [12400, 12100, 12300, 11800, 11600, 11900, 11400, 11200, 10900, 10700, 10600, 10500, 10460, 10420, 10200, 10000, 9800, 9700, 9600, 9500, 9400, 9350, 9300, 9250, 9220, 9200, 9195, 9190, 9185, 9180],
            sentiment: [0.42, 0.45, 0.41, 0.48, 0.5, 0.47, 0.52, 0.56, 0.54, 0.58, 0.61, 0.64, 0.66, 0.63, 0.62, 0.64, 0.65, 0.63, 0.61, 0.62, 0.63, 0.64, 0.65, 0.64, 0.63, 0.64, 0.65, 0.64, 0.64, 0.64],
            recovery: [18.2, 18.6, 19.1, 19.4, 19.9, 20.2, 20.8, 21.1, 21.6, 22.0, 22.4, 21.8, 21.2, 20.9, 20.6, 20.4, 20.9, 21.4, 21.8, 22.1, 22.3, 22.2, 22.4, 22.5, 22.4, 22.5, 22.6, 22.6, 22.6, 22.6]
        }[key];
        var days = RANGE_DAYS[range] || 30;
        return full.slice(-days);
    }

    function kpis(range) {
        var b = KPI_BASE[range] || KPI_BASE['30d'];
        return {
            activePlayers: {
                key: 'activePlayers', label: '活跃玩家', value: b.active, unit: 'K',
                change: b.dActive, trend: 'up', status: 'normal',
                definition: '窗口内每日登录去重玩家的均值',
                spark: sparkOf(range, 'active')
            },
            atRiskPlayers: {
                key: 'atRiskPlayers', label: '高危玩家', value: b.atRisk, unit: '',
                change: b.dAtRisk, trend: 'down', status: 'warning',
                definition: '风险评分 ≥ 70 且 7 天未登录的玩家',
                spark: sparkOf(range, 'atRisk')
            },
            averageSentiment: {
                key: 'averageSentiment', label: '情感指数', value: b.sentiment, unit: '',
                change: b.dSent, trend: 'up', status: 'normal',
                definition: '评论文本情感极性均值，区间 -1 ~ 1',
                spark: sparkOf(range, 'sentiment')
            },
            recoveryRate: {
                key: 'recoveryRate', label: '召回率', value: b.recovery, unit: '%',
                change: b.dRec, trend: 'up', status: 'normal',
                definition: '被召回玩家 / 目标召回玩家',
                spark: sparkOf(range, 'recovery')
            }
        };
    }

    /* ---------------- 活跃 & 情感序列 ---------------- */

    /* ---------------- 留存追踪（演示数据） ---------------- */
    var RET_KPI = [
        { key: 'd1', label: '次日留存率', value: 36.8, unit: '%', change: 1.2, icon: 'users',
          spark: [30.2, 31.4, 30.8, 32.6, 33.1, 32.4, 34.0, 33.6, 34.8, 35.2, 34.6, 35.8, 36.2, 36.8] },
        { key: 'd7', label: '7 日留存率', value: 24.7, unit: '%', change: 0.5, icon: 'calendar',
          spark: [21.4, 22.0, 21.6, 22.8, 23.1, 22.6, 23.4, 23.0, 23.8, 24.1, 23.7, 24.3, 24.5, 24.7] },
        { key: 'd30', label: '30 日留存率', value: 12.1, unit: '%', change: 0.3, icon: 'crown',
          spark: [10.2, 10.5, 10.3, 10.9, 11.1, 10.8, 11.3, 11.0, 11.5, 11.7, 11.4, 11.8, 12.0, 12.1] },
        { key: 'duration', label: '平均游戏时长', value: 2.6, unit: 'h', change: 5.1, icon: 'clock',
          spark: [1.9, 2.0, 1.95, 2.1, 2.15, 2.05, 2.2, 2.25, 2.35, 2.4, 2.45, 2.5, 2.55, 2.6] }
    ];

    var RET_COHORT = [
        { period: '10.01 – 10.07', d1: 52, d3: 40, d7: 32, d14: 24, d30: 16 },
        { period: '10.08 – 10.14', d1: 49, d3: 37, d7: 30, d14: 22, d30: 14 },
        { period: '10.15 – 10.21', d1: 47, d3: 35, d7: 28, d14: 20, d30: 13 },
        { period: '10.22 – 10.28', d1: 45, d3: 33, d7: 26, d14: 19, d30: 12 },
        { period: '10.29 – 10.31', d1: 43, d3: 31, d7: 24, d14: 17, d30: 11 }
    ];

    var RET_SEGMENTS = {
        'd1': [
            { name: '核心玩家', rate: 62.4, change: 1.5, icon: 'crown', c: '#35D9F2' },
            { name: '活跃玩家', rate: 45.7, change: 0.8, icon: 'gem', c: '#318BFF' },
            { name: '普通玩家', rate: 28.3, change: 0.3, icon: 'lock', c: '#9275FF' },
            { name: '流失风险玩家', rate: 15.1, change: -0.7, icon: 'warn', c: '#FFB45F' }
        ],
        'd7': [
            { name: '核心玩家', rate: 48.2, change: 1.1, icon: 'crown', c: '#35D9F2' },
            { name: '活跃玩家', rate: 33.6, change: 0.4, icon: 'gem', c: '#318BFF' },
            { name: '普通玩家', rate: 19.4, change: 0.1, icon: 'lock', c: '#9275FF' },
            { name: '流失风险玩家', rate: 8.2, change: -0.4, icon: 'warn', c: '#FFB45F' }
        ],
        'd30': [
            { name: '核心玩家', rate: 26.8, change: 0.6, icon: 'crown', c: '#35D9F2' },
            { name: '活跃玩家', rate: 15.2, change: 0.2, icon: 'gem', c: '#318BFF' },
            { name: '普通玩家', rate: 7.6, change: 0.0, icon: 'lock', c: '#9275FF' },
            { name: '流失风险玩家', rate: 2.4, change: -0.2, icon: 'warn', c: '#FFB45F' }
        ]
    };

    var RET_FACTORS = [
        { name: '游戏热点热度', value: 35.2, c: '#9275FF' },
        { name: '玩家情感倾向', value: 28.6, c: '#35D9F2' },
        { name: '活动参与度', value: 22.1, c: '#FFB45F' },
        { name: '其他因素', value: 14.1, c: '#35D399' }
    ];

    var RET_INSIGHTS = [
        {
            level: '高优先级', tone: 'high',
            title: '近期中高风险玩家占比上升 12.3%',
            desc: '主要集中在北美地区 · 竞技类玩家群体',
            action: '对北美高风险分群启用定向回流活动'
        },
        {
            level: '建议', tone: 'mid',
            title: '加大热点活动投入',
            desc: '当前热门话题「新赛季」带动留存提升显著',
            action: '把新赛季内容前置到新手引导第 2 天'
        }
    ];

    /** 留存趋势：7/30/90 天三档，三条曲线 */
    function retentionTrend(range) {
        var days = RANGE_DAYS[range] || 30;
        var out = [];
        for (var i = days - 1; i >= 0; i--) {
            var d = new Date(END_DATE.getTime() - i * 86400000);
            var k = (days - 1 - i) / Math.max(days - 1, 1);
            var wave = Math.sin(k * 8.4);
            out.push({
                timestamp: fmt(d),
                d1: +(34.5 + k * 2.3 + wave * 2.1).toFixed(1),
                d7: +(22.8 + k * 1.9 + wave * 1.4).toFixed(1),
                d30: +(10.6 + k * 1.5 + wave * 0.8).toFixed(1)
            });
        }
        return out;
    }

    var RET = {
        kpis: RET_KPI,
        cohort: RET_COHORT,
        segments: RET_SEGMENTS,
        factors: RET_FACTORS,
        insights: RET_INSIGHTS,
        trend: retentionTrend
    };

    var cache = {};

    function buildActivity(days) {
        var out = [];
        for (var i = days - 1; i >= 0; i--) {
            var d = new Date(END_DATE.getTime() - i * 86400000);
            var k = (days - 1 - i) / Math.max(days - 1, 1);
            out.push({
                date: iso(d),
                timestamp: fmt(d),
                activePlayers: Math.round(98000 + k * 33200 + Math.sin(k * 9) * 2400),
                newPlayers: Math.round(3200 + k * 1800 + Math.sin(k * 7) * 260)
            });
        }
        return out;
    }

    function buildSentiment(days, regionId) {
        var f = (regionId && REGION_FACTOR[regionId]) || { sent: 0 };
        var out = [];
        for (var i = days - 1; i >= 0; i--) {
            var d = new Date(END_DATE.getTime() - i * 86400000);
            var k = (days - 1 - i) / Math.max(days - 1, 1);
            var positive = 62 + k * 6.7 + f.sent * 26;
            var negative = 10 - k * 1.1 - f.sent * 14;
            var neutral = 100 - positive - negative;
            // 归一化，保证三项合计 100
            var sum = positive + neutral + negative;
            positive = +(positive / sum * 100).toFixed(1);
            neutral = +(neutral / sum * 100).toFixed(1);
            out.push({
                date: iso(d),
                timestamp: fmt(d),
                positive: positive,
                neutral: neutral,
                negative: +(100 - positive - neutral).toFixed(1)
            });
        }
        return out;
    }

    function series(range, regionId) {
        var days = RANGE_DAYS[range] || 30;
        var key = days + '|' + (regionId || 'all');
        if (!cache[key]) {
            cache[key] = {
                activity: buildActivity(days),
                sentiment: buildSentiment(days, regionId)
            };
        }
        return cache[key];
    }

    /* ---------------- 预警与建议 ---------------- */
    var ALERTS = [
        {
            id: 'al1', title: '竞技平衡性讨论升温',
            description: '负面情绪占比 72% · 可能引发大规模流失',
            severity: 'high', regionId: 'na', hotspotId: 'h1',
            metric: { name: '负面情绪占比', currentValue: 72, previousValue: 59, unit: '%' },
            status: 'active', createdAt: '2026-11-14T11:20:00+08:00',
            rule: '负面占比 24h 增幅 > 10pp 且热度 > 300',
            suggestion: '对受影响玩家分批推送平衡性调整说明 + 定向补偿'
        },
        {
            id: 'al2', title: '北美地区流失抬头',
            description: '近 7 日流失率上升 1.5% · 需持续关注',
            severity: 'medium', regionId: 'na', hotspotId: 'h1',
            metric: { name: '流失率', currentValue: 7.8, previousValue: 6.3, unit: '%' },
            status: 'active', createdAt: '2026-11-14T09:05:00+08:00',
            rule: '区域流失率周环比 > 1.2pp',
            suggestion: '复核北美匹配池与本地化活动排期'
        },
        {
            id: 'al3', title: '新赛季话题热度冲高',
            description: '讨论量 500K · 可借势做回流内容',
            severity: 'low', regionId: 'ap', hotspotId: 'h3',
            metric: { name: '话题热度', currentValue: 500, previousValue: 180, unit: 'K' },
            status: 'active', createdAt: '2026-11-14T08:30:00+08:00',
            rule: '话题热度 24h 增幅 > 150%',
            suggestion: '联动 KOL 做新赛季内容共创'
        }
    ];

    var ACTIONS = [
        {
            id: 'ac1', title: '针对北美高流失风险玩家推送专属回流活动',
            reason: '与「竞技平衡性争议」热点强关联，受影响玩家 8,420，其中 62% 处于沉默边缘分群',
            audience: '北美 · 风险评分 ≥ 70 · 14 天未登录',
            expectedImpact: '预计可挽回 12.8% 的潜在流失用户，预算约为常规投放的 0.4 倍',
            channel: '站内信 + 短信（召回包 A/B）',
            targetRegionId: 'na', relatedAlertId: 'al1', priority: 'high', status: 'pending',
            ctaLabel: '查看策略'
        }
    ];

    var LIVE_ACTIVITIES = [
        { id: 'lv1', type: 'player',    message: '亚太地区活跃度上升',   value: '12.3%', direction: 'up',   timestamp: '2026-11-14T15:18:00+08:00' },
        { id: 'lv2', type: 'hotspot',   message: '「新赛季」话题热度突破', value: '500K',  direction: 'up',   timestamp: '2026-11-14T15:12:00+08:00' },
        { id: 'lv3', type: 'churn',     message: '欧洲地区流失率下降',   value: '0.7%',  direction: 'down', timestamp: '2026-11-14T15:06:00+08:00' },
        { id: 'lv4', type: 'sentiment', message: '玩家情感整体向好，正面占比', value: '68.7%', direction: 'up', timestamp: '2026-11-14T15:00:00+08:00' }
    ];

    function regionsFor(range) {
        var r = RANGE_SCALE[range] ? range : '30d';
        if (!regionCache[r]) {
            var sc = RANGE_SCALE[r];
            regionCache[r] = REGIONS.map(function (x) {
                return {
                    id: x.id, name: x.name, lat: x.lat, lng: x.lng, risk: x.risk,
                    churnRate: +(x.churnRate * sc.churn).toFixed(1),
                    change: x.change,
                    atRisk: Math.round(x.atRisk * sc.atRisk)
                };
            }).sort(function (a, b) { return b.churnRate - a.churnRate; });
        }
        activeRange = r;
        return regionCache[r];
    }

    var api = {
        meta: META,
        regions: REGIONS,
        playerPoints: PLAYER_POINTS,
        hotspots: HOTSPOTS,
        arcs: CHURN_ARCS,
        alerts: ALERTS,
        actions: ACTIONS,
        liveActivities: LIVE_ACTIVITIES,

        windowOf: windowOf,
        rangeDays: function (r) { return RANGE_DAYS[r] || 30; },
        series: series,
        kpis: kpis,
        retention: RET,

        regionsFor: regionsFor,
        regionById: function (id) {
            return regionsFor(activeRange).filter(function (r) { return r.id === id; })[0] || null;
        },
        currentRegions: function () { return regionsFor(activeRange); },
        hotspotById: function (id) {
            return HOTSPOTS.filter(function (h) { return h.id === id; })[0] || null;
        },
        alertById: function (id) {
            return ALERTS.filter(function (a) { return a.id === id; })[0] || null;
        },
        arcsWithCoords: function () {
            return CHURN_ARCS.map(function (a) {
                var s = REGIONS.filter(function (r) { return r.id === a.from; })[0];
                var t = REGIONS.filter(function (r) { return r.id === a.to; })[0];
                return {
                    id: a.id, count: a.count, risk: a.risk,
                    fromId: a.from, toId: a.to,
                    startLat: s.lat, startLng: s.lng, endLat: t.lat, endLng: t.lng,
                    fromName: s.name, toName: t.name
                };
            });
        }
    };

    global.DashboardData = api;
})(window);
