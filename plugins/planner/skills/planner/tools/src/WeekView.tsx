import { useEffect, useRef, useState } from 'react'
import ActivityDialog, { type DialogTarget } from './ActivityDialog'
import { activityMenu, menuAt } from './actions'
import ContextMenu, { type MenuState } from './ContextMenu'
import * as C from './core'
import type { DayPlan, Store } from './core'
import type { Mutator } from './App'

interface Props {
  store: Store
  date: string // この日を含む週を表示する
  onOpen: (date: string) => void
  mutate: (date: string, fn: Mutator) => DayPlan | null
}

// ドラッグ中の範囲（a: 押した位置、b: 今の位置。どちらも15分単位の枠の先頭）
interface Drag {
  date: string
  a: number
  b: number
}

const HOUR = 48 // 1時間の高さ(px)
const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土']
const y = (m: number) => ((m - C.DAY_START) / 60) * HOUR

// 列の幅の比。今日をいちばん広く、明日をやや広く、過去は狭くする
function weight(date: string, today: string) {
  if (date === today) return 2.4
  if (date === C.addDays(today, 1)) return 1.5
  return date < today ? 0.6 : 1
}

// 時刻の目盛り（12時間表記）
const hourLabel = (m: number) => {
  const h = Math.floor(m / 60) % 24
  return `${h < 12 ? 'AM' : 'PM'} ${h % 12 || 12}`
}

