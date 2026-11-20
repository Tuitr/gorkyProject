import type { SourceFile, SourceTask } from '../domain'
import { uid } from '../domain'
import { latexToText } from '../../server/latexToText'
import { recognize } from './ocr'

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
    const { value: html } = await mammoth.convertToHtml({ arrayBuffer: buf })
    // оригинал храним: если задания не правили, в кабинет уходит именно он (с формулами и картинками)
    const dataUrl = (await readAs(file, 'dataUrl')) as string
    return { source: { name, kind: 'docx', html, dataUrl }, text: await docxText(html) }
  }
  const dataUrl = (await readAs(file, 'dataUrl')) as string
  if (/\.pdf$/i.test(name) || file.type === 'application/pdf') {
    const { readPdfText } = await import('./pdf')
    return { source: { name, kind: 'pdf', dataUrl }, text: await readPdfText(file) }
  }
  if (file.type.startsWith('image/')) {
    const ocr = await recognize(dataUrl)
    return { source: { name, kind: 'image', dataUrl }, text: ocr.lines.map((l) => latexToText(l.text)).join('\n') }
  }
  throw new Error('Нужен файл DOCX или PDF (можно и фото листа).')
}

const BLOCK = /^(P|LI|H[1-6]|TR|DIV|TABLE|UL|OL|BR)$/

/**
 * Текст DOCX по порядку документа. Картинки (часто условия вставлены скриншотами)
 * распознаются OCR, и их текст встаёт на место картинки — иначе у номера пустое условие.
 */
async function docxText(html: string): Promise<string> {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const parts: (string | HTMLImageElement)[] = []
  const walk = (n: Node) => {
    if (n.nodeType === Node.TEXT_NODE) parts.push(n.textContent ?? '')
    else if (n instanceof HTMLImageElement) parts.push(n)
    else if (n instanceof HTMLElement) {
      const block = BLOCK.test(n.tagName)
      if (block) parts.push('\n')
      if (n.tagName === 'TD' || n.tagName === 'TH') parts.push(' ')
      n.childNodes.forEach(walk)
      if (block) parts.push('\n')
    }
  }
  walk(doc.body)

  const images = parts.filter((p): p is HTMLImageElement => typeof p !== 'string' && p.src.startsWith('data:image'))
  const texts = new Map<HTMLImageElement, string>()
  // по одной: у OCR лимит 1 запрос в секунду (очередь — в api/ocr.ts)
  for (const img of images) {
    const ocr = await recognize(img.src)
    texts.set(img, ocrToText(ocr.lines.map((l) => l.text)))
  }
  return parts
    .map((p) => (typeof p === 'string' ? p : `\n${texts.get(p) ?? ''}\n`))
    .join('')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*\n+/g, '\n')
    .trim()
}

/**
 * Текст картинки: строки склеиваются в абзац, «мусор» из таблиц и графов
 * (числа, отдельные буквы вершин) отбрасывается — оставляем строки, где есть слова.
 */
function ocrToText(lines: string[]): string {
  return lines
    .map((l) => latexToText(l).trim())
    .filter((l) => {
      const words = (l.match(/[а-яёa-z]{3,}/gi) ?? []).length
      // короткая подпись без точки в конце («Номер пункта») — это шапка таблицы, а не условие
      return words >= 3 || (words >= 1 && /[.,:;?!)]$/.test(l))
    })
    .join(' ')
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
