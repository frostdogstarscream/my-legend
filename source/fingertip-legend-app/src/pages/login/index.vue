<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import BrandLogo from '../../components/BrandLogo.vue'
import { api } from '../../services/api'
import { config } from '../../config'
import { getRememberedAccount, rememberAccount, saveSession } from '../../stores/session'
import { errorMessage, goTab, showPending } from '../../utils/navigation'

const account = ref(''), password = ref(''), inviteCode = ref('')
const remember = ref(true), agreed = ref(false), visiblePassword = ref(false), busy = ref(false)
const formError = ref('')
onLoad(() => { account.value = getRememberedAccount() || (config.useMock ? 'FengQiYunYong' : '') })
async function login(demo = false) {
  if (busy.value) return
  formError.value = ''
  if (!demo && !account.value.trim()) { formError.value = '请输入账号'; return }
  if (!demo && !password.value) { formError.value = '请输入密码'; return }
  if (!demo && !agreed.value) { formError.value = '请先阅读并同意用户协议和隐私政策'; return }
  if (demo && !config.useMock && !config.localBackend) return
  busy.value = true
  try {
    const result = demo && config.localBackend ? await api.localLogin() : await api.login({ account: demo ? 'demo' : account.value.trim(), password: demo ? 'demo' : password.value, inviteCode: inviteCode.value.trim() || undefined })
    saveSession(result)
    if (!demo) rememberAccount(account.value.trim(), remember.value)
    password.value = ''
    goTab('home')
  } catch (error) { formError.value = errorMessage(error) }
  finally { busy.value = false }
}
const legal = (kind: string) => showPending(kind, '正式协议内容尚未配置。当前仅提供本地演示，发布和真实注册前需要接入正式协议版本与同意记录。')
</script>

<template>
  <view class="login-screen">
    <image class="login-background" src="/static/figma/login-background.png" mode="aspectFill" aria-hidden="true" />
    <view class="login-shade" />
    <view class="login-brand"><BrandLogo variant="login" /></view>
    <view class="login-panel">
      <view v-if="config.localBackend" class="local-entry">
        <text class="section-title serif">开始我的传说</text>
        <text class="small-note spaced">当前为本机创作体验。故事与广播剧由真实 AI 服务生成，先交付试听，再根据你的反馈修改。</text>
        <text v-if="formError" class="form-error" role="alert">{{formError}}</text>
        <button class="primary-button login-button" :loading="busy" :disabled="busy" @tap="login(true)">进入本机创作</button>
        <text class="small-note spaced">无需输入模型密钥。正式账号、会员和支付尚未开放。</text>
      </view>
      <template v-else>
      <view class="login-tabs">
        <view class="login-tab--selected"><text>账号密码登录</text><view class="tab-underline" /></view>
        <button class="sms-tab" @tap="showPending('验证码登录', '手机号、验证码发送与验证接口尚未接入，当前请使用账号演示。')">手机号验证码登录</button>
      </view>
      <view class="login-fields">
        <view class="login-field"><image src="/static/figma/account.svg" class="field-icon" aria-hidden="true" /><input v-model="account" class="field-input" aria-label="账号" placeholder="请输入账号" maxlength="64" :disabled="busy" /></view>
        <view class="login-field"><image src="/static/figma/lock.svg" class="field-icon" aria-hidden="true" /><input v-model="password" class="field-input" aria-label="密码" :password="!visiblePassword" placeholder="请输入密码" maxlength="128" :disabled="busy" @confirm="login(false)" /><button class="eye-button" :aria-label="visiblePassword ? '隐藏密码' : '显示密码'" @tap="visiblePassword = !visiblePassword"><image src="/static/figma/eye-off.svg" class="icon" /></button></view>
        <view class="login-field"><image src="/static/figma/gift.svg" class="field-icon" aria-hidden="true" /><input v-model="inviteCode" class="field-input" aria-label="邀请码（选填）" placeholder="邀请码（选填）" maxlength="32" :disabled="busy" /></view>
      </view>
      <view class="login-options">
        <button class="check-label" :aria-pressed="remember" @tap="remember = !remember"><view class="custom-check" :class="{ checked: remember }"><image v-if="remember" src="/static/figma/check.svg" /></view><text>记住账号</text></button>
        <button @tap="showPending('找回密码', '后续接入账号找回、验证码验证与密码重置接口。')">忘记密码？</button>
      </view>
      <text v-if="formError" class="form-error" role="alert">{{ formError }}</text>
      <button class="primary-button login-button" :disabled="busy" :loading="busy" @tap="login(false)"><text>{{ busy ? '登录中' : '登 录' }}</text><text class="login-star">✦</text></button>
      <view class="register-row"><text>还没有账号？</text><button @tap="showPending('注册账号', '注册入口已预留，需接入正式账号服务。')">立即注册 ›</button></view>
      <view class="legal-row">
        <button class="legal-check-button" aria-label="同意用户协议和隐私政策" :aria-pressed="agreed" @tap="agreed = !agreed"><view class="custom-check legal-check" :class="{ checked: agreed }"><image v-if="agreed" src="/static/figma/check-small.svg" /></view></button>
        <text>我已阅读并同意</text><button @tap="legal('用户协议')">《用户协议》</button><button @tap="legal('隐私政策')">《隐私政策》</button>
      </view>
      <view v-if="config.useMock" class="demo-row"><button :disabled="busy" @tap="login(true)">先体验一下 ›</button><text>本地演示 · 不验证账号</text></view>
      </template>
    </view>
  </view>
