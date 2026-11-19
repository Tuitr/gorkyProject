import type { SourceFile, SourceTask } from '../domain'
import { uid } from '../domain'
import { latexToText } from '../../server/latexToText'
import { recognize, recognizeRaw } from './ocr'

/** Разбор исходника работы: DOCX — в браузере (mammoth), PDF и фото — через Yandex OCR. */

const readAs = (file: File, how: 'dataUrl' | 'buffer') =>
  new Promise<string | ArrayBuffer>((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string | ArrayBuffer)
    r.onerror = () => reject(r.error)
    if (how === 'dataUrl') r.readAsDataURL(file)
    else r.readAsArrayBuffer(file)
  })

export async function readSource(file: File): Promise<{ source: SourceFile; text: string }> {
  const name = file.name
  if (/\.docx$/i.test(name)) {
    const mammoth = await import('mammoth')
    const buf = (await readAs(file, 'buffer')) as ArrayBuffer
    const [{ value: html }, { value: text }] = await Promise.all([
      mammoth.convertToHtml({ arrayBuffer: buf }),
      mammoth.extractRawText({ arrayBuffer: buf }),
    ])
    return { source: { name, kind: 'docx', html }, text }
  }
  const dataUrl = (await readAs(file, 'dataUrl')) as string
  if (/\.pdf$/i.test(name) || file.type === 'application/pdf') {
    const ocr = await recognizeRaw(dataUrl.split(',')[1], 'PDF')
    return { source: { name, kind: 'pdf', dataUrl }, text: ocr.lines.map((l) => latexToText(l.text)).join('\n') }
  }
  if (file.type.startsWith('image/')) {
    const ocr = await recognize(dataUrl)
    return { source: { name, kind: 'image', dataUrl }, text: ocr.lines.map((l) => latexToText(l.text)).join('\n') }
  }
  throw new Error('Нужен файл DOCX или PDF (можно и фото листа).')
}

const NUM_RE = /^\s*(?:№\s*|N\s*|задание\s+|задача\s+)?(\d{1,2}[а-я]?)\s*[.)]\s*(.*)$/i
const NUM_SIGN_RE = /^\s*№\s*(\d{1,2}[а-я]?)\s*[.)]?\s*(.*)$/i

/** Делит текст на номера: строка «1.», «2)», «№3» начинает новый номер, остальные приклеиваются. */
export function splitTasks(text: string): SourceTask[] {
  const tasks: SourceTask[] = []
  for (const line of text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)) {
    const m = line.match(NUM_SIGN_RE) ?? line.match(NUM_RE)
    if (m) tasks.push({ id: uid('t'), num: m[1], text: m[2].trim(), manual: false, max: 1 })
    else if (tasks.length) tasks[tasks.length - 1].text += (tasks[tasks.length - 1].text ? '\n' : '') + line
    // строки до первого номера — шапка (название, вариант), их пропускаем
  }
  return tasks
}

/* ---------- «Как будет проверяться»: правила из заметок ---------- */

export type Rule = { text: string; kind: string }

const PATTERNS: [RegExp, string][] = [
  [/(снимать|снижать|минус|−|-)\s*\d*\s*балл|балл\w*\s+(снимать|не снимать|снижать)/i, 'Баллы'],
  [/не\s+засчитыва|засчитыва|считать\s+(верн|ошибк)/i, 'Зачёт ответа'],
  [/(можно|допускается|разрешается)\b/i, 'Допустимая запись'],
  [/(округл|точност|десятичн|обыкновенн)/i, 'Форма ответа'],
  [/(одз|область\s+допустим|проверк\w*\s+корн)/i, 'Обязательный шаг'],
  [/(без\s+пояснени|обоснова|пояснени)/i, 'Обоснование'],
  [/(№\s*\d+|номер\w*\s+\d+).*(ручн|сам\w*\s+провер)/i, 'Ручная проверка'],
]

/** Распознаёт правила в заметках; что не подошло ни под один шаблон — «Не учтено автоматически». */
export function parseRules(notes: string): { rules: Rule[]; unrecognized: string[] } {
  const rules: Rule[] = []
  const unrecognized: string[] = []
  for (const line of notes.split(/\r?\n|(?<=\.)\s+(?=[А-ЯA-Z])/).map((l) => l.trim()).filter(Boolean)) {
    const hit = PATTERNS.find(([re]) => re.test(line))
    if (hit) rules.push({ text: line, kind: hit[1] })
    else unrecognized.push(line)
  }
  return { rules, unrecognized }
}
