/** Прямоугольник на фото в долях от ширины/высоты (0..1) — не зависит от размера картинки. */
export type Box = { x: number; y: number; w: number; h: number }

/** Один номер работы и его итог. */
export type TaskCheck = {
  id: string
  label: string // «№2»
  ok: boolean
  box: Box // где номер на фото (по правому краю рисуется ✓)
}

/** Ошибка ученика — подсвечивается на фото. */
export type Mistake = {
  id: string
  taskId: string
  box: Box
  title: string
  where?: string // где ошибка — человеческим текстом
  studentAnswer: string
  correctSolution: string
  explanation: string
}

export type WeakTopic = { name: string; reason: string }

/** То, что должен вернуть бэкенд/LLM после проверки фото. */
export type CheckResult = {
  subject: string
  workTopic: string
  student: string
  tasks: TaskCheck[]
  mistakes: Mistake[]
  summary: string
  weakTopics: WeakTopic[]
}

export type Work = {
  id: string
  title: string
  image: string // URL или data:URL
  taskImage?: string // фото/скан текста задания
  source: 'sample' | 'camera' | 'upload'
  createdAt: number
  result?: CheckResult // нет — ещё не проверена
}
