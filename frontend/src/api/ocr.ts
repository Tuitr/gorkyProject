import type { Box } from '../types'

/**
 * Распознавание фото через Yandex Vision OCR (модель math-markdown:
 * текст в Markdown, формулы — LaTeX в $...$ / $$...$$).
 * Ключи добавляет сервер Vite (vite.config.ts), в браузер они не попадают.
 */

export const MAX_OCR_IMAGE_SIDE = 2000 // OCR не любит огромные снимки; 2000px хватает для текста

export type OcrLine = { text: string; box: Box } // box — в долях от размера фото
export type OcrResult = { latex: string; lines: OcrLine[] }

type Vertex = { x?: string; y?: string }
type RawLine = { text?: string; boundingBox?: { vertices?: Vertex[] } }
type OcrResponse = {
  message?: string
  error?: { message?: string }
  result?: {
    textAnnotation?: { width?: string; height?: string; blocks?: { lines?: RawLine[] }[]; markdown?: string; fullText?: string }
  }
}

/** Уменьшает фото и перекодирует в JPEG, возвращает base64 без префикса data:. */
export async function toJpegBase64(src: string, rotation = 0): Promise<string> {
  const img = new Image()
  img.src = src
  await img.decode()
  const k = Math.min(1, MAX_OCR_IMAGE_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
  const w = Math.round(img.naturalWidth * k)
  const h = Math.round(img.naturalHeight * k)
  const turned = rotation % 180 !== 0
  const canvas = document.createElement('canvas')
  canvas.width = turned ? h : w
  canvas.height = turned ? w : h
  const ctx = canvas.getContext('2d')!
  ctx.translate(canvas.width / 2, canvas.height / 2)
  ctx.rotate((rotation * Math.PI) / 180)
  ctx.drawImage(img, -w / 2, -h / 2, w, h)
  return canvas.toDataURL('image/jpeg', 0.9).split(',')[1]
}

function toBox(vertices: Vertex[] | undefined, W: number, H: number): Box {
  const xs = (vertices ?? []).map((v) => Number(v.x ?? 0))
  const ys = (vertices ?? []).map((v) => Number(v.y ?? 0))
  if (!xs.length || !W || !H) return { x: 0, y: 0, w: 0, h: 0 }
  const x0 = Math.min(...xs)
  const y0 = Math.min(...ys)
  return { x: x0 / W, y: y0 / H, w: (Math.max(...xs) - x0) / W, h: (Math.max(...ys) - y0) / H }
}

export async function recognize(image: string): Promise<OcrResult> {
  return recognizeRaw(await toJpegBase64(image), 'JPEG')
}

/**
 * У Yandex Vision лимит — 1 запрос в секунду на каталог. Все запросы идут по очереди
 * с паузой, а при ответе «quota limit exceed» повторяются через нарастающую паузу.
 */
const GAP_MS = 1100
let chain: Promise<unknown> = Promise.resolve()
let lastAt = 0
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

function queued<T>(fn: () => Promise<T>): Promise<T> {
  const run = chain.then(async () => {
    const wait = lastAt + GAP_MS - Date.now()
    if (wait > 0) await sleep(wait)
    try {
      return await fn()
    } finally {
      lastAt = Date.now()
    }
  })
  chain = run.catch(() => undefined)
  return run
}

const isQuota = (status: number, msg: string) => status === 429 || /quota|rate/i.test(msg)

/** Готовое изображение в base64. */
export async function recognizeRaw(content: string, mimeType: 'JPEG' | 'PNG'): Promise<OcrResult> {
  const body = JSON.stringify({ mimeType, languageCodes: ['ru', 'en'], model: 'math-markdown', content })
  let data: OcrResponse | null = null
  for (let attempt = 0; ; attempt++) {
    const res = await queued(() =>
      fetch('/api/ocr', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }),
    )
    data = await res.json().catch(() => null)
    if (res.ok) break
    const msg = String(data?.message ?? data?.error?.message ?? `OCR: ошибка ${res.status}`)
    // квота и разовые сбои шлюза (502/503/504) — повторяем
    if (attempt < 4 && (isQuota(res.status, msg) || res.status >= 502)) {
      await sleep(GAP_MS * (attempt + 1))
      continue
    }
    throw new Error(isQuota(res.status, msg) ? 'Распознавание перегружено — попробуйте ещё раз через минуту.' : msg)
  }
  const t = data?.result?.textAnnotation ?? {}
  const W = Number(t.width)
  const H = Number(t.height)
  const lines: OcrLine[] = (t.blocks ?? [])
    .flatMap((b: { lines?: RawLine[] }) => b.lines ?? [])
    .filter((l: RawLine) => l.text?.trim())
    .map((l: RawLine) => ({ text: l.text!.trim(), box: toBox(l.boundingBox?.vertices, W, H) }))
  return { latex: String(t.markdown || t.fullText || '').trim(), lines }
}
