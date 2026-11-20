import { useEffect, useRef } from 'react'
import { useActions } from '../actions'
import { ReferenceLine, ReferenceStatus } from '../components/ReferenceAnswers'
import { useReferences } from '../useReferences'
import { sourceFiles, uid, type Assignment, type SourceFile, type SourceTask } from '../domain'
import { go, href, useRoute } from '../router'
import { patch, useStore } from '../store'
import { Badge } from '../ui'
import { AssignmentHeader, NotFound, useAssignment } from './common'

/** Экран 3. Сверка исходника: оригинал рядом со списком номеров, правка, объединение, правила проверки. */
export function SourceReview({ id }: { id: string }) {
  const { update } = useStore()
  const { publish } = useActions()
  const { a } = useAssignment(id)
  const { query } = useRoute()
  const wantAdd = query.get('add') === '1'
  // только что создана из формы «Добавить работу»: проверить номера и сохранить
  const isNew = query.get('new') === '1'
  const added = useRef(false)
  // «Добавить номер» со страницы работы: один пустой номер в конец, курсор — в его условие
  useEffect(() => {
    if (!wantAdd || !a || added.current) return
    added.current = true
    update((s) => ({
      ...s,
      assignments: patch(s.assignments, id, (x) => ({
        ...x,
        status: 'draft',
        tasks: [...x.tasks, { id: uid('t'), num: String(x.tasks.length + 1), text: '', manual: false, max: 1 }],
      })),
    }))
    history.replaceState(null, '', href('a', id, 'source', { f: query.get('f') ?? undefined }))
    setTimeout(() => {
      const last = [...document.querySelectorAll<HTMLTextAreaElement>('.src-task textarea')].pop()
      last?.scrollIntoView({ block: 'center' })
      last?.focus()
    }, 50)
  }, [wantAdd, a, id, update, query])

  // эталонные ответы из реестра решателей — учитель видит, с чем кабинет будет сверять работы
  const refs = useReferences(a)

  if (!a) return <NotFound />

  // любая правка исходника возвращает работу в черновик
  const setA = (fn: (x: Assignment) => Assignment) =>
    update((s) => ({
      ...s,
      assignments: patch(s.assignments, id, (x) => ({ ...fn(x), status: 'draft', sourceEdited: true })),
    }))
  const setTask = (tid: string, p: Partial<SourceTask>) =>
    setA((x) => ({ ...x, tasks: x.tasks.map((t) => (t.id === tid ? { ...t, ...p } : t)) }))

  const merge = (i: number) =>
    setA((x) => {
      const prev = x.tasks[i - 1]
      const cur = x.tasks[i]
      const tasks = x.tasks.filter((_, j) => j !== i)
      // номер случайно разбился на два — склеиваем текст, балл берём больший
      tasks[i - 1] = { ...prev, text: `${prev.text}\n${cur.text}`.trim(), max: Math.max(prev.max, cur.max) }
      return { ...x, tasks, variants: x.variants.map((v) => ({ ...v, tasks: v.tasks.filter((t) => t.taskId !== cur.id) })) }
    })

  const remove = (tid: string) =>
    setA((x) => ({
      ...x,
      tasks: x.tasks.filter((t) => t.id !== tid),
      variants: x.variants.map((v) => ({ ...v, tasks: v.tasks.filter((t) => t.taskId !== tid) })),
    }))

  const add = () =>
    setA((x) => ({
      ...x,
      tasks: [...x.tasks, { id: uid('t'), num: String(x.tasks.length + 1), text: '', manual: false, max: 1 }],
    }))

  const total = a.tasks.reduce((s, t) => s + t.max, 0)

  return (
    <div className="page page--wide page--fit">
      {isNew ? (
        <div className="page-head">
          <div>
            <h1>Проверка распознавания</h1>
          </div>
          <div className="page-head__actions">
            <a className="btn" href={href('a', a.id, 'edit', { new: '1' })}>
              ← Назад
            </a>
            <button
              className="btn"
              onClick={() => {
                update((s) => ({ ...s, assignments: s.assignments.filter((x) => x.id !== id) }))
                go()
              }}
            >
              Отмена
            </button>
            <button
              className="btn btn--primary"
              disabled={!a.tasks.length}
              onClick={() => {
                // варианты необязательны: сохранённая работа сразу готова к сбору (все сдают одну общую работу)
                update((s) => ({ ...s, assignments: patch(s.assignments, id, (x) => ({ ...x, status: 'approved' })) }))
                void publish(id) // режим кабинета: работа уходит на сервер
                go('a', a.id)
              }}
            >
              Сохранить
            </button>
          </div>
        </div>
      ) : (
        <AssignmentHeader
          a={a}
          step="source"
          actions={
            <a className="btn" href={href('a', a.id, 'edit')}>
              Параметры
            </a>
          }
        />
      )}

      <div className="source">
        <section className="panel source__orig">
          <div className="panel__head">
            <h2>Исходный документ</h2>
            <span className="muted small">
              {sourceFiles(a).map((f) => f.name).join(', ') || 'файл не загружен'}
            </span>
          </div>
          <Original a={a} />
        </section>

        <section className="panel source__tasks">
          <div className="panel__head">
            <h2>
              Задания: {a.tasks.length} · максимальный балл: {total}
            </h2>
            <Badge tone={a.status === 'draft' ? 'warn' : 'ok'}>{a.status === 'draft' ? 'Черновик' : 'Утверждена'}</Badge>
          </div>
          <ol className="src-tasks">
            {a.tasks.map((t, i) => (
              <li key={t.id} className="src-task">
                <div className="src-task__head">
                  <label className="num">
                    №
                    <input
                      className="input input--num"
                      value={t.num}
                      onChange={(e) => setTask(t.id, { num: e.target.value })}
                      aria-label="Номер задания"
                    />
                  </label>
                  <label className="max">
                    макс.
                    <input
                      className="input input--num"
                      type="number"
                      min={0}
                      value={t.max}
                      onChange={(e) => setTask(t.id, { max: Math.max(0, Number(e.target.value) || 0) })}
                      aria-label="Максимальный балл"
                    />
                    б.
                  </label>
                  <span className="spacer" />
                  {i > 0 && (
                    <button className="link-btn src-task__tool" onClick={() => merge(i)} title="Объединить с предыдущим заданием">
                      Объединить с предыдущим
                    </button>
                  )}
                  <button
                    className="icon-btn icon-btn--danger src-task__tool"
                    onClick={() => remove(t.id)}
                    aria-label={`Удалить задание №${t.num}`}
                    title="Удалить задание"
                  >
                    ✕
                  </button>
                </div>
                <textarea
                  className="input"
                  rows={Math.min(6, Math.max(2, t.text.split('\n').length + 1))}
                  value={t.text}
                  onChange={(e) => setTask(t.id, { text: e.target.value })}
                  aria-label={`Условие задания №${t.num}`}
                />
                <ReferenceLine refs={refs} num={t.num} />
              </li>
            ))}
          </ol>
          <ReferenceStatus refs={refs} saved={!!a.remote && Object.keys(a.remote).length > 0} />
          <div className="src-add">
            <button className="btn" onClick={add}>
              + Задание
            </button>
          </div>
        </section>

      </div>
    </div>
  )
}

function Original({ a }: { a: Assignment }) {
  const list = sourceFiles(a)
  if (!list.length) return <p className="muted">Файл не загружен: задания введены вручную.</p>
  return (
    <div className="orig-list">
      {list.map((f, i) => (
        <div key={i} className="orig-item">
          {list.length > 1 && <div className="orig-item__name muted small">{f.name}</div>}
          <OriginalFile f={f} />
        </div>
      ))}
    </div>
  )
}

function OriginalFile({ f }: { f: SourceFile }) {
  if (f.kind === 'docx' && f.html) return <div className="docx" dangerouslySetInnerHTML={{ __html: f.html }} />
  return <p className="muted">Предпросмотр недоступен.</p>
}
