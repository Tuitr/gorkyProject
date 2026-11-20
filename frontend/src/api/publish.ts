import { sourceFiles, type Assignment, type RemoteAssignment } from '../domain'
import { makeDocx } from '../lib/zip'
import { uploadAssignment, type Requirement } from './cabinet'

/**
 * Публикация работы в кабинет (бэкенд из main). Кабинет хранит работу как DOCX + требования учителя.
 * Работа у нас может прийти из DOCX, PDF или фото, поэтому DOCX собираем из распознанных заданий —
 * так номера и условия в кабинете совпадают с тем, что учитель сверил на «Проверке распознавания».
 */

export function assignmentDocx(a: Assignment): Blob {
  return makeDocx([
    { text: a.title, bold: true },
    ...a.tasks.flatMap((t) =>
      t.text
        .split(/\r?\n/)
        .filter((l, i) => i === 0 || l.trim())
        .map((line, i) => ({ text: i === 0 ? `${t.num}. ${line}`.trim() : line })),
    ),
  ])
}

/** Указания учителя → требования кабинета: строка = требование; «№4 …» — к заданию 4, иначе ко всей работе. */
export function requirementsOf(a: Assignment): Requirement[] {
  return a.notes
    .split(/\r?\n/)
    .map((l) => l.replace(/^\s*[-•*]\s*/, '').trim())
    .filter(Boolean)
    .map((text) => ({ text, taskNumber: text.match(/№\s*(\d+[а-яa-z]?)/i)?.[1] ?? null }))
}

/**
 * Что отправить как DOCX работы: исходный файл учителя, если работа — один DOCX и задания после
 * распознавания не правили (сохраняются формулы и картинки); иначе DOCX из сверенных заданий.
 */
export async function assignmentFile(a: Assignment): Promise<{ blob: Blob; name: string; original: boolean }> {
  const files = sourceFiles(a)
  const only = files.length === 1 ? files[0] : undefined
  if (only?.kind === 'docx' && only.dataUrl && !a.sourceEdited) {
    const blob = await (await fetch(only.dataUrl)).blob()
    return { blob, name: only.name, original: true }
  }
  return { blob: assignmentDocx(a), name: 'assignment.docx', original: false }
}

const dateOf = (a: Assignment) => a.date ?? new Date(a.createdAt).toLocaleDateString('sv-SE')

/** Отпечаток того, что уходит в кабинет: изменилось — нужна новая ревизия. */
export function contentHash(a: Assignment) {
  const s = JSON.stringify([a.title, a.tasks.map((t) => [t.num, t.text]), a.notes, dateOf(a), !!a.sourceEdited])
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
  return (h >>> 0).toString(36)
}

/** Отправляет работу каждому её классу (новая ревизия — только если содержимое изменилось). */
export async function publishAssignment(a: Assignment): Promise<Record<string, RemoteAssignment>> {
  if (!a.tasks.length) throw new Error('В работе нет заданий — нечего сохранять на сервере.')
  const hash = contentHash(a)
  const remote: Record<string, RemoteAssignment> = { ...(a.remote ?? {}) }
  let file: Awaited<ReturnType<typeof assignmentFile>> | null = null
  for (const classId of a.classIds) {
    const prev = remote[classId]
    if (prev?.hash === hash) continue
    file ??= await assignmentFile(a)
    const r = await uploadAssignment(classId, file.blob, requirementsOf(a), prev?.assignmentId, dateOf(a), file.name)
    remote[classId] = { assignmentId: r.assignmentId, rev: r.assignmentRev, hash }
  }
  return remote
}
