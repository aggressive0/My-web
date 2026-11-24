/* ============================================================================
 * UrlState —— 页面间共享的筛选状态（Phase D）
 *
 * 只处理三个参数：appid / range / topic。
 *   appid  Steam appid，字符串或空
 *   range  7d | 30d | 90d
 *   topic  主题标签（中文），用于在分析页定位某个主题
 *
 * 纯函数，不读 window.location 也不改 DOM —— 由调用方传入 search 字符串。
 * 这样可以在 Node 里直接单测，不需要浏览器。
 * ========================================================================== */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.UrlState = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    var RANGES = ['7d', '30d', '90d'];

    function clean(value) {
        return value == null ? '' : String(value).trim();
    }

    /** 解析 "?a=1&b=2" 或 "a=1" 形式的查询串；非法值直接丢弃，不做纠正。 */
    function parse(search) {
        var out = { appid: '', range: '', topic: '' };
        var text = clean(search).replace(/^[?#]/, '');
        if (!text) return out;

        text.split('&').forEach(function (pair) {
            if (!pair) return;
            var idx = pair.indexOf('=');
            var key = decodeURIComponent(idx < 0 ? pair : pair.slice(0, idx));
            var val = idx < 0 ? '' : decodeURIComponent(pair.slice(idx + 1).replace(/\+/g, ' '));
            if (key === 'appid') {
                // 只接受数字 appid：Steam appid 都是数字，其它值说明链接是手改坏的
                out.appid = /^\d+$/.test(val) ? val : '';
            } else if (key === 'range') {
                out.range = RANGES.indexOf(val) >= 0 ? val : '';
            } else if (key === 'topic') {
                out.topic = val.slice(0, 40);
            }
        });
        return out;
    }

    /** 生成查询串（不带问号）；空值不写，保证链接干净且可比较。 */
    function build(state) {
        var parts = [];
        var s = state || {};
        if (s.appid && /^\d+$/.test(String(s.appid))) {
            parts.push('appid=' + encodeURIComponent(String(s.appid)));
        }
        if (s.range && RANGES.indexOf(s.range) >= 0) {
            parts.push('range=' + encodeURIComponent(s.range));
        }
        if (s.topic) {
            parts.push('topic=' + encodeURIComponent(String(s.topic).slice(0, 40)));
        }
        return parts.join('&');
    }

    /** 在目标页 URL 上合并参数，用于「带着当前筛选跳转」。 */
    function href(path, state) {
        var qs = build(state);
        return qs ? path + '?' + qs : path;
    }

    /** 把状态写回地址栏但不新增历史（避免点一下就多一条返回记录）。 */
    function replace(win, state) {
        if (!win || !win.history || !win.history.replaceState) return;
        var qs = build(state);
        var url = win.location.pathname + (qs ? '?' + qs : '');
        win.history.replaceState(null, '', url);
    }

    return { parse: parse, build: build, href: href, replace: replace, RANGES: RANGES };
});
