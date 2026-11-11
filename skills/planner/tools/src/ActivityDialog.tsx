import { useEffect, useRef, useState } from 'react'
import { removeWithConfirm } from './actions'
import * as C from './core'
import type { Activity, DayPlan, Rule } from './core'
import TimeSelect from './TimeSelect'
import type { Mutator } from './App'

// activity が null なら新規作成。start/end は新規作成の初期値
export interface DialogTarget {
  date: string
  activity: Activity | null
  start: number
  end: number
}

interface Props {
  target: DialogTarget | null
  rules: Rule[]
  mutate: (fn: Mutator) => DayPlan | null // target.date の日に適用する
  onClose: (saved: DayPlan | null) => void // 保存したときは保存後の日を渡す
}

const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土']

// 予定の作成・編集のポップアップ（日表示と週表示で共通）
export default function ActivityDialog({ target, rules, mutate, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  const saved = useRef<DayPlan | null>(null)
  useEffect(() => {
    if (target && !ref.current!.open) ref.current!.showModal()
  }, [target])

  const finish = (next: DayPlan | null) => {
    saved.current = next
    ref.current!.close()
  }

  return (
    <dialog
      ref={ref}
      onClose={() => {
        onClose(saved.current)
        saved.current = null
      }}
    >
      {target && (
        <ActivityForm
          key={`${target.date}:${target.activity?.id ?? 'new'}:${target.start}`}
          target={target}
          rule={rules.find((r) => r.id === target.activity?.ruleId)}
          mutate={mutate}
          finish={finish}
        />
      )}
    </dialog>
  )
}

function ActivityForm({ target, rule, mutate, finish }: {
  target: DialogTarget
  rule: Rule | undefined
  mutate: Props['mutate']
  finish: (next: DayPlan | null) => void
}) {
  const a = target.activity
  const [weekdays, setWeekdays] = useState<number[]>(rule?.weekdays ?? [])
  const everyDay = weekdays.length === 7
  const toggle = (i: number) => setWeekdays((w) => (w.includes(i) ? w.filter((x) => x !== i) : [...w, i]))
  const [start, setStart] = useState(a ? a.planStart : target.start)
  const [end, setEnd] = useState(a ? a.planEnd : target.end)
  // 開始を変えたら、所要時間を保ったまま終了も動かす
  const changeStart = (s: number) => {
    setEnd(C.clamp(s + (end - start), s + C.STEP, C.DAY_END))
    setStart(s)
  }

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>
    const next = mutate((d, s) => {
      const act = a
        ? C.updateActivity(d, a.id, { title: f.title, memo: f.memo, planStart: start, planEnd: end })
        : C.addActivity(d, { title: f.title, memo: f.memo, start, end })
      C.setRecurrence(s, d, act.id, weekdays)
    })
    if (next) finish(next)
  }

  const onDelete = async (series: boolean) => {
    if (a && (await removeWithConfirm(a, series, mutate))) finish(null)
  }

  return (
    <form method="dialog" onSubmit={onSubmit}>
      <h2>{a ? '予定を編集' : '予定作成'}</h2>
      <label>タイトル<input name="title" autoFocus required defaultValue={a?.title ?? ''} /></label>
      <label>メモ<textarea name="memo" rows={3} defaultValue={a?.memo ?? ''} /></label>
      <div className="row">
        <label>開始<TimeSelect value={start} max={C.DAY_END - C.STEP} onChange={changeStart} /></label>
        <label>終了<TimeSelect value={end} min={start + C.STEP} from={start} onChange={setEnd} /></label>
      </div>
      <fieldset className="weekdays">
        <legend>繰り返す</legend>
        <div className="row">
          <div className="segmented multi">
            {WEEKDAYS.map((w, i) => (
              <label key={w}>
                <input type="checkbox" checked={weekdays.includes(i)} onChange={() => toggle(i)} />
                <span>{w}</span>
              </label>
            ))}
          </div>
          <button type="button" className={`daily ${everyDay ? 'on' : ''}`} aria-pressed={everyDay}
            onClick={() => setWeekdays(everyDay ? [] : [0, 1, 2, 3, 4, 5, 6])}>
            毎日
          </button>
        </div>
        <small>{rule ? '時刻やタイトルの変更は、まだ確定していない日の繰り返しにも反映されます。' : 'なにも選ばなければ、この日だけの予定です。'}</small>
      </fieldset>
      <div className="row">
        {a && <button type="button" className="danger" onClick={() => onDelete(false)}>{a.ruleId ? 'この日だけ削除' : '削除'}</button>}
        {rule && <button type="button" className="danger" onClick={() => onDelete(true)}>繰り返しを削除</button>}
        <span className="spacer" />
        <button type="button" onClick={() => finish(null)}>キャンセル</button>
        <button className="primary">保存</button>
      </div>
    </form>
  )
}
