<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArcElement,
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  DoughnutController,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'

Chart.register(
  CategoryScale, LinearScale, LineController, LineElement, PointElement,
  BarController, BarElement, DoughnutController, ArcElement, Tooltip, Legend, Filler,
)
Chart.defaults.color = '#6b7684'
Chart.defaults.font.family = "'Space Grotesk', 'PingFang SC', 'Microsoft YaHei', sans-serif"
Chart.defaults.font.size = 11

const props = withDefaults(
  defineProps<{
    type?: 'line' | 'bar' | 'doughnut'
    labels: string[]
    datasets: any[]
    height?: number
    yMoney?: boolean
  }>(),
  { type: 'line', height: 220 },
)

const el = ref<HTMLCanvasElement>()
let chart: Chart | null = null

onMounted(() => {
  chart = new Chart(el.value!, {
    type: props.type,
    data: { labels: props.labels, datasets: props.datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 150 },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true } },
      },
      scales:
        props.type === 'doughnut'
          ? undefined
          : {
              x: { grid: { display: false } },
              y: {
                beginAtZero: true,
                grid: { color: '#eef1f5' },
                ticks: {
                  maxTicksLimit: 5,
                  precision: 0,
                  callback: (v) => (props.yMoney ? `¥${v}` : v),
                },
              },
            },
    },
  })
})

watch(
  () => [props.labels, props.datasets],
  () => {
    if (!chart) return
    chart.data.labels = props.labels
    chart.data.datasets = props.datasets
    chart.update()
  },
  { deep: true },
)

onBeforeUnmount(() => chart?.destroy())
</script>

<template>
  <div class="chart-box" :style="{ height: height + 'px' }"><canvas ref="el" /></div>
</template>

<style scoped>
.chart-box { position: relative; width: 100%; }
</style>
