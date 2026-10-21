import { reactive } from 'vue'
import { config } from '../config'
import type { LoginResult, Session } from '../types/domain'

const SESSION_KEY = 'zj-shell:session:v1'
const ACCOUNT_KEY = 'zj-shell:account:v1'
const mode = config.useMock ? 'mock' : 'live'
export const sessionState = reactive<{ session: Session | null }>({ session: null })

export function hydrateSession() {
  try {
    const saved = uni.getStorageSync(SESSION_KEY) as Session | undefined
    if (saved && saved.mode === mode && typeof saved.accessToken === 'string' && saved.accessToken && saved.expiresAt > Date.now() && saved.user?.id) {
      sessionState.session = saved
      return
    }
  } catch { /* A failed storage read keeps the user signed out. */ }
  clearSession()
}
export function saveSession(result: LoginResult) {
  if (!result.accessToken || !Number.isFinite(result.expiresIn) || result.expiresIn <= 0 || !result.user?.id) {
    throw new Error('登录返回数据不完整，请联系服务维护人员')
  }
  const session: Session = { accessToken: result.accessToken, expiresAt: Date.now() + result.expiresIn * 1000, user: result.user, mode }
  uni.setStorageSync(SESSION_KEY, session)
  sessionState.session = session
}
export function clearSession() {
  sessionState.session = null
  uni.removeStorageSync(SESSION_KEY)
}
export function getToken(): string | null {
  const session = sessionState.session
  if (!session || session.mode !== mode || session.expiresAt <= Date.now()) {
    if (session) clearSession()
    return null
  }
  return session.accessToken
}
export function rememberAccount(account: string, remember: boolean) {
  if (remember) uni.setStorageSync(ACCOUNT_KEY, account)
  else uni.removeStorageSync(ACCOUNT_KEY)
}
export function getRememberedAccount(): string {
  return uni.getStorageSync(ACCOUNT_KEY) || ''
}
