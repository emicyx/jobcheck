<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'update:show', v: boolean): void }>()

const visible = computed({
  get: () => props.show,
  set: (v: boolean) => emit('update:show', v),
})

const downloadUrl = '/api/extension/download'
</script>

<template>
  <n-modal v-model:show="visible" preset="card" title="使用指南" :style="{ width: '640px', maxWidth: 'calc(100vw - 32px)' }">
    <div class="guide">
      <!-- 快速开始 -->
      <section>
        <h3>开始自动追踪（三步）</h3>
        <ol class="steps">
          <li>
            <b>下载并安装插件</b>
            <div class="step-detail">
              <n-button size="small" tag="a" :href="downloadUrl" download type="primary" secondary>下载插件压缩包</n-button>
              <span class="dl-hint">（jobcheck-extension.zip）</span>
              <div class="install">
                解压后得到 <code>extension/</code> 文件夹 → 浏览器打开
                <code>chrome://extensions</code>（Edge 为 <code>edge://extensions</code>）→
                打开右上角「开发者模式」→「加载已解压的扩展程序」→ 选择 <code>extension/</code> 文件夹。
              </div>
            </div>
          </li>
          <li>
            <b>接入门户</b>
            <div class="step-detail">
              看板页点「接入追踪」→ 粘贴公司校招官网链接或选择已支持门户 → 点「去登录」，
              在打开的官网页面正常登录（短信验证码会发到你手机）。
            </div>
          </li>
          <li>
            <b>自动同步</b>
            <div class="step-detail">
              登录成功后插件自动完成绑定并关闭登录页，你的投递记录随即出现在看板上；
              之后平台每 6 小时自动同步一次状态，变化会写入时间线。
            </div>
          </li>
        </ol>
      </section>

      <!-- 未支持网站 -->
      <section>
        <h3>网站提示「未支持 / 配置生成中」怎么办</h3>
        <p>
          用采样接入：在向导里点「开始采样」→ 用你投递时的账号登录该官网 →
          打开「我的投递 / 应聘进度」页 → 点浏览器右上角的 JobCheck 插件图标，在弹出的面板里点「采集当前页面」→
          回到向导点「我已采集，确认」。采样仅包含该页数据，用于生成追踪配置。
        </p>
      </section>

      <!-- 常见问题 -->
      <section>
        <h3>常见问题</h3>
        <ul class="faq">
          <li><b>多久同步一次？</b>默认每 6 小时，也可在设置里对单个门户点「立即同步」。</li>
          <li><b>看板出现黄色提醒条？</b>说明某公司的登录态已过期：到「设置 → 自动追踪」点「重新登录」，按引导再登录一次该官网即可。</li>
          <li><b>插件装好后向导仍提示未检测到？</b>刷新平台页面后再试；确认加载的是本项目的 <code>extension/</code> 目录。</li>
          <li><b>点插件图标没反应？</b>更新插件代码后必须在 <code>chrome://extensions</code> 里点该插件的「刷新」按钮重新加载；点图标会弹出操作面板，按面板提示操作即可。</li>
          <li><b>安全吗？</b>插件与平台只读你的投递状态，绝不执行投递/沟通等写操作；登录 Cookie 以 AES-256 加密存储，可随时在设置中删除。</li>
          <li><b>抓不到的渠道？</b>用看板的「+ 记录投递」手动记录，与自动记录混排管理，来源徽标可区分。</li>
        </ul>
      </section>
    </div>
  </n-modal>
</template>

<style scoped>
.guide { display: flex; flex-direction: column; gap: 20px; }
.guide h3 { font-size: 15px; margin: 0 0 10px; }
.steps { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 14px; }
.step-detail { color: var(--ink-2); font-size: 13px; margin-top: 6px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.dl-hint { color: var(--ink-3); font-size: 12px; }
.install { line-height: 1.7; }
.guide p { color: var(--ink-2); font-size: 13px; margin: 0; line-height: 1.7; }
.faq { margin: 0; padding-left: 20px; color: var(--ink-2); font-size: 13px; display: flex; flex-direction: column; gap: 6px; }
.faq b { color: var(--ink); }
code { background: var(--brand-soft); padding: 0 4px; border-radius: 4px; font-size: 12px; }
</style>
