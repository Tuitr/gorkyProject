import { number } from '../workspace'

// «7 / 10»; без максимума — empty, без набранных баллов — «— / 10».
export function scoreText(score: number | null | undefined, maxScore: number | undefined, empty = '—') {
  return maxScore === undefined ? empty : (score == null ? '—' : number(score)) + ' / ' + number(maxScore)
}

export function Score({ score, maxScore, empty }: { score: number | null | undefined; maxScore: number | undefined; empty?: string }) {
  return <span className="score">{scoreText(score, maxScore, empty)}</span>
}
