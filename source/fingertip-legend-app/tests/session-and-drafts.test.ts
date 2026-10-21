import { beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('../src/config', () => ({ config: { useMock: true } }))
const storage = new Map<string, unknown>()
const user = { id: 'demo-user', displayName: '测试用户', avatarUrl: '', vipDays: 28, balance: 1250 }
beforeEach(() => {
  vi.resetModules(); storage.clear()
  vi.stubGlobal('uni', {
    getStorageSync: (key: string) => { const value = storage.get(key); return value === undefined ? undefined : JSON.parse(JSON.stringify(value)) },
    setStorageSync: (key: string, value: unknown) => storage.set(key, JSON.parse(JSON.stringify(value))),
    removeStorageSync: (key: string) => storage.delete(key),
  })
})
describe('会话和本地草稿', () => {
  it('重新打开 App 恢复有效会话', async () => {
    const module = await import('../src/stores/session')
    module.saveSession({ accessToken: 'unit-test-only', expiresIn: 3600, user })
    const reopened = await import('../src/stores/session')
    reopened.sessionState.session = null
    reopened.hydrateSession()
    expect(reopened.getToken()).toBe('unit-test-only')
  })
  it('过期会话不会被恢复', async () => {
    storage.set('zj-shell:session:v1', { accessToken: 'unit-test-only', expiresAt: Date.now() - 1, mode: 'mock', user })
    const module = await import('../src/stores/session'); module.hydrateSession()
    expect(module.getToken()).toBeNull()
    expect(storage.has('zj-shell:session:v1')).toBe(false)
  })
  it('演示模式不会复用正式环境会话', async () => {
    storage.set('zj-shell:session:v1', { accessToken: 'unit-test-only', expiresAt: Date.now() + 60000, mode: 'live', user })
    const module = await import('../src/stores/session'); module.hydrateSession()
    expect(module.getToken()).toBeNull()
  })
  it('退出登录清除会话，同时保留用户选择记住的账号', async () => {
    const module = await import('../src/stores/session')
    module.saveSession({ accessToken: 'unit-test-only', expiresIn: 3600, user })
    module.rememberAccount('FengQiYunYong', true); module.clearSession()
    expect(module.getToken()).toBeNull()
    expect(module.getRememberedAccount()).toBe('FengQiYunYong')
  })
  it('记住账号只保存账号，不保存密码；取消记住时清除', async () => {
    const module = await import('../src/stores/session')
    module.rememberAccount('account-001', true)
    expect([...storage.values()]).toEqual(['account-001'])
    module.rememberAccount('account-001', false)
    expect(module.getRememberedAccount()).toBe('')
  })
  it('拒绝缺少有效期的登录结果', async () => {
    const module = await import('../src/stores/session')
    expect(() => module.saveSession({ accessToken: 'unit-test-only', expiresIn: NaN, user })).toThrow()
    expect(module.getToken()).toBeNull()
  })
  it('新草稿保存后重读仍存在，首页续写入口指向最新草稿', async () => {
    const { mockGateway } = await import('../src/services/mock')
    const story = await mockGateway.createDraft({ title: '本地草稿验证', genre: '国风奇幻', idea: '少年寻找失落的书卷' })
    expect((await mockGateway.stories())[0]).toMatchObject({ id: story.id, status: 'draft', progress: 0 })
    expect((await mockGateway.home()).latestStory?.id).toBe(story.id)
    expect((await mockGateway.stories()).some(item => item.id === 'demo-story-001')).toBe(true)
  })
})
