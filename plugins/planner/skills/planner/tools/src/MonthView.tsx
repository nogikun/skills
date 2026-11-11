import { useState } from 'react'
import { IconChevronLeft, IconChevronRight } from '@tabler/icons-react'
import { ask, notify } from './ask'
import * as C from './core'
import type { DayPlan, Store } from './core'

interface Props {
  store: Store
  month: string // YYYY-MM
  today: string
  onOpen: (date: string) => void
  onImport: (data: Store) => void
}

const h = (m: number) => `${(m / 60).toFixed(1)}h`
const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土']

// 週の行がそろうよう末尾も空セルで埋める
const fullWeeks = (month: string) => {
  const cells = C.monthCells(month)
  return [...cells, ...Array((7 - (cells.length % 7)) % 7).fill(null)] as (string | null)[]
}

// サイドバーの小さな月カレンダー
export function MiniCalendar({ month, selected, today, onPick, onMonth }: {
  month: string; selected: string; today: string; onPick: (date: string) => void; onMonth: (month: string) => void
}) {
  const [y, m] = month.split('-').map(Number)
  return (
    <div className="mini">
      <div className="mini-head">
        <span>{y}年 {m}月</span>
        <button className="icon small" aria-label="前月" onClick={() => onMonth(C.addMonths(month, -1))}><IconChevronLeft size={18} /></button>
        <button className="icon small" aria-label="翌月" onClick={() => onMonth(C.addMonths(month, 1))}><IconChevronRight size={18} /></button>
      </div>
      <div className="mini-grid">
        {WEEKDAYS.map((w) => <span key={w} className="mini-wd">{w}</span>)}
        {fullWeeks(month).map((date, i) => date ? (
          <button key={date} onClick={() => onPick(date)} aria-label={date}
            className={`mini-day ${date === today ? 'today' : ''} ${date === selected ? 'sel' : ''}`}>
            {Number(date.slice(8))}
          </button>
        ) : <span key={`e${i}`} />)}
      </div>
    </div>
  )
}

function download(name: string, text: string, type: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([text], { type }))
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}

export default function MonthView({ store, month, today, onOpen, onImport }: Props) {
  const cells = fullWeeks(month)
  const first = `${month}-01`
  const last = C.monthCells(month).at(-1)!
  const [range, setRange] = useState({ from: first, to: last })
  const [rangeMonth, setRangeMonth] = useState(month)
  if (rangeMonth !== month) {
    // 月を切り替えたら出力期間も追従させる
    setRangeMonth(month)
    setRange({ from: first, to: last })
  }

  const days = C.daysIn(store, first, last)
  const byDate = Object.fromEntries(days.map((d) => [d.date, d]))
  const stats = C.monthStats(days, store.colors)
  const planTotal = stats.reduce((s, x) => s + x.plan, 0)
  const actualTotal = stats.reduce((s, x) => s + x.actual, 0)
  const reviewedPlanTotal = stats.reduce((s, x) => s + x.reviewedPlan, 0)

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    try {
      const data = JSON.parse(await file.text())
      const ok = data && typeof data.days === 'object' && (data.rules === undefined || Array.isArray(data.rules)) &&
        Object.values(data.days).every((d) => Array.isArray((d as DayPlan)?.activities) && typeof (d as DayPlan).date === 'string')
      if (!ok) throw new Error('形式が正しくありません')
      const n = Object.keys(data.days).length
      if (await ask(`${n}日分を読み込みます。同じ日付のデータは上書きされます。よろしいですか？`, { ok: '読み込む' })) onImport(data)
    } catch (err) {
      notify(`読み込みに失敗しました: ${(err as Error).message}`)
    }
  }

  return (
    <section className="month">
      <div className="cal">
        {WEEKDAYS.map((w) => <div key={w} className="wd">{w}</div>)}
        {cells.map((date, i) => {
          if (!date) return <div key={`e${i}`} className="cell blank" />
          const d = byDate[date]
          const bg = d && C.conicGradient(d, store.colors)
          const dist = d ? C.distribution(d, store.colors) : []
          const mins = dist.reduce((s, x) => s + x.minutes, 0)
          // 内訳はホバーで見られるようにする（例: 設計作業 1.5h）
          const breakdown = dist.map((x) => `${x.title} ${h(x.minutes)}`).join('\n')
          return (
            <button key={date} className={`cell ${date === today ? 'today' : ''}`} onClick={() => onOpen(date)}
              aria-label={`${date} ${mins ? h(mins) : '予定なし'}`} title={breakdown || undefined}>
              <span className="cell-head">
                <span className="dnum">{Number(date.slice(8))}</span>
                {d && d.status !== 'draft' && <span className={`dot ${d.status}`} title={d.status === 'reviewed' ? '振り返り済' : '計画確定'} />}
              </span>
              {bg && <span className="pie" style={{ background: bg }} />}
              {mins > 0 && <span className="mins">{h(mins)}</span>}
            </button>
          )
        })}
      </div>

      <div className="panel">
        <h2>月間ダッシュボード</h2>
        <p className="kpis">
          <span>計画 <b>{h(planTotal)}</b></span>
          <span>実績 <b>{h(actualTotal)}</b></span>
          <span>差分 <b>{C.fmtDiff(actualTotal - reviewedPlanTotal)}</b>（振り返り済み分の計画 {h(reviewedPlanTotal)} との差）</span>
          <span>計画日数 <b>{days.filter((d) => d.activities.length).length}</b></span>
          <span>振り返り済 <b>{days.filter((d) => d.status === 'reviewed').length}</b></span>
        </p>
        {stats.length > 0 && (
          <table>
            <thead><tr><th></th><th>活動</th><th>計画</th><th>実績</th><th>差分</th><th>計画の構成比</th></tr></thead>
            <tbody>
              {stats.map((s) => (
                <tr key={s.title}>
                  <td><i className="swatch" style={{ background: s.color }} /></td>
                  <td>{s.title}</td>
                  <td className="num">{h(s.plan)}</td>
                  <td className="num">{s.reviewedPlan ? h(s.actual) : '—'}</td>
                  <td className="num">{s.reviewedPlan ? C.fmtDiff(s.actual - s.reviewedPlan) : '—'}</td>
                  <td className="barcell">
                    <i style={{ width: `${(s.plan / planTotal) * 100}%`, background: s.color }} />
                    {Math.round((s.plan / planTotal) * 100)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel row">
        <h2>データ出力</h2>
        <input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} aria-label="開始日" />
        〜
        <input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} aria-label="終了日" />
        <button onClick={() => download(`planner_${range.from}_${range.to}.csv`, `﻿${C.toCSV(store, range.from, range.to)}`, 'text/csv')}>
          CSV出力
        </button>
        <span className="spacer" />
        <button onClick={() => download(`planner_backup_${today}.json`, JSON.stringify(store, null, 2), 'application/json')}>
          JSONバックアップ
        </button>
        <label className="button">
          JSON読み込み<input type="file" accept="application/json,.json" hidden onChange={onFile} />
        </label>
      </div>
    </section>
  )
}
