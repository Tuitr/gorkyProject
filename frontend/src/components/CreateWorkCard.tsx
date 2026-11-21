// Плитка «+ Создать работу» в начале списка работ.
export function CreateWorkCard({ onClick }: { onClick: () => void }) {
  return <button className="work-card create-card" type="button" onClick={onClick}>
    <span className="create-mark" aria-hidden="true">+</span>
    <span>Создать работу</span>
  </button>
}
