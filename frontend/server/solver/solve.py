"""Солвер эталонов: по условию задания (обычный текст, как его сверил учитель) точно считает ответ на sympy.

Вход (stdin, JSON):  {"tasks": [{"num": "1", "text": "Найдите значение выражения (3 − a)/(1 + 4a) при a = −5"}]}
Выход (stdout, JSON): {"answers": [{"num": "1", "kind": "value", "answer": "-8/19"}]}
Задание, тип которого не распознан или которое не удалось посчитать, в ответ не попадает — его, как и раньше,
проверяет одна модель.

Типы (по формулировке):
  vector     — векторы: координаты суммы/разности/линейной комбинации, длина, разложение по i и j, «найдите x и y»
  undefined  — «при каких значениях … не имеет смысла»: нули знаменателя
  value      — «значение выражения … при a = −5» / «при p = −1,2»
  equation   — «решите уравнение …»
  simplify   — «сократите дробь», «упростите», «выполните действия», «представьте в виде дроби»
  compute    — «вычислите …» (числовое выражение)

Безопасность: разбор sympy вызывает eval, поэтому перед ним выражение проверяется — только цифры, знаки,
скобки и однобуквенные переменные (из слов разрешён только sqrt).
Тот же код можно перенести в сервис эталонов бэкенда как «ручной» солвер семейства (source=manual).
"""

import json
import re
import sys

from sympy import Matrix, Symbol, cancel, factor, nsimplify, solve, sqrt, together
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication,
    parse_expr,
    rationalize,
    split_symbols,
    standard_transformations,
)

TRANSFORMS = standard_transformations + (split_symbols, implicit_multiplication, convert_xor, rationalize)
SUP_IN = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁻', '0123456789-')
SUP_OUT = str.maketrans('0123456789-', '⁰¹²³⁴⁵⁶⁷⁸⁹⁻')
SUB_OUT = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
SAFE = re.compile(r'^[0-9a-zA-Z+\-*/^().= ]+$')
NUM = r'[-+]?\d+(?:[.,]\d+)?'


class Skip(Exception):
    """Задание не нашего типа или не разобралось — эталона нет."""


# ---------- разбор математики из текста ----------

def normalize(s: str) -> str:
    s = s.replace('−', '-').replace('–', '-').replace('—', '-')
    s = s.replace('·', '*').replace('×', '*').replace('∙', '*').replace('⋅', '*')
    s = s.replace('√', 'sqrt')
    s = re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+', lambda m: '**' + m.group(0).translate(SUP_IN), s)  # x² → x**2
    s = re.sub(r'(\d),(\d)', r'\1.\2', s)  # десятичная запятая
    return s.strip().rstrip('.;')


def parse(expr: str):
    e = normalize(expr).replace(':', '/')  # деление двоеточием
    probe = e.replace('sqrt', '')
    # слова (4+ латинские буквы подряд) — не математика; «xi», «abc» — произведение переменных
    if not e or not SAFE.match(probe) or re.search(r'[a-zA-Z]{4,}', probe) or '__' in e:
        raise Skip('не математическое выражение')
    e = e.replace('sqrt', '§')
    e = re.sub(r'(?<=[a-zA-Z])(?=[a-zA-Z])', '*', e)  # xi → x*i: после этого имён длиннее буквы нет
    e = re.sub(r'(?<=\d)(?=[a-zA-Z§])', '*', e)  # 4j → 4*j (иначе Python прочитает «4j» как комплексное число)
    e = e.replace('§', 'sqrt')
    # «b(b − 2)», «2(x+1)», «(a)(b)» — умножение, а не вызов функции
    e = re.sub(r'(?<=[0-9a-zA-Z)])(?<!sqrt)\s*\(', '*(', e)
    e = re.sub(r'\)\s*(?=[0-9a-zA-Z])', ')*', e)
    try:
        return parse_expr(e, transformations=TRANSFORMS, evaluate=True)
    except Exception as err:
        raise Skip(f'не разобралось: {err}') from None


