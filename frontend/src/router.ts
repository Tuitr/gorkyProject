import { useEffect, useState } from 'react'

/**
 * Простейший роутер на hash (#/путь?параметры) — без лишних зависимостей,
 * работает и в dev, и в preview, и при открытии dist как статики.
 */
export type Route = { path: string[]; query: URLSearchParams }

function parse(): Route {
  const raw = location.hash.replace(/^#\/?/, '')
  const [p, q = ''] = raw.split('?')
  return { path: p.split('/').filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q) }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const on = () => {
      setRoute(parse())
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

export const href = (...parts: (string | number | Record<string, string | undefined>)[]) => {
  const q = parts.find((p): p is Record<string, string | undefined> => typeof p === 'object')
  const path = parts.filter((p) => typeof p !== 'object').map((p) => encodeURIComponent(String(p))).join('/')
  const qs = q ? new URLSearchParams(Object.entries(q).filter((e): e is [string, string] => !!e[1])).toString() : ''
  return `#/${path}${qs ? `?${qs}` : ''}`
}

export const go = (...parts: Parameters<typeof href>) => {
  location.hash = href(...parts)
}
