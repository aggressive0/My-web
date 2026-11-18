/* =========================================================================
   globe-stage.js — 中央地球舞台（globe.gl）
   职责边界：只负责 3D 渲染与地球侧交互，不持有业务数据、不改动其他模块 DOM。
   图层：地球本体 / 大气辉光 / 玩家点位 / 热点脉冲环 / 流失弧线 / 热点标签
   ========================================================================= */
(function (global) {
    'use strict';

    /* 玩家点位使用低饱和色阶，避免像素级光斑压过地球底图 */
    var RISK_COLOR = { high: '#B9828C', medium: '#B5A184', low: '#739FB3' };
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
        hotspots: [],
        riskPillars: [],
        handlers: {},
        reduced: false,
        cloudMesh: null,
        cloudMaterial: null,
        cloudTexture: null
    };

    function webglAvailable() {
        try {
            var c = document.createElement('canvas');
            return !!(global.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl')));
        } catch (e) { return false; }
    }

    function riskColor(risk) { return RISK_COLOR[risk] || RISK_COLOR.low; }

    /* 根据业务 ID 生成稳定的弧线初始相位，避免每次刷新都随机跳动 */
    function stableArcGap(arc) {
        var key = String((arc && arc.id) || (arc && arc.fromId) || 'arc');
        var hash = 2166136261;
        for (var i = 0; i < key.length; i++) {
            hash ^= key.charCodeAt(i);
            hash = Math.imul(hash, 16777619);
        }
        return ((hash >>> 0) % 10000) / 10000;
    }

    /* 球面插值：把迁移弧线上的流动节点放在真实的大圆路径上 */
    function greatCirclePoint(a, b, t) {
        var rad = Math.PI / 180, deg = 180 / Math.PI;
        var p1 = a.startLat * rad, l1 = a.startLng * rad;
        var p2 = a.endLat * rad, l2 = a.endLng * rad;
        var cosOmega = Math.sin(p1) * Math.sin(p2) +
            Math.cos(p1) * Math.cos(p2) * Math.cos(l2 - l1);
        var omega = Math.acos(Math.max(-1, Math.min(1, cosOmega)));
        if (omega < 1e-6) {
            return { lat: a.startLat + (a.endLat - a.startLat) * t,
                     lng: a.startLng + (a.endLng - a.startLng) * t };
        }
        var sinOmega = Math.sin(omega);
        var x1 = Math.cos(p1) * Math.cos(l1), y1 = Math.cos(p1) * Math.sin(l1), z1 = Math.sin(p1);
        var x2 = Math.cos(p2) * Math.cos(l2), y2 = Math.cos(p2) * Math.sin(l2), z2 = Math.sin(p2);
        var w1 = Math.sin((1 - t) * omega) / sinOmega;
        var w2 = Math.sin(t * omega) / sinOmega;
        var x = w1 * x1 + w2 * x2, y = w1 * y1 + w2 * y2, z = w1 * z1 + w2 * z2;
        return {
            lat: Math.atan2(z, Math.sqrt(x * x + y * y)) * deg,
            lng: Math.atan2(y, x) * deg
        };
    }

    function buildFlowNodes(arcs) {
        var out = [];
        (arcs || []).forEach(function (arc) {
            [0.28, 0.62].forEach(function (t, index) {
                var p = greatCirclePoint(arc, null, t);
                out.push({
                    id: 'flow-' + arc.id + '-' + index,
                    kind: 'flow',
                    arcId: arc.id,
                    lat: p.lat,
                    lng: p.lng,
                    risk: arc.risk,
                    phase: stableArcGap({ id: arc.id + '-' + index })
                });
            });
        });
        return out;
    }

    function buildHtmlLayerData() {
        var hotspots = (state.hotspots || []).map(function (h) {
            return Object.assign({}, h, { kind: 'hotspot' });
        });
        var pillars = (state.riskPillars || []).map(function (r) {
            return Object.assign({}, r, { kind: 'risk' });
        });
        return hotspots.concat(pillars, buildFlowNodes(state.arcs || []));
    }

    /* ---------- 玩家分布点：半径单位为角度，压到亚度级，呈现贴地微光 ---------- */
    function pointRadius(p) {
        var count = Number(p.review_count != null ? p.review_count : p.players) || 0;
        var r = Math.sqrt(Math.max(0, count)) / 18;
        return Math.max(0.12, Math.min(0.42, r));
    }

    /* ---------- 生成横向无缝的程序云图：不请求外部图片，不增加网络依赖 ---------- */
    function cloudNoise(x, y, nx, ny, seed) {
        x = ((x % nx) + nx) % nx;
        y = ((y % ny) + ny) % ny;
        var n = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
        return n - Math.floor(n);
    }

    function cloudValueNoise(u, v, nx, ny, seed) {
        var px = u * nx, py = v * ny;
        var x0 = Math.floor(px), y0 = Math.floor(py);
        var fx = px - x0, fy = py - y0;
        fx = fx * fx * (3 - 2 * fx);
        fy = fy * fy * (3 - 2 * fy);
        var a = cloudNoise(x0, y0, nx, ny, seed);
        var b = cloudNoise(x0 + 1, y0, nx, ny, seed);
        var c = cloudNoise(x0, y0 + 1, nx, ny, seed);
        var d = cloudNoise(x0 + 1, y0 + 1, nx, ny, seed);
        var top = a + (b - a) * fx;
        var bottom = c + (d - c) * fx;
        return top + (bottom - top) * fy;
    }

    function createCloudCanvas() {
        var width = 768, height = 384;
        var canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        var ctx = canvas.getContext('2d');
        var image = ctx.createImageData(width, height);
        var pixels = image.data;
        var smooth = function (a, b, x) {
            var t = Math.max(0, Math.min(1, (x - a) / (b - a)));
            return t * t * (3 - 2 * t);
        };
        for (var y = 0; y < height; y++) {
            for (var x = 0; x < width; x++) {
                var u = x / width, v = y / height;
                var n = cloudValueNoise(u, v, 6, 3, 1) * 0.52 +
                        cloudValueNoise(u, v, 12, 6, 2) * 0.27 +
                        cloudValueNoise(u, v, 24, 12, 3) * 0.14 +
                        cloudValueNoise(u, v, 48, 24, 4) * 0.07;
                var alpha = Math.round(smooth(0.49, 0.68, n) * 190);
                var k = (y * width + x) * 4;
                pixels[k] = 220;
                pixels[k + 1] = 232;
                pixels[k + 2] = 240;
                pixels[k + 3] = alpha;
            }
        }
        ctx.putImageData(image, 0, 0);
        return canvas;
    }

    /* 复用地球网格、材质与纹理的 Three.js 构造器，避免额外加载第二份 Three.js */
    function attachCloudLayer(globe, earthMaterial) {
        if (state.cloudMesh || !earthMaterial || !earthMaterial.map) return;
        try {
            var scene = globe.scene();
            var earthMesh = null;
            scene.traverse(function (obj) {
                if (obj && obj.isMesh && obj.material === earthMaterial) earthMesh = obj;
            });
            if (!earthMesh || !earthMesh.geometry || !earthMesh.parent) return;

            var canvas = createCloudCanvas();
            var texture = new earthMaterial.map.constructor(canvas);
            texture.needsUpdate = true;
            texture.wrapS = earthMaterial.map.wrapS;
            texture.wrapT = earthMaterial.map.wrapT;
            if ('colorSpace' in texture) texture.colorSpace = earthMaterial.map.colorSpace;

            var material = new earthMaterial.constructor({
                map: texture,
                color: '#91aebe',
                transparent: true,
                opacity: 0.22,
                depthWrite: false,
                shininess: 5
            });
            if (material.specular && material.specular.set) material.specular.set('#243d50');

            var geometry = earthMesh.geometry.clone();
            geometry.scale(1.005, 1.005, 1.005);
            var clouds = new earthMesh.constructor(geometry, material);
            clouds.position.copy(earthMesh.position);
            clouds.quaternion.copy(earthMesh.quaternion);
            clouds.scale.copy(earthMesh.scale);
            clouds.renderOrder = (earthMesh.renderOrder || 0) + 1;
            clouds.frustumCulled = false;
            clouds.userData.globeCloudLayer = true;
            earthMesh.parent.add(clouds);

            state.cloudMesh = clouds;
            state.cloudMaterial = material;
            state.cloudTexture = texture;
        } catch (e) {
            /* 云层是增强层：创建失败不影响地球、点位和业务交互 */
        }
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
            .atmosphereColor('#4daaff')
            .atmosphereAltitude(0.205)
            .showGraticules(true)
            .width(el.clientWidth)
            .height(el.clientHeight);

        /* Phase 4.1：globeImageUrl 的贴图是异步加载的，init 执行到这里时 mat.map 仍为 null，
           原先 `if (mat.emissive && mat.map)` 会整块跳过 —— emissive / emissiveMap / emissiveIntensity
           从未生效（运行态恒为 #000000 / null / 1）。改为拆两步：不依赖贴图的属性立即应用，
           贴图就绪后再补一次自发光，用有界 rAF 轮询兜住异步时序。 */
        function applyGlobeMaterial() {
            var mat = globe.globeMaterial();
            if (!mat) return false;
            if (mat.color && mat.color.set) mat.color.set('#ffffff');
            if ('bumpScale' in mat) mat.bumpScale = 0.14;
            if (mat.specular && mat.specular.set) mat.specular.set('#315d86');
            if ('shininess' in mat) mat.shininess = 16;
            if (!mat.map) return false;                 // 贴图未就绪，等待下一帧
            /* 夜景灯带只做微弱自发光，暗面保留方向光塑造的明暗层次 */
            if (mat.emissive) mat.emissive.set('#1b3a58');
            mat.emissiveMap = mat.map;
            mat.emissiveIntensity = 0.2;
            mat.needsUpdate = true;
            attachCloudLayer(globe, mat);
            return true;
        }
        applyGlobeMaterial();
        (function waitForMap(n) {
            if (applyGlobeMaterial() || n <= 0) return;
            requestAnimationFrame(function () { waitForMap(n - 1); });
        })(180);

        /* Phase 4：微调 globe.gl 已创建的默认灯光，不额外创建灯源或增加渲染通道。
           环境光负责保留暗部细节，方向光提供球体受光面与边缘的明暗对比。 */
        try {
            (globe.lights() || []).forEach(function (light) {
                if (light.isAmbientLight) {
                    if (light.color && light.color.set) light.color.set('#8faecc');
                    light.intensity = 0.78;
                } else if (light.isDirectionalLight) {
                    if (light.color && light.color.set) light.color.set('#d7eaff');
                    light.intensity = 1.08;
                }
            });
        } catch (e) { /* 灯光调校失败时保留 globe.gl 默认灯光 */ }

        state.points = data.playerPoints;
        state.arcs = data.arcs;
        state.hotspots = data.hotspots || [];
        state.riskPillars = data.riskPillars || [];

        /* ---------- 玩家分布点 ---------- */
        globe
            .pointsData(data.playerPoints)
            .pointLat('lat')
            .pointLng('lng')
            .pointColor(function (p) {
                if (!state.selectedRegion) return riskColor(p.risk);
                return p.regionId === state.selectedRegion ? riskColor(p.risk) : '#344c60';
            })
            .pointRadius(function (p) {
                var r = pointRadius(p);
                return (state.selectedRegion && p.regionId === state.selectedRegion) ? r * 1.16 : r;
            })
            .pointAltitude(0.004)
            .pointResolution(6)
            .pointsMerge(false)
            .onPointClick(function (p) {
                if (p && p.marketId && state.handlers.onSelectMarket) state.handlers.onSelectMarket(p);
                else if (p && p.regionId && state.handlers.onSelectRegion) state.handlers.onSelectRegion(p.regionId, 'globe');
            });

        /* ---------- 热点脉冲环（只给高/中风险，控制密度） ---------- */
        var ringData = state.hotspots.filter(function (h) { return h.risk !== 'low'; });
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
            .arcDashInitialGap(stableArcGap)
            .arcDashAnimateTime(function (a) { return a.risk === 'high' ? 2400 : 3600; })
            .onArcClick(function (a) {
                if (a && state.handlers.onSelectArc) state.handlers.onSelectArc(a);
            });

        /* ---------- 热点标签（HTML 层，可点击、可 hover） ---------- */
        globe
            .htmlElementsData(buildHtmlLayerData())
            .htmlLat('lat').htmlLng('lng')
            .htmlAltitude(function (d) { return d.kind === 'risk' ? 0.004 : (d.kind === 'flow' ? 0.018 : 0.026); })
            .htmlElement(function (d) {
                if (d.kind === 'risk') {
                    var pillar = document.createElement('button');
                    pillar.type = 'button';
                    pillar.className = 'globe-risk-pillar globe-risk-pillar--' + d.risk;
                    pillar.dataset.regionId = d.id;
                    pillar.setAttribute('aria-label', d.name + '高危玩家 ' + Number(d.atRisk || 0).toLocaleString() + ' 人');
                    var height = Math.round(16 + Math.sqrt(Math.max(0, Number(d.atRisk) || 0)) / 3.5);
                    pillar.style.setProperty('--pillar-height', Math.min(44, height) + 'px');
                    pillar.innerHTML = '<span class="risk-pillar__value">' +
                        (Number(d.atRisk) >= 1000 ? (Number(d.atRisk) / 1000).toFixed(1) + 'k' : d.atRisk) +
                        '</span><span class="risk-pillar__stem"></span><span class="risk-pillar__base"></span>';
                    pillar.addEventListener('click', function (ev) {
                        ev.stopPropagation();
                        if (state.handlers.onSelectRegion) state.handlers.onSelectRegion(d.id, 'pillar');
                    });
                    return pillar;
                }

                if (d.kind === 'flow') {
                    var particle = document.createElement('span');
                    particle.className = 'globe-flow-particle globe-flow-particle--' + d.risk;
                    particle.setAttribute('aria-hidden', 'true');
                    particle.style.setProperty('--flow-delay', (-d.phase * 2.4).toFixed(2) + 's');
                    return particle;
                }

                var n = document.createElement('button');
                n.type = 'button';
                n.className = 'globe-label globe-label--' + d.risk;
                n.dataset.hotspotId = d.id;
                n.innerHTML = '<span class="gl-lb-name">' + d.name + '</span>' +
                              '<span class="gl-lb-val">热度 ' + d.heat + '</span>';
                n.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    if (state.handlers.onSelectHotspot) state.handlers.onSelectHotspot(d.id, 'label');
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
        controls.autoRotateSpeed = 0.32;

        /* 双击复位 */
        el.addEventListener('dblclick', function () { reset(); });

        /* 点击空白处取消选中 */
        globe.onGlobeClick(function () {
            if (state.handlers.onClearSelection) state.handlers.onClearSelection();
        });

        /* DPR 上限，避免高分屏过度消耗 */
        try {
            var r = globe.renderer();
            /* 1.75 DPR 在清晰度与持续帧率之间取平衡，避免高分屏过度绘制 */
            r.setPixelRatio(Math.min(global.devicePixelRatio || 1, 1.75));
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
        if (state.el) {
            state.el.querySelectorAll('.globe-risk-pillar').forEach(function (el) {
                el.classList.toggle('is-active', el.dataset.regionId === state.selectedRegion);
            });
        }
    }

    function setMarketData(markets) {
        if (!state.ready) return;
        var rows = (markets || []).map(function (m) {
            return Object.assign({}, m, {
                marketId: m.id,
                lat: Number(m.latitude),
                lng: Number(m.longitude),
                risk: m.risk_level || 'low'
            });
        }).filter(function (m) {
            return Number.isFinite(m.lat) && Number.isFinite(m.lng);
        });
        state.points = rows;
        state.hotspots = rows.map(function (m) {
            return Object.assign({}, m, { risk: m.risk_level || 'low' });
        });
        state.arcs = [];
        state.riskPillars = [];
        state.globe.pointsData(rows);
        state.globe.ringsData(rows.filter(function (m) { return m.risk !== 'low'; }));
        state.globe.arcsData([]);
        state.globe.htmlElementsData([]);
    }

    function setRiskPillars(rows) {
        if (!state.ready) return;
        state.riskPillars = rows || [];
        state.globe.htmlElementsData(buildHtmlLayerData());
        if (state.selectedRegion && state.el) {
            state.el.querySelectorAll('.globe-risk-pillar').forEach(function (el) {
                el.classList.toggle('is-active', el.dataset.regionId === state.selectedRegion);
            });
        }
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
        try {
            if (state.cloudMesh && state.cloudMesh.parent) state.cloudMesh.parent.remove(state.cloudMesh);
            if (state.cloudMesh && state.cloudMesh.geometry) state.cloudMesh.geometry.dispose();
            if (state.cloudMaterial) state.cloudMaterial.dispose();
            if (state.cloudTexture) state.cloudTexture.dispose();
        } catch (e) {}
        state.cloudMesh = null;
        state.cloudMaterial = null;
        state.cloudTexture = null;
        try { state.globe._destructor && state.globe._destructor(); } catch (e) {}
        try { state.globe.pauseAnimation(); } catch (e) {}
        state.globe = null;
        state.hotspots = [];
        state.riskPillars = [];
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
        setRiskPillars: setRiskPillars,
        dispose: dispose,
        isReady: function () { return state.ready; },
        instance: function () { return state.globe; }
    };

    global.GlobeStage = api;
})(window);
