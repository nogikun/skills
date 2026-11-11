import { useState } from 'react'
import { IconInfoCircle, IconRepeat } from '@tabler/icons-react'
import ActivityDialog, { type DialogTarget } from './ActivityDialog'
import { activityMenu, menuAt } from './actions'
import { ask } from './ask'
import Clock, { type Arc } from './Clock'
import ContextMenu, { type MenuState } from './ContextMenu'
import TimeSelect from './TimeSelect'
import * as C from './core'
import type { Activity, Colors, DayPlan, Rule, Status } from './core'
import type { Mutator } from './App'

interface Props {
  day: DayPlan
  colors?: Colors
  rules: Rule[]
  mutate: (fn: Mutator) => DayPlan | null
}

const STATUS_LABEL: Record<Status, string> = { '': '未入力', done: '完了', partial: '一部', skipped: '未実施' }
const DAY_STATUS = { draft: '下書き', planned: '計画確定', reviewed: '振り返り済' }

// 予定作成の初期値: 開始=直前の終了、終了=開始+60分
const planDraft = (d: DayPlan) => {
  const start = Math.min(C.nextStart(d), C.DAY_END - C.STEP)
  return { start, end: Math.min(start + 60, C.DAY_END) }
}
const actualDraft = (a: Activity) => ({ start: a.actualStart ?? a.planStart, end: a.actualEnd ?? a.planEnd })

