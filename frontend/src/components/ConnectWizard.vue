<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { portalsApi, bindingsApi, samplesApi } from '../api'
import { useBindFlow } from '../composables/useBindFlow'
import type { Portal } from '../types'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'update:show', v: boolean): void; (e: 'bound'): void }>()

const message = useMessage()
const { extReady, sampleArmed, phase, error, synced, syncDetail, start, reset, detectExtension } = useBindFlow()

const visible = computed({
  get: () => props.show,
  set: (v: boolean) => emit('update:show', v),
})

const urlInput = ref('')
const identifying = ref(false)
const portal = ref<Portal | null>(null)
const portalList = ref<Portal[]>([])
const starting = ref(false)

// 采样流程状态
const samplePhase = ref<'idle' | 'arming' | 'collecting' | 'done'>('idle')
const sampling = ref(false)
const targetUrl = ref('')

watch(visible, (v) => {
  if (v) {
    reset()
    urlInput.value = ''
    portal.value = null
    samplePhase.value = 'idle'
    portalsApi.list().then((list) => (portalList.value = list)).catch(() => {})
    setTimeout(detectExtension, 300)
  }
})

async function identify(quiet = false) {
  const url = urlInput.value.trim()
  if (!url) return
  if (!quiet) identifying.value = true
  try {
    const found = await portalsApi.identify(url)
    portal.value = found
    if (!quiet) {
      if (found && found.enabled) {
        targetUrl.value = url
      } else if (found) {
        targetUrl.value = url
        message.info(`已识别「${found.name}」，门户配置生成中，可通过采样加速`)
      } else {
        targetUrl.value = url
        message.info('该网站尚未支持：可通过采样接入（需先用你的账号登录该网站）')
      }
    }
  } catch (e: any) {
    if (!quiet) message.error(e?.message || '识别失败')
  } finally {
    identifying.value = false
  }
}

// 采样完成后：自动轮询识别，配置一生成立即进入绑定步骤
let pollTimer: ReturnType<typeof setInterval> | undefined
watch(samplePhase, (p) => {
  if (pollTimer) clearInterval(pollTimer)
  if (p === 'done') {
    let tries = 0
    pollTimer = setInterval(async () => {
      tries += 1
      try {
        await identify(true)
        if (portal.value?.enabled) {
          clearInterval(pollTimer)
          samplePhase.value = 'idle'
          message.success(`「${portal.value.name}」配置已生成，现在可以绑定了`)
        }
      } catch { /* 静默重试 */ }
      if (tries >= 60) clearInterval(pollTimer) // 约 8 分钟后停止自动轮询
    }, 8000)
  }
})
onUnmounted(() => pollTimer && clearInterval(pollTimer))

function pickPortal(p: Portal) {
  portal.value = p
  targetUrl.value = `https://${p.domains[0] ?? ''}`
}

async function goLogin() {
  if (!portal.value) return
  starting.value = true
  try {
    const intent = await bindingsApi.create(portal.value.id)
    await start(intent)
  } catch (e: any) {
    message.error(e?.message || '发起绑定失败')
    phase.value = 'failed'
  } finally {
    starting.value = false
  }
}

async function startSampling() {
  sampling.value = true
  sampleArmed.value = false
  try {
    const intent = await samplesApi.createIntent()
    window.postMessage(
      {
        source: 'jobcheck-page',
        type: 'jc.startSample',
        payload: { token: intent.token, platformOrigin: window.location.origin },
      },
      '*',
    )
    samplePhase.value = 'collecting'
    // 凭证交接回执：插件没回话说明内容脚本不在场（未装/未刷新）
    setTimeout(() => {
      if (!sampleArmed.value) {
        message.warning('未收到插件回执：请确认插件已安装并在 chrome://extensions 重新加载后，刷新本页重试')
      }
    }, 1600)
  } catch (e: any) {
    message.error(e?.message || '发起采样失败')
  } finally {
    sampling.value = false
  }
}

const checking = ref(false)
async function checkSample() {
  checking.value = true
  try {
    const list = await samplesApi.mine()
    const latest = list[0]
    if (latest && latest.status !== 'pending') {
      samplePhase.value = 'done'
      message.success('采样已收到！门户配置生成后即可在这里绑定')
    } else {
      message.warning('还没收到采样：请确认已在官网「我的投递」页点击插件图标')
    }
  } finally {
    checking.value = false
  }
}

function openTarget() {
  const url = targetUrl.value.includes('://') ? targetUrl.value : `https://${targetUrl.value}`
  window.open(url, '_blank')
}

function onBound() {
  emit('bound')
  visible.value = false
}

watch(phase, (p) => {
  if (p === 'success') {
    if (synced.value) {
      message.success('绑定成功，投递记录已自动同步')
      setTimeout(onBound, 600)
    } else {
      message.warning('绑定成功，但首次同步未完成，稍后会自动重试；详情见「设置 → 自动追踪」')
      setTimeout(onBound, 1800)
    }
  }
})

const bindPhase = computed(() => phase.value)
const isPendingPortal = computed(() => portal.value !== null && !portal.value.enabled)
const showBindFlow = computed(() => portal.value !== null && portal.value.enabled && bindPhase.value !== 'success')
</script>

