import type { ReactNode } from 'react'

// Плитки с крупным числом и подписью.
export function Metrics({ items }: { items: { value: ReactNode; label: ReactNode }[] }) {
  return <div className="metrics">
    {items.map((item, index) => <div key={index}><strong>{item.value}</strong><span>{item.label}</span></div>)}
  </div>
}
