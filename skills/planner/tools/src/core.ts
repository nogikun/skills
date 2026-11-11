// 時間計算・データ操作・集計（UI非依存）。時刻はすべて「0:00からの分」で保持し、表示時だけ HH:MM にする。

export const STEP = 15
// 1日は AM 6:00 〜 翌 AM 6:00。翌日分は 1440 以上の分で表す（例: 翌 1:00 = 1500）
export const DAY_START = 360
export const DAY_END = DAY_START + 1440

export type Status = '' | 'done' | 'partial' | 'skipped'
export type DayStatus = 'draft' | 'planned' | 'reviewed'

export interface Activity {
  id: string
  order: number
  title: string
  memo: string
  planStart: number
  planEnd: number
  actualStart: number | null
  actualEnd: number | null
  status: Status
  ruleId?: string // 繰り返し予定から生成された場合のルールID
}

export interface DayPlan {
  date: string // YYYY-MM-DD
  rangeStart: number
  rangeEnd: number
  status: DayStatus
  activities: Activity[]
  excluded?: string[] // この日だけ削除した繰り返しルールのID
}

// 毎週決まった曜日・時刻に行う予定
export interface Rule {
  id: string
  title: string
  memo: string
  start: number
  end: number
  weekdays: number[] // 0=日 … 6=土
  since: string // この日以降に適用
}

export type Colors = Record<string, number> // タイトル → PALETTE の番号

export interface Store {
  days: Record<string, DayPlan>
  colors?: Colors
  rules?: Rule[]
}

export type TimeInput = number | string | null | undefined

const pad = (n: number) => String(n).padStart(2, '0')
// 24時間表記 HH:MM（入力欄・CSV・API 用）。翌日分も 00:00〜05:59 で表す
export const fmt = (m: number | null) => (m == null ? '' : `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`)
// 12時間表記（画面表示用）例: AM 6:00, PM 1:30。翌日分も「翌」を付けずに AM 1:00 と書く
export function fmt12(m: number | null) {
  if (m == null) return ''
  const h = Math.floor(m / 60) % 24
  return `${h < 12 ? 'AM' : 'PM'} ${h % 12 || 12}:${pad(m % 60)}`
}
// 時間の範囲。終了が開始と同じ日の同じ午前／午後なら、終了側の AM/PM を省く（例: AM 9:00〜10:30）
// 12時間以上離れた同じ午前どうし（AM 9:00〜翌AM 1:00）は省くと1時間に読めるので省かない
export function fmtRange(s: number, e: number | null) {
  const [a, b] = [fmt12(s), fmt12(e)]
  if (e == null) return a
  const half = (m: number) => Math.floor(m / 720)
  return `${a}〜${half(s) === half(e) ? b.slice(b.indexOf(' ') + 1) : b}`
}

// "HH:MM" を分に。AM 6:00 より前（0:00〜5:59）は翌日の早朝として扱う
export function toMin(v: TimeInput): number | null {
  if (v == null || v === '') return null
  if (typeof v === 'number') return v
  const [h, m] = v.split(':').map(Number)
  if (!Number.isInteger(h) || !Number.isInteger(m) || h < 0 || h > 23 || m < 0 || m > 59) throw new Error(`時刻の形式が不正です: ${v}`)
  const min = h * 60 + m
  return min < DAY_START ? min + 1440 : min
}

export const clamp = (m: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, m))
export const fmtDiff = (n: number | null) => (n == null ? '' : n > 0 ? `+${n}分` : n < 0 ? `−${-n}分` : '±0分')

export const newDay = (date: string): DayPlan => ({ date, rangeStart: 540, rangeEnd: 1080, status: 'draft', activities: [] })

