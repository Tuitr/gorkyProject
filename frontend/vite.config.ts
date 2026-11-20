import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type ProxyOptions } from 'vite'
import { viteStaticCopy } from 'vite-plugin-static-copy'
import { authPlugin } from './server/auth.ts'
import { gigachatPlugin } from './server/gigachat.ts'
import { solverPlugin } from './server/solver.ts'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Ключ читается только здесь, на стороне dev-сервера, и в браузер не попадает.
  const env = loadEnv(mode, process.cwd(), 'CHECKER_')

  // Бэкенд «кабинет» из main (services/cabinet). Пусто — фронтенд работает сам (данные в браузере).
  const cabinetUrl = (env.CHECKER_CABINET_URL ?? '').replace(/\/$/, '')

  const ocrProxy: Record<string, ProxyOptions> = {
    '/api/ocr': {
      target: 'https://ocr.api.cloud.yandex.net',
      changeOrigin: true,
      rewrite: () => '/ocr/v1/recognizeText',
      headers: {
        Authorization: `Api-Key ${env.CHECKER_YC_API_KEY ?? ''}`,
        'x-data-logging-enabled': 'false',
        ...(env.CHECKER_YC_FOLDER_ID ? { 'x-folder-id': env.CHECKER_YC_FOLDER_ID } : {}),
      },
    },
  }
  if (cabinetUrl) {
    ocrProxy['/v1'] = {
      target: cabinetUrl,
      changeOrigin: true,
      configure: (proxy) => {
        // Кабинет отклоняет запросы, у которых Origin не совпадает с его адресом (защита от CSRF).
        // Запрос с нашей же страницы (Origin = адрес dev-сервера) подписываем адресом кабинета;
        // чужой Origin оставляем как есть — кабинет его отклонит.
        proxy.on('proxyReq', (proxyReq, req) => {
          const origin = req.headers.origin
          if (origin && req.headers.host && new URL(origin).host === req.headers.host) proxyReq.setHeader('origin', cabinetUrl)
        })
      },
    }
  }

  return {
    // authPlugin — первым: без входа остальные /api/* отвечают 401
    plugins: [
      authPlugin(env),
      react(),
      gigachatPlugin(env),
      solverPlugin(env),
      viteStaticCopy({
        targets: [{
          src: 'node_modules/pdfjs-dist/{cmaps,iccs,standard_fonts,wasm}',
          dest: 'pdfjs',
          rename: { stripBase: 2 },
        }],
      }),
    ],
    server: { proxy: ocrProxy },
    preview: { proxy: ocrProxy },
  }
})
