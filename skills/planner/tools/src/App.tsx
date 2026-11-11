import { useEffect, useRef, useState } from 'react'
import { IconCalendarTime, IconChevronLeft, IconChevronRight, IconHome, IconMoon, IconPlus, IconSun } from '@tabler/icons-react'
import { notify } from './ask'
import { connect } from './sync'
import * as C from './core'
import type { ActivityPatch, DayPlan, DayStatus, Store, TimeInput } from './core'
import DayView from './DayView'
import Glass from './Glass'
import MonthView, { MiniCalendar } from './MonthView'
import WeekView from './WeekView'

const KEY = 'planner.v1'
const todayStr = () => C.planningNow().date // AM 6:00 までは前日扱い

// 表示モード。端末の設定に従うのが既定で、切り替えボタンで選ぶと data-theme と localStorage に残す
const THEME_KEY = 'planner.theme'
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')
const isDark = () => (document.documentElement.dataset.theme ?? (darkQuery.matches ? 'dark' : 'light')) === 'dark'

function useTheme() {
  const [dark, setDark] = useState(isDark)
  // 端末の設定が変わったら追従する（ボタンで固定していないときだけ）
  useEffect(() => {
    const onChange = () => setDark(isDark())
    darkQuery.addEventListener('change', onChange)
    return () => darkQuery.removeEventListener('change', onChange)
  }, [])
  const toggle = () => {
    const next = !dark
    const root = document.documentElement
    // 端末の設定と同じモードを選んだら固定を外し、以後は端末の設定に従う
    if (next === darkQuery.matches) delete root.dataset.theme
    else root.dataset.theme = next ? 'dark' : 'light'
    try {
      if (root.dataset.theme) localStorage.setItem(THEME_KEY, root.dataset.theme)
      else localStorage.removeItem(THEME_KEY)
    } catch {
      // 保存できなくても、この画面では切り替わる
    }
    setDark(next)
  }
  return { dark, toggle }
}

function load(): Store {
  const raw = localStorage.getItem(KEY)
  try {
    const s = raw && JSON.parse(raw)
    if (s && typeof s.days === 'object') return { ...s, colors: C.assignColors(s) }
  } catch {
    // 壊れたデータを空データで上書きしないよう退避しておく
    localStorage.setItem(`${KEY}.broken-${Date.now()}`, raw!)
  }
  return { days: {} }
}

// AI やブラウザ自動操作から使う、時刻を HH:MM にした読み取り用の形
const describe = (d: DayPlan) => ({
  ...d,
  rangeStart: C.fmt(d.rangeStart),
  rangeEnd: C.fmt(d.rangeEnd),
  activities: d.activities.map((a) => ({
    ...a,
    planStart: C.fmt(a.planStart),
    planEnd: C.fmt(a.planEnd),
    actualStart: C.fmt(a.actualStart) || null,
    actualEnd: C.fmt(a.actualEnd) || null,
    ...C.metrics(a),
  })),
})

type View = 'day' | 'week' | 'month'
const VIEW_LABEL: Record<View, string> = { day: '日', week: '週', month: '月' }
const STEP_LABEL: Record<View, [string, string]> = { day: ['前日', '翌日'], week: ['前週', '翌週'], month: ['前月', '翌月'] }

// 週の見出し。例: 2026年11月8日〜14日、2026年11月29日〜12月5日
function weekTitle(date: string) {
  const [s, e] = [C.weekDates(date)[0], C.weekDates(date)[6]].map((d) => d.split('-').map(Number))
  return `${s[0]}年${s[1]}月${s[2]}日〜${e[0] !== s[0] ? `${e[0]}年` : ''}${e[1] !== s[1] ? `${e[1]}月` : ''}${e[2]}日`
}

export type Mutator =(d: DayPlan, s: Store) => unknown

declare global {
  interface Window {
    planner: unknown
  }
}

