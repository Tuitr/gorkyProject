/**
 * Проверка работ через GigaChat — middleware dev/preview-сервера Vite.
 * POST /api/check { lines: string[], task?: string } → JSON с разбором работы.
 * Ключ берётся из .env.local (CHECKER_GIGACHAT_AUTH_KEY, CHECKER_GIGACHAT_SCOPE) и в браузер не попадает.
 */
import crypto from 'node:crypto'
import fs from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import https from 'node:https'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import { latexToText } from './latexToText.ts'

const OAUTH_URL = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
const CHAT_URL = 'https://gigachat.devices.sberbank.ru/api/v1/chat/completions'
const MODEL = 'GigaChat-2-Max'

// Сертификаты GigaChat выпущены НУЦ Минцифры — доверяем только этому корню, проверку TLS не отключаем.
const here = path.dirname(fileURLToPath(import.meta.url))
// keepAlive — повторные запросы идут без нового TLS-рукопожатия.
const agent = new https.Agent({ ca: fs.readFileSync(path.join(here, 'russian_trusted_root_ca.cer')), keepAlive: true })

function request(url: string, opts: https.RequestOptions, body?: string): Promise<{ status: number; text: string }> {
  return new Promise((resolve, reject) => {
    const req = https.request(url, { ...opts, agent, timeout: 120_000 }, (res) => {
      let text = ''
      res.setEncoding('utf8')
      res.on('data', (c: string) => (text += c))
      res.on('end', () => resolve({ status: res.statusCode ?? 0, text }))
    })
    req.on('timeout', () => req.destroy(new Error('GigaChat не ответил вовремя')))
    req.on('error', reject)
    if (body) req.write(body)
    req.end()
  })
}

// Промпт короткий, а ответ — компактный JSON: меньше токенов на входе и выходе = быстрее ответ.
const SYSTEM_PROMPT = `Ты помогаешь учителю проверять письменные работы школьников.
Вход: текст фото работы после OCR, по строке на строку, формат «номер|текст». Формулы уже переведены в обычный текст (√, ², ₁, /, ·).
OCR ошибается в символах (векторы, скобки, похожие буквы и цифры) — восстанавливай смысл по контексту, дефект распознавания не считай ошибкой ученика.
Если дан текст задания — сверяйся с ним. Сам реши каждое задание и сравни с решением ученика.
Ошибка — неверный ответ, неверный шаг решения или пропущенное задание.

Правила:
- Начало задания — строка с номером: «№2», «No2», «N2», «2)», «2.»; OCR часто искажает «№» в «√», «π», «N» и т. п.
- «Номер 12», «Вариант 1» в шапке листа — номер работы/варианта, а не задание.
- tasks[].lines — [первая, последняя] строка задания. mistakes[].lines — только 1–3 строки, где видна ошибка.
- Нечитаемые строки не считай ошибкой. Не выдумывай ошибок: если ответ верный — ok: true.
- В "where" не упоминай номера строк — учитель их не видит. Пиши по содержанию: «№5, вторая координата ответа».
- Если даны заметки учителя — применяй их при оценке и в "rule" коротко называй применённое правило.
- score — балл за номер (максимум указан в задании как «макс. N»); не ставь балл, если номер нечитаем.
- doubts — места, где от прочтения символа (знак, цифра) зависит оценка: варианты прочтения и какое ты выбрал.
- Все тексты — обычным русским языком, как учитель в тетради. Без LaTeX и без «$», «\\»: дроби «1/2», корень «√6», степень «x²», индекс «x₁», умножение «·», векторы {3; 4}.

Ответ — ТОЛЬКО однострочный JSON без markdown:
{"subject":"предмет","workTopic":"тема","studentName":"имя с листа или пусто","tasks":[{"label":"№1","ok":true,"lines":[0,4],"answer":"верно|неверно|нет","technique":"показан|не показан|не требуется","status":"проверен|нечитаемо|нет решения|недостаточно записи","score":1}],"mistakes":[{"task":"№2","lines":[7],"title":"до 6 слов","category":"вычислительная|знак|правило|логика|запись|неполное решение|другое","rule":"применённое правило из заметок или пусто","where":"где ошибка","studentAnswer":"что написал ученик","correctSolution":"верное решение, 1–3 коротких предложения","explanation":"почему это ошибка, 1–2 предложения"}],"summary":"2–3 предложения: что верно, в чём причина ошибок","weakTopics":[{"name":"тема","reason":"зачем повторить"}],"doubts":[{"task":"№5","line":12,"options":["-8","8"],"chosen":"-8","note":"знак читается неуверенно"}]}

Пример ошибки:
{"task":"№3","lines":[9],"title":"Неверно найдены корни","where":"№3, запись корней уравнения","studentAnswer":"x₁ = (-2 + √24)/2 = -1 + √2/2","correctSolution":"x₁,₂ = (-b ± √D)/2a = (-2 ± √24)/2. √24 = 2√6, поэтому x₁ = -1 + √6, x₂ = -1 - √6.","explanation":"√24 не равно √2: при сокращении на 2 корень нужно сначала упростить до 2√6."}`

