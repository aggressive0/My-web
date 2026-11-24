/* ============================================================================
 * DashboardData —— 【已停用 mock，仅保留兼容外壳】（Phase A）
 *
 * 这个文件以前是「演示数据源」：硬编码 KPI、区域流失率、高危人数、
 * 玩家点位、迁移弧线、预警、召回动作与「预期召回 +18%」。
 * 按 docs/REAL_DATA_DASHBOARD_REFACTOR.md 的 Phase A 要求，
 * 这些**未经标注的虚构业务结论必须清除**，因此：
 *
 *   1. regions / hotspots / arcs / alerts / actions / liveActivities 一律返回空集合。
 *      —— 页面必须显式处理空态，而不是继续显示编造的数字。
 *   2. kpis / series / retention 返回 { state: 'unavailable' }，
 *      调用方若还在用，会拿到「不可用」而不是一个假数字。
 *   3. 真实数据统一走 RealData + DashboardAdapters：
 *        RealData.load('steam-ccu.json') → DashboardAdapters.ccuSeries(...)
 *
 * 保留这个文件只为兼容尚未迁移的调用点，Phase B/C 完成后应删除。
 * 任何在这里重新加回演示数值的改动，都违反上面的文档。
 * ========================================================================== */
(function (global) {
    'use strict';

    function unavailable(what) {
        return { state: 'unavailable', value: null, reason: what + ' 尚未接入真实数据' };
    }

    /* 纯日期窗口：不含任何业务数值，可以安全保留 */
    function windowOf(range) {
        var days = { '7d': 7, '30d': 30, '90d': 90 }[range] || 30;
        var end = new Date();
        var start = new Date(end.getTime() - days * 86400000);
        function pad(n) { return String(n).padStart(2, '0'); }
        function fmt(d) { return (d.getMonth() + 1) + '.' + pad(d.getDate()); }
        return { start: start.toISOString().slice(0, 10),
                 end: end.toISOString().slice(0, 10),
                 days: days, label: fmt(start) + ' – ' + fmt(end) };
    }

    function viaRealData(name) {
        if (!global.RealData) return Promise.resolve(null);
        return global.RealData.load(name).then(function (r) {
            return r && r.state === 'ready' ? r.doc : null;
        });
    }

    global.DashboardData = {
        /* —— 已清除的虚构集合：保持空数组，调用方必须处理空态 —— */
        regions: [],
        playerPoints: [],
        hotspots: [],
        arcs: [],
        alerts: [],
        actions: [],
        liveActivities: [],

        /* —— 演示指标：不再提供任何数值 —— */
        kpis: function () { return unavailable('KPI'); },
        series: function () { return unavailable('趋势序列'); },
        retention: unavailable('留存'),
        regionsFor: function () { return []; },
        currentRegions: function () { return []; },
        regionById: function () { return null; },
        hotspotById: function () { return null; },
        alertById: function () { return null; },
        arcsWithCoords: function () { return []; },
        sparkOf: function () { return []; },

        /* —— 日期窗口仍然可用（纯计算） —— */
        windowOf: windowOf,
        rangeDays: function (r) { return { '7d': 7, '30d': 30, '90d': 90 }[r] || 30; },

        /* —— 真实数据入口：委托给 RealData，失败返回 null 由调用方显示空态 —— */
        loadRealCcu: function () { return viaRealData('steam-ccu.json'); },
        loadRealFunnels: function () { return viaRealData('steam-funnels.json'); }
    };
})(window);
