/** Tailwind 配置：只为 solution 页生成 CSS，扫描范围收窄以控制体积。 */
module.exports = {
  content: ['./solution.html'],
  theme: {
    extend: {
      colors: {
        // 与总览页同一套文本色阶
        ink: { DEFAULT: '#EAF3FF', 2: '#9EB1CA', muted: '#9EB1CA', dim: '#637A96' },
        surface: { DEFAULT: '#05090F', card: 'rgba(13,22,38,0.86)' },
        line: 'rgba(90,140,200,0.18)',
        cyan: { DEFAULT: '#35D9F2', soft: '#7FEBFF' },
        blue: { DEFAULT: '#318BFF' }
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', '"Segoe UI"', '"Microsoft YaHei"', '"PingFang SC"', 'sans-serif'],
        mono: ['ui-monospace', '"Cascadia Mono"', '"SF Mono"', 'Menlo', 'Consolas', 'monospace']
      },
      borderRadius: { xl: '14px', lg: '10px', md: '8px' },
      boxShadow: {
        soft: '0 1px 0 rgba(255,255,255,0.04) inset, 0 18px 48px -32px rgba(0,0,0,0.9)',
        float: '0 24px 64px -40px rgba(0,0,0,1)'
      },
      transitionTimingFunction: { spring: 'cubic-bezier(.22,.61,.36,1)' }
    }
  },
  plugins: []
};
