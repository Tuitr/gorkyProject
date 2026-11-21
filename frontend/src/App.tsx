import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { AddClassDialog } from './AddClassDialog'
import { ClassStats } from './ClassStats'
import { prepare } from './convert'
import { UploadDialog, type UploadRequest } from './UploadDialog'
import { StudentWork, type OpenWork } from './StudentWork'
import { WorkFolders, type Work } from './WorkFolders'
import { api, type Assignment, type ClassItem, type Student, type Upload } from './api'
import { useChecks } from './useChecks'
import { useReports } from './useReports'
import { folderName } from './titles'

const PENDING = new Set(['pending_binding', 'matched'])

// Отправленные ответы учеников храним в браузере по классу: кабинет не отдаёт список загрузок,
// а без uploadId после перезагрузки не узнать номер проверки и её итог.
const worksKey = (classId: string) => `works:${classId}`
function loadWorks(classId: string): Work[] {
  try {
    return JSON.parse(localStorage.getItem(worksKey(classId)) ?? '[]')
  } catch {
    return []
  }
}

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
  // Класс, чьи ответы только что прочитаны из браузера: в этом проходе ещё старый список, не сохраняем.
  const justLoaded = useRef('')
  const [uploads, setUploads] = useState<Record<string, Upload>>({})
  const [open, setOpen] = useState<Set<string>>(new Set())
  const openFolder = (id: string) => setOpen((prev) => new Set(prev).add(id))
  const [selected, setSelected] = useState<string>()
  // Открытая работа ученика (вместо папок и статистики).
  const [openWork, setOpenWork] = useState<OpenWork | null>(null)
  const toggleFolder = (id: string) => {
    setSelected(id)
    toggle(id)
  }
  const toggle = (id: string) =>
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
    setSelected(undefined)
    setOpenWork(null)
    justLoaded.current = classId
    setWorks(classId ? loadWorks(classId) : [])
    setUploads({})
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

  useEffect(() => {
    if (!classId) return
    if (justLoaded.current === classId) {
      justLoaded.current = ''
      return
    }
    try {
      const sent = works.filter((w) => w.state === 'sent' && w.uploadId && w.assignmentId)
      localStorage.setItem(worksKey(classId), JSON.stringify(sent))
    } catch {
      /* хранилище недоступно — после перезагрузки статусы будут из отчёта */
    }
  }, [works, classId])

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

  // Delete удаляет выбранную папку (вариант со всеми ответами учеников) — после подтверждения.
  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      if (e.key !== 'Delete' || !selected || dialog || addClassOpen || openWork) return
      const target = e.target as HTMLElement
      if (target.closest('input, textarea, select, [contenteditable="true"]')) return
      const folder = assignments.find((a) => a.assignmentId === selected)
      if (!folder) return
      e.preventDefault()
      if (!window.confirm(`Удалить папку «${folderName(folder)}» вместе со всеми ответами учеников и результатами?`)) return
      try {
        await api.deleteAssignment(folder.assignmentId)
        setAssignments((prev) => prev.filter((a) => a.assignmentId !== folder.assignmentId))
        setWorks((prev) => prev.filter((w) => w.assignmentId !== folder.assignmentId))
        setSelected(undefined)
      } catch (err) {
        setError((err as Error).message)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, assignments, dialog, addClassOpen, openWork])

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

  // Проверки учеников: из отчётов и из загрузок этого сеанса.
  const checkIds = [
    ...Object.values(reports).flatMap((r) => r.results.map((x) => x.checkId)),
    ...works.flatMap((w) => (w.uploadId ? (uploads[w.uploadId]?.files ?? []) : [])).flatMap((f) => (f.checkId ? [f.checkId] : [])),
  ]
  const { checks, reload: reloadCheck } = useChecks(checkIds)
  // Оценки учителя: из отчётов кабинета; только что выставленные — поверх, до следующего чтения отчёта.
  const [gradeEdits, setGradeEdits] = useState<Record<string, number | null>>({})
  const reportGrades: Record<string, number | null | undefined> = Object.fromEntries(
    Object.values(reports).flatMap((r) => r.results.map((x) => [x.checkId, x.grade])),
  )
  const gradeOf = (checkId: string | undefined) =>
    checkId === undefined ? undefined : checkId in gradeEdits ? gradeEdits[checkId] : reportGrades[checkId]

  const setGrade = async (checkId: string, grade: number | null) => {
    try {
      await api.setGrade(checkId, grade)
      setGradeEdits((prev) => ({ ...prev, [checkId]: grade }))
      // Вместе с оценкой учитель принимает разбор: публикуем результат, если ещё не опубликован.
      const c = checks[checkId]
      if (grade != null && c && 'taskResults' in c && c.publishedResultRev !== c.resultRev) {
        await api.publish(checkId, c.resultRev).catch(() => undefined)
        await reloadCheck(checkId)
      }
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div style={s.page}>
      <header style={s.header}>
        <div style={s.brand}>
          {logoOk ? (
            <img src="/logo.svg" alt="Герб Нижегородской области" height={40} onError={() => setLogoOk(false)} />
          ) : (
            <span style={s.mark} aria-hidden>
              ✓
            </span>
          )}
          <div style={{ lineHeight: 1.2 }}>
            <div style={{ fontWeight: 700 }}>Проверка работ</div>
            <div style={{ fontSize: 13, color: '#6b7280' }}>Нижегородская область</div>
          </div>
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
            className="primary"
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

      {openWork ? (
        <StudentWork work={openWork} check={openWork.checkId ? checks[openWork.checkId] : undefined} grade={gradeOf(openWork.checkId)} onGrade={setGrade} onBack={() => setOpenWork(null)} />
      ) : (
        <div style={s.columns}>
          <section style={s.half}>
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
                selected={selected}
                onBind={bind}
                onAddAnswer={(folderId) => setDialog({ folderId })}
                checks={checks}
                gradeOf={gradeOf}
                onGrade={setGrade}
                onOpenAnswer={(checkId, studentName, assignment) => {
                  // Фото известно только для загрузок этого окна.
                  const file = works
                    .flatMap((w) => (w.uploadId ? (uploads[w.uploadId]?.files ?? []).map((f) => ({ uploadId: w.uploadId!, f })) : []))
                    .find(({ f }) => f.checkId === checkId && f.isPhoto)
                  setOpenWork({ checkId, studentName, assignment, photo: file && { uploadId: file.uploadId, fileId: file.f.fileId } })
                }}
              />
            ) : (
              <p style={s.muted}>Выберите класс, чтобы увидеть работы.</p>
            )}
          </section>

          <section style={s.half}>
            <h2 style={s.h2}>Статистика класса</h2>
            {classId ? (
              <ClassStats assignments={assignments} reports={reports} checks={checks} gradeOf={gradeOf} error={reportsError} loading={reportsLoading} />
            ) : (
              <p style={s.muted}>Выберите класс, чтобы увидеть статистику.</p>
            )}
          </section>
        </div>
      )}
    </div>
  )
}

const s: Record<string, CSSProperties> = {
  page: { height: '100vh', display: 'flex', flexDirection: 'column' },
  header: { display: 'flex', alignItems: 'center', gap: 24, minHeight: 64, flexShrink: 0, padding: '10px 24px', background: '#fff', borderBottom: '1px solid #e5e7eb', flexWrap: 'wrap' },
  brand: { display: 'flex', alignItems: 'center', gap: 12 },
  mark: { width: 36, height: 36, borderRadius: 10, background: 'linear-gradient(135deg, #3b82f6, #1d4ed8)', color: '#fff', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 18 },
  row: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  input: {},
  button: {},
  error: { color: '#b91c1c', fontSize: 14 },
  columns: { flex: 1, display: 'flex', gap: 16, padding: 16, minHeight: 0 },
  half: { flex: 1, minWidth: 0, padding: 20, overflowY: 'auto', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 14, boxShadow: 'var(--shadow)' },
  h2: { margin: '0 0 16px', fontSize: 17 },
  muted: { color: '#6b7280', margin: 0 },
}
