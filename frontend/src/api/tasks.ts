/** Номера заданий у нас и в бэкенде. */

/** «№2», «No2», «N 2», «Nº 2», «задание 2», «2)» → «2» (учителя пишут номер по-разному) */
export const normNum = (label: string) =>
  label
    .trim()
    .replace(/^(?:№|N[oо0º°]?\.?|задани[еяю]|номер|упр\.?|пример)\s*(?=[\dа-яa-z])/i, '')
    .replace(/^[^\dа-яa-z]*/i, '')
    .replace(/[).\s]+$/, '')
    .trim()
    .toLowerCase()

/** grading называет задание по напечатанному номеру: «1.» в DOCX → taskId «t1» (services/grading/grading/assignment.py). */
export const sameTask = (remoteTaskId: string, task: { id: string; num: string }) =>
  remoteTaskId === task.id || remoteTaskId === `t${normNum(task.num)}` || normNum(remoteTaskId) === normNum(task.num)
