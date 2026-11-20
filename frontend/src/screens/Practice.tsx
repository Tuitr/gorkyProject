import { analyzeClass, type Topic } from '../analytics'
import { askConfirm } from '../lib/confirm'
import { uid, type Assignment, type ClassGroup, type Practice as PracticeT, type PracticeGroup, type PracticeMode } from '../domain'
import { href } from '../router'
import { patch, useStore } from '../store'
import { Badge } from '../ui'
import { AssignmentHeader, ClassTabs, NotFound, useAssignment } from './common'

const MODES: { id: PracticeMode; title: string; text: string }[] = [
  { id: 'board', title: 'Разбор у доски', text: 'Анонимный пример с наиболее частой ошибкой и задание на закрепление для всего класса.' },
  { id: 'groups', title: 'Варианты по типам ошибок', text: 'Ученики распределяются по группам в соответствии с основной ошибкой; для выполнивших всё верно — группа «Повышенная сложность».' },
  { id: 'individual', title: 'Индивидуальный вариант', text: 'Для каждого ученика — задания по допущенным им ошибкам.' },
]

const DEEP = 'Повышенная сложность'
const deepTopic = (a: Assignment) => `${DEEP}: задачи повышенной сложности по теме «${a.title}»`
const topicName = (a: Assignment, t: Topic) => {
  const task = a.tasks.find((x) => x.id === t.taskId)
  return `${task?.text.split('\n')[0] ?? t.name}${t.categories.length ? ` (ошибки: ${t.categories.join(', ')})` : ''}`
}

/** Группы по умолчанию: каждый ученик — в группу своей самой проблемной темы. */
function buildGroups(a: Assignment, cls: ClassGroup, topics: Topic[], studentOf: (sid: string) => string | null, mode: PracticeMode, handed: Set<string>) {
  const groups: PracticeGroup[] = []
  if (mode === 'individual') {
    for (const st of cls.students.filter((s) => handed.has(s.id))) {
      const mine = topics.filter((t) => t.failed.some((sid) => studentOf(sid) === st.id)).slice(0, 3)
      groups.push({
        id: uid('g'),
        name: st.name,
        topic: mine.length ? mine.map((t) => topicName(a, t)).join(' | ') : deepTopic(a),
        studentIds: [st.id],
        tasks: [],
      })
    }
    return groups
  }
  const top = topics.slice(0, 3)
  const placed = new Set<string>()
  for (const t of top) {
    const ids = t.failed.map(studentOf).filter((x): x is string => !!x && !placed.has(x))
    ids.forEach((x) => placed.add(x))
    groups.push({ id: uid('g'), name: `Группа ${groups.length + 1}: №${a.tasks.find((x) => x.id === t.taskId)?.num}`, topic: topicName(a, t), studentIds: ids, tasks: [] })
  }
  const rest = cls.students.filter((s) => handed.has(s.id) && !placed.has(s.id)).map((s) => s.id)
  groups.push({ id: uid('g'), name: DEEP, topic: deepTopic(a), studentIds: rest, tasks: [] })
  return groups.filter((g) => g.studentIds.length || g.name === DEEP)
}