let token: { value: string; expiresAt: number } | null = null

async function getToken(authKey: string, scope: string): Promise<string> {
  if (token && token.expiresAt - Date.now() > 60_000) return token.value
  const res = await request(
    OAUTH_URL,
    {
      method: 'POST',
      headers: {
        Authorization: `Basic ${authKey}`,
        RqUID: crypto.randomUUID(),
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'application/json',
      },
    },
    `scope=${encodeURIComponent(scope)}`,
  )
  if (res.status !== 200) throw new Error(`GigaChat OAuth (scope ${scope}): ${res.status} ${res.text.slice(0, 200)}`)
  const data = JSON.parse(res.text) as { access_token: string; expires_at: number }
  token = { value: data.access_token, expiresAt: data.expires_at }
  return token.value
}

/** Достаёт JSON из ответа модели (на случай ```json ... ``` или текста вокруг). */
function extractJson(text: string): unknown {
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) throw new Error('GigaChat вернул ответ без JSON')
  const raw = text.slice(start, end + 1)
  try {
    return JSON.parse(raw)
  } catch {
    try {
      return JSON.parse(fixJsonEscapes(raw))
    } catch (e) {
      console.error('[gigachat] некорректный JSON от модели:\n' + raw)
      throw new Error(`GigaChat вернул некорректный JSON: ${e instanceof Error ? e.message : ''}`)
    }
  }
}

/**
 * Модель вставляет в строки LaTeX то с одиночным «\» (\vec, \{), то с правильным «\\vec».
 * Проходим по строкам JSON посимвольно: корректные экранирования оставляем,
 * одиночный «\» перед LaTeX-командой или другим символом удваиваем,
 * сырые переносы строк внутри строк заменяем на \n.
 */
export function fixJsonEscapes(s: string): string {
  let out = ''
  let inStr = false
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (!inStr) {
      out += c
      if (c === '"') inStr = true
      continue
    }
    if (c === '"') {
      out += c
      inStr = false
    } else if (c === '\\') {
      const n = s[i + 1] ?? ''
      const after = s[i + 2] ?? ''
      if (n === '"' || n === '\\' || n === '/') {
        out += c + n // уже корректная пара — переносим целиком
        i++
      } else if (n === 'u' && /^[0-9a-fA-F]{4}$/.test(s.slice(i + 2, i + 6))) {
        out += c // \uXXXX
      } else if ('bfnrt'.includes(n) && n !== '' && !/[a-zA-Z]/.test(after)) {
        out += c // настоящие \n, \t … (а \neq, \frac, \times — LaTeX)
      } else {
        out += '\\\\' // одиночный «\» → литеральный обратный слеш
      }
    } else if (c === '\n') {
      out += '\\n'
    } else if (c === '\r') {
      out += '\\r'
    } else if (c === '\t') {
      out += '\\t'
    } else {
      out += c
    }
  }
  return out
}

type Creds = { authKey: string; scope: string }

/** Один запрос к GigaChat с ответом-JSON; строки ответа очищаются от LaTeX. */
async function chatJson(creds: Creds, system: string, user: string, temperature = 0.1, maxTokens = 3000) {
  const res = await request(
    CHAT_URL,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${await getToken(creds.authKey, creds.scope)}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
    },
    JSON.stringify({
      model: MODEL,
      temperature,
      max_tokens: maxTokens,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user },
      ],
    }),
  )
  if (res.status === 401) token = null
  if (res.status !== 200) throw new Error(`GigaChat: ${res.status} ${res.text.slice(0, 300)}`)
  const content = JSON.parse(res.text).choices?.[0]?.message?.content ?? ''
  return cleanStrings(extractJson(content))
}

/** Ответ модели иногда не разбирается даже после починки — тогда спрашиваем ещё раз. */
async function withRetry<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn()
  } catch (e) {
    if (!(e instanceof Error) || !e.message.includes('JSON')) throw e
    return fn()
  }
}