// 日付は UTC として扱い、タイムゾーンや夏時間で日付がずれないようにする
export const weekdayOf = (date: string) => new Date(`${date}T00:00Z`).getUTCDay()
export function addDays(date: string, n: number) {
  const d = new Date(`${date}T00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

export function addMonths(month: string, n: number) {
  const [y, m] = month.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 7)
}

// 月のカレンダー表示用。先頭は日曜始まりに合わせた空セル (null)
export function monthCells(month: string) {
  const first = `${month}-01`
  const cells: (string | null)[] = Array(weekdayOf(first)).fill(null)
  for (let d = first; d.startsWith(month); d = addDays(d, 1)) cells.push(d)
  return cells
}

// date を含む週（日曜始まり）の7日
export function weekDates(date: string) {
  const sunday = addDays(date, -weekdayOf(date))
  return Array.from({ length: 7 }, (_, i) => addDays(sunday, i))
}

// 今の「計画上の日付」と分。AM 6:00 より前は前日の深夜として扱う
export function planningNow(d = new Date()) {
  const date = d.toLocaleDateString('sv-SE')
  const min = d.getHours() * 60 + d.getMinutes()
  return min < DAY_START ? { date: addDays(date, -1), min: min + 1440 } : { date, min }
}

// 時間が重なる予定を横に並べるための列番号（開始順に並んだ予定を受け取る）
export function lanes<T extends { planStart: number; planEnd: number }>(acts: T[]) {
  const ends: number[] = []
  const placed = acts.map((a) => {
    let lane = ends.findIndex((e) => e <= a.planStart)
    if (lane < 0) lane = ends.push(0) - 1
    ends[lane] = a.planEnd
    return { a, lane }
  })
  return { placed, count: Math.max(ends.length, 1) }
}

// 直前の予定の終了時刻を次の開始時刻として引き継ぐ (FR-02)
export const nextStart = (day: DayPlan) => day.activities.reduce((m, a) => Math.max(m, a.planEnd), day.rangeStart)

function reorder(day: DayPlan) {
  day.activities.sort((x, y) => x.planStart - y.planStart)
  day.activities.forEach((a, i) => (a.order = i))
}

function checkSpan(s: number | null, e: number | null, label: string) {
  if (s != null && e != null && !(e > s)) throw new Error(`${label}の終了は開始より後にしてください`)
}

// 以下の操作は day を直接書き換える。React 側では structuredClone したものに適用する。

export function addActivity(day: DayPlan, a: { title?: string; memo?: string; start?: TimeInput; end: TimeInput }) {
  const planStart = toMin(a.start) ?? nextStart(day)
  const planEnd = toMin(a.end)
  if (planEnd == null) throw new Error('終了時刻を指定してください')
  checkSpan(planStart, planEnd, '予定')
  const act: Activity = {
    id: crypto.randomUUID(), order: 0, title: a.title?.trim() || '(無題)', memo: a.memo ?? '',
    planStart, planEnd, actualStart: null, actualEnd: null, status: '',
  }
  day.activities.push(act)
  reorder(day)
  return act
}

export interface ActivityPatch {
  title?: string
  memo?: string
  status?: Status
  planStart?: TimeInput
  planEnd?: TimeInput
  actualStart?: TimeInput
  actualEnd?: TimeInput
}

export function updateActivity(day: DayPlan, id: string, patch: ActivityPatch) {
  const a = day.activities.find((x) => x.id === id)
  if (!a) throw new Error(`活動が見つかりません: ${id}`)
  const next = { ...a }
  if (patch.title !== undefined) next.title = patch.title.trim() || '(無題)'
  if (patch.memo !== undefined) next.memo = patch.memo
  if (patch.status !== undefined) next.status = patch.status
  if (patch.planStart !== undefined) next.planStart = toMin(patch.planStart) ?? a.planStart
  if (patch.planEnd !== undefined) next.planEnd = toMin(patch.planEnd) ?? a.planEnd
  if (patch.actualStart !== undefined) next.actualStart = toMin(patch.actualStart)
  if (patch.actualEnd !== undefined) next.actualEnd = toMin(patch.actualEnd)
  checkSpan(next.planStart, next.planEnd, '予定')
  checkSpan(next.actualStart, next.actualEnd, '実績')
  Object.assign(a, next)
  reorder(day)
  return a
}

export function removeActivity(day: DayPlan, id: string) {
  const ruleId = day.activities.find((a) => a.id === id)?.ruleId
  if (ruleId) day.excluded = [...(day.excluded ?? []), ruleId] // 繰り返しはこの日だけ消す
  day.activities = day.activities.filter((a) => a.id !== id)
  reorder(day)
}

// 保存済みの日に、まだ載っていない繰り返し予定を加えた日を返す（store は変更しない）
export function materialize(store: Store, date: string): DayPlan {
  const day = structuredClone(store.days[date] ?? newDay(date))
  const wd = weekdayOf(date)
  for (const r of store.rules ?? []) {
    if (date < r.since || !r.weekdays.includes(wd) || day.excluded?.includes(r.id)) continue
    if (day.activities.some((a) => a.ruleId === r.id)) continue
    day.activities.push({
      id: `${r.id}:${date}`, order: 0, title: r.title, memo: r.memo, planStart: r.start, planEnd: r.end,
      actualStart: null, actualEnd: null, status: '', ruleId: r.id,
    })
  }
  reorder(day)
  return day
}

// 活動の繰り返し曜日を設定する。活動のタイトル・メモ・時刻はシリーズ全体（ルール）にも反映する。
// 曜日を空にすると繰り返しを終了し、この日の活動は単発の予定として残す。
export function setRecurrence(store: Store, day: DayPlan, id: string, weekdays: number[]) {
  const a = day.activities.find((x) => x.id === id)
  if (!a) throw new Error(`活動が見つかりません: ${id}`)
  const rules = (store.rules ??= [])
  const idx = rules.findIndex((r) => r.id === a.ruleId)
  if (!weekdays.length) {
    if (idx >= 0) rules.splice(idx, 1)
    delete a.ruleId
    return null
  }
  const rule: Rule = {
    id: a.ruleId ?? crypto.randomUUID(), title: a.title, memo: a.memo, start: a.planStart, end: a.planEnd,
    weekdays: [...new Set(weekdays)].sort(), since: idx >= 0 ? rules[idx].since : day.date,
  }
  if (idx >= 0) rules[idx] = rule
  else rules.push(rule)
  a.ruleId = rule.id
  return rule
}

// 期間内の日（繰り返し予定を含む、活動のある日だけ）。期間を省くと保存済みの日だけを返す。
export function daysIn(store: Store, from?: string, to?: string) {
  let dates: string[]
  if (from && to) {
    dates = []
    for (let d = from; d <= to; d = addDays(d, 1)) dates.push(d)
  } else {
    dates = Object.keys(store.days).filter((d) => (!from || d >= from) && (!to || d <= to)).sort()
  }
  return dates.map((d) => materialize(store, d)).filter((d) => d.activities.length)
}

// 指定活動より後ろの実績をまとめて minutes ずらす (FR-18)。実績未入力なら予定時刻を基準にする。
export function shiftFollowing(day: DayPlan, id: string, minutes: number) {
  const after = day.activities.slice(day.activities.findIndex((a) => a.id === id) + 1)
  for (const a of after) {
    a.actualStart = (a.actualStart ?? a.planStart) + minutes
    a.actualEnd = (a.actualEnd ?? a.planEnd) + minutes
  }
  return after.length
}

export function metrics(a: Activity) {
  const planDur = a.planEnd - a.planStart
  const actualDur = a.actualStart != null && a.actualEnd != null ? a.actualEnd - a.actualStart : null
  return {
    planDur,
    actualDur,
    diff: actualDur == null ? null : actualDur - planDur,
    startDelay: a.actualStart == null ? null : a.actualStart - a.planStart,
  }
}

// shortcut: 色はタイトル単位（タイトル＝分類）。別の分類軸が必要になったら Activity に category を追加する。
// Apple のシステムカラー。隣り合う番号が似た色にならない順に並べ、新しいタイトルへ順に割り当てる。
const PALETTE = ['#007AFF', '#FF9500', '#34C759', '#AF52DE', '#FF3B30', '#30B0C7', '#FFCC00', '#5856D6', '#FF2D55', '#00C7BE', '#A2845E', '#32ADE6']
export function assignColors(store: Store) {
  // 範囲外の番号（旧バージョンの色相など）は割り当て直す
  const colors: Colors = Object.fromEntries(Object.entries(store.colors ?? {}).filter(([, i]) => Number.isInteger(i) && i >= 0 && i < PALETTE.length))
  const used = Object.values(colors)
  const titles = [...Object.values(store.days).flatMap((d) => d.activities), ...(store.rules ?? [])].map((a) => a.title)
  for (const t of titles) {
    if (t in colors) continue
    // 使われている回数が最も少ない色のうち、番号の小さいもの
    const count = (i: number) => used.filter((u) => u === i).length
    colors[t] = PALETTE.reduce((best, _, i) => (count(i) < count(best) ? i : best), 0)
    used.push(colors[t])
  }
  return colors
}

export const colorOf = (title: string, colors?: Colors) => PALETTE[(colors?.[title] ?? 0) % PALETTE.length]

// 円グラフ用の時間配分。実績があれば実績、なければ予定の所要時間で数える。
export function distribution(day: DayPlan, colors?: Colors) {
  const map = new Map<string, number>()
  for (const a of day.activities) {
    const m = metrics(a)
    map.set(a.title, (map.get(a.title) ?? 0) + (m.actualDur ?? m.planDur))
  }
  return [...map].map(([title, minutes]) => ({ title, minutes, color: colorOf(title, colors) }))
}

export function conicGradient(day: DayPlan, colors?: Colors) {
  const dist = distribution(day, colors)
  const total = dist.reduce((s, x) => s + x.minutes, 0)
  if (!total) return undefined
  let acc = 0
  const stops = dist.map((x) => `${x.color} ${(acc / total) * 360}deg ${((acc += x.minutes) / total) * 360}deg`)
  return `conic-gradient(${stops.join(',')})`
}

export function monthStats(days: DayPlan[], colors?: Colors) {
  const map = new Map<string, { title: string; color: string; plan: number; actual: number; reviewedPlan: number }>()
  for (const d of days) {
    for (const a of d.activities) {
      const m = metrics(a)
      const s = map.get(a.title) ?? { title: a.title, color: colorOf(a.title, colors), plan: 0, actual: 0, reviewedPlan: 0 }
      s.plan += m.planDur
      if (m.actualDur != null) {
        s.actual += m.actualDur
        s.reviewedPlan += m.planDur
      }
      map.set(a.title, s)
    }
  }
  return [...map.values()].sort((x, y) => y.plan - x.plan)
}

const csvCell = (v: unknown) => {
  const s = v == null ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
const CSV_HEADER = ['日付', '活動ID', '順序', 'タイトル', 'メモ', '予定開始', '予定終了', '実績開始', '実績終了',
  '予定時間(分)', '実績時間(分)', '差分(分)', '開始ずれ(分)', '実施状況']

export function toCSV(store: Store, from?: string, to?: string) {
  const rows: unknown[][] = [CSV_HEADER]
  for (const { date, activities } of daysIn(store, from, to)) {
    for (const a of activities) {
      const m = metrics(a)
      rows.push([date, a.id, a.order, a.title, a.memo, fmt(a.planStart), fmt(a.planEnd), fmt(a.actualStart),
        fmt(a.actualEnd), m.planDur, m.actualDur, m.diff, m.startDelay, a.status])
    }
  }
  return rows.map((r) => r.map(csvCell).join(',')).join('\r\n')
}
