import { useEffect, useState, type CSSProperties } from 'react'
import { api, type Assignment, type References } from './api'
import { folderName } from './titles'

const NOT_READY: Record<string, string> = {
  missing: 'эталона нет',
  pending: 'эталон готовится',
  unsupported: 'эталон не поддерживается',
  validation_failed: 'эталон не прошёл проверку',
}

// Окно со всеми заданиями варианта в текстовом виде (как их разобрал сервис проверки).
export function TasksDialog({ a, onClose }: { a: Assignment; onClose: () => void }) {
  const [refs, setRefs] = useState<References>()
  const [error, setError] = useState<string>()

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const load = async () => {
      try {
        const r = await api.references(a)
        if (!alive) return
        setRefs(r)
        // Пока вариант разбирается, задания появляются не сразу — перечитываем.
        if (r.tasks.length === 0 && !['failed', 'needs_review'].includes(r.state)) timer = window.setTimeout(load, 3000)
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    }
    load()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [a])

  return (
    <div style={st.backdrop} onClick={onClose}>
      <div style={st.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div style={st.head}>
          <h2 style={st.h2}>
            Задания · {folderName(a)}
          </h2>
          <button onClick={onClose} title="Закрыть" style={st.close}>
            ×
          </button>
        </div>

        <div style={st.body}>
          {error && <p style={st.error}>{error}</p>}
          {!refs && !error && <p style={st.muted}>Загружаем задания…</p>}
          {refs && refs.tasks.length === 0 && (
            <p style={st.muted}>
              {refs.state === 'failed' || refs.state === 'needs_review'
                ? 'Задания из этого варианта разобрать не удалось.'
                : 'Вариант ещё разбирается, задания скоро появятся…'}
            </p>
          )}
          {refs?.tasks.map((t) => (
            <div key={t.taskId} style={st.task}>
              <div style={st.number}>Задание {t.taskNumber}</div>
              <div style={st.statement}>{t.statement}</div>
              <div style={st.answer}>
                {t.state === 'ready' && t.answer ? (
                  <>
                    Ответ: <strong>{t.answer}</strong>
                  </>
                ) : (
                  <span style={st.muted}>{NOT_READY[t.state] ?? t.state}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  backdrop: { position: 'fixed', inset: 0, background: 'rgba(17, 24, 39, 0.45)', display: 'grid', placeItems: 'center', padding: 16, zIndex: 10 },
  dialog: { background: '#fff', borderRadius: 12, width: '100%', maxWidth: 680, maxHeight: 'calc(100vh - 32px)', display: 'flex', flexDirection: 'column', boxShadow: '0 10px 40px rgba(0,0,0,0.2)' },
  head: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '16px 20px', borderBottom: '1px solid #e5e7eb' },
  h2: { margin: 0, fontSize: 18 },
  close: { border: 'none', background: 'none', cursor: 'pointer', color: '#6b7280', fontSize: 24, lineHeight: 1 },
  body: { padding: '12px 20px 20px', overflowY: 'auto' },
  task: { padding: '12px 0', borderBottom: '1px solid #f3f4f6' },
  number: { fontWeight: 600, marginBottom: 4 },
  statement: { whiteSpace: 'pre-wrap', lineHeight: 1.5 },
  answer: { marginTop: 6, fontSize: 14 },
  muted: { color: '#6b7280', margin: 0 },
  error: { color: '#b91c1c', margin: 0 },
}