function checkWork(creds: Creds, lines: string[], task?: string, notes?: string) {
  // OCR отдаёт LaTeX — переводим в обычный текст: модель читает и цитирует его как есть, токенов меньше.
  const numbered = lines.map((l, i) => `${i}|${latexToText(l)}`).join('\n')
  const user =
    (task ? `Задание:\n${latexToText(task)}\n\n` : '') +
    (notes ? `Заметки учителя (как проверять):\n${notes}\n\n` : '') +
    `Работа:\n${numbered}`
  return withRetry(() => chatJson(creds, SYSTEM_PROMPT, user))
}

/* ---------- генерация вариантов и задач ---------- */

const PLAIN = `Все тексты — обычным русским языком, без LaTeX и без «$», «\\\\»: дроби «1/2», корень «√6», степень «x²», индекс «x₁», умножение «·», векторы a{3; 4}.
Ответ — ТОЛЬКО однострочный JSON без markdown.`

const VARIANTS_PROMPT = `Ты методист по математике. Тебе дают номера контрольной (вариант А) в формате «номер|условие».
1) Реши каждый номер варианта А и дай краткий ответ.
2) Составь ещё N равнозначных вариантов: тот же тип задачи, то же число шагов, та же трудность, но другие числа. Ответы — «удобные» (целые, если в А целые).
3) Для каждого номера дай 2–3 признака равнозначности со значениями по всем вариантам (А, Б, …). soft: true — если признак у вариантов отличается и это на границе равнозначности (например, в одном варианте есть отрицательные числа, в другом нет).
${PLAIN}
Схема: {"key":[{"num":"1","answer":"..."}],"variants":[{"name":"Б","tasks":[{"num":"1","text":"...","answer":"..."}]}],"features":[{"num":"1","rows":[{"name":"признак","values":["для А","для Б"],"soft":false}]}]}`

const REPLACE_PROMPT = `Ты методист по математике. Замени один номер варианта контрольной на равнозначный: тот же тип и трудность, другие числа, не совпадающий с перечисленными.
${PLAIN}
Схема: {"text":"условие","answer":"краткий ответ"}`

const PRACTICE_PROMPT = `Ты методист по математике. По типовым ошибкам класса составь задачи для отработки.
Для каждой темы — столько задач, сколько просят, от простой к сложной, с краткими ответами. Не упоминай учеников.
Если просят разбор у доски — в example дай обезличенный пример: условие и НЕВЕРНОЕ решение с этой ошибкой (как будто его написал «ученик»), без имён и БЕЗ правильного решения — ошибку класс ищет сам. Плюс одна задача на закрепление.
В topics поле topic повторяй дословно, как в запросе.
${PLAIN}
Схема: {"board":{"example":"...","task":{"topic":"...","text":"...","answer":"..."}},"topics":[{"topic":"...","tasks":[{"text":"...","answer":"..."}]}]}
Поле board — только если просят разбор у доски.`

const SPLIT_PROMPT = `Тебе дают строки файла с заданиями (контрольная, домашняя работа) в формате «номер_строки|текст»; длинные строки обрезаны.
Найди, где начинается и заканчивается каждое задание. Номер задания — как в файле («1», «2а»); если номеров нет — нумеруй по порядку.
from и to — номера строк (включительно) с условием задания. Шапку (название, класс, вариант, ФИО, общие инструкции) и ответы/решения в задания не включай.
Ответ — ТОЛЬКО однострочный JSON без markdown.
Схема: {"tasks":[{"num":"1","from":0,"to":2}]}`

/**
 * Делит текст на номера: модель возвращает только границы (строки from–to),
 * а условия собираются из исходных строк — дословно и с коротким ответом модели (несколько секунд).
 */
async function splitTasks(creds: Creds, text: string) {
  const lines = latexToText(text)
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(0, 400)
  const user = lines.map((l, i) => `${i}|${l.length > 160 ? `${l.slice(0, 160)}…` : l}`).join('\n')
  const raw = (await withRetry(() => chatJson(creds, SPLIT_PROMPT, user, 0.1, 1500))) as { tasks?: unknown }
  const NUM_PREFIX = /^\s*(?:№\s*|задание\s+|задача\s+)?\d{1,2}[а-я]?\s*[.)]\s*/i
  const tasks = (Array.isArray(raw.tasks) ? raw.tasks : [])
    .map((t: { num?: unknown; from?: unknown; to?: unknown }) => {
      const from = Math.max(0, Math.floor(Number(t.from)))
      const to = Math.min(lines.length - 1, Math.floor(Number(t.to)))
      if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return null
      const body = lines.slice(from, to + 1).join('\n').replace(NUM_PREFIX, '').trim()
      return { num: String(t.num ?? '').trim(), text: body }
    })
    .filter((t): t is { num: string; text: string } => !!t && !!t.num && !!t.text)
  return { tasks }
}

