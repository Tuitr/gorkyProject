import { useEffect, useState } from 'react'
import { api, type ClassItem, type Work } from './api'
import { AddStudentsDialog, ConfirmDialog, CreateWorkDialog, ErrorText, errorText, NameDialog } from './Dialogs'
import { dateLabel, readWorks, subjectName, workName } from './workspace'
import { WorkView } from './WorkView'
import type { Account } from './Auth'

function route() {
  return window.location.hash.match(/^#work\/([0-9a-z-]+)$/i)?.[1] ?? ''
}

export function App({ account }: { account: Account }) {
  const [classes, setClasses] = useState<ClassItem[]>([])
  const [works, setWorks] = useState<Work[]>([])
  const [workId, setWorkId] = useState(route)
  const [selectedClass, setSelectedClass] = useState('')
  const [sort, setSort] = useState('newest')
  const [modal, setModal] = useState<'work' | 'class' | 'students' | null>(null)
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
        <a className="brand" href="#" aria-label="Проверка работ — главная"><span className="brand-mark" aria-hidden="true">✓</span>Проверка работ</a>
        <div className="header-side">
          {!selected && <div className="header-actions">
            <button className="button" type="button" onClick={() => setModal('class')}>+ Добавить класс</button>
            <button className="button" type="button" disabled={!classes.length}
              title={classes.length ? undefined : 'Сначала добавьте класс'} onClick={() => setModal('students')}>+ Добавить учеников</button>
          </div>}
          <div className="account">
            <span aria-hidden="true">{(account.name.trim()[0] ?? 'У').toUpperCase()}</span>{account.name || 'Кабинет учителя'}
            <button className="text-button account-logout" type="button" onClick={account.logout}>Выйти</button>
          </div>
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
              {classes.map((item) => <option key={item.classId} value={item.classId}>{item.name}</option>)}
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
          <button className="work-card create-card" type="button" onClick={() => setModal('work')}>
            <span className="create-mark" aria-hidden="true">+</span>
            <span>Создать работу</span>
          </button>
          {visible.map((work) => <article className="work-card saved-card" key={work.assignmentId}>
            <button type="button" className="card-remove" aria-label={'Удалить работу ' + workName(work)}
              title="Удалить работу" onClick={() => setToDelete(work)}>×</button>
            <div className="card-top"><time dateTime={work.assignmentDate}>{dateLabel(work.assignmentDate)}</time><span className="subject-pill">{subjectName(work.subject)}</span></div>
            <a className="card-title" href={'#work/' + work.assignmentId}>{workName(work)}</a>
            <div className="card-bottom"><span>{work.className}</span>
              <span aria-hidden="true">↗</span>
            </div>
          </article>)}
        </div>
        {!visible.length && !loading && !error && selectedClass && <p className="subtle">В этом классе пока нет работ.</p>}
      </>}
    </main>
    {modal === 'class' && <NameDialog title="Добавить класс" label="Название класса" placeholder="Например, 7А"
      action={async (name) => { const added = await api.createClass(name); setClasses((list) => [...list, added]) }} onClose={() => setModal(null)} />}
    {modal === 'students' && <AddStudentsDialog classes={classes} selectedClass={selectedClass} onClose={() => setModal(null)} />}
    {modal === 'work' && <CreateWorkDialog classes={classes} selectedClass={selectedClass} onClose={() => setModal(null)}
      onCreated={(work) => { setWorks((list) => [work, ...list]); setModal(null); open(work) }} />}
    {toDelete && <ConfirmDialog title="Удалить работу?" text={'Работа «' + workName(toDelete) + '» и её результаты исчезнут из кабинета.'}
      onClose={() => setToDelete(undefined)} action={async () => {
        await api.deleteAssignment(toDelete.assignmentId)
        setWorks((list) => list.filter((work) => work.assignmentId !== toDelete.assignmentId))
      }} />}
  </>
}
