<script setup lang="ts">
import { NConfigProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'

const themeOverrides = {
  common: {
    primaryColor: '#223a5e',
    primaryColorHover: '#2e4e7a',
    primaryColorPressed: '#1a2d47',
    primaryColorSuppl: '#2e4e7a',
    borderRadius: '8px',
    fontFamily:
      "'Space Grotesk', -apple-system, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
    bodyColor: '#fafbfc',
    cardColor: '#ffffff',
    textColorBase: '#1f2733',
  },
}
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider placement="top">
      <router-view />
    </n-message-provider>
  </n-config-provider>
</template>
