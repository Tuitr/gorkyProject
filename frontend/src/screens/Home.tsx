import { useState } from 'react'
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
  const [exporting, setExporting] = useState(false)

  // Excel со статистикой по всем работам; модуль грузится только по нажатию
  const exportExcel = async () => {
    setExporting(true)
    try {
      const { downloadStats } = await import('../api/exportStats')
      downloadStats(state)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="page">
      <div className="home-head">
        <h1 className="heading">Работы</h1>
        <a className="btn btn--primary" href="#/new">
          + Добавить работу
        </a>
        {list.length > 0 && (
          <button
            className="btn home-head__export"
            onClick={() => void exportExcel()}
            disabled={exporting}
            title="Сводка, ученики, результаты, задания и ошибки по всем контрольным и домашним заданиям"
          >
            {exporting ? 'Подготовка…' : 'Скачать Excel'}
          </button>
        )}
      </div>

      {!classes.length && (
        <div className="notice">
          Сначала создайте класс в разделе <a href="#/classes">«Мои классы»</a>.
        </div>
      )}
      {!list.length ? (
        <div className="empty">
          <h2>Работы отсутствуют</h2>
          <a className="btn btn--primary" href="#/new">
            Добавить работу
          </a>
        </div>
      ) : (
        <ul className="cards">
          {list.map((a) => {
            const subs = state.submissions.filter((s) => s.assignmentId === a.id)
            const q = state.review.filter((r) => !r.resolved && subs.some((s) => s.id === r.submissionId)).length
            const main = href('a', a.id)
            return (
              <li key={a.id} className={`card${a.status === 'draft' ? ' is-draft' : ''}`}>
                <div className="card__head">
                  <span className="card__subject">
                    {WORK_TYPE_LABEL[a.type]}
                  </span>
                  {a.status === 'draft' ? <Badge tone="info">Черновик</Badge> : <Badge tone="ok">Утверждена</Badge>}
                </div>
                <h3 className="card__title">
                  <a href={main}>{a.title}</a>
                </h3>
                <div className="card__meta">
                  <span>Заданий: {a.tasks.length}</span>
                  <span>Вариантов: {a.variants.length || a.variantCount}</span>
                  {q > 0 && <Badge tone="warn">требует проверки учителем: {q}</Badge>}
                </div>
                <div className="card__foot">
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
