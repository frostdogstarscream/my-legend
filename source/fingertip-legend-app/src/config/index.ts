export const config = Object.freeze({
  useMock: import.meta.env.VITE_USE_MOCK !== 'false',
  localBackend: import.meta.env.VITE_LOCAL_BACKEND === 'true',
  apiBaseUrl: (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, ''),
  requestTimeout: 15_000,
})