</template>

<style scoped>
.login-screen { position: relative; display: flex; flex-direction: column; min-height: 100vh; overflow: hidden; padding-bottom: calc(120rpx + var(--bottom-inset)); }
.login-background, .login-shade { position: absolute; inset: 0; width: 100%; height: 100%; }
.login-shade { background: linear-gradient(#0003,transparent 50%,#0000004d); }
.login-brand { position: relative; padding-top: var(--status-inset); }
.login-panel { position: relative; width: calc(100% - 76rpx); margin: clamp(190rpx, 19vh, 330rpx) auto 0; padding: 38rpx; border: 2rpx solid #f59e0b59; border-radius: 54rpx; background: #fffdf8f2; backdrop-filter: blur(20px); box-shadow: 0 30rpx 68rpx #00000059; }
.login-tabs { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; padding-bottom: 23rpx; border-bottom: 2rpx solid #78350f1a; }
.login-tab--selected { display: flex; flex-direction: column; align-items: center; color: #1d4ed8; font-size: 27rpx; font-weight: 700; white-space: nowrap; }
.tab-underline { width: 61rpx; height: 4rpx; background: #2563eb; margin-top: 8rpx; border-radius: 999rpx; }
.sms-tab { padding: 8rpx 24rpx; border-radius: 999rpx; background: #78350f0d; color: #475569; font-size: 22rpx; white-space: nowrap; }
.login-fields { display: flex; flex-direction: column; gap: 19rpx; padding-top: 30rpx; }
.login-field { display: flex; align-items: center; height: 77rpx; padding: 0 27rpx; border: 2rpx solid #78350f26; border-radius: 23rpx; background: #fffbeb99; box-shadow: inset 0 4rpx 8rpx #0000000d; }
.field-icon { width: 28rpx; height: 31rpx; margin-right: 19rpx; }
.field-input { flex: 1; min-width: 0; height: 100%; font-size: 23rpx; color: #1e293b; }
.eye-button { display: flex; align-items: center; justify-content: center; width: 55rpx; height: 70rpx; }
.login-options { display: flex; align-items: center; justify-content: space-between; color: #64748b; padding: 22rpx 8rpx 0; font-size: 23rpx; }
.login-options button { min-height: 40rpx; }
.check-label { display: flex; align-items: center; gap: 11rpx; color: #475569; }
.custom-check { display: flex; align-items: center; justify-content: center; width: 27rpx; height: 27rpx; border: 2rpx solid #94a3b8; border-radius: 4rpx; background: white; }
.custom-check.checked { background: #2563eb; border-color: #2563eb; }
.custom-check image { width: 23rpx; height: 23rpx; }
.login-button { margin-top: 22rpx; height: 92rpx; min-height: 92rpx; font-size: 31rpx; letter-spacing: 3rpx; }
.login-star { color: #ffffff85; font-size: 23rpx; }
.register-row { display: flex; align-items: center; justify-content: center; gap: 14rpx; margin-top: 23rpx; font-size: 23rpx; color: #64748b; }
.register-row button { color: #2563eb; font-weight: 700; min-height: 35rpx; }
.legal-row { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; margin-top: 22rpx; padding-top: 19rpx; border-top: 2rpx solid #78350f1a; font-size: 18rpx; color: #64748b; }
.legal-row button { color: #2563eb; min-height: 36rpx; }
.legal-check-button { display: flex; align-items: center; justify-content: center; width: 36rpx; }
.legal-check { width: 23rpx; height: 23rpx; }
.legal-check image { width: 19rpx; height: 19rpx; }
.demo-row { display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 14rpx; padding-top: 18rpx; font-size: 18rpx; color: #a38d77; }
.demo-row button { font-size: 21rpx; color: #856143; padding: 8rpx 12rpx; }
.form-error { display: block; color: #b42318; font-size: 22rpx; line-height: 1.5; margin-top: 18rpx; }
</style>
