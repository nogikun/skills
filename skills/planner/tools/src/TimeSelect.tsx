import * as C from './core'

interface Props {
  value: number
  onChange: (m: number) => void
  min?: number
  max?: number
  from?: number // 渡すと、各時刻に from からの所要時間を添える（終了時刻用）
  'aria-label'?: string
}

const duration = (m: number) => (m < 60 ? `${m}分` : `${Math.floor(m / 60)}時間${m % 60 ? `${m % 60}分` : ''}`)

// 15分刻みの時刻の選択。ブラウザの時刻入力の一覧は見た目を変えられず15分刻みも効かないため、select で出す
export default function TimeSelect({ value, onChange, min = C.DAY_START, max = C.DAY_END, from, ...rest }: Props) {
  const options: number[] = []
  for (let m = min; m <= max; m += C.STEP) options.push(m)
  // 15分刻みでない時刻（API などで入れたもの）も選べる形で残す
  if (!options.includes(value)) {
    options.push(value)
    options.sort((a, b) => a - b)
  }
  return (
    <select className="time-select" value={value} onChange={(e) => onChange(Number(e.target.value))} {...rest}>
      {options.map((m) => (
        <option key={m} value={m}>
          {C.fmt12(m)}{from != null && m > from ? `（${duration(m - from)}）` : ''}
        </option>
      ))}
    </select>
  )
}
