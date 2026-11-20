/**
 * Солвер эталонов: точный расчёт ответов на sympy (server/solver/solve.py).
 * POST /api/solve { tasks: [{ num, text }] } → { answers: [{ num, kind, answer }] }
 * Используется и при проверке: задания без ответа получают посчитанный эталон до вызова GigaChat.
 *
 * Python ищется сам: CHECKER_PYTHON из .env.local, затем python3, python, py -3.12, py -3 —
 * первый, в котором импортируется sympy. Нет такого Python — солвер просто не даёт эталонов, проверка идёт как раньше.
 */
import { spawn, spawnSync } from 'node:child_process'
import type { IncomingMessage, ServerResponse } from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'

export type SolverTask = { num: string; text: string }
export type SolverAnswer = { num: string; kind: string; answer: string }

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'solver', 'solve.py')
const TIMEOUT_MS = 20_000

let python: string[] | null | undefined // undefined — ещё не искали, null — не нашли

function findPython(env: Record<string, string>): string[] | null {
  if (python !== undefined) return python
  const candidates: string[][] = []
  if (env.CHECKER_PYTHON) candidates.push(env.CHECKER_PYTHON.split(' ').filter(Boolean))
  candidates.push(['python3'], ['python'], ['py', '-3.12'], ['py', '-3.13'], ['py', '-3'])
  for (const [cmd, ...args] of candidates) {
    const r = spawnSync(cmd, [...args, '-c', 'import sympy'], { timeout: 15_000, windowsHide: true })
    if (r.status === 0) {
      python = [cmd, ...args]
      console.log(`[solver] Python для эталонов: ${python.join(' ')}`)
      return python
    }
  }
  console.warn('[solver] Не найден Python с sympy — эталоны не считаются. Поставьте: pip install sympy (или CHECKER_PYTHON в .env.local).')
  python = null
  return python
}

/** Точные ответы для заданий, которые солвер умеет считать. Любой сбой — пустой список, а не ошибка. */
export function solveTasks(env: Record<string, string>, tasks: SolverTask[]): Promise<SolverAnswer[]> {
  const py = findPython(env)
  if (!py || !tasks.length) return Promise.resolve([])
  const [cmd, ...args] = py
  return new Promise((resolve) => {
    // -I: изолированный режим — не подхватывать модули из текущей папки и переменные PYTHON*
    const child = spawn(cmd, [...args, '-I', SCRIPT], { windowsHide: true })
    const out: Buffer[] = []
    const timer = setTimeout(() => child.kill(), TIMEOUT_MS)
    child.stdout.on('data', (d: Buffer) => out.push(d))
    child.on('error', () => resolve([]))
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code !== 0) return resolve([])
      try {
        const data = JSON.parse(Buffer.concat(out).toString('utf-8')) as { answers?: SolverAnswer[] }
        resolve(Array.isArray(data.answers) ? data.answers : [])
      } catch {
        resolve([])
      }
    })
    child.stdin.end(JSON.stringify({ tasks: tasks.slice(0, 60) }), 'utf-8')
  })
}

/**
 * Задание для модели («№1 (макс. 1): условие\n   Ответ: …») → то же, но у номеров без ответа
 * дописан точный эталон. Формат строк задаёт taskBrief() в src/api/process.ts.
 */
export async function withSolvedAnswers(env: Record<string, string>, brief: string) {
  const blocks = brief.split(/\n(?=№)/)
  const open: SolverTask[] = []
  for (const b of blocks) {
    const m = /^№(\S+?) \(макс\. [^)]*\): ([\s\S]*)$/.exec(b)
    if (m && !/\n\s*Ответ:/.test(m[2])) open.push({ num: m[1], text: m[2] })
  }
  if (!open.length) return brief
  const answers = new Map((await solveTasks(env, open)).map((a) => [a.num, a.answer]))
  if (!answers.size) return brief
  return blocks
    .map((b) => {
      const num = /^№(\S+?) \(/.exec(b)?.[1]
      const ans = num && answers.get(num)
      return ans ? `${b}\n   Ответ (посчитан точно): ${ans}` : b
    })
    .join('\n')
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', (c) => {
      body += c
      if (body.length > 200_000) req.destroy()
    })
    req.on('end', () => resolve(body))
    req.on('error', reject)
  })
}

export function solverPlugin(env: Record<string, string>): Plugin {
  const handler = async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    if (req.url !== '/api/solve' || req.method !== 'POST') return next()
    res.setHeader('Content-Type', 'application/json; charset=utf-8')
    try {
      const body = JSON.parse(await readBody(req)) as { tasks?: unknown }
      const tasks = (Array.isArray(body.tasks) ? body.tasks : [])
        .filter((t): t is Record<string, unknown> => !!t && typeof t === 'object')
        .map((t) => ({ num: String(t.num ?? ''), text: String(t.text ?? '').slice(0, 2000) }))
      res.end(JSON.stringify({ answers: await solveTasks(env, tasks) }))
    } catch {
      res.statusCode = 400
      res.end(JSON.stringify({ message: 'Неверный запрос' }))
    }
  }
  return {
    name: 'solver',
    configureServer: (server) => void server.middlewares.use(handler),
    configurePreviewServer: (server) => void server.middlewares.use(handler),
  }
}
