import { useEffect, useRef } from 'react'
import { DAY_END, DAY_START, STEP, clamp, fmtRange } from './core'

export interface Arc {
  start: number
  end: number
  color: string
  inner?: boolean
}

interface Props {
  rangeStart: number
  rangeEnd: number
  start: number
  end: number
  which: 'start' | 'end'
  arcs: Arc[]
  inner?: boolean // 編集中の範囲を内側（実績）リングに描く
  onChange: (start: number, end: number) => void
}

const SIZE = 280
const C = SIZE / 2
const R = 105
const HALF = 720 // 文字盤1周 = 12時間

// 12時間の文字盤（上が12時）。1日を AM 6〜PM 6 と PM 6〜翌 AM 6 の2面に分け、
// 調整中の針がある面を表示する。針が 6時をまたぐと面が自動で切り替わる。
export default function Clock({ rangeStart, rangeEnd, start, end, which, arcs, inner, onChange }: Props) {
  const hand = which === 'end' ? end : start
  // 0=昼面, 1=夜面。終了時刻がちょうど 6時のときは、その直前までの面に置く
  const atBoundary = which === 'end' && hand > DAY_START && (hand - DAY_START) % HALF === 0
  const face = clamp(Math.floor((hand - DAY_START) / HALF) - (atBoundary ? 1 : 0), 0, 1)
  const faceStart = DAY_START + HALF * face
  const faceEnd = faceStart + HALF

  const angle = (m: number) => ((m % HALF) / HALF) * 360
  const pt = (deg: number, r: number) => [C + r * Math.sin((deg * Math.PI) / 180), C - r * Math.cos((deg * Math.PI) / 180)]
  // 表示中の面に収まる部分だけ描く（clip=false なら面をまたいでそのまま描く）
  const arcPath = (s: number, e: number, r: number, clip = true) => {
    if (clip) [s, e] = [Math.max(s, faceStart), Math.min(e, faceEnd)]
    if (e <= s) return null
    const a0 = angle(s)
    const sweep = Math.min((e - s) / 2, 359.99)
    const [x0, y0] = pt(a0, r)
    const [x1, y1] = pt(a0 + sweep, r)
    return `M${x0} ${y0}A${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${x1} ${y1}`
  }

  const step = (n: number, target = which) => {
    const d = n * STEP
    if (target === 'end') onChange(start, clamp(end + d, start + STEP, DAY_END))
    else onChange(clamp(start + d, DAY_START, end - STEP), end)
  }

  // ホイールは passive: false で登録しないとページスクロールを止められない
  const svgRef = useRef<SVGSVGElement>(null)
  const stepRef = useRef(step)
  useEffect(() => {
    stepRef.current = step
  })
  useEffect(() => {
    const el = svgRef.current!
    let acc = 0
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      // macOS は Shift+ホイールを横スクロール (deltaX) に変換する
      acc += (e.deltaY || e.deltaX) * (e.deltaMode === 1 ? 40 : 1)
      if (Math.abs(acc) < 40) return // トラックパッドの細かい入力を貯める。マウス1ノッチ=15分
      stepRef.current(Math.sign(acc), e.shiftKey ? 'start' : undefined)
      acc = 0
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const onKey = (e: React.KeyboardEvent) => {
    const n = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[e.key]
    if (!n) return
    e.preventDefault()
    step(n, e.shiftKey ? 'start' : undefined)
  }

  // クリックした位置の時刻（表示中の面の中で）へ、調整対象を合わせる
  const onClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - box.left) / box.width) * SIZE - C
    const y = ((e.clientY - box.top) / box.height) * SIZE - C
    const deg = ((Math.atan2(x, -y) * 180) / Math.PI + 360) % 360
    const m12 = Math.round((deg * 2) / STEP) * STEP
    const m = faceStart + ((m12 - (faceStart % HALF) + HALF) % HALF)
    step((m - hand) / STEP)
  }

  const ring = inner ? R - 20 : R
  const night = faceStart >= DAY_START + HALF
  const handAt = pt(angle(hand), R - 30)
  const [bx0, by0] = pt(180, R + 12)
  const [bx1, by1] = pt(180, R - 30)

  return (
    <svg
      ref={svgRef}
      className={`clock ${night ? 'night' : ''}`}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      tabIndex={0}
      role="slider"
      aria-label={`${which === 'end' ? '終了' : '開始'}時刻。ホイールまたは矢印キーで15分ずつ変更`}
      aria-valuetext={fmtRange(start, end)}
      aria-valuenow={hand}
      aria-valuemin={DAY_START}
      aria-valuemax={DAY_END}
      onKeyDown={onKey}
      onClick={onClick}
    >
      <circle cx={C} cy={C} r={R + 30} className="face" />
      <circle cx={C} cy={C} r={R} className="track" />
      <circle cx={C} cy={C} r={R - 20} className="track inner" />
      {arcPath(rangeStart, rangeEnd, R + 10) && <path d={arcPath(rangeStart, rangeEnd, R + 10)!} className="range" />}
      {Array.from({ length: 12 }, (_, i) => {
        const h = i || 12
        const [x0, y0] = pt(i * 30, R + 10)
        const [x1, y1] = pt(i * 30, R + 4)
        const [tx, ty] = pt(i * 30, R + 22)
        return (
          <g key={i}>
            <line x1={x0} y1={y0} x2={x1} y2={y1} className="tick" />
            <text x={tx} y={ty} className={`hour ${i === 6 ? 'boundary' : ''}`}>{h}</text>
          </g>
        )
      })}
      {/* 6時の位置が面の切り替わり */}
      <line x1={bx0} y1={by0} x2={bx1} y2={by1} className="boundary" />
      {arcs.map((a, i) => {
        const d = arcPath(a.start, a.end, a.inner ? R - 20 : R)
        return d && <path key={i} d={d} stroke={a.color} className="arc" />
      })}
      {/* 12時間を超えたら、1周目を薄い色、超えた分を通常の色で重ねる */}
      {end - start > HALF ? (
        <>
          <path d={arcPath(start, start + HALF, ring, false)!} className="arc draft lap" />
          <path d={arcPath(start + HALF, end, ring, false)!} className="arc draft" />
        </>
      ) : (
        <path d={arcPath(start, end, ring, false)!} className="arc draft" />
      )}
      <line x1={C} y1={C} x2={handAt[0]} y2={handAt[1]} className="hand" />
      <circle cx={C} cy={C} r={4} className="hub" />
    </svg>
  )
}
