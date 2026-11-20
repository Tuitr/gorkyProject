import { useState } from 'react'
import type { OcrResult } from '../api/ocr'

/** Ход проверки нового фото: OCR → GigaChat → готово. */
export type Pipeline =
  | { stage: 'ocr' }
  | { stage: 'check'; ocr: OcrResult }
  | { stage: 'done'; ocr: OcrResult }
  | { stage: 'error'; error: string; ocr?: OcrResult }

/** Распознанный LaTeX с кнопкой «Копировать». */
export function LatexBlock({ latex }: { latex: string }) {
  const [copied, setCopied] = useState(false)
  if (!latex) return <p className="muted">Текст на фотографии не обнаружен.</p>
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(latex)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* буфер обмена недоступен — текст можно выделить вручную */
    }
  }
  return (
    <div className="latex-wrap">
      <pre className="latex">{latex}</pre>
      <button className="link-btn latex-copy" onClick={copy}>
        {copied ? 'Скопировано ✓' : 'Копировать'}
      </button>
    </div>
  )
}

type Props = { pipeline?: Pipeline; onRetry: () => void }

/** Панель справа, пока у работы ещё нет результата проверки. */
export function OcrPanel({ pipeline, onRetry }: Props) {
  const ocr = pipeline && 'ocr' in pipeline ? pipeline.ocr : undefined
  return (
    <aside className="report">
      <ol className="steps">
        <li className={!pipeline || pipeline.stage === 'ocr' ? 'is-now' : ocr ? 'is-done' : 'is-err'}>
          Распознавание текста
        </li>
        <li
          className={
            pipeline?.stage === 'check' ? 'is-now' : pipeline?.stage === 'error' && ocr ? 'is-err' : ''
          }
        >
          Проверка решения
        </li>
      </ol>

      {pipeline?.stage === 'error' ? (
        <div className="report__empty">
          <p className="ocr-error">{pipeline.error}</p>
          <button className="btn" onClick={onRetry}>
            Повторить
          </button>
        </div>
      ) : (
        <div className="report__empty report__empty--row">
          <div className="spinner" aria-hidden="true" />
          <p>{pipeline?.stage === 'check' ? 'Выполняется проверка работы. Обычно она занимает 10–40 секунд…' : 'Выполняется распознавание…'}</p>
        </div>
      )}

      {ocr && (
        <section>
          <h3>Распознанный текст (LaTeX)</h3>
          <LatexBlock latex={ocr.latex} />
        </section>
      )}
    </aside>
  )
}
