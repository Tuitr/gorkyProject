import { useEffect, type ReactNode } from 'react'
import { ConfirmHost } from './components/ConfirmHost'
import { ErrorBoundary } from './components/ErrorBoundary'
import { pullClasses, withRemoteClasses } from './api/sync'
import './App.css'
import './mvp.css'
import { href, useRoute } from './router'
import { shortName, useStore } from './store'
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

/** При входе подтягиваем классы и учеников с сервера. */
function useCabinetClasses() {
  const { update, toast } = useStore()
  useEffect(() => {
    let alive = true
    pullClasses()
      .then((remote) => alive && update((s) => withRemoteClasses(s, remote)))
      .catch((e) => alive && toast(e instanceof Error ? e.message : 'Не удалось загрузить классы', 'err'))
    return () => {
      alive = false
    }
  }, [update, toast])
}

export default function App() {
  const { path } = useRoute()
  const { state, user, logout } = useStore()
  useCabinetClasses()
  if (path[0] === 'print') return <PrintView path={path.slice(1)} />

  const open = state.review.filter((r) => !r.resolved).length
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
          Spirit Code
        </a>
        <nav className="shell__nav" aria-label="Основная навигация">
          {link('', 'Работы')}
          {link('classes', 'Классы')}
          {link('queue', 'Спорные места', open ? <span className="count">{open}</span> : null)}
        </nav>
        <div className="shell__user">
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
        {/* ошибка в разделе — сообщение вместо белого экрана; при переходе в другой раздел сбрасывается */}
        <ErrorBoundary key={path.join('/')}>
          <Screen />
        </ErrorBoundary>
      </main>
      <ConfirmHost />
    </div>
  )
}
