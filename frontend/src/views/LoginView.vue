<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { authApi } from '../api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const message = useMessage()
const auth = useAuthStore()

const mode = ref<'login' | 'register'>('login')
const submitting = ref(false)

const form = reactive({
  email: '',
  password: '',
  invite_code: '',
})

const errors = reactive({ email: '', password: '', invite_code: '' })

function validate(): boolean {
  errors.email = /.+@.+\..+/.test(form.email) ? '' : '请输入有效的邮箱地址'
  errors.password = form.password.length >= 8 ? '' : '密码至少 8 位'
  errors.invite_code = mode.value === 'register' && form.invite_code.length < 4 ? '请输入邀请码' : ''
  return !errors.email && !errors.password && !errors.invite_code
}

async function submit() {
  if (!validate()) return
  submitting.value = true
  try {
    if (mode.value === 'login') {
      auth.user = await authApi.login({ email: form.email, password: form.password })
    } else {
      auth.user = await authApi.register({
        email: form.email,
        password: form.password,
        invite_code: form.invite_code,
      })
    }
    message.success(mode.value === 'login' ? '欢迎回来' : '账号创建成功，开始记录你的投递吧')
    router.push({ name: 'board' })
  } catch (e: any) {
    message.error(e?.message || '操作失败，请重试')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page dotgrid">
    <div class="auth-card">
      <div class="brand">
        <span class="brand-dot" aria-hidden="true"></span>
        <span class="brand-name">JobCheck</span>
      </div>
      <h1 class="auth-title">{{ mode === 'login' ? '看看今天谁动了' : '把秋招进度收进一张看板' }}</h1>
      <p class="auth-sub">投递状态追踪 · 只记录，不打扰</p>

      <n-tabs :value="mode" size="medium" pane-style="padding: 8px 2px 0">
        <n-tab name="login" tab="登录" @click="mode = 'login'" />
        <n-tab name="register" tab="注册" @click="mode = 'register'" />
      </n-tabs>

      <n-form label-placement="top" @keyup.enter="submit">
        <n-form-item label="邮箱" :feedback="errors.email" :validation-status="errors.email ? 'error' : undefined">
          <n-input v-model:value="form.email" placeholder="you@example.com" :input-props="{ type: 'email', autocomplete: 'username' }" />
        </n-form-item>
        <n-form-item label="密码" :feedback="errors.password" :validation-status="errors.password ? 'error' : undefined">
          <n-input v-model:value="form.password" type="password" show-password-on="click" placeholder="至少 8 位" :input-props="{ autocomplete: 'current-password' }" />
        </n-form-item>
        <n-form-item v-if="mode === 'register'" label="邀请码" :feedback="errors.invite_code" :validation-status="errors.invite_code ? 'error' : undefined">
          <n-input v-model:value="form.invite_code" placeholder="向管理员索取" />
        </n-form-item>
        <n-button type="primary" block size="large" :loading="submitting" @click="submit">
          {{ mode === 'login' ? '登录' : '创建账号' }}
        </n-button>
      </n-form>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.auth-card {
  width: 400px;
  max-width: 100%;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 36px 36px 30px;
  box-shadow: 0 12px 40px rgba(31, 39, 51, 0.06);
}
.brand { display: flex; align-items: center; gap: 10px; margin-bottom: 22px; }
.brand-dot {
  width: 14px; height: 14px; border-radius: 4px;
  background: linear-gradient(135deg, #6188d8 0%, #3e9e8c 50%, #d89c2e 100%);
}
.brand-name { font-weight: 600; letter-spacing: 0.14em; font-size: 15px; color: var(--brand); }
.auth-title { font-size: 22px; margin: 0 0 4px; line-height: 1.35; }
.auth-sub { color: var(--ink-2); margin: 0 0 14px; font-size: 13px; }
</style>
