import { WORK_TYPE_LABEL } from '../domain'
import { href } from '../router'
import { useStore } from '../store'
import { Badge } from '../ui'
import { useOwnClasses } from './common'

/** Главная: список работ и кнопка «Добавить работу». Прогресс проверки — в шапке. */
export function Home() {
  const { state } = useStore()
  const classes = useOwnClasses()
  const list = [...state.assignments].sort((a, b) => b.createdAt - a.createdAt)

  return (
    <div className="page">
      <div className="home-head">
        <h1 className="heading">Работы</h1>
        <a className="btn btn--primary" href="#/new">
          + Добавить работу
        </a>
      </div>

      {!classes.length && (
        <div className="notice">
          Сначала создайте класс в разделе <a href="#/classes">«Мои классы»</a>.
        </div>
      )}
      {!list.length ? (
        <div className="empty">
          <h2>Работ пока нет</h2>
          <p>Загрузите DOCX или PDF с заданиями — номера разберутся автоматически.</p>
          <a className="btn btn--primary" href="#/new">
            Добавить работу
          </a>
        </div>
      ) : (
        <ul className="cards">
          {list.map((a) => {
            const subs = state.submissions.filter((s) => s.assignmentId === a.id)
            const q = state.review.filter((r) => !r.resolved && subs.some((s) => s.id === r.submissionId)).length
            const main = href('a', a.id, a.status === 'draft' ? 'source' : 'collect')
            return (
              <li key={a.id} className={`card${a.status === 'draft' ? ' is-draft' : ''}`}>
                <div className="card__head">
                  <span className="card__subject">
                    {WORK_TYPE_LABEL[a.type]} · {new Date(a.createdAt).toLocaleDateString('ru-RU')}
                  </span>
                  {a.status === 'draft' ? <Badge tone="info">Черновик</Badge> : <Badge tone="ok">Утверждена</Badge>}
                </div>
                <h3 className="card__title">
                  <a href={main}>{a.title}</a>
                </h3>
                <div className="card__meta">
                  <span>{a.tasks.length} номеров</span>
                  <span>{a.variants.length || a.variantCount} вар.</span>
                  {q > 0 && <Badge tone="warn">требует внимания: {q}</Badge>}
                </div>
                <div className="card__classes">
                  {a.classIds.map((cid) => {
                    const c = classes.find((x) => x.id === cid)
                    if (!c) return null
                    const cs = subs.filter((s) => s.classId === cid)
                    const done = cs.filter((s) => s.status === 'processed').length
                    const got = cs.filter((s) => s.status !== 'none' && s.pages.length).length
                    return (
                      <a key={cid} className="class-pill" href={href('a', a.id, 'collect', { c: cid })}>
                        <b>{c.name}</b>
                        <span className="muted small">
                          сдано {got}/{c.students.length} · проверено {done}
                        </span>
                      </a>
                    )
                  })}
                </div>
                <div className="card__foot">
                  <div className="card__actions">
                    <a className="link-btn" href={href('a', a.id, 'variants', { f: '1' })}>
                      Варианты
                    </a>
                    <a className="link-btn" href={href('a', a.id, 'sheet', { f: '1' })}>
                      Ведомость
                    </a>
                    <a className="link-btn" href={href('a', a.id, 'class-report', { f: '1' })}>
                      Отчёт
                    </a>
                  </div>
                  <a className="card__open" href={main}>
                    Открыть →
                  </a>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
