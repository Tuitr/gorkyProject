import type { Work } from '../api'
import { dateLabel, subjectName, workName } from '../workspace'
import { RemoveButton } from './RemoveButton'

// Карточка сохранённой работы на главной: дата, предмет, название-ссылка, класс и крестик удаления.
export function WorkCard({ work, onRemove }: { work: Work; onRemove: (work: Work) => void }) {
  return <article className="work-card saved-card">
    <RemoveButton className="card-remove" label={'Удалить работу ' + workName(work)}
      title="Удалить работу" onClick={() => onRemove(work)} />
    <div className="card-top"><time dateTime={work.assignmentDate}>{dateLabel(work.assignmentDate)}</time><span className="subject-pill">{subjectName()}</span></div>
    <a className="card-title" href={'#work/' + work.assignmentId}>{workName(work)}</a>
    <div className="card-bottom"><span>{work.className}</span>
      <span aria-hidden="true">↗</span>
    </div>
  </article>
}
