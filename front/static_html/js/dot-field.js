/* =========================================================================
   背景点阵（NEXUS 公共组件）
   来源：反查 traedemos.com 首页 hero 的实现 —— 交错网格 + 每点独立相位/速度的
   三角波脉动 + 7% 高亮点；本实现把「进入 Hero 整体加速」改成 150px 指针唤醒，
   附近点变亮并在 54px 内连线。
   用法：页面里放 <canvas id="bgDots" class="dot-field">，加载本文件即可。
   性能：alpha 8 桶 + 预分配 Int32 缓冲（不产生 GC）+ fillRect 批量绘制，
   帧率自适应（指针活跃 30fps / 静息 15fps）；窄屏与 reduced-motion 下不启动。
   ========================================================================= */
(function () {
  'use strict';

  function dotField() {
    var canvas = document.getElementById('bgDots');
    if (!canvas) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { canvas.remove(); return; }
    if (window.innerWidth < 820) { canvas.remove(); return; }

    var ctx = canvas.getContext('2d');
    if (!ctx) { canvas.remove(); return; }

    var DPR = Math.min(window.devicePixelRatio || 1, 1.25);
    // 与页面主强调色一致：#35D9F2（--cyan）；唤醒点提亮到 #7FEBFF
    var HI = '53,217,242';
    var HI_LIGHT = '127,235,255';
    var BASE = '158,177,202';
    var GAP = 25;
    var R = 1.15;
    var WAKE_R = 150;
    var TAU = Math.PI * 2;

    var w = 0, h = 0, dots = [], raf = 0, t0 = Date.now(), last = 0, dead = false;
    var pointer = { x: -9999, y: -9999, inside: false, energy: 0 };

    // 预分配：中性点 / 高亮点各 8 桶，唤醒点一条扁平数组
    var nBuf = [], nCnt = new Int32Array(8);
    var hBuf = [], hCnt = new Int32Array(8);
    var wBuf = null, wN = 0;

    function alloc(n) {
      nBuf = []; hBuf = [];
      for (var i = 0; i < 8; i++) {
        nBuf.push(new Int32Array(n * 2));
        hBuf.push(new Int32Array(Math.ceil(n * 0.2) * 2));
      }
      wBuf = new Float32Array(n * 3);
    }

    function build() {
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = Math.floor(w * DPR);
      canvas.height = Math.floor(h * DPR);
      canvas.style.width = w + 'px';
      canvas.style.height = h + 'px';
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

      dots = [];
      var cols = Math.ceil(w / GAP) + 1;
      var rows = Math.ceil(h / GAP) + 1;
      for (var j = 0; j < rows; j++) {
        for (var i = 0; i < cols; i++) {
          dots.push({
            x: i * GAP + (j % 2 ? GAP * 0.5 : 0),
            y: j * GAP,
            phase: Math.random() * TAU,
            speed: 0.35 + Math.random() * 0.85,
            hi: Math.random() < 0.07
          });
        }
      }
      alloc(dots.length);
    }

    // 每桶只改一次 fillStyle，桶内用 fillRect 直出：
    // 实测比「rect 组路径 + 一次 fill」快（几千个 2px 方块时更明显）
    function paint(bufs, cnts, rgb, alphaBase, size) {
      for (var b = 0; b < 8; b++) {
        var n = cnts[b];
        if (!n) continue;
        var arr = bufs[b];
        ctx.fillStyle = 'rgba(' + rgb + ',' + ((b + 1) / 8 * alphaBase).toFixed(3) + ')';
        for (var k = 0; k < n; k++) {
          ctx.fillRect(arr[k * 2] - size, arr[k * 2 + 1] - size, size * 2, size * 2);
        }
      }
    }

    function frame(now) {
      if (dead) return;
      raf = requestAnimationFrame(frame);

      // 自适应帧率：指针活跃 30fps，静息 15fps。
      // 脉动一圈约 6s，15fps 完全看不出台阶；整屏 canvas 的合成开销才是大头。
      var active = pointer.inside && pointer.energy > 0.04;
      if (now - last < (active ? 1000 / 30 : 1000 / 15)) return;
      var delta = last ? Math.min((now - last) / 1000, 0.25) : 0.04;
      last = now;

      pointer.energy *= Math.exp(-delta * 1.7);
      var t = (now - t0) / 1000 * (0.55 + pointer.energy * 0.5);

      ctx.clearRect(0, 0, w, h);
      nCnt.fill(0); hCnt.fill(0); wN = 0;

      var px = pointer.x, py = pointer.y;
      var wakeR2 = WAKE_R * WAKE_R;

      for (var k = 0; k < dots.length; k++) {
        var d = dots[k];
        var tri = (t * d.speed + d.phase) % 2;
        if (tri < 0) tri += 2;
        var n = 0.22 + 0.5 * (tri < 1 ? tri : 2 - tri);

        var wake = 0;
        if (pointer.inside) {
          var dx = d.x - px, dy = d.y - py;
          var dist2 = dx * dx + dy * dy;
          if (dist2 < wakeR2) {
            var q = 1 - Math.sqrt(dist2) / WAKE_R;
            wake = q * q;
          }
        }

        if (wake > 0.035) {
          wBuf[wN * 3] = d.x;
          wBuf[wN * 3 + 1] = d.y;
          wBuf[wN * 3 + 2] = wake;
          wN++;
          continue;
        }

        var bi = (n * 8) | 0;
        if (bi > 7) bi = 7;
        var bufs = d.hi ? hBuf : nBuf;
        var cnts = d.hi ? hCnt : nCnt;
        var c = cnts[bi];
        bufs[bi][c * 2] = d.x;
        bufs[bi][c * 2 + 1] = d.y;
        cnts[bi] = c + 1;
      }

      paint(nBuf, nCnt, BASE, 0.55, R);

      // 高亮点的柔光（数量少，单独一批）
      var hiTotal = 0;
      for (var b2 = 0; b2 < 8; b2++) hiTotal += hCnt[b2];
      if (hiTotal) {
        ctx.fillStyle = 'rgba(' + HI + ',0.05)';
        for (var b3 = 0; b3 < 8; b3++) {
          var arr3 = hBuf[b3];
          for (var m = 0; m < hCnt[b3]; m++) {
            ctx.fillRect(arr3[m * 2] - R * 3.2, arr3[m * 2 + 1] - R * 3.2, R * 6.4, R * 6.4);
          }
        }
      }
      paint(hBuf, hCnt, HI, 0.95, R);

      // 指针附近：细线 + 唤醒点
      if (wN) {
        ctx.lineWidth = 0.6;
        for (var a = 0; a < wN; a++) {
          var ax = wBuf[a * 3], ay = wBuf[a * 3 + 1], aw = wBuf[a * 3 + 2];
          for (var c2 = a + 1; c2 < wN; c2++) {
            var bx = wBuf[c2 * 3] - ax, by = wBuf[c2 * 3 + 1] - ay;
            var dd = bx * bx + by * by;
            if (dd > 2916) continue;
            var alpha = Math.min(aw, wBuf[c2 * 3 + 2]) * (1 - Math.sqrt(dd) / 54) * 0.5;
            if (alpha < 0.02) continue;
            ctx.strokeStyle = 'rgba(' + HI + ',' + alpha.toFixed(3) + ')';
            ctx.beginPath();
            ctx.moveTo(ax, ay);
            ctx.lineTo(wBuf[c2 * 3], wBuf[c2 * 3 + 1]);
            ctx.stroke();
          }
        }

        ctx.beginPath();
        for (var e = 0; e < wN; e++) {
          var rr = 1.1 + 1.7 * wBuf[e * 3 + 2];
          ctx.moveTo(wBuf[e * 3] + rr, wBuf[e * 3 + 1]);
          ctx.arc(wBuf[e * 3], wBuf[e * 3 + 1], rr, 0, TAU);
        }
        ctx.fillStyle = 'rgba(' + HI_LIGHT + ',0.62)';
        ctx.fill();
      }
    }

    var resizeTimer = 0;
    function onResize() {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(build, 160);
    }
    function onMove(ev) {
      pointer.x = ev.clientX;
      pointer.y = ev.clientY;
      pointer.inside = true;
      pointer.energy = 1;
    }
    function onLeave() {
      pointer.inside = false;
      pointer.energy = 0;
    }

    build();
    window.addEventListener('resize', onResize);
    window.addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('pointerleave', onLeave);
    window.addEventListener('blur', onLeave);

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) cancelAnimationFrame(raf);
      else { last = 0; cancelAnimationFrame(raf); raf = requestAnimationFrame(frame); }
    });

    raf = requestAnimationFrame(frame);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', dotField);
  else dotField();
})();