def fmt(v) -> str:
    """Ответ «как в тетради»: -8/19, 4y⁹/(5x⁵), √13."""
    s = str(v)
    s = re.sub(r'\*\*\((-?\d+)\)', lambda m: m.group(1).translate(SUP_OUT), s)
    s = re.sub(r'\*\*(-?\d+)', lambda m: m.group(1).translate(SUP_OUT), s)
    s = re.sub(r'sqrt\((\d+)\)', r'√\1', s)
    s = re.sub(r'sqrt\(', '√(', s)
    s = re.sub(r'(\d)\*(?=[a-zA-Z(√])', r'\1', s)  # 4*y → 4y
    s = re.sub(r'([a-zA-Z)⁰¹²³⁴⁵⁶⁷⁸⁹])\*(?=[a-zA-Z(√])', r'\1', s)  # x*y → xy
    return s.replace('*', '·')


def simplest(e):
    """Сокращённый вид: общая дробь, разложенные числитель и знаменатель."""
    return factor(cancel(together(e)))


def decimal_tail(r) -> str:
    """Для конечной дроби — ещё и десятичная запись: «-6/5 = -1,2»."""
    if not (r.is_Rational and not r.is_Integer):
        return ''
    q = int(r.q)
    for p in (2, 5):
        while q % p == 0:
            q //= p
    if q != 1:
        return ''
    return ' = ' + f'{float(r):.10g}'.replace('.', ',')


# ---------- типы заданий ----------

def vectors(text: str):
    t = normalize(text.replace('{', '(').replace('}', ')'))
    low = t.lower()
    defs = {
        name: Matrix([[nsimplify(x.replace(',', '.'))], [nsimplify(y.replace(',', '.'))]])
        for name, x, y in re.findall(rf'\b([a-zA-Z])\s*\(\s*({NUM})\s*;\s*({NUM})\s*\)', t)
    }

    # «Найдите числа x и y, если xi + yj = 7i − 4j»
    if 'числа' in low:
        m = re.search(r'если\s+(.+?)=(.+)$', t)
        if not m:
            raise Skip('нет равенства')
        i, j = Symbol('i'), Symbol('j')
        diff = (parse(m.group(1)) - parse(m.group(2))).expand()
        unknowns = sorted(diff.free_symbols - {i, j}, key=str)
        sol = solve([diff.coeff(i), diff.coeff(j)], unknowns, dict=True)
        if not sol:
            raise Skip('система не решилась')
        return '; '.join(f'{k} = {fmt(v)}' for k, v in sorted(sol[0].items(), key=lambda kv: str(kv[0])))

    if not defs:
        raise Skip('нет координат векторов')

    # «Разложите вектор a{−2; 5} по координатным векторам i и j»
    if 'разлож' in low:
        name = next(iter(defs))
        x, y = defs[name][0], defs[name][1]
        sign = '-' if y < 0 else '+'
        return f'{name} = {fmt(x)}i {sign} {fmt(abs(y))}j'

    # длина (модуль) вектора
    m = re.search(r'(?:длин\w*|модул\w*)\s+вектора?\s+(.+?)(?:,\s*если|\s+если|$)', t)
    if m:
        v = combo(m.group(1), defs)
        return fmt(sqrt(v[0] ** 2 + v[1] ** 2))

    # «Найдите координаты вектора a + b» / «вектора v = 2a − b + c» / «вектора −2a»
    m = re.search(r'координат\w*\s+вектора?\s+(?:[a-zA-Z]\s*=\s*)?(.+?)(?:,\s*если|\s+если|$)', t)
    if m:
        v = combo(m.group(1), defs)
        return f'{{{fmt(v[0])}; {fmt(v[1])}}}'
    raise Skip('векторное задание не распознано')


def combo(expr: str, defs: dict) -> Matrix:
    """Линейная комбинация векторов: «2a − b + c» → координаты. «a(2; -3)» в выражении → просто «a»."""
    expr = re.sub(rf'\(\s*{NUM}\s*;\s*{NUM}\s*\)', '', expr)
    e = parse(expr).expand()
    syms = {Symbol(k): v for k, v in defs.items()}
    if not e.free_symbols or not e.free_symbols <= set(syms):
        raise Skip('в выражении неизвестный вектор')
    res = Matrix([[0], [0]])
    for s, v in syms.items():
        res += e.coeff(s) * v
    return res


