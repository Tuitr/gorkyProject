/**
 * LaTeX (как его отдаёт Yandex OCR math-markdown или пишет модель) → обычный текст «как в тетради».
 * \vec{a} → a⃗, \frac{1}{2} → 1/2, x^{2} → x², x_{1} → x₁, \sqrt{24} → √24, \cdot → · …
 * OCR пишет LaTeX с пробелами («x _ {1}», «\frac {a} {b}») — сначала их убираем.
 */

const SUP: Record<string, string> = { '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '-': '⁻', '+': '⁺', n: 'ⁿ' }
const SUB: Record<string, string> = { '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉' }
const SYMBOLS: Record<string, string> = {
  cdot: '·', times: '×', div: ':', pm: '±', mp: '∓', neq: '≠', ne: '≠', le: '≤', leq: '≤', ge: '≥', geq: '≥',
  approx: '≈', infty: '∞', Rightarrow: '⇒', rightarrow: '→', to: '→', Leftrightarrow: '⇔', degree: '°', circ: '°',
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', Delta: 'Δ', pi: 'π', varphi: 'φ', phi: 'φ', lambda: 'λ', angle: '∠',
  triangle: '△', perp: '⊥', parallel: '∥', in: '∈', cup: '∪', cap: '∩', ldots: '…', dots: '…', cdots: '…',
  quad: ' ', qquad: ' ', lt: '<', gt: '>', sim: '~', varnothing: '∅', emptyset: '∅',
}
const ROOTS: Record<string, string> = { '': '√', '2': '√', '3': '∛', '4': '∜' }

const mapChars = (s: string, table: Record<string, string>) =>
  [...s].every((ch) => table[ch]) ? [...s].map((ch) => table[ch]).join('') : null

/** «24», «x₁», «2a» — можно не брать в скобки; «-2 + √6» — нужно. */
const simple = (s: string) => /^[^\s+\-−·×/=:<>]+$/.test(s)
const wrap = (s: string) => (simple(s) ? s : `(${s.trim()})`)

export function latexToText(input: string): string {
  let s = input
    .replace(/\$\$?/g, '')
    .replace(/\\\\/g, '; ') // перенос строки в системах/массивах
    .replace(/\\(?:begin|end)\s*\{[^{}]*\}/g, ' ')
    .replace(/&/g, ' ')
    // склеиваем пробелы, которые OCR ставит внутри LaTeX
    .replace(/(\\[a-zA-Z]+)\s+(?=[{[])/g, '$1')
    .replace(/\}\s+\{/g, '}{')
    .replace(/\]\s+\{/g, ']{')
    .replace(/\s*([_^])\s*/g, '$1')
    .replace(/\\(?:left|right|big|Big|bigl|bigr|displaystyle)\b\s*/g, '')

  // изнутри наружу: каждый проход раскрывает самые внутренние {…}
  for (let i = 0; i < 10; i++) {
    const prev = s
    s = s
      .replace(/\\(?:vec|overrightarrow)\{([^{}]*)\}/g, (_, a: string) => `${a.trim()}⃗`)
      .replace(/\\(?:overline|bar)\{([^{}]*)\}/g, (_, a: string) => a.trim())
      .replace(/\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}/g, (_, a: string, b: string) => `${wrap(a)}/${wrap(b)}`)
      .replace(/\\sqrt(?:\[(\d*)\])?\{([^{}]*)\}/g, (_, n = '', a: string) => `${ROOTS[n] ?? `${n}√`}${wrap(a)}`)
      .replace(/\\(?:text|textbf|mathrm|mathbf|mathit|mathcal|mathfrak|mathbb|operatorname|boldsymbol)\{([^{}]*)\}/g, '$1')
      .replace(/\^\{([^{}]*)\}/g, (_, a: string) => mapChars(a.trim(), SUP) ?? `^${wrap(a)}`)
      .replace(/_\{([^{}]*)\}/g, (_, a: string) => mapChars(a.trim(), SUB) ?? `_${a.trim()}`)
    if (s === prev) break
  }

  s = s
    .replace(/\\(?:vec|overrightarrow)\s*([a-zA-Z])/g, '$1⃗')
    .replace(/\\[dt]?frac\s*(\w)\s*(\w)/g, '$1/$2')
    .replace(/\\sqrt\s*(\w+)/g, '√$1')
    .replace(/\^([\w+-])/g, (m, a: string) => SUP[a] ?? m)
    .replace(/_(\w)/g, (m, a: string) => SUB[a] ?? m)
    .replace(/\\([{}])/g, '$1')
    .replace(/\\([a-zA-Z]+)/g, (m, name: string) => SYMBOLS[name] ?? m.slice(1))
    .replace(/\\[,;:! ]/g, ' ')
    // типографика: «( - 2» → «(-2», «= - 1» → «= -1», «x _ 1» уже склеено выше
    .replace(/([([{])\s+/g, '$1')
    .replace(/\s+([)\]};,.])/g, '$1')
    .replace(/(^|[([{=≠<>≤≥,;·×:±]\s*)-\s+(?=[\w√(])/g, '$1-')
    .replace(/[ \t]{2,}/g, ' ')
  return s.trim()
}
