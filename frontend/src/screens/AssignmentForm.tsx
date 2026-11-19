import { useState } from 'react'
import { readSource, splitTasks } from '../api/source'
import { uid, WORK_TYPE_LABEL, type Assignment, type SourceFile, type SourceTask, type WorkType } from '../domain'
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
  const [file, setFile] = useState<SourceFile | undefined>(existing?.file)
  const [parsed, setParsed] = useState<SourceTask[] | null>(null)
  const [pasted, setPasted] = useState('')
  const [lastFile, setLastFile] = useState<File | null>(null)
  const [draftKey] = useState(() => uid('parse'))
  const job = jobs[draftKey]

  if (id && !existing) return <NotFound />

  const parse = (f: File) => {
    setLastFile(f)
    setParsed(null)
    if (!title.trim()) setTitle(f.name.replace(/\.(docx|pdf|jpe?g|png)$/i, ''))
    void runJob(draftKey, 'ocr', 'Разбираем файл', async () => {
      const { source, text } = await readSource(f)
      setFile(source)
      const tasks = splitTasks(text)
      setParsed(tasks)
      if (!tasks.length) setPasted(text)
    })
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
          ...(file !== a.file ? { file } : {}),
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
      file,
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
          <span>Файл с заданиями — DOCX или PDF</span>
          <label className="dropzone">
            <input
              type="file"
              accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/*"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) parse(f)
                e.target.value = ''
              }}
            />
            {file ? (
              <span>
                📄 <b>{file.name}</b> <span className="muted small">— нажмите, чтобы заменить</span>
              </span>
            ) : (
              <span>Нажмите, чтобы выбрать файл</span>
            )}
          </label>
          {job?.status === 'running' && (
            <span className="job">
              <span className="spinner spinner--sm" /> Разбираем файл…
            </span>
          )}
          {job?.status === 'error' && (
            <span className="job job--err">
              <Badge tone="err">Сбой</Badge> {job.error}
              {lastFile && (
                <button className="link-btn" onClick={() => parse(lastFile)}>
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
          {(job?.status === 'error' || (parsed && !parsed.length) || (!existing && !file)) && (
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
