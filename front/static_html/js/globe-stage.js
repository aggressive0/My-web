/* =========================================================================
   globe-stage.js — 中央地球舞台（globe.gl）
   职责边界：只负责 3D 渲染与地球侧交互，不持有业务数据、不改动其他模块 DOM。
   图层：地球本体 / 大气辉光 / 玩家点位 / 热点脉冲环 / 流失弧线 / 热点标签
   ========================================================================= */
(function (global) {
    'use strict';

    var RISK_COLOR = { high: '#FF6B75', medium: '#FFB45F', low: '#35D9F2' };
    var DEFAULT_VIEW = { lat: 26, lng: 18, altitude: 2.35 };
    var state = {
        globe: null,
        el: null,
        ro: null,
        ready: false,
        selectedRegion: null,
        points: null,
        arcs: null,
        selectedHotspot: null,
        handlers: {},
        reduced: false
    };

    function webglAvailable() {
        try {
            var c = document.createElement('canvas');
            return !!(global.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl')));
        } catch (e) { return false; }
    }

    function riskColor(risk) { return RISK_COLOR[risk] || RISK_COLOR.low; }

    /* ---------- 工具：玩家点数 → 点半径（平方根压缩，避免超级点） ---------- */
    function pointRadius(p) {
        /* globe.gl 的半径单位是「度」，需按像素观感放大：1° ≈ 1.1px（当前缩放） */
        var r = Math.sqrt(p.players) / 90;
        return Math.max(1.1, Math.min(2.4, r));
    }

    function init(opts) {
        state.el = opts.el;
        state.handlers = opts.handlers || {};
        state.reduced = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;

        if (!global.Globe) {
            showFallback('3D 组件未能加载');
            return null;
        }
        if (!webglAvailable()) {
            showFallback('当前浏览器或显卡不支持 WebGL');
            return null;
        }

        var data = opts.data;
        var el = state.el;

        var globe = global.Globe()(el)
            .backgroundColor('rgba(0,0,0,0)')
            .globeImageUrl('libs/textures/earth_lights_2048.png')
            .bumpImageUrl('libs/textures/earth_normal_2048.jpg')
            .showAtmosphere(true)
            .atmosphereColor('#3f8cff')
            .atmosphereAltitude(0.2)
            .showGraticules(true)
            .width(el.clientWidth)
            .height(el.clientHeight);

        /* 材质：夜景底 + 轻微蓝色偏移，让大陆轮廓可辨 */
        var mat = globe.globeMaterial();
        if (mat) {
            /* 贴图本身是夜景灯带，不要再乘色压暗；用自发光让灯带透出来 */
            mat.color.set('#ffffff');
            mat.roughness = 0.9;
            mat.metalness = 0.02;
            if (mat.emissive && mat.map) {
                mat.emissive.set('#2d4f74');
                mat.emissiveMap = mat.map;
                mat.emissiveIntensity = 0.55;
            }
        }

        state.points = data.playerPoints;
        state.arcs = data.arcs;

        /* ---------- 玩家分布点 ---------- */
        globe
            .pointsData(data.playerPoints)
            .pointLat('lat')
            .pointLng('lng')
            .pointColor(function (p) {
                if (!state.selectedRegion) return riskColor(p.risk);
                return p.regionId === state.selectedRegion ? riskColor(p.risk) : 'rgba(110,140,180,.20)';
            })
            .pointRadius(function (p) {
                var r = pointRadius(p);
                return (state.selectedRegion && p.regionId === state.selectedRegion) ? r * 1.4 : r;
            })
            .pointAltitude(0.016)
            .pointsMerge(false)
            .onPointClick(function (p) {
                if (p && p.regionId && state.handlers.onSelectRegion) state.handlers.onSelectRegion(p.regionId, 'globe');
            });

        /* ---------- 热点脉冲环（只给高/中风险，控制密度） ---------- */
        var ringData = data.hotspots.filter(function (h) { return h.risk !== 'low'; });
        globe
            .ringsData(ringData)
            .ringLat('lat')
            .ringLng('lng')
            .ringColor(function (h) {
                var c = h.risk === 'high' ? [255, 107, 117] : [255, 180, 95];
                return function (t) {
                    var a = Math.max(0, 1 - t);
                    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (a * 0.85).toFixed(3) + ')';
                };
            })
            .ringMaxRadius(function (h) { return h.risk === 'high' ? 13 : 9; })
            .ringPropagationSpeed(1.15)
            .ringRepeatPeriod(function (h) { return h.risk === 'high' ? 1500 : 2200; })
            .ringAltitude(0.016)
            .ringResolution(72);

        /* ---------- 流失迁移弧线 ---------- */
        globe
            .arcsData(data.arcs)
            .arcStartLat('startLat').arcStartLng('startLng')
            .arcEndLat('endLat').arcEndLng('endLng')
            .arcColor(function (a) {
                var related = !state.selectedRegion || a.fromId === state.selectedRegion || a.toId === state.selectedRegion;
                return related ? [riskColor(a.risk), 'rgba(120,170,255,.55)']
                               : ['rgba(90,120,160,.20)', 'rgba(90,120,160,.06)'];
            })
            .arcStroke(function (a) {
                var w = a.risk === 'high' ? 0.62 : 0.45;
                if (state.selectedRegion && (a.fromId === state.selectedRegion || a.toId === state.selectedRegion)) w *= 1.35;
                return w;
            })
            .arcAltitudeAutoScale(0.38)
            .arcDashLength(0.45)
            .arcDashGap(0.55)
            .arcDashInitialGap(function () { return Math.random(); })
            .arcDashAnimateTime(function (a) { return a.risk === 'high' ? 2400 : 3600; })
            .onArcClick(function (a) {
                if (a && state.handlers.onSelectArc) state.handlers.onSelectArc(a);
            });

        /* ---------- 热点标签（HTML 层，可点击、可 hover） ---------- */
        globe
            .htmlElementsData(data.hotspots)
            .htmlLat('lat').htmlLng('lng')
            .htmlAltitude(0.02)
            .htmlElement(function (h) {
                var n = document.createElement('button');
                n.type = 'button';
                n.className = 'globe-label globe-label--' + h.risk;
                n.dataset.hotspotId = h.id;
                n.innerHTML = '<span class="gl-lb-name">' + h.name + '</span>' +
                              '<span class="gl-lb-val">热度 ' + h.heat + '</span>';
                n.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    if (state.handlers.onSelectHotspot) state.handlers.onSelectHotspot(h.id, 'label');
                });
                return n;
            })
            .htmlTransitionDuration(0);

        /* ---------- 控制器 ---------- */
        var controls = globe.controls();
        controls.enableDamping = true;
        controls.dampingFactor = 0.12;
        controls.rotateSpeed = 0.55;
        controls.zoomSpeed = 0.7;
        controls.minDistance = 160;
        controls.maxDistance = 620;
        controls.autoRotate = !state.reduced;
        controls.autoRotateSpeed = 0.4;

        /* 双击复位 */
        el.addEventListener('dblclick', function () { reset(); });

        /* 点击空白处取消选中 */
        globe.onGlobeClick(function () {
            if (state.handlers.onClearSelection) state.handlers.onClearSelection();
        });

        /* DPR 上限，避免高分屏过度消耗 */
        try {
            var r = globe.renderer();
            r.setPixelRatio(Math.min(global.devicePixelRatio || 1, 2));
        } catch (e) { /* 非关键 */ }

        globe.pointOfView(DEFAULT_VIEW, 0);
        /* 首帧布局可能未稳定，延迟重测一次，避免画布比容器宽出滚动条 */
        global.setTimeout(function () {
            if (!state.el) return;
            globe.width(state.el.clientWidth).height(state.el.clientHeight);
        }, 80);
        state.globe = globe;
        state.ready = true;

        /* 尺寸跟随容器 */
        if (global.ResizeObserver) {
            state.ro = new ResizeObserver(function () {
                if (!state.el.clientWidth) return;
                globe.width(state.el.clientWidth).height(state.el.clientHeight);
            });
            state.ro.observe(el);
        }

        return api;
    }

    function showFallback(reason) {
        if (!state.el) return;
        state.el.innerHTML = '<div class="globe-fallback">' +
            '<b>3D 地球不可用</b><span>' + reason + '</span>' +
            '<span>其余数据模块不受影响，可继续查看图表与排行。</span></div>';
        state.el.classList.add('is-fallback');
    }

    function focus(lat, lng, altitude, ms) {
        if (!state.ready) return;
        state.globe.pointOfView({ lat: lat, lng: lng, altitude: altitude || 1.85 }, ms === undefined ? 900 : ms);
        pauseAutoRotateTemporarily();
    }

    function pauseAutoRotateTemporarily() {
        if (!state.ready) return;
        var c = state.globe.controls();
        c.autoRotate = false;
        global.clearTimeout(state.rotateTimer);
        state.rotateTimer = global.setTimeout(function () {
            if (state.ready && !state.reduced) state.globe.controls().autoRotate = true;
        }, 9000);
    }

    function reset() {
        if (!state.ready) return;
        state.globe.pointOfView(DEFAULT_VIEW, 900);
        if (state.handlers.onClearSelection) state.handlers.onClearSelection();
    }

    /** 选中区域：其余点位与弧线压暗，选中项加粗 —— 让"点了哪里"在地球上看得见 */
    function setSelected(regionId) {
        if (!state.ready) return;
        state.selectedRegion = regionId || null;
        state.globe
            .pointsData(state.points.slice())
            .arcsData(state.arcs.slice());
    }

    function setAutoRotate(on) {
        if (!state.ready) return;
        state.globe.controls().autoRotate = !!on;
    }

    /* 页面不可见时暂停渲染，回来再续 */
    function bindVisibility() {
        document.addEventListener('visibilitychange', function () {
            if (!state.ready) return;
            if (document.hidden) state.globe.pauseAnimation();
            else state.globe.resumeAnimation();
        });
    }

    function dispose() {
        if (!state.ready) return;
        try { state.ro && state.ro.disconnect(); } catch (e) {}
        try { state.globe._destructor && state.globe._destructor(); } catch (e) {}
        try { state.globe.pauseAnimation(); } catch (e) {}
        state.globe = null;
        state.ready = false;
    }

    bindVisibility();

    /* 对外 API：init 返回同一对象，调用方统一用 stage.focus / stage.reset */
    var api = {
        init: init,
        focus: focus,
        reset: reset,
        setAutoRotate: setAutoRotate,
        setSelected: setSelected,
        dispose: dispose,
        isReady: function () { return state.ready; },
        instance: function () { return state.globe; }
    };

    global.GlobeStage = api;
})(window);
