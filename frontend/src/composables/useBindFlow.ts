import { onUnmounted, ref } from 'vue'

/**
 * 与浏览器插件的绑定交互流：
 * 页面 postMessage → 内容脚本中继 → background 打开登录页/捕获 Cookie；
 * 页面同时轮询 intent 状态作为兜底（插件消息丢失时依然能感知激活完成）。
 */
export function useBindFlow() {
  const extReady = ref(false)
  const sampleArmed = ref(false) // 插件已收到采样凭证的回执
  const phase = ref<'idle' | 'waiting' | 'success' | 'failed'>('idle')
  const error = ref('')
  // 激活成功 ≠ 首次同步成功：Cookie 有效但门户抓取失败时 synced=false，向导据此提示
  const synced = ref(true)
  const syncDetail = ref('')

  let pollTimer: ReturnType<typeof setInterval> | undefined
  let watchdog: ReturnType<typeof setTimeout> | undefined

  function detectExtension() {
    window.postMessage({ source: 'jobcheck-page', type: 'jc.ping' }, '*')
  }

  async function start(intent: { token: string; login_url: string; session_cookie_names: string[] }) {
    error.value = ''
    phase.value = 'waiting'
    window.postMessage(
      {
        source: 'jobcheck-page',
        type: 'jc.startBind',
        payload: {
          token: intent.token,
          loginUrl: intent.login_url,
          sessionCookieNames: intent.session_cookie_names,
          platformOrigin: window.location.origin,
        },
      },
      '*',
    )
    stopPolling()
    pollTimer = setInterval(() => pollIntent(intent.token), 2000)
    watchdog = setTimeout(() => {
      if (phase.value === 'waiting') {
        error.value = '等待登录超时（15 分钟），请重新发起'
        phase.value = 'failed'
        stopPolling()
      }
    }, 15 * 60 * 1000)
  }

  async function pollIntent(token: string) {
    try {
      const res = await import('../api').then((m) => m.bindingsApi.intentStatus(token))
      if (res.status === 'activated') {
        synced.value = res.synced !== false
        const detail = res.detail || ''
        syncDetail.value = detail.length > 140 ? detail.slice(0, 140) + '…' : detail
        phase.value = 'success'
        stopPolling()
      } else if (res.status === 'failed' || res.status === 'invalid') {
        error.value = '绑定未完成或已过期，请重新发起'
        phase.value = 'failed'
        stopPolling()
      }
    } catch {
      /* 轮询失败静默重试 */
    }
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer)
    if (watchdog) clearTimeout(watchdog)
    pollTimer = undefined
    watchdog = undefined
  }

  function reset() {
    stopPolling()
    phase.value = 'idle'
    error.value = ''
    synced.value = true
    syncDetail.value = ''
  }

  const onMessage = (ev: MessageEvent) => {
    const msg = ev.data
    if (!msg || msg.source !== 'jobcheck-ext') return
    if (msg.type === 'jc.pong') extReady.value = true
    if (msg.type === 'jc.sampleArmed') {
      extReady.value = true
      sampleArmed.value = true
    }
    if (msg.type === 'jc.bindArmed') extReady.value = true
    if (msg.type === 'jc.bindResult') {
      if (msg.ok) {
        // 后端激活接口返回 {activated, synced, detail}；插件原样透传在 info 里
        synced.value = (msg.info as any)?.synced !== false
        const detail = String((msg.info as any)?.detail || '')
        syncDetail.value = detail.length > 140 ? detail.slice(0, 140) + '…' : detail
        phase.value = 'success'
        stopPolling()
      } else {
        const detail = (msg.info as any)?.detail
        error.value = typeof detail === 'string' ? detail : '激活失败，请确认已用投递账号完成登录'
        phase.value = 'failed'
        stopPolling()
      }
    }
  }

  window.addEventListener('message', onMessage)
  detectExtension()
  onUnmounted(() => {
    window.removeEventListener('message', onMessage)
    stopPolling()
  })

  return { extReady, sampleArmed, phase, error, synced, syncDetail, start, reset, detectExtension }
}
