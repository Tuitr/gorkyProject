import './App.css'
import './mvp.css'
import { href, useRoute } from './router'
import { useStore } from './store'
import { AssignmentForm } from './screens/AssignmentForm'
import { ClassReport } from './screens/ClassReport'
import { Classes } from './screens/Classes'
import { Collect } from './screens/Collect'
import { Gradebook } from './screens/Gradebook'
import { Home } from './screens/Home'
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
  const { state, reset } = useStore()
  if (path[0] === 'print') return <PrintView path={path.slice(1)} />

  const open = state.review.filter((r) => !r.resolved).length
  const section = path[0] === 'a' || path[0] === 'new' ? '' : (path[0] ?? '')
  const link = (to: string, label: string, extra?: React.ReactNode) => (
    <a href={href(...(to ? [to] : []))} className={section === to ? 'is-active' : ''}>
      {label}
      {extra}
    </a>
  )

  return (
    <div className="shell">
      <header className="shell__top">
        <a className="logo" href="#/">
          <span className="logo__mark">✓</span>
          <span>Проверка работ</span>
        </a>
        <nav className="shell__nav" aria-label="Разделы">
          {link('', 'Работы')}
          {link('classes', 'Мои классы')}
          {link('queue', 'Очередь', open ? <span className="count">{open}</span> : null)}
          {link('quick', 'Быстрая проверка')}
        </nav>
        <div className="shell__user">
          <span className="muted small">{state.teacher.name}</span>
          <button
            className="link-btn"
            onClick={() => confirm('Сбросить все данные и вернуть демо-данные?') && reset()}
            title="Вернуть демо-классы и демо-работу"
          >
            Демо заново
          </button>
        </div>
      </header>
      <main className="shell__main">
        <Screen />
      </main>
    </div>
  )
}
