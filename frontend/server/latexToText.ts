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
  epsilon: 'ε', varepsilon: 'ε', kappa: 'κ', varkappa: 'κ', theta: 'θ', mu: 'μ', nu: 'ν', rho: 'ρ', sigma: 'σ',
  tau: 'τ', omega: 'ω', Omega: 'Ω', xi: 'ξ', eta: 'η', psi: 'ψ', chi: 'χ', zeta: 'ζ', Sigma: 'Σ', Pi: 'Π',
  partial: '∂', sum: 'Σ', prod: '∏', int: '∫', nabla: '∇', vdots: '⋮', ddots: '⋱', prime: '′', backprime: '′',
  leftarrow: '←', Leftarrow: '⇐', mapsto: '↦', equiv: '≡', cong: '≅', propto: '∝', notin: '∉', subset: '⊂',
  forall: '∀', exists: '∃', neg: '¬', wedge: '∧', vee: '∨', top: '', bot: '⊥', colon: ':', mid: '|', vert: '|',
  // встречались в выводе OCR по самостоятельной работе
  Downarrow: '⇓', Uparrow: '⇑', downarrow: '↓', uparrow: '↑', leftrightarrow: '↔', implies: '⇒', iff: '⇔',
  longrightarrow: '→', Longrightarrow: '⇒', longleftarrow: '←', Longleftarrow: '⇐', Longleftrightarrow: '⇔',
  leqslant: '≤', geqslant: '≥', nleq: '≰', ngeq: '≱', rvert: '|', lvert: '|', rVert: '‖', lVert: '‖', Vert: '‖',
  hline: '', cline: '', therefore: '∴', because: '∵', checkmark: '✓', square: '□', blacksquare: '■',
  setminus: '∖', ast: '*', star: '⋆', bullet: '•', ell: 'ℓ',
}
/** Математические слова латиницей от 5 букв — не считаем их выдуманным мусором OCR. */
const MATH_WORDS = new Set(['arcsin', 'arccos', 'arctg', 'arcctg', 'arctan', 'arccot', 'const'])
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
    // описание столбцов массива («{l}», «{rcl}») — не текст
    .replace(/\\begin\s*\{(?:array|tabular)\}\s*\{[^{}]*\}/g, ' ')
    .replace(/\\(?:begin|end)\s*\{[^{}]*\}/g, ' ')
    // «\right.» — невидимая скобка
    .replace(/\\(?:left|right)\s*\.\s*/g, '')
    .replace(/&/g, ' ')
    // склеиваем пробелы, которые OCR ставит внутри LaTeX
    .replace(/(\\[a-zA-Z]+)\s+(?=[{[])/g, '$1')
    .replace(/\}\s+\{/g, '}{')
    .replace(/\]\s+\{/g, ']{')
    .replace(/\s*([_^])\s*/g, '$1')
    .replace(/\\(?:left|right|[Bb]igg?[lrm]?|displaystyle|textstyle|limits|nolimits)(?![a-zA-Z])\s*/g, '')

  // изнутри наружу: каждый проход раскрывает самые внутренние {…}
  for (let i = 0; i < 10; i++) {
    const prev = s
    s = s
      .replace(/\\(?:vec|overrightarrow)\{([^{}]*)\}/g, (_, a: string) => `${a.trim()}⃗`)
      .replace(/\\(?:overline|bar)\{([^{}]*)\}/g, (_, a: string) => a.trim())
      // оформление, не нужное в тексте (подчёркивания, рамки, шляпки), — оставляем содержимое
      // (а также зачёркивания, обводки, жирный — то, что OCR оставляет вокруг цифр и выражений)
      .replace(
        /\\(?:underline|underbrace|overbrace|boxed|hat|widehat|tilde|widetilde|cancel|bcancel|xcancel|sout|dot|ddot|textcircled|circled|bm|pmb|mbox|hbox|fbox|textit|emph|textrm|textsf|texttt|mathsf|mathtt)\{([^{}]*)\}/g,
        '$1',
      )
      .replace(/\\(?:overset|underset|stackrel)\{[^{}]*\}\{([^{}]*)\}/g, '$1')
      .replace(/\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}/g, (_, a: string, b: string) => `${wrap(a)}/${wrap(b)}`)
      .replace(/\\sqrt(?:\[(\d*)\])?\{([^{}]*)\}/g, (_, n = '', a: string) => `${ROOTS[n] ?? `${n}√`}${wrap(a)}`)
      .replace(/\\(?:text|textbf|mathrm|mathbf|mathit|mathcal|mathfrak|mathbb|operatorname|boldsymbol)\{([^{}]*)\}/g, '$1')
      .replace(/\^\{([^{}]*)\}/g, (_, a: string) => mapChars(a.trim(), SUP) ?? `^${wrap(a)}`)
      .replace(/_\{([^{}]*)\}/g, (_, a: string) => mapChars(a.trim(), SUB) ?? `_${a.trim()}`)
      // лишние группирующие скобки «{(p + 2)}^{2}», «{{x}^{2} - 9}» раскрываем
      // (не трогаем аргументы команд: перед ними буква, «}», «]», «^» или «_»; перед степенью — в круглые скобки)
      .replace(/(^|[^\\\w}\]^_])\{([^{}]*)\}(?=([\^_]?))/g, (_, pre: string, a: string, next: string) =>
        `${pre}${next ? wrap(a) : a}`,
      )
    if (s === prev) break
  }

  s = s
    .replace(/\\(?:vec|overrightarrow)\s*([a-zA-Z])/g, '$1⃗')
    .replace(/\\[dt]?frac\s*(\w)\s*(\w)/g, '$1/$2')
    .replace(/\\sqrt\s*(\w+)/g, '√$1')
    .replace(/\^([\w+-])/g, (m, a: string) => SUP[a] ?? m)
    .replace(/_(\w)/g, (m, a: string) => SUB[a] ?? m)
    .replace(/\\([{}%#&$])/g, '$1')
    .replace(/\\([a-zA-Z]+)/g, (m, name: string) => SYMBOLS[name] ?? m.slice(1))
    .replace(/\\[,;:! ]/g, ' ')
    // типографика: «( - 2» → «(-2», «= - 1» → «= -1», «x _ 1» уже склеено выше
    .replace(/([([{])\s+/g, '$1')
    .replace(/\s+([)\]};,.])/g, '$1')
    .replace(/(^|[([{=≠<>≤≥,;·×:±]\s*)-\s+(?=[\w√(])/g, '$1-')
    // номер задания: OCR пишет «No7», «N4», «Nº 5» — приводим к «№7», иначе номер не узнаётся
    .replace(/(^|[\s(;,.:])(?:No|N[oо0º°]?)\.?\s*(?=\d)/g, '$1№')
    // мусор, который OCR «дописывает» из чужих решебников: теги [tex], «Respuesta: … Explicación»
    .replace(/\[\s*\/?\s*tex\s*\]/gi, ' ')
    .replace(/(^|[^A-Za-z])(?:Respuesta|Explicaci[oó]n|Answer|Explanation|Solution)\s*:?/gi, '$1 ')
    // выдуманные слова латиницей (от 5 букв, не математические) и английские служебные слова
    .replace(/[A-Za-zÀ-ÿ]{5,}/g, (w) => (MATH_WORDS.has(w.toLowerCase()) ? w : ' '))
    .replace(/(^|\s)(?:the|for|and|with|from|this|that|of)(?=\s|$)/gi, '$1')
    .replace(/[ \t]{2,}/g, ' ')
  return s.trim()
}