/** Экран 12. Практика по ошибкам: режим, группы, задачи с ключами, утверждение и печать. */
export function Practice({ id, classId }: { id: string; classId?: string }) {
  const { state, update, toast } = useStore()
  const { a, classes } = useAssignment(id)
  const cls = classes.find((c) => c.id === classId) ?? classes[0]
  if (!a) return <NotFound />
  if (!cls) return <NotFound what="Класс" />

  const subs = state.submissions.filter((s) => s.assignmentId === a.id && s.classId === cls.id)
  const { topics, processed } = analyzeClass(a, subs)
  const studentOf = (sid: string) => subs.find((s) => s.id === sid)?.studentId ?? null
  const handed = new Set(subs.filter((s) => s.status === 'processed' && s.studentId).map((s) => s.studentId!))
  const p = state.practices.find((x) => x.assignmentId === a.id && x.classId === cls.id)
  const setP = (fn: (x: PracticeT) => PracticeT) => update((st) => ({ ...st, practices: patch(st.practices, p!.id, fn) }))

  const chooseMode = async (mode: PracticeMode) => {
    if (
      p &&
      p.groups.some((g) => g.tasks.length) &&
      !(await askConfirm('Сменить режим? Сформированные задания будут удалены.', { ok: 'Сменить', danger: true }))
    )
      return
    const next: PracticeT = {
      id: p?.id ?? uid('pr'),
      assignmentId: a.id,
      classId: cls.id,
      mode,
      groups: mode === 'board' ? [] : buildGroups(a, cls, topics, studentOf, mode, handed),
      approved: false,
    }
    update((st) => ({ ...st, practices: [...st.practices.filter((x) => x.id !== next.id), next] }))
  }

  const moveStudent = (sid: string, to: string) =>
    setP((x) => ({
      ...x,
      approved: false,
      groups: x.groups.map((g) => ({
        ...g,
        studentIds: g.id === to ? [...g.studentIds.filter((y) => y !== sid), sid] : g.studentIds.filter((y) => y !== sid),
      })),
    }))

  const hasTasks = p && (p.mode === 'board' ? !!p.board : p.groups.some((g) => g.tasks.length))
  const name = (sid: string) => cls.students.find((s) => s.id === sid)?.name ?? sid

  return (
    <div className="page page--wide">
      <AssignmentHeader a={a} step="practice" classId={cls.id} />
      <ClassTabs classes={classes} value={cls.id} a={a} step="practice" />

      {!processed ? (
        <div className="empty">
          <h2>Нет проверенных работ</h2>
        </div>
      ) : (
        <>
          <div className="modes" role="radiogroup" aria-label="Режим">
            {MODES.map((m) => (
              <button key={m.id} role="radio" aria-checked={p?.mode === m.id} className="mode" onClick={() => chooseMode(m.id)}>
                <b>{m.title}</b>
                <span className="muted small">{m.text}</span>
              </button>
            ))}
          </div>

          {p && (
            <>
              <div className="toolbar">
                <span className="muted small">
                  Группы собраны по ошибкам из результатов кабинета. Автоматическое формирование заданий пока отключено.
                </span>
                <span className="spacer" />
                {hasTasks && (
                  <>
                    <a className="btn" href={href('print', 'practice', p.id)} target="_blank" rel="noreferrer">
                      ⤓ PDF {p.mode === 'individual' ? 'учеников' : p.mode === 'groups' ? 'групп' : 'для доски'}
                    </a>
                    <a className="btn" href={href('print', 'practice-keys', p.id)} target="_blank" rel="noreferrer">
                      ⤓ Ответы для учителя
                    </a>
                  </>
                )}
                {p.approved ? (
                  <span className="ok-line">✓ Утверждено</span>
                ) : (
                  <button className="btn btn--primary" disabled={!hasTasks} onClick={() => (setP((x) => ({ ...x, approved: true })), toast('Работа над ошибками утверждена'))}>
                    Утвердить
                  </button>
                )}
              </div>

              {p.mode === 'board' ? (
                <section className="panel">
                  <h2>Разбор у доски</h2>
                  {topics[0] && <p className="muted">Тема: {topics[0].name} — ошиблись {Math.round(topics[0].share * 100)}%</p>}
                  {p.board ? (
                    <>
                      <h3>Пример (анонимно)</h3>
                      <div className="solution solution--bad">
                        <p>{p.board.example}</p>
                      </div>
                      <h3>Задание на закрепление</h3>
                      <p>{p.board.task.text}</p>
                      <p className="key-line">
                        Ответ: <b>{p.board.task.answer}</b>
                      </p>
                    </>
                  ) : (
                    null
                  )}
                </section>
              ) : (
                <div className="groups">
                  {p.groups.map((g) => (
                    <section key={g.id} className={`panel group${g.name === DEEP ? ' group--deep' : ''}`}>
                      <div className="panel__head">
                        <h2>{g.name}</h2>
                        <Badge tone={g.name === DEEP ? 'ok' : 'warn'}>{g.studentIds.length} уч.</Badge>
                      </div>
                      <p className="muted small">{g.topic.split(' | ').join(' · ')}</p>
                      {p.mode === 'groups' && (
                        <ul className="group__students">
                          {g.studentIds.map((sid) => (
                            <li key={sid}>
                              {name(sid)}
                              <select
                                className="input input--sm"
                                value={g.id}
                                onChange={(e) => moveStudent(sid, e.target.value)}
                                aria-label={`Перенести ${name(sid)}`}
                              >
                                {p.groups.map((x) => (
                                  <option key={x.id} value={x.id}>
                                    {x.id === g.id ? 'перенести в группу…' : x.name}
                                  </option>
                                ))}
                              </select>
                            </li>
                          ))}
                          {!g.studentIds.length && <li className="muted small">нет учеников</li>}
                        </ul>
                      )}
                      {g.tasks.length > 0 && (
                        <ol className="ptasks">
                          {g.tasks.map((t) => (
                            <li key={t.id}>
                              <p>{t.text}</p>
                              <div className="ptask__foot">
                                <span className="key-line">
                                  Ответ: <b>{t.answer || '—'}</b>
                                </span>
                              </div>
                            </li>
                          ))}
                        </ol>
                      )}
                    </section>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
