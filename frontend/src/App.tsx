import { useEffect, useState, type CSSProperties } from 'react'
import { AddClassDialog } from './AddClassDialog'
import { ClassStats } from './ClassStats'
import { prepare } from './convert'
import { UploadDialog, type UploadRequest } from './UploadDialog'
import { WorkFolders, type Work } from './WorkFolders'
import { api, type Assignment, type ClassItem, type Student, type Upload } from './api'
import { useReports } from './useReports'

const PENDING = new Set(['pending_binding', 'matched'])

export function App() {
  // Окно загрузки: закрыто или открыто (с папкой, если открыли из папки).
  const [dialog, setDialog] = useState<{ folderId?: string } | null>(null)
  const [addClassOpen, setAddClassOpen] = useState(false)
  const [logoOk, setLogoOk] = useState(true)

  const [classes, setClasses] = useState<ClassItem[]>([])
  const [classId, setClassId] = useState('')
  const [assignments, setAssignments] = useState<Assignment[]>([])
  const [students, setStudents] = useState<Student[]>([])
  const [error, setError] = useState<string>()

  const [works, setWorks] = useState<Work[]>([])
  const [uploads, setUploads] = useState<Record<string, Upload>>({})
  const [open, setOpen] = useState<Set<string>>(new Set())
  const openFolder = (id: string) => setOpen((prev) => new Set(prev).add(id))
  const toggleFolder = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })


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
    setOpen(new Set())
    if (!classId) return
    api.assignments(classId).then(
      (list) => {
        setAssignments(list)
        if (list[0]) openFolder(list[0].assignmentId) // самое свежее задание открыто
      },
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

  const loadAssignments = () =>
    api.assignments(classId).then(
      (list) => setAssignments(list),
      (e) => setError(e.message),
    )

  // Показывает загрузку в списке; job возвращает итог (null — строку убрать).
  const track = async (name: string, assignmentId: string | undefined, job: () => Promise<Partial<Work> | null>) => {
    const key = crypto.randomUUID()
    setWorks((prev) => [{ key, name, state: 'sending', assignmentId }, ...prev])
    try {
      const patch = await job()
      setWorks((prev) => (patch ? prev.map((w) => (w.key === key ? { ...w, ...patch } : w)) : prev.filter((w) => w.key !== key)))
    } catch (e) {
      setWorks((prev) => prev.map((w) => (w.key === key ? { ...w, state: 'failed', error: (e as Error).message } : w)))
    }
  }

  const submit = async (r: UploadRequest) => {
    setDialog(null)
    if (r.kind === 'task') {
      // Вариант проверочной становится новой папкой.
      for (const file of r.files)
        await track(file.name, undefined, async () => {
          // Несколько файлов с одним названием различаем номером.
          const title = r.files.length > 1 ? `${r.title} (${r.files.indexOf(file) + 1})` : r.title
          const v = await api.uploadTask(classId, await prepare(file), r.subject, title)
          openFolder(v.assignmentId)
          return null
        })
      await loadAssignments()
      return
    }
    // Ответ ученика — в выбранную папку.
    const folder = assignments.find((a) => a.assignmentId === r.assignmentId)
    if (!folder) return setError('Папка не найдена — обновите страницу')
    openFolder(folder.assignmentId)
    for (const file of r.files)
      await track(file.name, folder.assignmentId, async () => ({
        state: 'sent',
        uploadId: (await api.uploadWork(folder, await prepare(file))).uploadId,
      }))
  }

  const bind = async (uploadId: string, fileId: string, studentId: string) => {
    try {
      await api.bind(uploadId, fileId, studentId)
      setUploads({ ...uploads, [uploadId]: await api.upload(uploadId) })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // Когда работа принята на проверку, перечитываем отчёты (папки и статистику).
  const boundCount = works.flatMap((w) => (w.uploadId ? (uploads[w.uploadId]?.files ?? []) : [])).filter((f) => f.status === 'bound').length
  const { reports, error: reportsError, loading: reportsLoading } = useReports(assignments, boundCount)

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
          <button
            onClick={() => setDialog({})}
            disabled={!classId}
            title={classId ? '' : 'Сначала выберите класс'}
            style={s.button}
          >
            Загрузить файлы
          </button>
        </div>

        <button onClick={() => setAddClassOpen(true)} style={{ ...s.button, marginLeft: 'auto' }}>
          Добавить класс
        </button>
      </header>

      {addClassOpen && (
        <AddClassDialog
          onClose={() => setAddClassOpen(false)}
          onCreated={(c) => {
            setClasses((prev) => [...prev, c])
            setClassId(c.classId)
            setAddClassOpen(false)
          }}
        />
      )}

      {dialog && (
        <UploadDialog
          folders={assignments}
          folderId={dialog.folderId}
          onSubmit={submit}
          onClose={() => setDialog(null)}
        />
      )}

      {error && (
        <div style={{ ...s.error, padding: '8px 16px', background: '#fef2f2' }} onClick={() => setError(undefined)}>
          {error}
        </div>
      )}

      <div style={s.columns}>
        <section style={{ ...s.half, borderRight: '1px solid #e5e7eb' }}>
          <h2 style={s.h2}>Работы учеников</h2>
          {classId ? (
            <WorkFolders
              assignments={assignments}
              reports={reports}
              works={works}
              uploads={uploads}
              students={students}
              open={open}
              onToggle={toggleFolder}
              onBind={bind}
              onAddAnswer={(folderId) => setDialog({ folderId })}
            />
          ) : (
            <p style={s.muted}>Выберите класс, чтобы увидеть работы.</p>
          )}
        </section>

        <section style={s.half}>
          <h2 style={s.h2}>Статистика класса</h2>
          {classId ? (
            <ClassStats assignments={assignments} reports={reports} error={reportsError} loading={reportsLoading} />
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
}
