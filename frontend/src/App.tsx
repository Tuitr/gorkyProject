import { useEffect, useState } from 'react'
import { api, type ClassItem, type Work } from './api'
import { Brand, ClassOptions, ConfirmDialog, CreateWorkCard, ErrorText, errorText, WorkCard } from './components'
import { ClassStudentsDialog, CreateWorkDialog } from './Dialogs'
import { readWorks, workName } from './workspace'
import { WorkView } from './WorkView'
import { AccountMenu, type Account } from './Auth'

function route() {
  return window.location.hash.match(/^#work\/([0-9a-z-]+)$/i)?.[1] ?? ''
}

export function App({ account }: { account: Account }) {
  const [classes, setClasses] = useState<ClassItem[]>([])
  const [works, setWorks] = useState<Work[]>([])
  const [workId, setWorkId] = useState(route)
  const [selectedClass, setSelectedClass] = useState('')
  const [sort, setSort] = useState('newest')
  const [modal, setModal] = useState<'work' | 'students' | null>(null)
  const [toDelete, setToDelete] = useState<Work>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    const onRoute = () => setWorkId(route())
    window.addEventListener('hashchange', onRoute)
    return () => window.removeEventListener('hashchange', onRoute)
  }, [])

  useEffect(() => {
    let disposed = false
    readWorks().then(({ classes: nextClasses, works: nextWorks }) => {
      if (disposed) return
      setClasses(nextClasses)
      setWorks(nextWorks)
      setError('')
    }).catch((cause) => {
      if (!disposed) setError(errorText(cause))
    }).finally(() => {
      if (!disposed) setLoading(false)
    })
    return () => { disposed = true }
  }, [reloadKey])

  const selected = works.find((work) => work.assignmentId === workId)
  const visible = works.filter((work) => !selectedClass || work.classId === selectedClass)
    .sort((a, b) => sort === 'newest' ? b.assignmentDate.localeCompare(a.assignmentDate) : a.assignmentDate.localeCompare(b.assignmentDate))

  function open(work: Work) {
    window.location.hash = 'work/' + work.assignmentId
    setWorkId(work.assignmentId)
  }

  return <>
    <header className="site-header">
      <div className="site-inner">
        <Brand href="#" />
        <div className="header-side">
          {!selected && <div className="header-actions">
            <button className="button" type="button" onClick={() => setModal('students')}>+ Класс и ученики</button>
          </div>}
          <AccountMenu account={account} />
        </div>
      </div>
    </header>
    <main className={'page' + (selected ? ' work-page' : '')}>
      {selected ? <WorkView key={selected.assignmentId + ':' + selected.assignmentRev} work={selected} /> : <>
        <div className="page-head home-head">
          <h1>Работы</h1>
        </div>
        <div className="home-toolbar">
          <div className="filter-set" aria-label="Фильтры работ">
            <label>Класс<select aria-label="Класс" value={selectedClass} onChange={(event) => setSelectedClass(event.target.value)}>
              <option value="">Все классы</option>
              <ClassOptions classes={classes} />
            </select></label>
            <label>Порядок<select value={sort} onChange={(event) => setSort(event.target.value)}>
              <option value="newest">Сначала новые</option><option value="oldest">Сначала старые</option>
            </select></label>
          </div>
        </div>
        <ErrorText message={error} />
        {error && <button type="button" className="button" onClick={() => { setLoading(true); setReloadKey((key) => key + 1) }}>Повторить загрузку</button>}
        {workId && !loading && !error && <p className="notice-error">Работа не найдена. Выберите работу из списка.</p>}
        {loading && <p className="subtle" role="status">Загружаем работы…</p>}
        <div className="cards">
          <CreateWorkCard onClick={() => setModal('work')} />
          {visible.map((work) => <WorkCard key={work.assignmentId} work={work} onRemove={setToDelete} />)}
        </div>
        {!visible.length && !loading && !error && selectedClass && <p className="subtle">В этом классе пока нет работ.</p>}
      </>}
    </main>
    {modal === 'students' && <ClassStudentsDialog classes={classes} selectedClass={selectedClass} onClose={() => setModal(null)}
      onClassCreated={(added) => setClasses((list) => [...list, added])} />}
    {modal === 'work' && <CreateWorkDialog classes={classes} selectedClass={selectedClass} onClose={() => setModal(null)}
      onCreated={(work) => { setWorks((list) => [work, ...list]); setModal(null); open(work) }} />}
    {toDelete && <ConfirmDialog title="Удалить работу?" text={'Работа «' + workName(toDelete) + '» и её результаты исчезнут из кабинета.'}
      onClose={() => setToDelete(undefined)} action={async () => {
        await api.deleteAssignment(toDelete.assignmentId)
        setWorks((list) => list.filter((work) => work.assignmentId !== toDelete.assignmentId))
      }} />}
  </>
}
