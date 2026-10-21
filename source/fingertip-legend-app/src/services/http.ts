import type { ApiEnvelope } from '../types/domain'

export class ApiError extends Error {
  constructor(message: string, public readonly code: number, public readonly status?: number) {
    super(message)
    this.name = 'ApiError'
  }
}
export interface TransportRequest {
  url: string
  method: 'GET' | 'POST'
  data?: Record<string, unknown>
  headers: Record<string, string>
  timeout: number
}
export interface TransportResponse { statusCode: number; body: unknown }
export type Transport = (request: TransportRequest) => Promise<TransportResponse>

export function createHttpClient(options: {
  baseUrl: string; timeout: number; getToken: () => string | null;
  onUnauthorized: () => void; transport: Transport
}) {
  return async function request<T>(path: string, method: 'GET' | 'POST' = 'GET', data?: Record<string, unknown>, auth = true): Promise<T> {
    if (!/^https?:\/\//.test(options.baseUrl)) {
      throw new ApiError('请先配置后台服务地址', -1)
    }
    const token = auth ? options.getToken() : null
    if (auth && !token) {
      options.onUnauthorized()
      throw new ApiError('登录已过期，请重新登录', 401, 401)
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers.Authorization = `Bearer ${token}`
    let response: TransportResponse
    try {
      response = await options.transport({ url: options.baseUrl + path, method, data, headers, timeout: options.timeout })
    } catch {
      throw new ApiError('网络连接失败，请稍后重试', -2)
    }
    const body = response.body as Partial<ApiEnvelope<T>> | null
    if (response.statusCode === 401 || (body && body.code === 401)) {
      if (auth) options.onUnauthorized()
      throw new ApiError(auth ? '登录已过期，请重新登录' : '账号或密码错误', 401, response.statusCode)
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      if (body && typeof body.code === 'number' && [404,409,422,503].includes(response.statusCode) && typeof body.message === 'string') {
        throw new ApiError(body.message, body.code, response.statusCode)
      }
      throw new ApiError(response.statusCode === 403 ? '暂时没有访问权限' : '服务暂时不可用，请稍后重试', response.statusCode, response.statusCode)
    }
    if (!body || typeof body.code !== 'number') throw new ApiError('服务返回格式不正确', -3, response.statusCode)
    if (body.code !== 0) throw new ApiError(body.message || '操作未成功，请重试', body.code, response.statusCode)
    if (!Object.prototype.hasOwnProperty.call(body, 'data')) throw new ApiError('服务返回数据不完整', -3, response.statusCode)
    return body.data as T
  }
}
