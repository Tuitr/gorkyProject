import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type ProxyOptions } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'CHECKER_')

  // Единственный сервер фронтенда — кабинет (services/cabinet): его /v1 проксируем с того же адреса,
  // чтобы cookie сессии была своей для страницы.
  const cabinetUrl = (env.CHECKER_CABINET_URL || 'http://127.0.0.1:18080').replace(/\/$/, '')

  const proxy: Record<string, ProxyOptions> = {
    '/v1': {
      target: cabinetUrl,
      changeOrigin: true,
      configure: (p) => {
        // Кабинет отклоняет запросы, у которых Origin не совпадает с его адресом (защита от CSRF).
        // Запрос с нашей же страницы (Origin = адрес dev-сервера) подписываем адресом кабинета;
        // чужой Origin оставляем как есть — кабинет его отклонит.
        p.on('proxyReq', (proxyReq, req) => {
          const origin = req.headers.origin
          if (origin && req.headers.host && new URL(origin).host === req.headers.host) proxyReq.setHeader('origin', cabinetUrl)
        })
      },
    },
  }

  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
  }
})