def undefined_at(text: str):
    """«При каких значениях b выражение (b + 3)/(b(b − 2)) не имеет смысла»."""
    m = re.search(r'(?:выражение|дробь)\s+(.+?)\s+не имеет смысла', text) or re.search(
        r'не имеет смысла\s+(?:выражение|дробь)?\s*:?\s*(.+)$', text
    )
    if not m:
        raise Skip('не нашли выражение')
    den = together(parse(m.group(1))).as_numer_denom()[1]
    vs = sorted(den.free_symbols, key=str)
    if len(vs) != 1:
        raise Skip('не одна переменная')
    roots = sorted({r for r in solve(den, vs[0]) if r.is_real}, key=float)
    if not roots:
        return 'таких значений нет'
    return ', '.join(f'{vs[0]} = {fmt(r)}' for r in roots)


def value_at(text: str):
    """«Найдите значение выражения … при a = −5» (одна или несколько переменных)."""
    m = re.search(r'выражени\w*\s*:?\s+(.+?)\s+при\s+(.+)$', text) or re.search(r'^(?:[^(]*?:\s*)?(.+?)\s+при\s+(.+)$', text)
    if not m:
        raise Skip('нет «при»')
    expr, cond = m.group(1), normalize(m.group(2))
    subs = {Symbol(k): nsimplify(v.replace(',', '.')) for k, v in re.findall(rf'\b([a-zA-Z])\s*=\s*({NUM})', cond)}
    if not subs:
        raise Skip('нет значений переменных')
    e = parse(expr)
    if not e.free_symbols or not e.free_symbols <= set(subs):
        raise Skip('не все переменные заданы')
    if together(e).as_numer_denom()[1].subs(subs) == 0:
        return 'выражение не имеет смысла (деление на ноль)'
    r = simplest(e.subs(subs))
    return fmt(r) + decimal_tail(r)


def equation(text: str):
    m = re.search(r'уравнени\w*\s*:?\s+(.+)$', text)
    body = m.group(1) if m else text
    if body.count('=') != 1:
        raise Skip('не уравнение')
    lhs, rhs = body.split('=')
    e = parse(lhs) - parse(rhs)
    vs = sorted(e.free_symbols, key=str)
    if len(vs) != 1:
        raise Skip('не одна переменная')
    v = vs[0]
    roots = sorted({r for r in solve(e, v) if r.is_real}, key=float)
    if not roots:
        return 'корней нет'
    if len(roots) == 1:
        return f'{v} = {fmt(roots[0])}'
    return '; '.join(f'{v}{str(k + 1).translate(SUB_OUT)} = {fmt(r)}' for k, r in enumerate(roots))


SIMPLIFY_WORDS = ('сократ', 'упрост', 'выполните действи', 'представьте', 'преобраз', 'выполните умножение', 'выполните деление')


def simplify_task(text: str):
    m = re.search(r'(?:дробь|дроби|выражение|действия|умножение|деление)\s*:?\s+(.+)$', text)
    if not m:
        raise Skip('не нашли выражение')
    return fmt(simplest(parse(m.group(1))))


def compute(text: str):
    m = re.search(r'вычисли\w*\s*:?\s+(.+)$', text, re.I)
    if not m:
        raise Skip('нет «вычислите»')
    e = parse(m.group(1))
    if e.free_symbols:
        raise Skip('есть переменные')
    r = simplest(e)
    return fmt(r) + decimal_tail(r)


def solve_task(text: str):
    t = ' '.join(text.split())
    low = t.lower()
    if 'вектор' in low or ('числа' in low and re.search(r'[ij]\s*=', t)):
        return 'vector', vectors(t)
    if 'смысл' in low:
        return 'undefined', undefined_at(t)
    if re.search(r'\bпри\s+[a-zA-Z]\s*=', t):
        return 'value', value_at(t)
    if 'уравнени' in low:
        return 'equation', equation(t)
    if any(w in low for w in SIMPLIFY_WORDS):
        return 'simplify', simplify_task(t)
    if 'вычисли' in low:
        return 'compute', compute(t)
    raise Skip('тип задания не распознан')


def main():
    data = json.loads(sys.stdin.buffer.read().decode('utf-8') or '{}')
    answers = []
    for task in data.get('tasks', [])[:60]:
        try:
            kind, answer = solve_task(str(task.get('text', ''))[:2000])
            answers.append({'num': str(task.get('num', '')), 'kind': kind, 'answer': answer})
        except Exception:  # Skip или сбой sympy — у этого задания просто нет эталона
            pass
    sys.stdout.buffer.write(json.dumps({'answers': answers}, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
