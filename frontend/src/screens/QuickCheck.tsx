import { useCallback, useRef, useState } from 'react'
import { checkWork } from '../api/check'
import { recognize, type OcrResult } from '../api/ocr'
import { CameraCapture } from '../components/CameraCapture'
import { LatexBlock, OcrPanel, type Pipeline } from '../components/OcrPanel'
import { PhotoText } from '../components/PhotoText'
import { ReportPanel } from '../components/ReportPanel'
import { WorkViewer } from '../components/WorkViewer'
import type { Work } from '../types'

const fmtDate = (t: number) =>
  new Date(t).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Быстрая проверка одного фото без класса и ученика (прежний экран). */
export function QuickCheck() {
  const [works, setWorks] = useState<Work[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [cameraOpen, setCameraOpen] = useState(false)
  // Новое фото: OCR (LaTeX) → проверка GigaChat
  const [pipeline, setPipeline] = useState<Record<string, Pipeline>>({})
  const [activeMistake, setActiveMistake] = useState<string | null>(null)
  const [showMarks, setShowMarks] = useState(true)
  const [showTask, setShowTask] = useState(false)
  // Проверка учителем: снятые ошибки и подтверждённые работы (пока только в памяти)
  const [dismissed, setDismissed] = useState<Record<string, string[]>>({})
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({})
  const fileRef = useRef<HTMLInputElement>(null)

  const current = works.find((w) => w.id === currentId) ?? works[0]

  const select = (id: string | null) => {
    setCurrentId(id)
    setActiveMistake(null)
    setShowTask(false)
  }

  const toggleDismiss = (workId: string, mistakeId: string) =>
    setDismissed((d) => {
      const list = d[workId] ?? []
      return {
        ...d,
        [workId]: list.includes(mistakeId) ? list.filter((x) => x !== mistakeId) : [...list, mistakeId],
      }
    })

  const runPipeline = useCallback(async (work: Work, cached?: OcrResult) => {
    const set = (p: Pipeline) => setPipeline((all) => ({ ...all, [work.id]: p }))
    let ocr = cached
    try {
      if (!ocr) {
        set({ stage: 'ocr' })
        ocr = await recognize(work.image)
      }
      if (!ocr.lines.length) throw new Error('Текст на фотографии не обнаружен. Сфотографируйте работу повторно: ближе и без наклона.')
      set({ stage: 'check', ocr })
      const result = await checkWork(ocr.lines)
      setWorks((ws) => ws.map((w) => (w.id === work.id ? { ...w, result, title: result.workTopic } : w)))
      set({ stage: 'done', ocr })
    } catch (e) {
      set({ stage: 'error', error: e instanceof Error ? e.message : 'Не удалось проверить работу', ocr })
    }
  }, [])

  const addWork = (image: string, source: Work['source'], fileName?: string, createdAt = Date.now()) => {
    // id и номер — уникальные, даже когда несколько фото добавляются разом
    const work: Work = {
      id: `w${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      title: '',
      image,
      source,
      fileName,
      createdAt,
    }
    setWorks((ws) => [{ ...work, title: `Новая работа ${ws.length + 1}` }, ...ws])
    select(work.id)
    setCameraOpen(false)
    void runPipeline(work)
  }

  const removeWork = (id: string) => {
    const rest = works.filter((w) => w.id !== id)
    setWorks(rest)
    if (id === currentId) select(rest[0]?.id ?? null)
  }

  /** Время берём в момент выбора: файлы читаются параллельно, а порядок должен остаться как при выборе. */
  const onFile = (file: File, createdAt: number) => {
    const reader = new FileReader()
    reader.onload = () => addWork(String(reader.result), 'upload', file.name, createdAt)
    reader.readAsDataURL(file)
  }

  return (
    <div className="quick">
      <header className="page-head quick__head">
        <div className="page-head__actions">
          <button className="btn" onClick={() => fileRef.current?.click()}>
            Загрузить фото (можно несколько)
          </button>
          <button className="btn btn--primary" onClick={() => setCameraOpen(true)}>
            Сфотографировать
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              const now = Date.now()
              Array.from(e.target.files ?? []).forEach((f, i) => onFile(f, now + i))
              e.target.value = ''
            }}
          />
        </div>
      </header>

      <PhotoText works={works} pipeline={pipeline} />

      {!current ? (
        <div className="empty">
          <h2>Работы не загружены</h2>
        </div>
      ) : (
      <div className="quick__layout">
        <nav className="works" aria-label="Работы">
          <h3>Работы</h3>
          <ul>
            {works.map((w) => {
              const err = w.result?.mistakes.filter((m) => !(dismissed[w.id] ?? []).includes(m.id)).length
              return (
                <li key={w.id}>
                  <button className={`work-card${w.id === current.id ? ' is-active' : ''}`} onClick={() => select(w.id)}>
                    <img src={w.image} alt="" />
                    <span className="work-card__text">
                      <b>{w.title}</b>
                      <span className="muted small">
                        {fmtDate(w.createdAt)}
                      </span>
                      {pipeline[w.id]?.stage === 'ocr' ? (
                        <span className="badge">распознавание…</span>
                      ) : pipeline[w.id]?.stage === 'check' ? (
                        <span className="badge">на проверке…</span>
                      ) : pipeline[w.id]?.stage === 'error' ? (
                        <span className="badge badge--err">ошибка</span>
                      ) : w.result ? (
                        <span className={`badge ${err ? 'badge--err' : 'badge--ok'}`}>
                          {err ? `ошибок: ${err}` : 'без ошибок'}
                          {confirmed[w.id] && ' · проверено'}
                        </span>
                      ) : (
                        <span className="badge">не проверена</span>
                      )}
                    </span>
                  </button>
                  <button className="icon-btn work-del" onClick={() => removeWork(w.id)} aria-label="Удалить работу">
                    ✕
                  </button>
                </li>
              )
            })}
          </ul>
        </nav>

        <main className="main">
          <div className="main__bar">
            <h1>{current.title}</h1>
            <div className="main__tools">
              {current.taskImage && (
                <div className="segmented" role="tablist">
                  <button role="tab" aria-selected={!showTask} onClick={() => setShowTask(false)}>
                    Работа
                  </button>
                  <button role="tab" aria-selected={showTask} onClick={() => setShowTask(true)}>
                    Задание
                  </button>
                </div>
              )}
              {current.result && !showTask && (
                <label className="toggle">
                  <input type="checkbox" checked={showMarks} onChange={(e) => setShowMarks(e.target.checked)} />
                  Разметка
                </label>
              )}
            </div>
          </div>
          <div className="main__body">
            <WorkViewer
              work={current}
              activeMistake={activeMistake}
              onSelectMistake={setActiveMistake}
              showMarks={showMarks}
              showTask={showTask}
              dismissed={dismissed[current.id] ?? []}
            />
            {current.result ? (
              <ReportPanel
                work={current}
                checking={false}
                activeMistake={activeMistake}
                onSelectMistake={(id) => {
                  setActiveMistake(id)
                  if (id) {
                    setShowMarks(true)
                    setShowTask(false)
                  }
                }}
                onRetry={() => {}}
                dismissed={dismissed[current.id] ?? []}
                onToggleDismiss={(mid) => toggleDismiss(current.id, mid)}
                confirmed={!!confirmed[current.id]}
                onToggleConfirm={() => setConfirmed((c) => ({ ...c, [current.id]: !c[current.id] }))}
                extra={(() => {
                  const p = pipeline[current.id]
                  return p && 'ocr' in p && p.ocr ? (
                    <details className="ocr-details">
                      <summary>Распознанный текст (LaTeX)</summary>
                      <LatexBlock latex={p.ocr.latex} />
                    </details>
                  ) : null
                })()}
              />
            ) : (
              <OcrPanel
                pipeline={pipeline[current.id]}
                onRetry={() => {
                  const p = pipeline[current.id]
                  void runPipeline(current, p && 'ocr' in p ? p.ocr : undefined)
                }}
              />
            )}
          </div>
        </main>
      </div>
      )}

      {cameraOpen && <CameraCapture onCapture={(img) => addWork(img, 'camera')} onClose={() => setCameraOpen(false)} />}
    </div>
  )
}