<template>
  <n-modal v-model:show="visible" preset="card" title="接入自动追踪" :style="{ width: '540px', maxWidth: 'calc(100vw - 32px)' }">
    <!-- 第 1 步：识别门户 -->
    <template v-if="!portal">
      <p class="hint">粘贴公司校招官网的任意页面链接，平台识别后引导你完成登录绑定；未支持的网站可一键采样接入。</p>
      <n-input v-model:value="urlInput" placeholder="https://join.qq.com/ 或 hr.xiaomi.com" @keyup.enter="identify">
        <template #suffix>
          <n-button size="tiny" :loading="identifying" @click="identify">识别</n-button>
        </template>
      </n-input>

      <div v-if="portalList.length" class="quick-list">
        <div class="quick-label">已支持的门户：</div>
        <n-tag v-for="p in portalList" :key="p.id" size="small" round checkable :checked="false" @update:checked="() => pickPortal(p)">
          {{ p.name }}
        </n-tag>
      </div>
    </template>

    <!-- 已支持门户：绑定流程 -->
    <template v-else-if="showBindFlow">
      <div class="portal-head">
        <b>{{ portal.name }}</b>
        <n-tag v-if="portal.verified" size="tiny" type="success" :bordered="false">已验证</n-tag>
        <n-tag v-else size="tiny" type="warning" :bordered="false">配置待验证</n-tag>
      </div>
      <p v-if="portal.note" class="hint">{{ portal.note }}</p>

      <n-alert v-if="!extReady && bindPhase === 'idle'" type="warning" :show-icon="false" class="mb">
        未检测到浏览器插件。<a href="/api/extension/download" download><b>点此下载压缩包</b></a>，解压后到
        <b>chrome://extensions</b> 打开开发者模式 →「加载已解压的扩展程序」→ 选择 <code>extension/</code> 文件夹，装好后
        <a @click="detectExtension">点此重试检测</a>。
      </n-alert>
      <n-alert v-else-if="extReady" type="success" :show-icon="false" class="mb">插件已就绪 ✓</n-alert>

      <n-alert v-if="bindPhase === 'waiting'" type="info" class="mb">
        已在浏览器中打开官网登录页，请正常完成登录（短信验证码会发到你手机）。若你本就登录过，插件会直接自动完成；
        长时间无动静时，点浏览器右上角的插件图标，在面板里点「立即检查」。
      </n-alert>
      <n-alert v-if="bindPhase === 'failed'" type="error" class="mb">{{ error || '绑定失败' }}</n-alert>

      <div class="actions">
        <n-button v-if="bindPhase === 'idle'" type="primary" :loading="starting" :disabled="!extReady" @click="goLogin">去登录</n-button>
        <n-button v-if="bindPhase === 'failed'" type="primary" @click="goLogin">重新发起绑定</n-button>
        <n-button quaternary @click="((portal = null), reset())">返回</n-button>
      </div>
    </template>

    <n-result
      v-else-if="bindPhase === 'success'"
      :status="synced ? 'success' : 'warning'"
      :title="synced ? '绑定成功' : '绑定成功，同步待完成'"
      :description="synced ? '投递列表已自动同步到看板' : syncDetail || '首次同步未完成，系统会自动重试；也可到「设置 → 自动追踪」手动同步'"
    />

    <!-- 未支持 / 待配置门户：采样接入 -->
    <template v-else-if="isPendingPortal || (!portal && samplePhase !== 'idle' && !!targetUrl)">
      <div class="portal-head">
        <b>{{ portal?.name ?? '新网站' }}</b>
        <n-tag size="tiny" type="warning" :bordered="false">{{ portal ? '配置生成中' : '未支持' }}</n-tag>
      </div>
      <p class="hint" v-if="portal?.note">{{ portal.note }}</p>
      <p class="hint">
        用你投递时用的账号登录该网站，打开「我的投递 / 应聘进度」页面，然后点浏览器右上角的
        JobCheck 插件图标，在弹出的面板里点「采集当前页面」。采样只包含该页数据，用于生成追踪配置。
      </p>

      <n-alert v-if="samplePhase === 'collecting' || samplePhase === 'done'" :type="samplePhase === 'done' ? 'success' : 'info'" class="mb">
        <template v-if="samplePhase === 'collecting'">
          采样已就绪：去目标网站的「我的投递」页，点插件图标并在面板里点「采集当前页面」，完成后回到这里确认。
        </template>
        <template v-else>
          采样已收到 ✓ 配置生成后这里会自动变为可绑定（每 8 秒检查一次）。
        </template>
      </n-alert>

      <div class="actions">
        <n-button v-if="samplePhase === 'idle'" type="primary" :loading="sampling" :disabled="!extReady" @click="startSampling">
          开始采样
        </n-button>
        <n-button v-if="samplePhase === 'collecting'" type="primary" :loading="checking" @click="checkSample">我已采集，确认</n-button>
        <n-button v-if="samplePhase === 'done'" type="primary" :loading="identifying" @click="identify()">重新识别</n-button>
        <n-button secondary @click="openTarget">打开网站</n-button>
        <n-button quaternary @click="((portal = null), (samplePhase = 'idle'))">返回</n-button>
      </div>
    </template>

    <!-- 识别为 null 且未开始采样：提示 + 采样入口 -->
    <template v-else-if="!portal && !!targetUrl">
      <p class="hint">该网站尚未支持。可先手动记录投递，或用采样接入（需先用你的账号登录该网站）。</p>
      <div class="actions">
        <n-button type="primary" @click="samplePhase = 'arming'; startSampling()">采样接入</n-button>
        <n-button quaternary @click="((targetUrl = ''), (urlInput = ''))">返回</n-button>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.hint { color: var(--ink-2); font-size: 13px; margin: 0 0 12px; }
.quick-list { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.quick-label { font-size: 12px; color: var(--ink-3); width: 100%; }
.portal-head { display: flex; align-items: center; gap: 8px; font-size: 16px; margin-bottom: 8px; }
.mb { margin-bottom: 12px; }
.actions { display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
a { color: var(--brand); cursor: pointer; }
code { background: var(--brand-soft); padding: 0 4px; border-radius: 4px; }
</style>
