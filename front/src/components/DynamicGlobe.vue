<template>
  <div class="globe-wrapper">
    <div ref="globeContainer" class="globe-container"></div>
    <div v-if="status !== 'ready'" class="globe-status" role="status">{{ statusText }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import * as echarts from 'echarts'
import 'echarts-gl'

const globeContainer = ref(null)
const chartInstance = ref(null)
const isRotating = ref(true)
const status = ref('loading')
const statusText = computed(() => ({
  loading: '正在加载真实 Steam 评论市场数据…',
  empty: '暂无达到最小样本量的评论语言市场',
  error: '真实地球数据暂不可用'
}[status.value] || ''))

const DATA_URL = 'https://raw.githubusercontent.com/aggressive0/My-web/data/globe-daily.json'
const riskColor = (level) => ({
  high: '#FF6B75',
  medium: '#FFB45F',
  low: '#35D399'
}[level] || '#35D9F2')

function initChart(markets) {
  if (!globeContainer.value) return
  chartInstance.value = echarts.init(globeContainer.value)
  const points = markets.map((market) => ({
    name: market.name,
    value: [market.longitude, market.latitude, market.review_count],
    reviewCount: market.review_count,
    negativeRate: market.negative_rate,
    riskScore: market.risk_score,
    riskLevel: market.risk_level,
    topTopic: market.top_topic,
    language: market.language,
    game: market.game
  }))
  chartInstance.value.setOption({
    backgroundColor: '#0a0a1a',
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        const p = params.data
        return [
          p.name + ' · ' + p.game,
          '评论样本：' + Number(p.reviewCount).toLocaleString(),
          '不推荐比例：' + (p.negativeRate * 100).toFixed(1) + '%',
          '风险信号：' + p.riskScore + ' / ' + p.riskLevel,
          '热点主题：' + (p.topTopic || '暂无'),
          '语言：' + p.language,
          '<small>评论语言市场，不代表玩家真实所在地</small>'
        ].join('<br/>')
      }
    },
    globe: {
      baseTexture: './textures/world.jpg',
      environment: './textures/starfield.jpg',
      heightTexture: './textures/world.jpg',
      displacementScale: 0.05,
      viewControl: {
        autoRotate: isRotating.value,
        autoRotateSpeed: 1,
        distance: 180,
        pitch: 30,
        heading: 160,
        zoomSensitivity: 0.5
      },
      light: {
        main: { intensity: 1.2, shadow: true },
        ambient: { intensity: 0.3 }
      }
    },
    series: [{
      type: 'scatter3D',
      coordinateSystem: 'globe',
      data: points,
      symbolSize: (value) => Math.max(5, Math.min(28, Math.log10(Number(value[2]) + 1) * 10)),
      itemStyle: {
        color: (params) => riskColor(params.data.riskLevel),
        opacity: 0.88
      },
      emphasis: {
        itemStyle: { opacity: 1, shadowBlur: 32, shadowColor: '#ffffff' }
      },
      blendMode: 'lighter'
    }]
  })
}

async function loadGlobeData() {
  status.value = 'loading'
  try {
    const response = await fetch(DATA_URL, { cache: 'no-store' })
    if (!response.ok) throw new Error('HTTP ' + response.status)
    const doc = await response.json()
    if (!Array.isArray(doc.markets) || doc.markets.length === 0) {
      status.value = 'empty'
      return
    }
    initChart(doc.markets)
    status.value = 'ready'
  } catch (error) {
    console.error('[DynamicGlobe] failed to load real globe data:', error)
    status.value = 'error'
  }
}

function handleResize() {
  chartInstance.value?.resize()
}

const toggleRotation = (statusValue) => {
  isRotating.value = statusValue
  chartInstance.value?.setOption({ globe: { viewControl: { autoRotate: statusValue } } })
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
  loadGlobeData()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chartInstance.value?.dispose()
  chartInstance.value = null
})

defineExpose({ toggleRotation })
</script>

<style scoped>
.globe-wrapper {
  position: relative;
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  min-height: 420px;
  background: #0a0a1a;
}
.globe-container {
  width: 100%;
  min-height: 420px;
  height: 70vh;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 0 30px rgba(0, 255, 255, 0.3);
}
.globe-status {
  position: absolute;
  inset: auto 16px 16px;
  color: #9eb1ca;
  text-align: center;
  font-size: 13px;
  pointer-events: none;
}
</style>
