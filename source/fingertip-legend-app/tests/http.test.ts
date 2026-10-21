import { describe, expect, it, vi } from 'vitest'
import { createHttpClient, type TransportResponse } from '../src/services/http'

function setup(response: TransportResponse = { statusCode: 200, body: { code: 0, message: 'ok', data: { id: 'story-1' } } }) {
  const transport = vi.fn(async () => response)
  const onUnauthorized = vi.fn()
  const getToken = vi.fn(() => 'unit-test-session')
  const request = createHttpClient({ baseUrl: 'https://example.test/api/v1', timeout: 15000, transport, onUnauthorized, getToken })
  return { request, transport, onUnauthorized, getToken }
}
describe('真实接口边界', () => {
  it('只解析约定的 envelope，并向已登录接口附加认证', async () => {
    const { request, transport } = setup()
    expect(await request('/stories')).toEqual({ id: 'story-1' })
    expect(transport).toHaveBeenCalledWith(expect.objectContaining({ url: 'https://example.test/api/v1/stories', method: 'GET', timeout: 15000, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer unit-test-session' } }))
  })
  it('登录请求不带旧会话，也不因账号错误清理其他登录状态', async () => {
    const { request, transport, onUnauthorized, getToken } = setup({ statusCode: 401, body: { code: 401, message: 'invalid', data: null } })
    await expect(request('/auth/login', 'POST', { account: 'demo', password: 'test-only' }, false)).rejects.toMatchObject({ code: 401, message: '账号或密码错误' })
    expect(getToken).not.toHaveBeenCalled()
    expect(transport.mock.calls[0][0].headers).not.toHaveProperty('Authorization')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
  it.each([401, 200])('HTTP 或业务层 401 均触发重新登录（HTTP %s）', async statusCode => {
    const { request, onUnauthorized } = setup({ statusCode, body: { code: 401, message: 'expired', data: null } })
    await expect(request('/home')).rejects.toMatchObject({ code: 401 })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })
  it('缺少会话时不向服务器发送请求', async () => {
    const transport = vi.fn(), onUnauthorized = vi.fn()
    const request = createHttpClient({ baseUrl: 'https://example.test/api/v1', timeout: 15000, transport, onUnauthorized, getToken: () => null })
    await expect(request('/home')).rejects.toMatchObject({ code: 401 })
    expect(transport).not.toHaveBeenCalled()
  })
  it('没有配置后台地址时不请求错误目标', async () => {
    const transport = vi.fn()
    const request = createHttpClient({ baseUrl: '', timeout: 15000, transport, onUnauthorized: vi.fn(), getToken: () => null })
    await expect(request('/auth/login', 'POST', {}, false)).rejects.toMatchObject({ code: -1 })
    expect(transport).not.toHaveBeenCalled()
  })
  it('HTTP 500 不被 success 回调当成业务成功', async () => {
    const { request } = setup({ statusCode: 500, body: { code: 0, message: 'ok', data: {} } })
    await expect(request('/stories')).rejects.toMatchObject({ status: 500 })
  })
  it.each([404,409,422,503])('保留故事接口的可操作错误说明（HTTP %s）',async statusCode=>{
    const {request}=setup({statusCode,body:{code:statusCode,message:'大纲已更新，请重新加载',data:null}})
    await expect(request('/generation-tasks/test/approve','POST',{})).rejects.toMatchObject({status:statusCode,message:'大纲已更新，请重新加载'})
  })
  it('HTTP 200 下的业务错误仍向页面报告失败', async () => {
    const { request } = setup({ statusCode: 200, body: { code: 409, message: '草稿版本已更新', data: null } })
    await expect(request('/stories', 'POST', {})).rejects.toMatchObject({ code: 409, message: '草稿版本已更新' })
  })
  it.each([null, '<html>proxy page</html>', { code: 0 }])('拒绝不完整的响应结构：%j', async body => {
    const { request } = setup({ statusCode: 200, body })
    await expect(request('/home')).rejects.toMatchObject({ code: -3 })
  })
  it('允许正常注销接口返回 data:null', async () => {
    const { request } = setup({ statusCode: 200, body: { code: 0, message: 'ok', data: null } })
    expect(await request('/auth/logout', 'POST', {})).toBeNull()
  })
  it('网络异常提供可读消息，不暴露底层认证请求', async () => {
    const { request, transport } = setup()
    transport.mockRejectedValueOnce(new Error('transport internal details'))
    await expect(request('/home')).rejects.toMatchObject({ code: -2, message: '网络连接失败，请稍后重试' })
  })
})