export default function WeekView({ store, date, onOpen, mutate }: Props) {
  const [now, setNow] = useState(() => C.planningNow())
  const [drag, setDrag] = useState<Drag | null>(null)
  const [dialog, setDialog] = useState<DialogTarget | null>(null)
  const [menu, setMenu] = useState<MenuState | null>(null)

  // 列の中の縦位置を、15分単位の時刻に変える
  const slotAt = (e: React.PointerEvent<HTMLElement>) => {
    const top = e.currentTarget.getBoundingClientRect().top
    const m = C.DAY_START + Math.floor((((e.clientY - top) / HOUR) * 60) / C.STEP) * C.STEP
    return C.clamp(m, C.DAY_START, C.DAY_END - C.STEP)
  }
  // 押してから動かさずに離したら日表示を開き、動かしたらその範囲で予定作成を開く
  const endDrag = () => {
    if (!drag) return
    setDrag(null)
    if (drag.a === drag.b) return onOpen(drag.date)
    setDialog({ date: drag.date, activity: null, start: Math.min(drag.a, drag.b), end: Math.max(drag.a, drag.b) + C.STEP })
  }
  useEffect(() => {
    const t = setInterval(() => setNow(C.planningNow()), 60_000)
    return () => clearInterval(t)
  }, [])

  const dates = C.weekDates(date)
  const today = now.date
  const cols = `56px ${dates.map((d) => `minmax(0, ${weight(d, today)}fr)`).join(' ')}`
  const thisWeek = dates.includes(today)

  // 開いたときは、今週なら現在時刻、それ以外は AM 8:00 付近までスクロールする
  const body = useRef<HTMLDivElement>(null)
  useEffect(() => {
    body.current!.scrollTop = y(thisWeek ? now.min : 480) - HOUR * 1.5
    // 週を切り替えたときだけ動かす（毎分の現在時刻の更新ではスクロールしない）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date])

  const hours = Array.from({ length: 24 }, (_, i) => C.DAY_START + i * 60)

  return (
    <section className="week">
      <div className="wk-head" style={{ gridTemplateColumns: cols }}>
        <span />
        {dates.map((d) => {
          const day = C.materialize(store, d)
          const total = day.activities.reduce((s, a) => s + (a.planEnd - a.planStart), 0)
          const when = d < today ? 'past' : d === today ? 'today' : ''
          return (
            <button key={d} className={`wk-day ${when}`} onClick={() => onOpen(d)} aria-label={`${d}の日表示を開く`}>
              <span className="wk-wd">{WEEKDAYS[C.weekdayOf(d)]}</span>
              <span className="wk-num">{Number(d.slice(8))}</span>
              {total > 0 && <span className="wk-total">{(total / 60).toFixed(1)}h</span>}
            </button>
          )
        })}
      </div>

      <div className="wk-body" ref={body}>
        <div className="wk-grid" style={{ gridTemplateColumns: cols, height: 24 * HOUR, '--hour': `${HOUR}px` } as React.CSSProperties}>
          <div className="wk-axis">
            {hours.slice(1).map((m) => (
              <span key={m} style={{ top: y(m) }}>{hourLabel(m)}</span>
            ))}
            {thisWeek && <span className="wk-now-label" style={{ top: y(now.min) }}>{C.fmt12(now.min)}</span>}
          </div>
          {dates.map((d) => {
            const day = C.materialize(store, d)
            const { placed, count } = C.lanes(day.activities)
            const when = d < today ? 'past' : d === today ? 'today' : d === C.addDays(today, 1) ? 'tomorrow' : ''
            return (
              <div
                key={d}
                className={`wk-col ${when}`}
                onPointerDown={(e) => {
                  if (e.button !== 0) return
                  e.currentTarget.setPointerCapture(e.pointerId)
                  const m = slotAt(e)
                  setDrag({ date: d, a: m, b: m })
                }}
                onPointerMove={(e) => drag?.date === d && setDrag({ ...drag, b: slotAt(e) })}
                onPointerUp={endDrag}
                onPointerCancel={() => setDrag(null)}
              >
                {placed.map(({ a, lane }) => {
                  const m = C.metrics(a)
                  return (
                    <button
                      key={a.id}
                      className="wk-ev"
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={() => onOpen(d)}
                      onContextMenu={(e) => setMenu(menuAt(e, activityMenu(a, {
                        edit: () => setDialog({ date: d, activity: a, start: a.planStart, end: a.planEnd }),
                        open: () => onOpen(d),
                        mutate: (fn) => mutate(d, fn),
                      })))}
                      title={`${a.title}　${C.fmtRange(a.planStart, a.planEnd)}`}
                      style={{
                        top: y(a.planStart),
                        height: Math.max(y(a.planEnd) - y(a.planStart), 14),
                        left: `${(lane / count) * 100}%`,
                        width: `calc(${100 / count}% - 4px)`,
                        '--c': C.colorOf(a.title, store.colors),
                      } as React.CSSProperties}
                    >
                      <b>{a.title}</b>
                      <span className="wk-time">{C.fmtRange(a.planStart, a.planEnd)}</span>
                      {m.actualDur != null && (
                        <span className="wk-actual">
                          実績 {C.fmtRange(a.actualStart!, a.actualEnd)}（{C.fmtDiff(m.diff)}）
                        </span>
                      )}
                      {a.memo && <span className="wk-memo">{a.memo}</span>}
                    </button>
                  )
                })}
                {drag?.date === d && drag.a !== drag.b && (
                  <div className="wk-ev ghost" style={{
                    top: y(Math.min(drag.a, drag.b)),
                    height: y(Math.max(drag.a, drag.b) + C.STEP) - y(Math.min(drag.a, drag.b)),
                  }}>
                    <span>{C.fmtRange(Math.min(drag.a, drag.b), Math.max(drag.a, drag.b) + C.STEP)}</span>
                  </div>
                )}
                {thisWeek && <i className={`wk-now ${d === today ? 'strong' : ''}`} style={{ top: y(now.min) }} />}
              </div>
            )
          })}
        </div>
      </div>

      <ActivityDialog
        target={dialog}
        rules={store.rules ?? []}
        mutate={(fn) => mutate(dialog!.date, fn)}
        onClose={() => setDialog(null)}
      />
      <ContextMenu menu={menu} onClose={() => setMenu(null)} />
    </section>
  )
}
