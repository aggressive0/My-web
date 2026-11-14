/* =========================================================================
   live-ticker.js — 底部「实时动态」条（跨页面共用）
   数据来源：window.DashboardData.liveActivities；缺失时用内置兜底。
   规范：不伪造实时状态 —— 文案与时间均来自演示数据集。
   ========================================================================= */
(function (global) {
    'use strict';

    var FALLBACK = [
        { type: 'player', message: '亚太地区活跃度上升', value: '12.3%', direction: 'up', timestamp: '2026-11-14T15:18:00+08:00' },
        { type: 'hotspot', message: '「新赛季」话题热度突破', value: '500K', direction: 'up', timestamp: '2026-11-14T15:12:00+08:00' },
        { type: 'churn', message: '欧洲地区流失率下降', value: '0.7%', direction: 'down', timestamp: '2026-11-14T15:06:00+08:00' },
        { type: 'sentiment', message: '玩家情感整体向好，正面占比', value: '68.7%', direction: 'up', timestamp: '2026-11-14T15:00:00+08:00' }
    ];

    var TYPE = {
        player: { c: '#35D9F2', t: '玩家' },
        hotspot: { c: '#9275FF', t: '热点' },
        churn: { c: '#FFB45F', t: '流失' },
        sentiment: { c: '#35D399', t: '情感' }
    };

    function mount(root) {
        if (!root) return;
        var data = (global.DashboardData && global.DashboardData.liveActivities) || FALLBACK;

        root.innerHTML = '';
        root.className = 'lt-bar';

        var label = document.createElement('span');
        label.className = 'lt-label';
        label.innerHTML = '<i aria-hidden="true"></i>实时动态';

        var sep = document.createElement('span');
        sep.className = 'lt-sep';
        sep.setAttribute('aria-hidden', 'true');

        var track = document.createElement('div');
        track.className = 'lt-track';

        data.forEach(function (a) {
            var ty = TYPE[a.type] || TYPE.player;
            var el = document.createElement('span');
            el.className = 'lt-item';
            el.innerHTML =
                '<i class="lt-type" style="--c:' + ty.c + '">' + ty.t + '</i>' +
                a.message + ' <b>' + a.value + '</b>' +
                '<span class="' + (a.direction === 'down' ? 'down' : 'up') + '">' +
                (a.direction === 'down' ? '▼' : '▲') + '</span>' +
                '<em class="lt-time">' + String(a.timestamp).slice(11, 16) + '</em>';
            track.appendChild(el);
        });

        var wave = document.createElement('div');
        wave.className = 'lt-wave';
        wave.setAttribute('aria-hidden', 'true');
        for (var i = 0; i < 14; i++) {
            var b = document.createElement('b');
            b.style.animationDelay = (i * 0.09).toFixed(2) + 's';
            b.style.height = (4 + Math.round(Math.abs(Math.sin(i * 1.7)) * 12)) + 'px';
            wave.appendChild(b);
        }

        root.appendChild(label);
        root.appendChild(sep);
        root.appendChild(track);
        root.appendChild(wave);
    }

    global.LiveTicker = { mount: mount };

    /* 自动挂载：页面上任意 [data-live-ticker] 容器 */
    function autoMount() {
        document.querySelectorAll('[data-live-ticker]').forEach(function (el) {
            if (!el.dataset.mounted) {
                mount(el);
                el.dataset.mounted = '1';
            }
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoMount);
    } else {
        autoMount();
    }
})(window);
