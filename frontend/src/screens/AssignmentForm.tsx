import { useState } from 'react'
import { readSource, splitTasks } from '../api/source'
import { sourceFiles, uid, WORK_TYPE_LABEL, type Assignment, type SourceFile, type SourceTask, type WorkType } from '../domain'
import { go, href, useRoute } from '../router'
import { patch, useStore } from '../store'
import { Badge, PageHead } from '../ui'
import { NotFound, useOwnClasses } from './common'

const NOTES_EXAMPLE = `Например:
За арифметическую ошибку при верном ходе решения снимать 1 балл.
Ответ без проверки корней в №4 не засчитывать.
Можно записывать ответ десятичной дробью.
№7 проверю сам.`

/** Экран 2. Добавить работу (и правка параметров существующей). */
export function AssignmentForm({ id }: { id?: string }) {
  const { state, update, runJob, jobs, toast } = useStore()
  const classes = useOwnClasses()
  const existing = id ? state.assignments.find((a) => a.id === id) : undefined
  // пришли из проверки распознавания новой работы — туда же и возвращаемся (с «Отмена»/«Сохранить»)
  const back = useRoute().query.get('new') === '1' ? { new: '1' } : {}
  const [title, setTitle] = useState(existing?.title ?? '')
  const [date, setDate] = useState(existing?.date ?? new Date().toLocaleDateString('sv-SE'))
  const [type, setType] = useState<WorkType>(existing?.type ?? 'control')
  // null — учитель ещё не выбирал: берём первый класс (классы приходят из кабинета уже после открытия формы)
  const [picked, setPicked] = useState<string[] | null>(existing?.classIds ?? null)
  const classIds = (picked ?? (classes[0] ? [classes[0].id] : [])).filter((cid) => classes.some((c) => c.id === cid))
  const setClassIds = (fn: (ids: string[]) => string[]) => setPicked(fn(classIds))
  const [notes, setNotes] = useState(existing?.notes ?? '')
  // Файлы с заданиями по порядку; text = null — файл сохранён раньше, его текст заново не читаем
  const [items, setItems] = useState<{ source: SourceFile; text: string | null }[]>(() =>
    existing ? sourceFiles(existing).map((source) => ({ source, text: null })) : [],
  )
  const [parsed, setParsed] = useState<SourceTask[] | null>(null)
  const [pasted, setPasted] = useState('')
  const [lastFiles, setLastFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const files = items.map((i) => i.source)
  const [draftKey] = useState(() => uid('parse'))
  const job = jobs[draftKey]

  if (id && !existing) return <NotFound />

  /** Делит на номера общий текст всех файлов по «1)», «2.», «№3». */
  const split = (list: { text: string | null }[]) => {
    const text = list.map((i) => i.text ?? '').filter(Boolean).join('\n')
    if (!text.trim()) {
      setParsed(null)
      return
    }
    const tasks = splitTasks(text)
    setParsed(tasks)
    if (!tasks.length) setPasted(text)
  }

  /** Добавляет файлы к уже выбранным (сохранённые раньше файлы без текста заменяются новыми). */
  const addFiles = (fs: File[]) => {
    if (!fs.length) return
    setLastFiles(fs)
    setParsed(null)
    if (!title.trim()) setTitle(fs[0].name.replace(/\.docx$/i, ''))
    const kept = items.filter((i) => i.text !== null)
    void runJob(draftKey, 'ocr', fs.length > 1 ? `Обработка файлов (${fs.length})` : 'Обработка файла', async () => {
      const read: { source: SourceFile; text: string }[] = []
      for (const f of fs) read.push(await readSource(f)) // по очереди — задания нумеруются в порядке файлов
      const next = [...kept, ...read]
      setItems(next)
      split(next)
    })
  }

  const removeFile = (idx: number) => {
    const next = items.filter((_, i) => i !== idx)
    setItems(next)
    setParsed(null)
    split(next)
  }

  const tasksToSave = parsed?.length ? parsed : pasted.trim() ? splitTasks(pasted) : null
  // новая работа: материалы, классы, заметки; название — по имени файла, тип — в «Параметрах»
  const canSave = classIds.length && (existing ? title.trim() : tasksToSave?.length)
  // почему кнопка неактивна — пишем рядом с ней
  const blocker = !classIds.length
    ? 'Выберите класс.'
    : existing
      ? !title.trim() && 'Укажите название работы.'
      : !tasksToSave?.length &&
        (files.length
          ? 'В DOCX не найдены задания с номерами «1.», «2.» — номера должны быть напечатаны текстом, не автонумерацией Word. Или введите текст заданий ниже.'
          : 'Добавьте DOCX с заданиями или введите их текст.')

  const save = () => {
    if (existing) {
      update((s) => ({
        ...s,
        assignments: patch(s.assignments, existing.id, (a) => ({
          ...a,
          title: title.trim(),
          date,
          type,
          classIds,
          notes,
          ...(items.some((i) => i.text !== null) || files.length !== sourceFiles(a).length
            ? { file: files[0], files }
            : {}),
          ...(tasksToSave?.length ? { tasks: tasksToSave, variants: [], features: {}, status: 'draft' as const } : {}),
        })),
      }))
      toast('Сохранено')
      go('a', existing.id, 'source', back)
      return
    }
    const a: Assignment = {
      id: uid('a'),
      title: title.trim() || 'Новая работа',
      date,
      type,
      createdAt: Date.now(),
      file: files[0],
      files,
      classIds,
      notes,
      classNotes: {},
      tasks: tasksToSave ?? [],
      variantCount: type === 'control' ? 2 : 1,
      variants: [],
      features: {},
      status: 'draft',
    }
    update((s) => ({ ...s, assignments: [a, ...s.assignments] }))
    go('a', a.id, 'source', { new: '1' })
  }

  return (
    <div className="page page--narrow">
      <PageHead
        title={existing ? 'Параметры работы' : 'Добавить работу'}
        sub={existing?.title}
      />

      <div className="form">
        <label className="field">
          <span>Название работы</span>
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Например: Квадратные уравнения"
          />
        </label>

        <label className="field">
          <span>Дата работы</span>
          <input className="input input--date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>

        {existing && (
        <div className="field">
          <span>Тип</span>
          <div className="segmented" role="radiogroup" aria-label="Тип работы">
            {(Object.keys(WORK_TYPE_LABEL) as WorkType[]).map((t) => (
              <button key={t} role="radio" aria-checked={type === t} onClick={() => setType(t)}>
                {WORK_TYPE_LABEL[t]}
              </button>
            ))}
          </div>
        </div>
        )}

        <div className="field">
          <span>Файлы с заданиями: DOCX (допускается несколько файлов)</span>
          {files.length > 0 && (
            <ol className="file-list">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`}>
                  <span>📄</span>
                  <b>{f.name}</b>
                  <button
                    className="icon-btn icon-btn--danger"
                    onClick={() => removeFile(i)}
                    disabled={job?.status === 'running'}
                    aria-label={`Убрать ${f.name}`}
                    title="Убрать файл"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ol>
          )}
          <label
            className={`dropzone${dragOver ? ' is-over' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              addFiles(Array.from(e.dataTransfer.files))
            }}
          >
            <input
              type="file"
              multiple
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              hidden
              onChange={(e) => {
                addFiles(Array.from(e.target.files ?? []))
                e.target.value = ''
              }}
            />
            <span>
              {files.length ? '+ Добавить ещё файлы' : 'Нажмите, чтобы выбрать файлы, или перетащите их сюда'}
            </span>
          </label>
          {job?.status === 'running' && (
            <span className="job">
              <span className="spinner spinner--sm" /> {job.title}…
            </span>
          )}
          {job?.status === 'error' && (
            <span className="job job--err">
              <Badge tone="err">Ошибка</Badge> {job.error}
              {lastFiles.length > 0 && (
                <button className="link-btn" onClick={() => addFiles(lastFiles)}>
                  Повторить
                </button>
              )}
            </span>
          )}
          {parsed && parsed.length > 0 && (
            <span className="ok-line">
              ✓ Найдено заданий: {parsed.length}. Их необходимо проверить на следующем шаге.
            </span>
          )}
          {existing && parsed?.length ? (
            <span className="muted small">При сохранении задания и варианты будут заменены, работа получит статус «Черновик».</span>
          ) : null}
          {(job?.status === 'error' || (parsed && !parsed.length) || (!existing && !files.length)) && (
            <details className="paste" open={!!parsed && !parsed.length}>
              <summary>Или введите текст заданий</summary>
              <textarea
                className="input"
                rows={7}
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder={'1. Решите уравнение x² + 2x − 5 = 0.\n2. ...'}
              />
              {pasted.trim() && <span className="muted small">Заданий: {splitTasks(pasted).length}</span>}
            </details>
          )}
        </div>

        <div className="field">
          <span>Классы</span>
          {!classes.length ? (
            <span className="muted">
              Нет классов — <a href="#/classes">создайте класс</a>.
            </span>
          ) : (
            <div className="class-selects">
              {/* по выпадающему списку на класс: в каждом — этот класс и ещё не выбранные */}
              {(classIds.length ? classIds : ['']).map((id, i) => (
                <div key={i} className="class-select">
                  <select
                    className="input"
                    aria-label={`Класс ${i + 1}`}
                    value={id}
                    onChange={(e) =>
                      setClassIds((ids) => {
                        const next = ids.length ? [...ids] : ['']
                        next[i] = e.target.value
                        return next.filter(Boolean)
                      })
                    }
                  >
                    {!id && <option value="">Выберите класс</option>}
                    {classes
                      .filter((c) => c.id === id || !classIds.includes(c.id))
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                  </select>
                  {classIds.length > 1 && (
                    <button
                      className="icon-btn icon-btn--danger"
                      aria-label="Убрать класс"
                      title="Убрать класс"
                      onClick={() => setClassIds((ids) => ids.filter((_, j) => j !== i))}
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
              {classIds.length > 0 && classIds.length < classes.length && (
                <button
                  className="link-btn"
                  onClick={() => {
                    const free = classes.find((c) => !classIds.includes(c.id))
                    if (free) setClassIds((ids) => [...ids, free.id])
                  }}
                >
                  + Добавить ещё класс
                </button>
              )}
            </div>
          )}
        </div>

        <label className="field">
          <span>Указания по проверке</span>
          <textarea
            className="input"
            rows={6}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={NOTES_EXAMPLE}
          />
        </label>

        <div className="form__foot">
          <a className="btn" href={existing ? href('a', existing.id, 'source', back) : '#/'}>
            Отмена
          </a>
          {blocker && job?.status !== 'running' && <span className="muted small">{blocker}</span>}
          <button className="btn btn--primary" disabled={!canSave || job?.status === 'running'} onClick={save}>
            {existing ? 'Сохранить' : 'Далее: проверка распознавания →'}
          </button>
        </div>
      </div>
    </div>
  )
}