export default function App() {
  const [store, setStore] = useState(load)
  const storeRef = useRef(store)
  const [view, setView] = useState<View>('week')
  const [date, setDate] = useState(todayStr)
  const [month, setMonth] = useState(() => todayStr().slice(0, 7))
  const theme = useTheme()

  // 予定データのファイルと同期する（AI が CLI で入れた予定もここから届く）
  const serverSave = useRef<((s: Store) => void) | null>(null)
  const fromServer = useRef(false) // ファイルから読んだ変更は、ファイルへ書き戻さない

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(store)) // ファイルが使えないときの控えとしても残す
    } catch (e) {
      notify(`保存に失敗しました。JSONバックアップを取ってください: ${(e as Error).message}`)
    }
    if (fromServer.current) fromServer.current = false
    else serverSave.current?.(store)
  }, [store])

  const commit = (next: Store) => {
    storeRef.current = { ...next, colors: C.assignColors(next) }
    setStore(storeRef.current)
  }
  useEffect(() => {
    void connect(storeRef.current, (s) => {
      fromServer.current = true
      commit(s)
    }).then((save) => (serverSave.current = save))
  }, [])
  // 繰り返し予定を含めた day のコピーに fn を適用して保存する（この日の繰り返し予定はこの時点で確定する）。
  // fn には繰り返しルールを変更できる store のコピーも渡す。fn が投げた例外はそのまま呼び出し側へ。
  const apply = (date: string, fn: Mutator) => {
    const s: Store = { ...storeRef.current, rules: structuredClone(storeRef.current.rules ?? []) }
    const d = C.materialize(s, date)
    fn(d, s)
    commit({ ...s, days: { ...s.days, [date]: d } })
    return C.materialize(storeRef.current, date)
  }
  // UI 用: エラーはダイアログで知らせて null を返す
  const mutate = (date: string, fn: Mutator) => {
    try {
      return apply(date, fn)
    } catch (e) {
      notify((e as Error).message)
      return null
    }
  }

  useEffect(() => {
    window.planner = {
      getDay: (date: string) => describe(C.materialize(storeRef.current, date)),
      // from と to を両方渡すと、繰り返し予定だけの日も含める
      listDays: (from?: string, to?: string) => C.daysIn(storeRef.current, from, to).map(describe),
      listRules: () => (storeRef.current.rules ?? []).map((r) => ({ ...r, start: C.fmt(r.start), end: C.fmt(r.end) })),
      // weekdays: 0=日 … 6=土。空配列で繰り返しを終了
      setRecurrence: (date: string, id: string, weekdays: number[]) =>
        describe(apply(date, (d, s) => C.setRecurrence(s, d, id, weekdays))),
      addActivity: (date: string, a: { title?: string; memo?: string; start?: TimeInput; end: TimeInput }) =>
        describe(apply(date, (d) => C.addActivity(d, a))),
      updateActivity: (date: string, id: string, patch: ActivityPatch) => describe(apply(date, (d) => C.updateActivity(d, id, patch))),
      removeActivity: (date: string, id: string) => describe(apply(date, (d) => C.removeActivity(d, id))),
      shiftFollowing: (date: string, id: string, minutes: number) => describe(apply(date, (d) => C.shiftFollowing(d, id, minutes))),
      setRange: (date: string, start: TimeInput, end: TimeInput) =>
        describe(apply(date, (d) => {
          d.rangeStart = C.toMin(start) ?? d.rangeStart
          d.rangeEnd = C.toMin(end) ?? d.rangeEnd
          if (d.rangeEnd <= d.rangeStart) throw new Error('表示終了は開始より後にしてください')
        })),
      setStatus: (date: string, status: DayStatus) => describe(apply(date, (d) => (d.status = status))),
      exportCSV: (from?: string, to?: string) => C.toCSV(storeRef.current, from, to),
    }
  }, [])

  const today = todayStr()
  const pick = (d: string) => {
    setDate(d)
    setMonth(d.slice(0, 7))
  }
  const openDay = (d: string) => {
    pick(d)
    setView('day')
  }
  const go = (n: number) => (view === 'month' ? setMonth(C.addMonths(month, n)) : pick(C.addDays(date, view === 'week' ? 7 * n : n)))
  const [y, m] = month.split('-').map(Number)
  const title = view === 'month' ? `${y}年 ${m}月` : view === 'week' ? weekTitle(date)
    : new Date(`${date}T00:00Z`).toLocaleDateString('ja-JP', { timeZone: 'UTC', year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' })

  return (
    <div className="app">
      {/* 浮いたツールバー（光の屈折するガラス）。角の丸み 14px − 余白 6px = 中の部品 8px で同心にする */}
      <header className="topbar-slot">
        <Glass width="calc(100vw - 24px)" height={52} radius={14}>
        <div className="topbar">
        {/* ホーム: 画面の幅にかかわらず常に左上に出し、月表示に戻る */}
        <button className="icon home" aria-label="ホーム（月表示）" title="ホーム（月表示）" onClick={() => setView('month')}>
          <IconHome size={24} />
        </button>
        <div className="brand"><IconCalendarTime className="brand-icon" size={28} stroke={1.5} aria-hidden />日次プランナー</div>
        <button className="outlined" onClick={() => (view === 'month' ? setMonth(today.slice(0, 7)) : pick(today))}>今日</button>
        <button className="icon" aria-label={STEP_LABEL[view][0]} onClick={() => go(-1)}><IconChevronLeft size={22} /></button>
        <button className="icon" aria-label={STEP_LABEL[view][1]} onClick={() => go(1)}><IconChevronRight size={22} /></button>
        <h1>{title}</h1>
        <span className="spacer" />
        <button className="icon theme" onClick={theme.toggle} aria-pressed={theme.dark}
          aria-label={theme.dark ? 'ライトモードにする' : 'ダークモードにする'} title={theme.dark ? 'ライトモードにする' : 'ダークモードにする'}>
          {theme.dark ? <IconSun size={20} /> : <IconMoon size={20} />}
        </button>
        <div className="viewswitch" role="tablist" aria-label="表示">
          {(['day', 'week', 'month'] as const).map((v) => (
            <button key={v} role="tab" aria-selected={view === v} onClick={() => (v === 'month' ? setView(v) : (pick(date), setView(v)))}>
              {VIEW_LABEL[v]}
            </button>
          ))}
        </div>
        </div>
        </Glass>
      </header>
      <aside className="sidebar">
        {/* 目立たせる主操作なので、ここだけカプセル形のガラス */}
        <Glass width="112px" height={44} radius={22} elasticity={0.15} pressable className="create-slot">
          <button className="create" onClick={() => openDay(date)}><IconPlus size={20} aria-hidden />作成</button>
        </Glass>
        <MiniCalendar month={month} selected={date} today={today} onPick={view === 'week' ? pick : openDay} onMonth={setMonth} />
      </aside>
      <main>{view === 'week' ? (
    <WeekView store={store} date={date} onOpen={openDay} mutate={mutate} />
  ) : view === 'month' ? (
    <MonthView
      store={store}
      month={month}
      today={today}
      onOpen={openDay}
      onImport={(data) => {
        const cur = storeRef.current
        const ids = new Set((data.rules ?? []).map((r) => r.id))
        commit({
          days: { ...cur.days, ...data.days },
          colors: { ...data.colors, ...cur.colors },
          rules: [...(cur.rules ?? []).filter((r) => !ids.has(r.id)), ...(data.rules ?? [])],
        })
      }}
    />
  ) : (
    <DayView
      key={date}
      day={C.materialize(store, date)}
      colors={store.colors}
      rules={store.rules ?? []}
      mutate={(fn) => mutate(date, fn)}
    />
  )}</main>
    </div>
  )
}
