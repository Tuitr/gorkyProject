import type { ReactNode } from 'react'
import './App.css'
import './mvp.css'
import { href, useRoute } from './router'
import { shortName, useStore } from './store'
import { Donut } from './ui'
import { AssignmentForm } from './screens/AssignmentForm'
import { ClassReport } from './screens/ClassReport'
import { Classes } from './screens/Classes'
import { Collect } from './screens/Collect'
import { Gradebook } from './screens/Gradebook'
import { Home } from './screens/Home'
import { Hub } from './screens/Hub'
import { Practice } from './screens/Practice'
import { PrintView } from './screens/Print'
import { Queue } from './screens/Queue'
import { QuickCheck } from './screens/QuickCheck'
import { SourceReview } from './screens/SourceReview'
import { StudentCard } from './screens/StudentCard'
import { StudentReport } from './screens/StudentReport'
import { Variants } from './screens/Variants'

function Screen() {
  const { path, query } = useRoute()
  const [p0, p1, p2, p3, p4] = path
  if (p0 === 'classes') return <Classes />
  if (p0 === 'new') return <AssignmentForm />
  if (p0 === 'queue') return <Queue />
  if (p0 === 'quick') return <QuickCheck />
  if (p0 === 'a' && p1) {
    const c = query.get('c') ?? undefined
    if (!p2) return <Hub id={p1} />
    switch (p2) {
      case 'edit':
        return <AssignmentForm id={p1} />
      case 'source':
        return <SourceReview id={p1} />
      case 'variants':
        return <Variants id={p1} />
      case 'collect':
        return <Collect id={p1} classId={c} />
      case 's':
        return <StudentCard id={p1} classId={p3} studentId={p4} />
      case 'queue':
        return <Queue assignmentId={p1} />
      case 'sheet':
        return <Gradebook id={p1} classId={c} />
      case 'report':
        return <StudentReport id={p1} submissionId={p3} />
      case 'class-report':
        return <ClassReport id={p1} classId={c} />
      case 'practice':
        return <Practice id={p1} classId={c} />
    }
  }
  return <Home />
}

export default function App() {
  const { path } = useRoute()
  const { state, reset, user, logout } = useStore()
  if (path[0] === 'print') return <PrintView path={path.slice(1)} />

  const open = state.review.filter((r) => !r.resolved).length
  const handed = state.submissions.filter((s) => s.pages.length && s.status !== 'none')
  const processed = handed.filter((s) => s.status === 'processed').length
  const approved = handed.filter((s) => s.approved).length
  const section = path[0] === 'a' || path[0] === 'new' ? '' : (path[0] ?? '')
  const link = (to: string, label: string, extra?: ReactNode) => (
    <a
      href={href(...(to ? [to] : []))}
      className={section === to ? 'is-active' : ''}
      aria-current={section === to ? 'page' : undefined}
    >
      {label}
      {extra}
    </a>
  )

  return (
    <div className="shell">
      <header className="shell__top">
        <a className="logo" href="#/">
          <span className="logo__mark" aria-hidden="true">
            &gt;_
          </span>
          Spirit Code <span className="logo__sub">· Проверка работ</span>
        </a>
        <nav className="shell__nav" aria-label="Основная навигация">
          {link('', 'Работы')}
          {link('classes', 'Классы')}
          {link('queue', 'Спорные места', open ? <span className="count">{open}</span> : null)}
        </nav>
        <div className="shell__user">
          <div
            className="shell__progress"
            title={`Проверено ${processed} из ${handed.length} сданных работ, утверждено ${approved}`}
          >
            <Donut done={processed} total={handed.length} size={30} bare label={`Проверено ${processed} из ${handed.length}`} />
            <span>
              <b>
                {processed} из {handed.length}
              </b>{' '}
              проверено
            </span>
          </div>
          <a
            href={href('quick')}
            className={`shell__quiet${section === 'quick' ? ' is-active' : ''}`}
            title="Проверка одной фотографии без привязки к классу и работе"
          >
            Быстрая проверка
          </a>
          <button
            className="shell__reset"
            onClick={() => confirm('Удалить все данные и восстановить демонстрационные?') && reset()}
            title="Восстановить демонстрационные классы и работу"
          >
            Демонстрационные данные
          </button>
          {user && (
            <span className="shell__teacher" title={`${user.name} · ${user.email}`}>
              {shortName(user)}
              {logout && (
                <button className="shell__logout" onClick={logout}>
                  Выйти
                </button>
              )}
            </span>
          )}
        </div>
      </header>
      <main className="shell__main">
        <Screen />
      </main>
    </div>
  )
}
