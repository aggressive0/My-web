/* =========================================================================
   ops-nav.js — 顶部导航行为：时钟 + 时间范围下拉
   下拉选择后派发 rangechange 事件，由各页面自行决定是否消费。
   ========================================================================= */
(function () {
    'use strict';

    /* ---------- 时钟 ---------- */
    var clock = document.getElementById('timeDisplay');
    if (clock) {
        var days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
        var p2 = function (n) { return String(n).padStart(2, '0'); };
        var tick = function () {
            var d = new Date();
            clock.textContent = days[d.getDay()] + ' ' + p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds());
        };
        tick();
        setInterval(tick, 1000);
    }

    /* ---------- 时间范围下拉 ---------- */
    var btn = document.getElementById('rangeBtn');
    var menu = document.getElementById('rangeMenu');
    var label = document.getElementById('rangeLabel');
    if (!btn || !menu) return;

    function setOpen(open) {
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    btn.addEventListener('click', function (e) {
        e.stopPropagation();
        setOpen(menu.hidden);
    });

    document.addEventListener('click', function () { setOpen(false); });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') setOpen(false);
    });

    menu.addEventListener('click', function (e) {
        var opt = e.target.closest && e.target.closest('button[data-range]');
        if (!opt) return;
        menu.querySelectorAll('button[data-range]').forEach(function (b) {
            b.setAttribute('aria-selected', b === opt ? 'true' : 'false');
        });
        if (label) label.textContent = opt.getAttribute('data-label') || label.textContent;
        setOpen(false);
        window.dispatchEvent(new CustomEvent('rangechange', { detail: { range: opt.dataset.range } }));
    });

    /* 外部（如页面内分段控件）切换时，同步下拉的选中态与文案 */
    window.addEventListener('rangechange', function (ev) {
        var r = ev.detail && ev.detail.range;
        if (!r) return;
        menu.querySelectorAll('button[data-range]').forEach(function (b) {
            if (b.dataset.range === r) {
                b.setAttribute('aria-selected', 'true');
                if (label) label.textContent = b.getAttribute('data-label') || label.textContent;
            } else {
                b.setAttribute('aria-selected', 'false');
            }
        });
    });
})();
