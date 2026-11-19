import type {
  Assignment,
  ClassGroup,
  MistakeCategory,
  ReviewItem,
  State,
  Submission,
  TaskResult,
} from '../domain'
import { SAMPLE_WORKS } from './samples'

/**
 * Демо-данные: три параллельных класса и проверенная контрольная «Координаты вектора»
 * с настоящими фото из кейса (public/samples). Сбрасываются кнопкой «Демо-данные заново».
 */

const TEACHER = { id: 'teacher-1', name: 'Учитель математики' }

const names = (list: string) =>
  list
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((name, i) => ({ id: `${name.split(' ')[0]}-${i}`.toLowerCase(), name }))

const CLASSES: ClassGroup[] = [
  {
    id: 'c8a',
    name: '8А',
    ownerId: TEACHER.id,
    students: names(`Алексеев Дмитрий
Белова Анна
Васильев Максим
Григорьева Софья
Дмитриев Артём
Егорова Полина
Жуков Иван
Зайцева Виктория
Иванова Мария
Козлов Никита`),
  },
  {
    id: 'c8b',
    name: '8Б',
    ownerId: TEACHER.id,
    students: names(`Лебедев Кирилл
Морозова Дарья
Новиков Егор
Орлова Алиса
Павлов Михаил
Романова Ева
Соколов Лев`),
  },
  { id: 'c8v', name: '8В', ownerId: TEACHER.id, students: names(`Тихонов Матвей
Усова Ксения
Фёдоров Тимофей
Чернова Вера`) },
]

const T = (n: number) => `vt${n}`

const ASSIGNMENT: Assignment = {
  id: 'a-vectors',
  title: 'Координаты вектора',
  type: 'control',
  createdAt: Date.parse('2026-10-14T09:00:00'),
  file: { name: 'Задание — координаты вектора.jpg', kind: 'image', dataUrl: '/samples/vectors-task.jpg' },
  classIds: ['c8a', 'c8b'],
  notes: `За арифметическую ошибку при верном ходе решения в №6 снимать 1 балл, а не все.
Ответ без разложения по i и j в №1 не засчитывать.
Координаты можно записывать в фигурных или круглых скобках.
Обращать внимание на аккуратность записи.`,
  classNotes: { c8b: 'В 8Б тему проходили на неделю позже — №6 оценивать мягче.' },
  tasks: [
    { id: T(1), num: '1', text: 'Разложите вектор a{−2; 5} по координатным векторам i и j.', manual: false, max: 1 },
    { id: T(2), num: '2', text: 'Найдите числа x и y, если xi + yj = 7i − 4j.', manual: false, max: 1 },
    { id: T(3), num: '3', text: 'Найдите координаты вектора a + b, если a{1; 3}, b{4; 5}.', manual: false, max: 1 },
    { id: T(4), num: '4', text: 'Найдите координаты вектора a − b, если a{3; 4}, b{1; −3}.', manual: false, max: 1 },
    { id: T(5), num: '5', text: 'Найдите координаты вектора −2a, если a{−4; 3}.', manual: false, max: 1 },
    { id: T(6), num: '6', text: 'Найдите координаты вектора v = 2a − b + c, если a{−3; 1}, b{1; 5}, c{0; 4}.', manual: false, max: 2 },
  ],
  variantCount: 2,
  variants: [
    {
      id: 'vA',
      name: 'А',
      tasks: [
        { taskId: T(1), text: 'Разложите вектор a{−2; 5} по координатным векторам i и j.', answer: 'a = −2i + 5j' },
        { taskId: T(2), text: 'Найдите числа x и y, если xi + yj = 7i − 4j.', answer: 'x = 7, y = −4' },
        { taskId: T(3), text: 'Найдите координаты вектора a + b, если a{1; 3}, b{4; 5}.', answer: '{5; 8}' },
        { taskId: T(4), text: 'Найдите координаты вектора a − b, если a{3; 4}, b{1; −3}.', answer: '{2; 7}' },
        { taskId: T(5), text: 'Найдите координаты вектора −2a, если a{−4; 3}.', answer: '{8; −6}' },
        { taskId: T(6), text: 'Найдите координаты вектора v = 2a − b + c, если a{−3; 1}, b{1; 5}, c{0; 4}.', answer: '{−7; 1}' },
      ],
    },
    {
      id: 'vB',
      name: 'Б',
      tasks: [
        { taskId: T(1), text: 'Разложите вектор c{0; 3} по координатным векторам i и j.', answer: 'c = 3j' },
        { taskId: T(2), text: 'Найдите числа x и y, если xi + yj = −2i + 9j.', answer: 'x = −2, y = 9' },
        { taskId: T(3), text: 'Найдите координаты вектора a + b, если a{−2; 7}, b{5; −3}.', answer: '{3; 4}' },
        { taskId: T(4), text: 'Найдите координаты вектора a − b, если a{4; 2}, b{−2; 2}.', answer: '{6; 0}' },
        { taskId: T(5), text: 'Найдите координаты вектора 3a, если a{2; −6}.', answer: '{6; −18}' },
        { taskId: T(6), text: 'Найдите координаты вектора v = a + 3b − c, если a{1; 0}, b{−2; 1}, c{3; 4}.', answer: '{−8; −1}' },
      ],
    },
  ],
  features: {
    [T(1)]: [
      { name: 'Тип задания', values: ['разложение по i, j', 'разложение по i, j'], soft: false },
      { name: 'Нулевая координата', values: ['нет', 'есть (x = 0)'], soft: true },
    ],
    [T(2)]: [
      { name: 'Тип задания', values: ['равенство векторов', 'равенство векторов'], soft: false },
      { name: 'Знаки ответа', values: ['+ / −', '− / +'], soft: false },
    ],
    [T(3)]: [
      { name: 'Действие', values: ['сумма', 'сумма'], soft: false },
      { name: 'Отрицательные числа в условии', values: ['нет', 'есть'], soft: true },
    ],
    [T(4)]: [
      { name: 'Действие', values: ['разность', 'разность'], soft: false },
      { name: 'Вычитание отрицательного', values: ['есть', 'есть'], soft: false },
    ],
    [T(5)]: [
      { name: 'Множитель', values: ['отрицательный (−2)', 'положительный (3)'], soft: true },
      { name: 'Шагов', values: ['1', '1'], soft: false },
    ],
    [T(6)]: [
      { name: 'Линейная комбинация из 3 векторов', values: ['да', 'да'], soft: false },
      { name: 'Множитель при векторе', values: ['2 при a', '3 при b'], soft: false },
      { name: 'Целые координаты ответа', values: ['да', 'да'], soft: false },
    ],
  },
  status: 'approved',
}