type GenerateBody =
  | { kind: 'split'; text: string }
  | { kind: 'variants'; tasks: { num: string; text: string }[]; count: number; notes?: string }
  | { kind: 'replace'; num: string; original: string; current: string; avoid: string[] }
  | { kind: 'practice'; topics: { topic: string; examples: string[]; count: number }[]; board: boolean }

function generate(creds: Creds, body: GenerateBody) {
  if (body.kind === 'split') {
    return splitTasks(creds, body.text)
  }
  if (body.kind === 'variants') {
    const names = 'БВГДЕ'.slice(0, Math.max(0, Math.min(5, body.count - 1)))
    const user =
      `Нужно вариантов кроме А: ${names.length} (${names.split('').join(', ') || 'нет — только ключ и признаки'}).\n` +
      (body.notes ? `Заметки учителя: ${body.notes}\n` : '') +
      `Вариант А:\n${body.tasks.map((t) => `${t.num}|${latexToText(t.text)}`).join('\n')}`
    return withRetry(() => chatJson(creds, VARIANTS_PROMPT, user, 0.5, 4000))
  }
  if (body.kind === 'replace') {
    const user = `Номер ${body.num}. Исходное условие (вариант А): ${body.original}\nТекущее условие, которое надо заменить: ${body.current}\nНе повторять: ${body.avoid.join(' | ')}`
    return withRetry(() => chatJson(creds, REPLACE_PROMPT, user, 0.8, 600))
  }
  const user =
    (body.board ? 'Нужен разбор у доски по первой теме.\n' : '') +
    body.topics
      .map((t) => `Тема: ${t.topic}; задач: ${t.count}; примеры ошибок: ${t.examples.slice(0, 3).join(' | ') || '—'}`)
      .join('\n')
  return withRetry(() => chatJson(creds, PRACTICE_PROMPT, user, 0.6, 3000))
}

/** Применяет latexToText ко всем строкам ответа модели. */
function cleanStrings(v: unknown): unknown {
  if (typeof v === 'string') return latexToText(v)
  if (Array.isArray(v)) return v.map(cleanStrings)
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cleanStrings(x)]))
  return v
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = ''
    req.setEncoding('utf8')
    req.on('data', (c: string) => {
      body += c
      if (body.length > 1_000_000) reject(new Error('Слишком большой запрос'))
    })
    req.on('end', () => resolve(body))
    req.on('error', reject)
  })
}

export function gigachatPlugin(env: Record<string, string>): Plugin {
  const handler = async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    if ((req.url !== '/api/check' && req.url !== '/api/generate') || req.method !== 'POST') return next()
    const send = (status: number, data: unknown) => {
      res.statusCode = status
      res.setHeader('Content-Type', 'application/json; charset=utf-8')
      res.end(JSON.stringify(data))
    }
    try {
      if (!env.CHECKER_GIGACHAT_AUTH_KEY) return send(500, { message: 'Нет CHECKER_GIGACHAT_AUTH_KEY в .env.local' })
      const creds = { authKey: env.CHECKER_GIGACHAT_AUTH_KEY, scope: env.CHECKER_GIGACHAT_SCOPE || 'GIGACHAT_API_PERS' }
      const body = JSON.parse(await readBody(req)) as Record<string, unknown>
      if (req.url === '/api/generate') return send(200, await generate(creds, body as GenerateBody))
      const { lines, task, notes } = body
      if (!Array.isArray(lines) || lines.length === 0) return send(400, { message: 'Нет текста работы' })
      const result = await checkWork(
        creds,
        lines.map(String).slice(0, 400),
        typeof task === 'string' ? task : undefined,
        typeof notes === 'string' ? notes : undefined,
      )
      send(200, result)
    } catch (e) {
      send(502, { message: e instanceof Error ? e.message : 'Ошибка GigaChat' })
    }
  }
  return {
    name: 'gigachat-check',
    configureServer: (server) => void server.middlewares.use(handler),
    configurePreviewServer: (server) => void server.middlewares.use(handler),
  }
}
