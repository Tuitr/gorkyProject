// Логотип «✓ Проверка работ»: ссылка на главную в шапке или просто надпись на экране входа.
export function Brand({ href }: { href?: string }) {
  const content = <><span className="brand-mark" aria-hidden="true">✓</span>Проверка работ</>
  return href === undefined ? <div className="brand">{content}</div>
    : <a className="brand" href={href} aria-label="Проверка работ — главная">{content}</a>
}
