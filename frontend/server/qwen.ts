import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

/**
 * Распознавание рукописи мультимодальной Qwen. По умолчанию — флагман qwen3.8-max в Alibaba Model Studio
 * (международный регион, Сингапур): новым аккаунтам 1 млн токенов на модель бесплатно на 90 дней.
 * POST /api/ocr-qwen { content: base64 JPEG/PNG, mimeType } → { latex, lines: [{ text, box }] },
 * box — в долях от размера фото (как у Yandex OCR в api/ocr.ts).
 * Если ключа нет — 501, и клиент сам уходит на Yandex Vision.
 *
 * .env.local:
 *   CHECKER_QWEN_API_KEY=sk-...         ключ Model Studio (или OpenRouter — тогда поменять адрес и модель)
 *   CHECKER_QWEN_MODEL=qwen3.8-max      необязательно
 *   CHECKER_QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1   необязательно
 */

const PROMPT = `Ты — точный OCR рукописных школьных работ по математике (русский язык).
Перепиши ВСЁ, что написано на фото, строка за строкой, ДОСЛОВНО — как написал ученик.
- НИЧЕГО не исправляй и не дописывай: ошибки, описки, неверные ответы, зачёркивания оставь как есть (зачёркнутое — \\sout{...}).
- Не решай задачи и не додумывай неразборчивое: неразборчивый фрагмент — [?].
- Формулы — LaTeX в $...$ (векторы \\vec{a}, дроби \\frac{}{}, корни \\sqrt{}, системы \\begin{cases}...\\end{cases}); обычный текст — как есть.
- Одна строка рукописи = один элемент. Порядок — сверху вниз, слева направо.
- box — рамка строки [x0, y0, x1, y1] в долях 0–1000 от ширины и высоты фото.
Ответ — только JSON без пояснений:
{"lines":[{"text":"...","box":[x0,y0,x1,y1]}]}`

type Line = { text: string; box: { x: number; y: number; w: number; h: number } }

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let s = ''
    req.setEncoding('utf8')
    req.on('data', (c: string) => (s += c))
    req.on('end', () => resolve(s))
    req.on('error', reject)
  })
}

/** Достаёт JSON из ответа модели (может быть в ```json ... ``` или с текстом вокруг). */
function parseLines(raw: string): Line[] {
  const from = raw.indexOf('{')
  const to = raw.lastIndexOf('}')
  if (from < 0 || to <= from) throw new Error('Qwen вернула не JSON')
  // Одиночный \ перед командой LaTeX (\vec, \frac, \neq…) в JSON — ошибка или, хуже, \f/\n/\t.
  // Удваиваем его, уже экранированные \\ не трогаем.
  const json = raw.slice(from, to + 1).replace(/(?<!\\)\\(?=[A-Za-z]{2,})/g, '\\\\')
  const data = JSON.parse(json) as { lines?: { text?: unknown; box?: unknown }[] }
  return (data.lines ?? [])
    .filter((l) => typeof l.text === 'string' && l.text.trim())
    .map((l) => {
      const b = Array.isArray(l.box) && l.box.length === 4 ? l.box.map(Number) : [0, 0, 0, 0]
      const [x0, y0, x1, y1] = b.map((v) => Math.min(1, Math.max(0, (Number.isFinite(v) ? v : 0) / 1000)))
      return { text: String(l.text).trim(), box: { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) } }
    })
}

async function recognize(env: Record<string, string>, content: string, mimeType: string) {
  const base = (env.CHECKER_QWEN_BASE_URL || 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1').replace(/\/$/, '')
  const model = env.CHECKER_QWEN_MODEL || 'qwen3.8-max'
  const openrouter = base.includes('openrouter.ai')
  const mime = mimeType === 'PNG' ? 'image/png' : 'image/jpeg'
  const body = {
    model,
    temperature: 0,
    max_tokens: 8000,
    // переписывание — не рассуждение: без «обдумывания» быстрее, дешевле (бесплатная квота)
    // и модель меньше «исправляет» ученика
    ...(openrouter ? { reasoning: { effort: 'low' } } : { enable_thinking: false }),
    messages: [
      {
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: `data:${mime};base64,${content}` } },
          { type: 'text', text: PROMPT },
        ],
      },
    ],
  }
  let lastErr = ''
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(`${base}/chat/completions`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${env.CHECKER_QWEN_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = (await res.json().catch(() => null)) as {
      choices?: { message?: { content?: string } }[]
      error?: { message?: string }
    } | null
    if (!res.ok) {
      lastErr = data?.error?.message ?? `Qwen: ошибка ${res.status}`
      if (res.status === 429 || res.status >= 500) {
        await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)))
        continue
      }
      throw new Error(lastErr)
    }
    const raw = data?.choices?.[0]?.message?.content ?? ''
    try {
      const lines = parseLines(raw)
      return { latex: lines.map((l) => l.text).join('\n\n'), lines }
    } catch (e) {
      lastErr = e instanceof Error ? e.message : 'Qwen: битый ответ'
      console.warn('[qwen] не разобран ответ:', raw.slice(0, 500))
    }
  }
  throw new Error(lastErr || 'Qwen не ответила')
}

export function qwenPlugin(env: Record<string, string>): Plugin {
  const handler = async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    if (req.url !== '/api/ocr-qwen' || req.method !== 'POST') return next()
    const send = (status: number, data: unknown) => {
      res.statusCode = status
      res.setHeader('Content-Type', 'application/json; charset=utf-8')
      res.end(JSON.stringify(data))
    }
    if (!env.CHECKER_QWEN_API_KEY) return send(501, { message: 'Нет CHECKER_QWEN_API_KEY в .env.local' })
    try {
      const { content, mimeType } = JSON.parse(await readBody(req)) as { content?: string; mimeType?: string }
      if (!content) return send(400, { message: 'Нет фото' })
      send(200, await recognize(env, content, mimeType ?? 'JPEG'))
    } catch (e) {
      send(502, { message: e instanceof Error ? e.message : 'Ошибка Qwen' })
    }
  }
  return {
    name: 'qwen-ocr',
    configureServer: (server) => void server.middlewares.use(handler),
    configurePreviewServer: (server) => void server.middlewares.use(handler),
  }
}
