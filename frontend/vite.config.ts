import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'

// Учитель входит сам: страница шлёт POST /v1/sessions через прокси, кабинет (services/cabinet)
// ставит HttpOnly-cookie сессии, и браузер дальше прикладывает её к каждому запросу /v1.
// Адрес кабинета — CHECKER_CABINET_URL в .env.local.
const COOKIE = '__Host-cabinet'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'CHECKER_')
  const cabinetUrl = (env.CHECKER_CABINET_URL || 'http://127.0.0.1:18080').replace(/\/$/, '')

  // У кабинета нет выхода, а cookie HttpOnly — JS её не сотрёт. Выход: POST /auth/logout, сервер dev-стенда гасит cookie.
  const logout: Plugin = {
    name: 'cabinet-logout',
    configureServer: (server) => attach(server.middlewares),
    configurePreviewServer: (server) => attach(server.middlewares),
  }
  function attach(m: { use: (fn: (req: any, res: any, next: () => void) => void) => void }) {
    m.use((req, res, next) => {
      if (req.url !== '/auth/logout' || req.method !== 'POST') return next()
      res.statusCode = 204
      res.setHeader('Set-Cookie', `${COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict`)
      res.setHeader('Cache-Control', 'no-store')
      res.end()
    })
  }

  const proxy: Record<string, ProxyOptions> = {
    '/v1': {
      target: cabinetUrl,
      changeOrigin: true,
      configure: (p) => {
        // Кабинет отклоняет запросы с чужим Origin (защита от CSRF): свой Origin подменяем адресом кабинета.
        p.on('proxyReq', (proxyReq, req) => {
          const origin = req.headers.origin
          if (origin && req.headers.host && new URL(origin).host === req.headers.host) {
            proxyReq.setHeader('origin', cabinetUrl)
          }
        })
      },
    },
  }

  return { plugins: [react(), logout], server: { proxy }, preview: { proxy } }
})
