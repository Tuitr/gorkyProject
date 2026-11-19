import { useState } from 'react'
import { splitTasksAi } from '../api/generate'
import { readSource, splitTasks } from '../api/source'
import { sourceFiles, uid, WORK_TYPE_LABEL, type Assignment, type SourceFile, type SourceTask, type WorkType } from '../domain'
import { go, href } from '../router'
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
  const [title, setTitle] = useState(existing?.title ?? '')
  const [type, setType] = useState<WorkType>(existing?.type ?? 'control')
  const [classIds, setClassIds] = useState<string[]>(existing?.classIds ?? (classes[0] ? [classes[0].id] : []))
  const [notes, setNotes] = useState(existing?.notes ?? '')
  const [classNotes, setClassNotes] = useState<Record<string, string>>(existing?.classNotes ?? {})
  const [openNotes, setOpenNotes] = useState<string[]>(Object.keys(existing?.classNotes ?? {}))
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

  /** Делит на номера общий текст всех файлов: сначала нейросеть, при сбое — по «1)», «2.». */
  const split = async (list: { text: string | null }[]) => {
    const text = list.map((i) => i.text ?? '').filter(Boolean).join('\n')
    if (!text.trim()) {
      setParsed(null)
      return
    }
    let tasks = splitTasks(text)
    try {
      // нейросеть делит на номера надёжнее регулярки (номера без «1)», мусор с картинок)
      const ai = await splitTasksAi(text)
      if (ai.length) tasks = ai.map((t) => ({ id: uid('t'), num: t.num, text: t.text, manual: false, max: 1 }))
    } catch (e) {
      console.warn('GigaChat не разделил номера, делим по «1)», «2.»', e)
    }
    setParsed(tasks)
    if (!tasks.length) setPasted(text)
  }

  /** Добавляет файлы к уже выбранным (сохранённые раньше файлы без текста заменяются новыми). */
  const addFiles = (fs: File[]) => {
    if (!fs.length) return
    setLastFiles(fs)
    setParsed(null)
    if (!title.trim()) setTitle(fs[0].name.replace(/\.(docx|pdf|jpe?g|png|heic|webp)$/i, ''))
    const kept = items.filter((i) => i.text !== null)
    void runJob(draftKey, 'ocr', fs.length > 1 ? `Разбираем файлы (${fs.length})` : 'Разбираем файл', async () => {
      const read: { source: SourceFile; text: string }[] = []
      for (const f of fs) read.push(await readSource(f)) // по очереди — номера идут в порядке файлов
      const next = [...kept, ...read]
      setItems(next)
      await split(next)
    })
  }

  const removeFile = (idx: number) => {
    const next = items.filter((_, i) => i !== idx)
    setItems(next)
    setParsed(null)
    if (next.some((i) => i.text)) void runJob(draftKey, 'ocr', 'Разбираем файлы', () => split(next))
  }

  const tasksToSave = parsed?.length ? parsed : pasted.trim() ? splitTasks(pasted) : null
  const canSave = title.trim() && classIds.length && (existing || tasksToSave?.length)

  const save = () => {
    const cleanClassNotes = Object.fromEntries(
      Object.entries(classNotes).filter(([k, v]) => classIds.includes(k) && v.trim()),
    )
    if (existing) {
      update((s) => ({
        ...s,
        assignments: patch(s.assignments, existing.id, (a) => ({
          ...a,
          title: title.trim(),
          type,
          classIds,
          notes,
          classNotes: cleanClassNotes,
          ...(items.some((i) => i.text !== null) || files.length !== sourceFiles(a).length
            ? { file: files[0], files }
            : {}),
          ...(tasksToSave?.length ? { tasks: tasksToSave, variants: [], features: {}, status: 'draft' as const } : {}),
        })),
      }))
      toast('Сохранено')
      go('a', existing.id, 'source')
      return
    }
    const a: Assignment = {
      id: uid('a'),
      title: title.trim(),
      type,
      createdAt: Date.now(),
      file: files[0],
      files,
      classIds,
      notes,
      classNotes: cleanClassNotes,
      tasks: tasksToSave ?? [],
      variantCount: type === 'control' ? 2 : 1,
      variants: [],
      features: {},
      status: 'draft',
    }
    update((s) => ({ ...s, assignments: [a, ...s.assignments] }))
    go('a', a.id, 'source')
  }

  return (
    <div className="page page--narrow">
      <PageHead
        title={existing ? 'Параметры работы' : 'Добавить работу'}
        sub={existing ? existing.title : 'Загрузите задания — дальше сверите номера и получите варианты'}
      />

      <div className="form">
        <label className="field">
          <span>Название</span>
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Квадратные уравнения"
          />
        </label>

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

        <div className="field">
          <span>Файлы с заданиями — DOCX, PDF или фото (можно несколько)</span>
          {files.length > 0 && (
            <ol className="file-list">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`}>
                  <span>{f.kind === 'image' ? '🖼' : '📄'}</span>
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
              accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/*"
              hidden
              onChange={(e) => {
                addFiles(Array.from(e.target.files ?? []))
                e.target.value = ''
              }}
            />
            <span>
              {files.length ? '+ Добавить ещё файлы' : 'Нажмите или перетащите сюда файлы'}{' '}
              <span className="muted small">— номера идут в порядке файлов</span>
            </span>
          </label>
          {job?.status === 'running' && (
            <span className="job">
              <span className="spinner spinner--sm" /> {job.title}…
            </span>
          )}
          {job?.status === 'error' && (
            <span className="job job--err">
              <Badge tone="err">Сбой</Badge> {job.error}
              {lastFiles.length > 0 && (
                <button className="link-btn" onClick={() => addFiles(lastFiles)}>
                  Повторить
                </button>
              )}
            </span>
          )}
          {parsed && parsed.length > 0 && (
            <span className="ok-line">
              ✓ Найдено номеров: {parsed.length}. Проверите их на следующем шаге.
            </span>
          )}
          {existing && parsed?.length ? (
            <span className="muted small">При сохранении номера и варианты будут заменены — работа станет черновиком.</span>
          ) : null}
          {(job?.status === 'error' || (parsed && !parsed.length) || (!existing && !files.length)) && (
            <details className="paste" open={!!parsed && !parsed.length}>
              <summary>Или вставьте текст заданий</summary>
              <textarea
                className="input"
                rows={7}
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder={'1. Решите уравнение x² + 2x − 5 = 0.\n2. ...'}
              />
              {pasted.trim() && <span className="muted small">Номеров: {splitTasks(pasted).length}</span>}
            </details>
          )}
        </div>

        <div className="field">
          <span>Для каких классов</span>
          {!classes.length ? (
            <span className="muted">
              Нет классов — <a href="#/classes">создайте класс</a>.
            </span>
          ) : (
            <div className="checks">
              {classes.map((c) => (
                <label key={c.id} className="check">
                  <input
                    type="checkbox"
                    checked={classIds.includes(c.id)}
                    onChange={(e) =>
                      setClassIds((ids) => (e.target.checked ? [...ids, c.id] : ids.filter((x) => x !== c.id)))
                    }
                  />
                  {c.name}
                </label>
              ))}
            </div>
          )}
        </div>

        <label className="field">
          <span>Заметки к работе — как проверять</span>
          <textarea
            className="input"
            rows={6}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={NOTES_EXAMPLE}
          />
          <span className="muted small">
            Пишите обычным языком. На шаге «Исходник» увидите, какие правила распознаны, а что не учтено автоматически.
          </span>
        </label>

        {classIds.length > 0 && (
          <div className="field">
            <span>Заметки для конкретного класса</span>
            {classes
              .filter((c) => classIds.includes(c.id))
              .map((c) =>
                openNotes.includes(c.id) ? (
                  <label key={c.id} className="class-note">
                    <b>{c.name}</b>
                    <textarea
                      className="input"
                      rows={2}
                      value={classNotes[c.id] ?? ''}
                      onChange={(e) => setClassNotes((n) => ({ ...n, [c.id]: e.target.value }))}
                      placeholder="Например: в этом классе №6 оценивать мягче"
                    />
                  </label>
                ) : (
                  <button key={c.id} className="link-btn" onClick={() => setOpenNotes((o) => [...o, c.id])}>
                    + заметка для {c.name}
                  </button>
                ),
              )}
          </div>
        )}

        <div className="form__foot">
          <a className="btn" href={existing ? href('a', existing.id, 'source') : '#/'}>
            Отмена
          </a>
          <button className="btn btn--primary" disabled={!canSave || job?.status === 'running'} onClick={save}>
            {existing ? 'Сохранить' : 'Далее: сверка исходника →'}
          </button>
        </div>
      </div>
    </div>
  )
}