export default function DayView({ day, colors, rules, mutate }: Props) {
  const [mode, setMode] = useState<'plan' | 'review'>('plan')
  const [draft, setDraft] = useState(() => planDraft(day))
  const [which, setWhich] = useState<'start' | 'end'>('end')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [reviewStatus, setReviewStatus] = useState<Status>('done')
  const [dialog, setDialog] = useState<DialogTarget | null>(null)
  const [menu, setMenu] = useState<MenuState | null>(null)

  const selected = day.activities.find((a) => a.id === selectedId) ?? null

  const select = (a: Activity | undefined) => {
    setSelectedId(a?.id ?? null)
    if (a) {
      setDraft(actualDraft(a))
      setReviewStatus(a.status || 'done')
    }
  }

  const switchMode = (m: 'plan' | 'review') => {
    setMode(m)
    if (m === 'plan') setDraft(planDraft(day))
    else select(day.activities.find((a) => a.actualEnd == null) ?? day.activities[0])
  }

  const openDialog = (a: Activity | null) => setDialog({ date: day.date, activity: a, start: draft.start, end: draft.end })
  const onContextMenu = (e: React.MouseEvent, a: Activity) => setMenu(menuAt(e, activityMenu(a, { edit: () => openDialog(a), mutate })))

  // 実績を保存し、終了がずれた分だけ後続をずらすか確認する (FR-18: ユーザー承認方式)
  const saveActual = async () => {
    if (!selected) return
    const delta = draft.end - (selected.actualEnd ?? selected.planEnd)
    const following = day.activities.length - 1 - selected.order
    const shift = delta !== 0 && following > 0 &&
      (await ask(`後続の${following}件の実績を${C.fmtDiff(delta)}ずらしますか？`, { ok: 'ずらす' }))
    const next = mutate((d) => {
      C.updateActivity(d, selected.id, { actualStart: draft.start, actualEnd: draft.end, status: reviewStatus })
      if (shift) C.shiftFollowing(d, selected.id, delta)
    })
    if (next) select(next.activities[selected.order + 1])
  }

  const setRange = (key: 'rangeStart' | 'rangeEnd', m: number) => {
    const next = mutate((d) => {
      d[key] = m
      if (d.rangeEnd <= d.rangeStart) throw new Error('表示終了は開始より後にしてください')
    })
    if (next && mode === 'plan') setDraft(planDraft(next))
  }

  const arcs: Arc[] = day.activities.flatMap((a) => {
    const color = C.colorOf(a.title, colors)
    const list: Arc[] = [{ start: a.planStart, end: a.planEnd, color }]
    if (mode === 'review' && a.actualStart != null && a.actualEnd != null && a.id !== selectedId)
      list.push({ start: a.actualStart, end: a.actualEnd, color, inner: true })
    return list
  })

  const total = (pick: (m: ReturnType<typeof C.metrics>) => number | null) =>
    day.activities.reduce((s, a) => s + (pick(C.metrics(a)) ?? 0), 0)

  return (
    <section className="day">
      <header className="bar">
        <label>
          時間帯 <TimeSelect value={day.rangeStart} max={C.DAY_END - C.STEP} onChange={(m) => setRange('rangeStart', m)} />
        </label>
        〜
        <TimeSelect value={day.rangeEnd} min={day.rangeStart + C.STEP} onChange={(m) => setRange('rangeEnd', m)} aria-label="時間帯の終了" />
        <span className={`badge ${day.status}`}>{DAY_STATUS[day.status]}</span>
        <div className="tabs" role="tablist">
          {(['plan', 'review'] as const).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} onClick={() => switchMode(m)}>
              {m === 'plan' ? '計画' : '振り返り'}
            </button>
          ))}
        </div>
      </header>

      <div className="daybody">
        <div className="panel clockpanel">
          <p className="hint">
            {mode === 'plan' ? '新しい予定' : selected ? `${selected.title}の実績` : '活動を選択してください'}
          </p>
          <p className="draft-time" aria-live="polite">
            <b>{C.fmtRange(draft.start, draft.end)}</b>
            <span>{draft.end - draft.start}分</span>
          </p>
          <Clock
            rangeStart={day.rangeStart}
            rangeEnd={day.rangeEnd}
            start={draft.start}
            end={draft.end}
            which={which}
            arcs={arcs}
            inner={mode === 'review'}
            onChange={(start, end) => setDraft({ start, end })}
          />
          <div className="row">
            <div className="segmented" role="radiogroup" aria-label="ホイールで調整する時刻">
              {(['start', 'end'] as const).map((w) => (
                <label key={w}>
                  <input type="radio" name="which" checked={which === w} onChange={() => setWhich(w)} />
                  <span>{w === 'start' ? '開始' : '終了'}</span>
                </label>
              ))}
            </div>
            <Info text="ホイールか↑↓キーで15分ずつ変わり、Shiftを押しながらだと開始時刻が変わります。文字盤をクリックすると、その時刻に合わせます。" />
          </div>
          {mode === 'plan' ? (
            <div className="row">
              <button className="primary" disabled={draft.end <= draft.start} onClick={() => openDialog(null)}>予定作成</button>
              <button onClick={() => mutate((d) => (d.status = 'planned'))} disabled={!day.activities.length}>計画確定</button>
            </div>
          ) : (
            <div className="row">
              <select value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value as Status)} aria-label="実施状況">
                {(['done', 'partial', 'skipped'] as const).map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
              </select>
              <button className="primary" disabled={!selected} onClick={saveActual}>実績を保存</button>
              <button onClick={() => mutate((d) => (d.status = 'reviewed'))} disabled={!day.activities.length}>振り返り完了</button>
            </div>
          )}
        </div>

        <div className="panel">
          {day.activities.length === 0 ? (
            <p className="empty">予定がありません。時計をホイールで調整して「予定作成」を押してください。</p>
          ) : mode === 'plan' ? (
            <table>
              <thead><tr><th></th><th>時間</th><th>タイトル</th><th>メモ</th><th>分</th></tr></thead>
              <tbody>
                {day.activities.map((a) => (
                  <tr key={a.id} onClick={() => openDialog(a)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && openDialog(a)}
                    onContextMenu={(e) => onContextMenu(e, a)}>
                    <td><i className="swatch" style={{ background: C.colorOf(a.title, colors) }} /></td>
                    <td>{C.fmtRange(a.planStart, a.planEnd)}</td>
                    <td>{a.title}{a.ruleId && <Repeat />}</td>
                    <td className="memo">{a.memo}</td>
                    <td className="num">{a.planEnd - a.planStart}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot><tr><td colSpan={4}>合計</td><td className="num">{total((m) => m.planDur)}</td></tr></tfoot>
            </table>
          ) : (
            <table>
              <thead>
                <tr><th></th><th>タイトル</th><th>予定</th><th>実績</th><th>予定分</th><th>実績分</th><th>差分</th><th>開始ずれ</th><th>状況</th></tr>
              </thead>
              <tbody>
                {day.activities.map((a) => {
                  const m = C.metrics(a)
                  return (
                    <tr key={a.id} className={a.id === selectedId ? 'sel' : ''} onClick={() => select(a)} tabIndex={0}
                      onKeyDown={(e) => e.key === 'Enter' && select(a)} onContextMenu={(e) => onContextMenu(e, a)}>
                      <td><i className="swatch" style={{ background: C.colorOf(a.title, colors) }} /></td>
                      <td>{a.title}{a.ruleId && <Repeat />}</td>
                      <td>{C.fmtRange(a.planStart, a.planEnd)}</td>
                      <td>{a.actualStart != null ? C.fmtRange(a.actualStart, a.actualEnd) : '—'}</td>
                      <td className="num">{m.planDur}</td>
                      <td className="num">{m.actualDur ?? '—'}</td>
                      <td className={`num ${m.diff! > 0 ? 'over' : m.diff! < 0 ? 'under' : ''}`}>{C.fmtDiff(m.diff)}</td>
                      <td className="num">{C.fmtDiff(m.startDelay)}</td>
                      <td>{STATUS_LABEL[a.status]}</td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={4}>合計</td>
                  <td className="num">{total((m) => m.planDur)}</td>
                  <td className="num">{total((m) => m.actualDur)}</td>
                  <td className="num">{C.fmtDiff(total((m) => m.diff))}</td>
                  <td colSpan={2}></td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>

      <ActivityDialog
        target={dialog}
        rules={rules}
        mutate={mutate}
        onClose={(saved) => {
          // 新しい予定を保存したら、次の予定の開始をその終了に合わせる
          if (saved && !dialog?.activity) setDraft(planDraft(saved))
          setDialog(null)
        }}
      />
      <ContextMenu menu={menu} onClose={() => setMenu(null)} />
    </section>
  )
}

// i マーク。ホバーかキーボードのフォーカスで説明を出す
const Info = ({ text }: { text: string }) => (
  <span className="info" tabIndex={0} aria-label={text}>
    <IconInfoCircle size={18} aria-hidden />
    <span className="tip" role="tooltip">{text}</span>
  </span>
)

const Repeat = () => (
  <span className="repeat" title="繰り返す予定" aria-label="繰り返し"><IconRepeat size={14} aria-hidden /></span>
)
