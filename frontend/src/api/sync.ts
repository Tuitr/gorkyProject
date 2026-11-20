import { CATEGORIES, uid, type Assignment, type ClassGroup, type MistakeCategory, type ReviewItem, type State, type Submission, type TaskResult, type Technique } from '../domain'
import { CabinetError, hasResult, listClasses, listStudents, readCheck, reasonText, type RemoteCheckResult, type RemoteTaskResult } from './cabinet'
import { sameTask } from './tasks'

/** Классы и ученики из кабинета → состояние (id классов и учеников — серверные). */
export async function pullClasses(): Promise<Omit<ClassGroup, 'ownerId'>[]> {
  const classes = await listClasses()
  return Promise.all(
    classes.map(async (c) => ({
      id: c.classId,
      name: c.name,
      students: (await listStudents(c.classId)).map((st) => ({ id: st.studentId, name: st.name })),
    })),
  )
}

export const withRemoteClasses = (s: State, remote: Omit<ClassGroup, 'ownerId'>[]): State => ({
  ...s,
  classes: remote.map((c) => ({ ...c, ownerId: s.teacher.id })),
})

/* ---------- результат проверки из кабинета → наш формат ---------- */


const TECH: Record<RemoteTaskResult['method'], Technique> = {
  met: 'shown',
  unmet: 'not_shown',
  unconfirmed: 'not_shown',
  optional: 'not_required',
}

/** Готовый результат кабинета (taskResults + overallReview) → наши результаты по номерам и спорные места. */
export function fromRemoteCheck(
  a: Assignment,
  submissionId: string,
  check: RemoteCheckResult,
): { results: TaskResult[]; summary: string; review: ReviewItem[] } {
  const review: ReviewItem[] = []
  const results = a.tasks.map((t): TaskResult => {
    const rt = check.taskResults.find((x) => sameTask(x.taskId, t))
    if (!rt) return { taskId: t.id, status: 'missing', answer: 'none', technique: 'not_shown', mistakes: [] }
    const mistakes = rt.issues.map((is) => {
      const cat = is.category as MistakeCategory
      return {
        id: is.issueId || uid('m'),
        title: is.comment.length > 80 ? `${is.comment.slice(0, 77)}…` : is.comment,
        category: CATEGORIES.includes(cat) ? cat : ('другое' as const),
        where: is.stepId ? `Шаг ${is.stepId}` : '',
        studentAnswer: '—',
        correctSolution: is.correction || '—',
        explanation: is.comment,
        page: 0,
        lines: [],
        box: { x: 0, y: 0, w: 0, h: 0 },
      }
    })
    const ok = rt.answer === 'correct' && rt.reasoning !== 'invalid' && !mistakes.length
    if (rt.needsReview)
      review.push({
        id: uid('r'),
        submissionId,
        kind: 'method',
        taskId: t.id,
        page: 0,
        note: `№${t.num}: проверка отметила задание для решения учителя.`,
        readings: [],
        auto: false,
        resolved: false,
      })
    return {
      taskId: t.id,
      status: rt.answer === 'unreadable' ? 'unreadable' : rt.answer === 'skipped' ? 'missing' : ok ? 'ok' : 'error',
      answer: rt.answer === 'correct' ? 'correct' : rt.answer === 'incorrect' ? 'wrong' : 'none',
      technique: TECH[rt.method],
      aiScore: ok ? t.max : 0,
      mistakes,
      remote: { taskId: rt.taskId, answer: rt.answer, reasoning: rt.reasoning, method: rt.method },
    }
  })
  return { results, summary: check.overallReview, review }
}

/* ---------- состояние проверки в кабинете → наша сдача ---------- */


/**
 * Спрашивает кабинет о проверке сдачи и возвращает правку состояния:
 * - готово и версия новая — результаты кабинета заменяют наши (замечания, вердикты, общий отзыв);
 * - не готово — состояние и причина (в сборе работ); «нужен учитель» — пункт в «Спорных местах»;
 * - сервис проверки недоступен — так и отмечаем, остаётся наша проверка.
 */
export async function refreshCheck(st: State, submissionId: string): Promise<((s: State) => State) | null> {
  const sub = st.submissions.find((x) => x.id === submissionId)
  const a = st.assignments.find((x) => x.id === sub?.assignmentId)
  const checkId = sub?.remote?.checkId
  if (!sub || !a || !checkId) return null
  type Remote = NonNullable<Submission['remote']>
  const setRemote = (s: State, fn: (r: Remote) => Remote): State => ({
    ...s,
    submissions: s.submissions.map((x) => (x.id === submissionId && x.remote ? { ...x, remote: fn(x.remote) } : x)),
  })
  try {
    const c = await readCheck(checkId)
    if (!hasResult(c)) {
      const note = reasonText(c.reason)
      return (s) => {
        let next = setRemote(s, (r) => ({ ...r, checkState: c.state, checkReason: note }))
        const has = next.review.some((r) => r.submissionId === submissionId && !r.resolved && r.note === note)
        if (c.state === 'needs_review' && !has)
          next = {
            ...next,
            review: [
              ...next.review,
              {
                id: uid('r'),
                submissionId,
                kind: 'method',
                page: 0,
                note,
                readings: [],
                auto: false,
                resolved: false,
              },
            ],
          }
        return next
      }
    }
    // та же версия и результаты кабинета на месте — обновляем только отметку публикации
    if (c.resultRev === sub.remote?.resultRev && sub.results.some((r) => r.remote))
      return (s) => setRemote(s, (r) => ({ ...r, checkState: c.state, checkReason: undefined, publishedRev: c.publishedResultRev ?? undefined }))
    const out = fromRemoteCheck(a, submissionId, c)
    return (s) => ({
      ...s,
      submissions: s.submissions.map((x) =>
        x.id !== submissionId
          ? x
          : {
              ...x,
              results: out.results,
              summary: out.summary,
              status: 'processed',
              approved: false,
              remote: x.remote && {
                ...x.remote,
                checkState: c.state,
                checkReason: undefined,
                resultRev: c.resultRev,
                publishedRev: c.publishedResultRev ?? undefined,
              },
            },
      ),
      review: [...s.review.filter((r) => r.submissionId !== submissionId || r.resolved), ...out.review],
    })
  } catch (e) {
    if (e instanceof CabinetError && (e.status >= 500 || e.code === 'unavailable'))
      return (s) => setRemote(s, (r) => ({ ...r, checkState: 'unavailable', checkReason: undefined }))
    return null
  }
}
