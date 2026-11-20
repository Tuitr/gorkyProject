import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'

// На странице входа нет. Кабинет (services/cabinet) пускает только учителя с сессией,
// поэтому dev-сервер сам входит служебным учителем и подставляет его cookie в каждый запрос /v1.
// Учётка — CHECKER_TEACHER_EMAIL / CHECKER_TEACHER_PASSWORD в .env.local
// (по умолчанию демо-учитель стенда из .env.example бэкенда).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'CHECKER_')
  const cabinetUrl = (env.CHECKER_CABINET_URL || 'http://127.0.0.1:18080').replace(/\/$/, '')
  const email = env.CHECKER_TEACHER_EMAIL || 'teacher.demo@example.org'
  const password = env.CHECKER_TEACHER_PASSWORD || 'stand-only-password'

  let cookie: string | undefined
  let pending: Promise<string | undefined> | undefined

  const login = async (): Promise<string | undefined> => {
    try {
      const res = await fetch(`${cabinetUrl}/v1/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Origin: cabinetUrl },
        body: JSON.stringify({ email, password }),
      })
      if (!res.ok) {
        console.error(`[cabinet] вход служебного учителя не удался: ${res.status}`)
        return undefined
      }
      const set = res.headers.getSetCookie().find((c) => c.startsWith('__Host-cabinet='))
      return set?.split(';')[0]
    } catch {
      console.error(`[cabinet] кабинет не отвечает: ${cabinetUrl}`)
      return undefined
    }
  }
  const ensureCookie = () => {
    if (cookie) return Promise.resolve(cookie)
    pending ??= login().then((c) => {
      cookie = c
      pending = undefined
      return c
    })
    return pending
  }

  // Срабатывает до прокси: дожидаемся сессии и кладём её в запрос.
  const session: Plugin = {
    name: 'cabinet-session',
    configureServer: (server) => attach(server.middlewares),
    configurePreviewServer: (server) => attach(server.middlewares),
  }
  function attach(m: { use: (fn: (req: any, res: any, next: () => void) => void) => void }) {
    m.use(async (req, _res, next) => {
      if (req.url?.startsWith('/v1/')) {
        const c = await ensureCookie()
        if (c) req.headers.cookie = c
        else delete req.headers.cookie
      }
      next()
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
        p.on('proxyRes', (proxyRes) => {
          // Сессия истекла (8 ч) — следующий запрос войдёт заново. Cookie кабинета браузеру не отдаём.
          if (proxyRes.statusCode === 401) cookie = undefined
          delete proxyRes.headers['set-cookie']
        })
      },
    },
  }

  return { plugins: [react(), session], server: { proxy }, preview: { proxy } }
})
