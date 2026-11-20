import { useState } from 'react'
import { latexToText } from '../../server/latexToText'
import type { Work } from '../types'
import type { Pipeline } from './OcrPanel'

/**
 * Текст, считанный со всех загруженных фото, — одним сплошным абзацем в порядке загрузки,
 * обычным текстом (без LaTeX), с кнопкой «Копировать».
 */
export function PhotoText({ works, pipeline }: { works: Work[]; pipeline: Record<string, Pipeline> }) {
  const [copied, setCopied] = useState(false)
  const photos = works.filter((w) => w.source !== 'sample').sort((a, b) => a.createdAt - b.createdAt)
  if (!photos.length) return null

  /** Текст одного фото — сплошной строкой, без LaTeX. null — фото ещё распознаётся. */
  const parts = photos.map((w) => {
    const p = pipeline[w.id]
    const ocr = p && 'ocr' in p && p.ocr ? p.ocr : null
    const text = ocr
      ? ocr.lines
          .map((l) => latexToText(l.text).trim())
          .filter(Boolean)
          .join(' ')
          .replace(/\s+/g, ' ')
          .trim()
      : null
    return { w, name: w.fileName ?? (w.source === 'camera' ? `Снимок с камеры · ${w.title}` : w.title), text }
  })
  const waiting = parts.filter((p) => p.text === null).length
  // одно фото — просто текст; несколько — перед каждым текстом имя файла
  const several = parts.length > 1
  const ready = parts.filter((p) => p.text)
  const text = several ? ready.map((p) => `${p.name}:\n${p.text}`).join('\n\n') : (ready[0]?.text ?? '')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* буфер обмена недоступен — текст можно выделить вручную */
    }
  }

  return (
    <section className="panel photo-text">
      <div className="panel__head">
        <h2>Текст с фото</h2>
        <span className="muted small">
          фото: {photos.length}
          {waiting > 0 && ` · распознаётся: ${waiting}`}
        </span>
        <span className="spacer" />
        {text && (
          <button className="btn" onClick={copy}>
            {copied ? 'Скопировано ✓' : 'Копировать'}
          </button>
        )}
      </div>
      {text && several ? (
        <div className="photo-text__body">
          {parts.map((p) => (
            <div key={p.w.id} className="photo-text__part">
              <div className="photo-text__name">📄 {p.name}</div>
              {p.text === null ? (
                <span className="muted">распознаётся…</span>
              ) : p.text ? (
                <p>{p.text}</p>
              ) : (
                <span className="muted">текст не найден</span>
              )}
            </div>
          ))}
        </div>
      ) : text ? (
        <p className="photo-text__body">{text}</p>
      ) : (
        <p className="muted">{waiting ? 'Распознаём фото…' : 'Текст на фото не найден.'}</p>
      )}
    </section>
  )
}
