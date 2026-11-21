import type { Assignment } from './api'

export const titleOf = (a: Assignment): string | undefined => a.title ?? undefined

export const subjectDate = (a: Assignment) => `${a.subject === 'physics' ? 'Физика' : 'Математика'} · ${a.assignmentDate}`

/** Название папки: «Тепловые явления» или, если названия нет, «Физика · 2026-11-21». */
export const folderName = (a: Assignment) => titleOf(a) ?? subjectDate(a)
