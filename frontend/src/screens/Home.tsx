import { WORK_TYPE_LABEL } from '../domain'
import { href } from '../router'
import { useStore } from '../store'
import { Badge, PageHead } from '../ui'
import { useOwnClasses } from './common'

/** Главная: список работ и куда идти дальше. */
export function Home() {
  const { state } = useStore()
  const classes = useOwnClasses()
  const list = [...state.assignments].sort((a, b) => b.createdAt - a.createdAt)

  return (
    <div className="page">
      <PageHead
        title="Работы"
        sub="Контрольные и домашние задания: подготовка вариантов, сбор и проверка"
        actions={
          <a className="btn btn--primary" href="#/new">
            + Добавить работу
          </a>
        }
      />
      {!classes.length && (
        <div className="notice">
          Сначала создайте класс в разделе <a href="#/classes">«Мои классы»</a>.
        </div>
      )}
      {!list.length ? (
        <div className="empty">
          <h2>Работ пока нет</h2>
          <p className="muted">Загрузите DOCX или PDF с заданиями — номера разберутся автоматически.</p>
          <a className="btn btn--primary" href="#/new">
            Добавить работу
          </a>
        </div>
      ) : (
        <ul className="cards">
          {list.map((a) => {
            const subs = state.submissions.filter((s) => s.assignmentId === a.id)
            const queue = state.review.filter((r) => !r.resolved && subs.some((s) => s.id === r.submissionId)).length
            return (
              <li key={a.id} className="card">
                <div className="card__head">
                  <div>
                    <div className="muted small">
                      {WORK_TYPE_LABEL[a.type]} · {new Date(a.createdAt).toLocaleDateString('ru-RU')}
                    </div>
                    <h2>
                      <a href={href('a', a.id, a.status === 'draft' ? 'source' : 'collect')}>{a.title}</a>
                    </h2>
                  </div>
                  {a.status === 'draft' ? <Badge tone="warn">Черновик</Badge> : <Badge tone="ok">Утверждена</Badge>}
                </div>
                <div className="card__meta">
                  <span>{a.tasks.length} номеров</span>
                  <span>{a.variants.length || a.variantCount} вар.</span>
                  {queue > 0 && <Badge tone="warn">требует внимания: {queue}</Badge>}
                </div>
                <div className="card__classes">
                  {a.classIds.map((cid) => {
                    const c = classes.find((x) => x.id === cid)
                    if (!c) return null
                    const cs = subs.filter((s) => s.classId === cid)
                    const done = cs.filter((s) => s.status === 'processed').length
                    const handed = cs.filter((s) => s.status !== 'none').length
                    return (
                      <a key={cid} className="class-pill" href={href('a', a.id, 'collect', { c: cid })}>
                        <b>{c.name}</b>
                        <span className="muted small">
                          сдано {handed}/{c.students.length} · обработано {done}
                        </span>
                      </a>
                    )
                  })}
                </div>
                <div className="card__actions">
                  <a className="link-btn" href={href('a', a.id, 'source')}>
                    Исходник
                  </a>
                  <a className="link-btn" href={href('a', a.id, 'variants')}>
                    Варианты
                  </a>
                  <a className="link-btn" href={href('a', a.id, 'sheet')}>
                    Ведомость
                  </a>
                  <a className="link-btn" href={href('a', a.id, 'class-report')}>
                    Отчёт
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