const CATEGORY: Record<string, MistakeCategory> = {
  'sample-9/m1': 'вычислительная',
  'sample-9/m2': 'правило',
  'sample-9/m3': 'запись',
  'sample-4/m1': 'правило',
  'sample-4/m2': 'правило',
  'sample-4/m3': 'неполное решение',
  'sample-12/m1': 'запись',
  'sample-12/m2': 'знак',
  'sample-12/m3': 'неполное решение',
}

/** Перекладывает ручную разметку тестовых работ в формат результатов по номерам. */
function sampleSubmission(sampleId: string, studentId: string, variantId: string): Submission {
  const w = SAMPLE_WORKS.find((x) => x.id === sampleId)!
  const r = w.result!
  const results: TaskResult[] = ASSIGNMENT.tasks.map((task, i) => {
    const t = r.tasks[i]
    const ms = r.mistakes.filter((m) => m.taskId === t.id)
    const missing = ms.some((m) => m.title === 'Задание не выполнено')
    return {
      taskId: task.id,
      status: missing ? 'insufficient' : t.ok ? 'ok' : 'error',
      answer: missing ? 'none' : t.ok ? 'correct' : 'wrong',
      technique: missing ? 'not_shown' : t.ok ? 'shown' : ms.length > 1 ? 'not_shown' : 'shown',
      aiScore: t.ok ? task.max : task.max > 1 && !missing && ms.length === 1 ? task.max - 1 : 0,
      mistakes: ms.map((m) => ({
        id: `${sampleId}-${m.id}`,
        title: m.title,
        category: CATEGORY[`${sampleId}/${m.id}`] ?? 'другое',
        where: m.where ?? '',
        studentAnswer: m.studentAnswer,
        correctSolution: m.correctSolution,
        explanation: m.explanation,
        rule:
          task.num === '6' && !missing
            ? 'За арифметическую ошибку при верном ходе решения в №6 снимать 1 балл'
            : task.num === '1'
              ? 'Ответ без разложения по i и j в №1 не засчитывать'
              : undefined,
        page: 0,
        lines: [],
        box: m.box,
      })),
    }
  })
  return {
    id: `s-${sampleId}`,
    assignmentId: ASSIGNMENT.id,
    classId: 'c8a',
    studentId,
    variantId,
    pages: [{ id: `p-${sampleId}`, image: w.image, hash: sampleId }],
    status: 'processed',
    results,
    approved: false,
    summary: r.summary,
  }
}

export function seedState(): State {
  const a = CLASSES[0].students
  const submissions: Submission[] = [
    sampleSubmission('sample-9', a[8].id, 'vB'),
    sampleSubmission('sample-4', a[0].id, 'vA'),
    sampleSubmission('sample-12', a[3].id, 'vA'),
    {
      id: 's-err',
      assignmentId: ASSIGNMENT.id,
      classId: 'c8a',
      studentId: a[5].id,
      variantId: 'vB',
      pages: [{ id: 'p-err', image: '/samples/vectors-9.jpg', hash: 'err-demo' }],
      status: 'error',
      error: 'На фото не найден текст — переснимите страницу ровнее.',
      results: [],
      approved: false,
    },
  ]
  // №5 у работы 12: OCR прочитал «−8» неуверенно — прочтение выбрано автоматически
  const s12 = submissions[2].results[4]
  s12.autoReading = '−2·(−4) прочитано как «−8» (могло быть «8»)'
  const review: ReviewItem[] = [
    {
      id: 'r1',
      submissionId: 's-sample-12',
      kind: 'sign',
      taskId: T(5),
      page: 0,
      box: { x: 0.0, y: 0.8, w: 0.38, h: 0.1 },
      note: 'Знак перед 8 во второй строке читается неуверенно.',
      readings: ['−8', '8'],
      chosen: '−8',
      auto: true,
      resolved: false,
    },
    {
      id: 'r2',
      submissionId: 's-sample-9',
      kind: 'method',
      taskId: T(6),
      page: 0,
      box: { x: 0.15, y: 0.77, w: 0.68, h: 0.22 },
      note: 'Ученик решал через общую формулу координат, а не по шагам. Проверьте, засчитывать ли такой способ.',
      readings: [],
      auto: false,
      resolved: false,
    },
    {
      id: 'r3',
      submissionId: 's-err',
      kind: 'reshoot',
      page: 0,
      note: 'Страница повёрнута и не распознана — переснимите или добавьте страницу.',
      readings: [],
      auto: false,
      resolved: false,
    },
  ]
  return {
    version: 1,
    teacher: TEACHER,
    classes: CLASSES,
    assignments: [ASSIGNMENT],
    submissions,
    review,
    practices: [],
  }
}
