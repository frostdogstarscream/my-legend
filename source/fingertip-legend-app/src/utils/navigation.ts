import { getToken } from '../stores/session'
export type TabId = 'home' | 'create' | 'stories' | 'characters' | 'profile'
export function requireSession(): boolean {
  if (getToken()) return true
  uni.reLaunch({ url: '/pages/login/index' })
  return false
}
export function goTab(id: TabId) { uni.reLaunch({ url: `/pages/${id}/index` }) }
export type PageId = 'shop' | 'detail' | 'reader' | 'progress' | 'outline' | 'role-detail' | 'performance' | 'production' | 'work' | TabId
export function goPage(id: PageId, query: Record<string, string | number> = {}) { const qs = Object.entries(query).map(([k,v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&'); uni.navigateTo({ url: `/pages/${id}/index${qs ? '?' + qs : ''}` }) }
export function goBack() {
  if (getCurrentPages().length > 1) uni.navigateBack({ fail: () => goTab('home') })
  else goTab('home')
}
export function showMessage(message: string) { uni.showToast({ title: message, icon: 'none', duration: 2200 }) }
export function showPending(title: string, content: string) {
  uni.showModal({ title, content, showCancel: false, confirmText: '知道了', confirmColor: '#cf502a' })
}
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '加载失败，请重试' }
