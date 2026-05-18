<template>
  <div class="globe-wrapper">
    <div ref="globeContainer" class="globe-container"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import 'echarts-gl'

const globeContainer = ref(null)
const chartInstance = ref(null)
const isRotating = ref(true)

// 地区数据
const regionData = [
  { name: '美国（United States of America）', value: [ -95.7129, 37.0902, 50 ], percentage: '22.79%' },
  { name: '加拿大（Canada）', value: [ -106.3468, 56.1304, 40 ], percentage: '3.06%' },
  { name: '巴西（Brazil）', value: [ -51.9253, -14.2350, 35 ], percentage: '2.28%' },
  { name: '阿根廷（Argentina）', value: [ -63.6167, -38.4161, 35 ], percentage: '3.08%' },
  { name: '英国（United Kingdom）', value: [ -3.4359, 55.3781, 40 ], percentage: '9.02%' },
  { name: '法国（France）', value: [ 2.2137, 46.2276, 35 ], percentage: '7.29%' },
  { name: '西班牙（Spain）', value: [ -3.7038, 40.4637, 40 ], percentage: '9.74%' },
  { name: '意大利（Italy）', value: [ 12.5674, 41.8719, 30 ], percentage: '2.25%' },
  { name: '德国（Germany）', value: [ 10.4515, 51.1657, 35 ], percentage: '5.05%' },
  { name: '俄罗斯（Russia）', value: [ 37.6173, 55.7558, 40 ], percentage: '5.05%' },
  { name: '中国', value: [ 104.1954, 35.8617, 30 ], percentage: '0.03%' },
  { name: '日本', value: [ 138.2529, 36.2048, 35 ], percentage: '3.20%' },
  { name: '台湾（Taiwan，中国省级行政区）', value: [ 121.5200, 25.0330, 25 ], percentage: '0.30%' },
  { name: '香港（Hong Kong，中国省级行政区）', value: [ 114.1694, 22.3193, 25 ], percentage: '1.57%' },
  { name: '沙特阿拉伯（Saudi Arabia）', value: [ 45.0792, 23.8859, 30 ], percentage: '2.51%' },
  { name: '澳大利亚（Australia）', value: [ 133.7751, -25.2744, 35 ], percentage: '1.47%' }
]

// 初始化图表
const initChart = () => {
  if (!globeContainer.value) return
  
  chartInstance.value = echarts.init(globeContainer.value)
  
  const option = {
    backgroundColor: '#0a0a1a',
    tooltip: {
      trigger: 'item',
      formatter: function(params) {
        return params.name + '<br/>占比: ' + params.data.percentage;
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
        main: {
          intensity: 1.2,
          shadow: true
        },
        ambient: {
          intensity: 0.3
        }
      }
    },
    series: [
      {
        type: 'scatter3D',
        coordinateSystem: 'globe',
        data: regionData,
        symbolSize: function(val) {
          return val[2] * 0.6; // 缩小光点大小
        },
        itemStyle: {
          color: 'rgba(255, 255, 255, 0.7)',
          opacity: 0.8
        },
        emphasis: {
          itemStyle: {
            color: 'rgba(255, 255, 255, 1)',
            opacity: 1,
            shadowBlur: 40,
            shadowColor: 'rgba(255, 255, 255, 1)'
          }
        },
        blendMode: 'lighter'
      }
    ]
  }
  
  chartInstance.value.setOption(option)
  
  // 响应式调整
  window.addEventListener('resize', () => {
    chartInstance.value?.resize()
  })
}

// 切换旋转状态
const toggleRotation = (status) => {
  isRotating.value = status
  chartInstance.value?.setOption({
    globe: {
      viewControl: {
        autoRotate: status
      }
    }
  })
}

onMounted(() => {
  initChart()
})

onUnmounted(() => {
  chartInstance.value?.dispose()
  window.removeEventListener('resize', () => {})
})

defineExpose({
  toggleRotation
})
</script>

<style scoped>
.globe-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
}

.globe-container {
  width: 1000px;
  height: 700px;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 0 30px rgba(0, 255, 255, 0.3);
}
</style>