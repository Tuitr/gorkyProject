import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { ClassStats } from './ClassStats'
import { api, type Assignment, type ClassItem, type Student, type Upload, type UploadFile } from './api'

// Строка в списке работ: файл ещё отправляется, не ушёл, или уже принят кабинетом (uploadId).
type Work = { key: string; name: string; state: 'sending' | 'failed' | 'sent'; error?: string; uploadId?: string }

const STATUS: Record<UploadFile['status'], [string, string]> = {
  pending_binding: ['Определяем ученика…', '#2563eb'],
  matched: ['Ученик найден', '#2563eb'],
  needs_confirmation: ['Укажите ученика', '#b45309'],
  bound: ['Принято, отправлено на проверку', '#15803d'],
  rejected: ['Отклонено', '#b91c1c'],
}
const PENDING = new Set(['pending_binding', 'matched'])

export function App() {
  const fileInput = useRef<HTMLInputElement>(null)
  const [logoOk, setLogoOk] = useState(true)

  const [classes, setClasses] = useState<ClassItem[]>([])
  const [classId, setClassId] = useState('')
  const [assignments, setAssignments] = useState<Assignment[]>([])
  const [subject, setSubject] = useState<'' | 'math' | 'physics'>('')
  const [students, setStudents] = useState<Student[]>([])
  const [error, setError] = useState<string>()

  const [works, setWorks] = useState<Work[]>([])
  const [uploads, setUploads] = useState<Record<string, Upload>>({})

  // Работы уходят в последний вариант класса по выбранному предмету (кабинет отдаёт варианты от новых к старым).
  const assignment = subject ? assignments.find((a) => a.subject === subject) : undefined

  // классы
  useEffect(() => {
    api.classes().then(
      (list) => {
        setClasses(list)
        if (list.length === 1) setClassId(list[0].classId)
      },
      (e) => setError(e.message),
    )
  }, [])

  // варианты и ученики выбранного класса
  useEffect(() => {
    setAssignments([])
    setStudents([])
    if (!classId) return
    api.assignments(classId).then(
      (list) => setAssignments(list),
      (e) => setError(e.message),
    )
    api.students(classId).then(setStudents, (e) => setError(e.message))
  }, [classId])

  // опрос статусов, пока кабинет определяет учеников
  useEffect(() => {
    const waiting = works.filter(
      (w) => w.uploadId && (!uploads[w.uploadId] || uploads[w.uploadId].files.some((f) => PENDING.has(f.status))),
    )
    if (waiting.length === 0) return
    const t = window.setTimeout(async () => {
      for (const w of waiting) {
        const u = await api.upload(w.uploadId!).catch(() => null)
        if (u) setUploads((prev) => ({ ...prev, [u.uploadId]: u }))
      }
    }, 2000)
    return () => window.clearTimeout(t)
  }, [works, uploads])

  const send = async (files: File[]) => {
    if (!assignment) return
    for (const file of files) {
      const key = crypto.randomUUID()
      setWorks((prev) => [{ key, name: file.name, state: 'sending' }, ...prev])
      try {
        const { uploadId } = await api.uploadWork(assignment, file)
        setWorks((prev) => prev.map((w) => (w.key === key ? { ...w, state: 'sent', uploadId } : w)))
      } catch (e) {
        setWorks((prev) => prev.map((w) => (w.key === key ? { ...w, state: 'failed', error: (e as Error).message } : w)))
      }
    }
  }

  const bind = async (uploadId: string, fileId: string, studentId: string) => {
    try {
      await api.bind(uploadId, fileId, studentId)
      setUploads({ ...uploads, [uploadId]: await api.upload(uploadId) })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const sentFiles = works.flatMap((w) => (w.uploadId ? (uploads[w.uploadId]?.files ?? []) : []))
  const boundCount = sentFiles.filter((f) => f.status === 'bound').length

  return (
    <div style={s.page}>
      <header style={s.header}>
        <div style={s.brand}>
          {logoOk && <img src="/logo.svg" alt="Герб Нижегородской области" height={44} onError={() => setLogoOk(false)} />}
          <span style={{ fontWeight: 600 }}>Нижегородская область</span>
        </div>

        <div style={s.row}>
          <select value={classId} onChange={(e) => setClassId(e.target.value)} style={s.input}>
            <option value="">Класс…</option>
            {classes.map((c) => (
              <option key={c.classId} value={c.classId}>
                {c.name}
              </option>
            ))}
          </select>
          <select value={subject} onChange={(e) => setSubject(e.target.value as typeof subject)} style={s.input} disabled={!classId}>
            <option value="">Предмет…</option>
            <option value="math">Математика</option>
            <option value="physics">Физика</option>
          </select>
          {classId && subject && !assignment && <span style={s.error}>В этом классе нет варианта по этому предмету</span>}
          <button
            onClick={() => fileInput.current?.click()}
            disabled={!assignment}
            title={assignment ? '' : 'Сначала выберите класс и предмет'}
            style={s.button}
          >
            Загрузить файлы
          </button>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept=".docx,.zip,.jpg,.jpeg,.png,.webp"
            onChange={(e) => {
              send(Array.from(e.target.files ?? []))
              e.target.value = ''
            }}
          />
        </div>
      </header>

      {error && (
        <div style={{ ...s.error, padding: '8px 16px', background: '#fef2f2' }} onClick={() => setError(undefined)}>
          {error}
        </div>
      )}

      <div style={s.columns}>
        <section style={{ ...s.half, borderRight: '1px solid #e5e7eb' }}>
          <h2 style={s.h2}>Работы учеников</h2>
          {works.length === 0 && (
            <p style={s.muted}>
              Выберите класс и предмет, затем нажмите «Загрузить файлы» (DOCX, ZIP или фото).
            </p>
          )}
          <ul style={s.list}>
            {works.map((w) =>
              w.state !== 'sent' ? (
                <li key={w.key} style={s.item}>
                  <span style={s.name}>{w.name}</span>
                  {w.state === 'sending' ? <span style={s.muted}>Отправляем…</span> : <span style={s.error}>{w.error}</span>}
                </li>
              ) : (
                (uploads[w.uploadId!]?.files ?? [{ fileId: w.key, originalName: w.name, status: 'pending_binding', candidates: [] } as UploadFile]).map((f) => (
                  <li key={w.uploadId + f.fileId} style={s.item}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
                      <span style={s.name}>{f.originalName}</span>
                      {f.studentId && <span style={s.muted}>{students.find((x) => x.studentId === f.studentId)?.name}</span>}
                      {f.reason && <span style={{ ...s.muted, fontSize: 13 }}>{f.reason}</span>}
                      {f.status === 'needs_confirmation' && (
                        <select
                          defaultValue=""
                          onChange={(e) => e.target.value && bind(w.uploadId!, f.fileId, e.target.value)}
                          style={s.input}
                        >
                          <option value="" disabled>
                            Чья это работа?
                          </option>
                          {[...f.candidates, ...students.filter((x) => !f.candidates.some((c) => c.studentId === x.studentId))].map((x) => (
                            <option key={x.studentId} value={x.studentId}>
                              {x.name}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                    <span style={{ color: STATUS[f.status][1], fontSize: 13, whiteSpace: 'nowrap' }}>{STATUS[f.status][0]}</span>
                  </li>
                ))
              ),
            )}
          </ul>
        </section>

        <section style={s.half}>
          <h2 style={s.h2}>Статистика класса</h2>
          {classId ? (
            <ClassStats assignments={assignments} subject={subject} refreshKey={boundCount} />
          ) : (
            <p style={s.muted}>Выберите класс, чтобы увидеть статистику.</p>
          )}
        </section>
      </div>
    </div>
  )
}

const s: Record<string, CSSProperties> = {
  page: { height: '100vh', display: 'flex', flexDirection: 'column', background: '#fff', font: '15px system-ui, sans-serif', color: '#1f2937' },
  header: { display: 'flex', alignItems: 'center', gap: 16, minHeight: 64, flexShrink: 0, padding: '8px 16px', borderBottom: '1px solid #e5e7eb', flexWrap: 'wrap', boxSizing: 'border-box' },
  brand: { display: 'flex', alignItems: 'center', gap: 10 },
  row: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  input: { padding: '7px 10px', font: 'inherit', border: '1px solid #d1d5db', borderRadius: 6, background: '#fff' },
  button: { padding: '8px 16px', font: 'inherit', cursor: 'pointer' },
  error: { color: '#b91c1c', fontSize: 14 },
  columns: { flex: 1, display: 'flex', minHeight: 0 },
  half: { width: '50%', padding: 16, overflowY: 'auto', boxSizing: 'border-box' },
  h2: { margin: '0 0 12px', fontSize: 18 },
  muted: { color: '#6b7280', margin: 0 },
  list: { listStyle: 'none', margin: 0, padding: 0 },
  item: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, padding: '10px 12px', marginBottom: 8, border: '1px solid #e5e7eb', borderRadius: 8 },
  name: { wordBreak: 'break-all' },
}
