import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type ProxyOptions } from 'vite'
import { gigachatPlugin } from './server/gigachat.ts'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Ключ читается только здесь, на стороне dev-сервера, и в браузер не попадает.
  const env = loadEnv(mode, process.cwd(), 'CHECKER_')

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

  return {
    plugins: [react(), gigachatPlugin(env)],
    server: { proxy: ocrProxy },
    preview: { proxy: ocrProxy },
  }
})
