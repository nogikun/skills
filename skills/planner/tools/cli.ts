#!/usr/bin/env node
// 日次プランナーの CLI。AI（Claude）が予定を読み書きするための入口。
// 画面（開発サーバー）と同じデータファイルを読み書きするので、書き込むと開いている画面にもすぐ反映される。
// 実行: node cli.ts <command> ...（Node 22.18 以降。TypeScript をそのまま実行する）
import { readFileSync } from 'node:fs'
import { parseArgs } from 'node:util'
import * as C from './src/core.ts'
import type { Activity, DayPlan, DayStatus, Status, Store } from './src/core.ts'
import { ConflictError, dataPath, readSaved, writeSaved } from './server/storeFile.ts'

const HELP = `使い方: node cli.ts <command> [引数]

  brief [date]                         今の時刻・その日の予定・空き時間・持ち越し候補を文章で
  show <date> [--to <date>]            予定を JSON で（繰り返し予定を含む）
  add <date> --title T --start HH:MM --end HH:MM [--memo M] [--repeat 曜日]
  update <date> <id> [--title T] [--memo M] [--start HH:MM] [--end HH:MM]
  remove <date> <id> [--series]        --series で繰り返しごと削除
  repeat <date> <id> <曜日>            曜日: 月水 / 1,3 / daily / weekdays / none
  actual <date> <id> --start HH:MM --end HH:MM [--status done|partial|skipped] [--shift]
  plan <date> --file <path|-> [--dry-run] [--replace]   予定をまとめて入れる（JSON）
  range <date> --start HH:MM --end HH:MM
  status <date> draft|planned|reviewed
  rules                                繰り返しの一覧
  csv [--from <date>] [--to <date>]
  where                                データファイルの場所
  hook session-start|prompt            Claude Code のフック用（標準入力に JSON）

  date: today / tomorrow / yesterday / +N / -N / YYYY-MM-DD（AM 6:00 までは前日が「今日」）
  時刻: 24時間の HH:MM。00:00〜05:59 はその日の深夜（翌朝）として扱う
  id: show や brief に出る id。先頭の4文字以上で一意なら省略できる`

const WEEKDAY_CHARS = '日月火水木金土'
const STATUSES: Status[] = ['done', 'partial', 'skipped']
const DAY_STATUSES: DayStatus[] = ['draft', 'planned', 'reviewed']
const STATUS_LABEL: Record<Status, string> = { '': '未記録', done: '完了', partial: '一部', skipped: '未実施' }
const DAY_STATUS_LABEL: Record<DayStatus, string> = { draft: '下書き', planned: '計画確定', reviewed: '振り返り済' }
const CARRYOVER_DAYS = 7 // 持ち越し候補を探す日数。1週間より前のやり残しは、たいてい計画し直しになるため
const UI_URL = 'http://localhost:5173'

class UsageError extends Error {}

// ---- 入力の解釈 ----

function parseDate(token: string | undefined): string {
  const today = C.planningNow().date
  if (!token || token === 'today') return today
  if (token === 'tomorrow') return C.addDays(today, 1)
  if (token === 'yesterday') return C.addDays(today, -1)
  if (/^[+-]\d+$/.test(token)) return C.addDays(today, Number(token))
  if (/^\d{4}-\d{2}-\d{2}$/.test(token) && !Number.isNaN(Date.parse(`${token}T00:00Z`))) return token
  throw new UsageError(`日付の形式が不正です: ${token}（today / tomorrow / +1 / 2026-11-12 のように指定）`)
}

function parseTime(v: string | undefined, label: string): number {
  if (!v) throw new UsageError(`${label}を --start / --end で指定してください（例: 09:30）`)
  const m = C.toMin(v)!
  if (m < C.DAY_START || m > C.DAY_END) throw new UsageError(`${label}は AM 6:00〜翌 AM 6:00 の範囲で指定してください: ${v}`)
  return m
}

function parseWeekdays(v: string): number[] {
  const s = v.trim()
  if (s === 'daily' || s === '毎日') return [0, 1, 2, 3, 4, 5, 6]
  if (s === 'weekdays' || s === '平日') return [1, 2, 3, 4, 5]
  if (s === 'none' || s === '' || s === 'なし') return []
  if (/^[0-6](,[0-6])*$/.test(s)) return s.split(',').map(Number)
  if ([...s].every((c) => WEEKDAY_CHARS.includes(c))) return [...s].map((c) => WEEKDAY_CHARS.indexOf(c))
  throw new UsageError(`曜日の指定が不正です: ${v}（月水 / 1,3 / daily / weekdays / none）`)
}

function findActivity(day: DayPlan, id: string | undefined): Activity {
  if (!id) throw new UsageError('予定の id を指定してください（show か brief で確認できます）')
  const hits = day.activities.filter((a) => a.id === id || (id.length >= 4 && a.id.startsWith(id)))
  if (hits.length === 1) return hits[0]
  const list = day.activities.map((a) => `  ${a.id}  ${C.fmtRange(a.planStart, a.planEnd)} ${a.title}`).join('\n') || '  （予定なし）'
  throw new UsageError(`${hits.length ? 'id が複数の予定に当てはまります' : '予定が見つかりません'}: ${id}\n${day.date} の予定:\n${list}`)
}

// ---- 読み書き ----

// その日（繰り返し予定を確定させた状態）に fn を適用して保存する。画面と同時に書いてぶつかったら読み直してやり直す
function mutateDay(date: string, fn: (d: DayPlan, s: Store) => void): DayPlan {
  for (let attempt = 0; ; attempt++) {
    const saved = readSaved()
    const s: Store = { ...saved.store, rules: structuredClone(saved.store.rules ?? []) }
    const d = C.materialize(s, date)
    fn(d, s)
    const next: Store = { ...s, days: { ...s.days, [date]: d } }
    next.colors = C.assignColors(next)
    try {
      writeSaved(next, saved.rev)
      return C.materialize(next, date)
    } catch (e) {
      if (!(e instanceof ConflictError) || attempt >= 2) throw e
    }
  }
}

// ---- 出力 ----

const describe = (d: DayPlan) => ({
  date: d.date,
  weekday: WEEKDAY_CHARS[C.weekdayOf(d.date)],
  status: d.status,
  range: { start: C.fmt(d.rangeStart), end: C.fmt(d.rangeEnd) },
  activities: d.activities.map((a) => {
    const m = C.metrics(a)
    return {
      id: a.id,
      title: a.title,
      memo: a.memo,
      start: C.fmt(a.planStart),
      end: C.fmt(a.planEnd),
      label: C.fmtRange(a.planStart, a.planEnd),
      minutes: m.planDur,
      actual: a.actualStart != null ? { start: C.fmt(a.actualStart), end: C.fmt(a.actualEnd), minutes: m.actualDur, diff: m.diff } : null,
      status: a.status || null,
      repeat: a.ruleId ? true : undefined,
    }
  }),
})

const out = (v: unknown) => console.log(JSON.stringify(v, null, 2))

// 空き時間（from 以降、時間帯の終わりまで）。15分未満の隙間は数えない
function freeSlots(d: DayPlan, from: number) {
  const slots: { start: number; end: number }[] = []
  let cursor = Math.max(from, d.rangeStart)
  for (const a of [...d.activities].sort((x, y) => x.planStart - y.planStart)) {
    if (a.planStart - cursor >= C.STEP) slots.push({ start: cursor, end: a.planStart })
    cursor = Math.max(cursor, a.planEnd)
  }
  if (d.rangeEnd - cursor >= C.STEP) slots.push({ start: cursor, end: d.rangeEnd })
  return slots
}

// 直近の日で、実績が未記録・一部・未実施のまま終わった予定
function carryovers(store: Store, today: string) {
  const items: string[] = []
  for (let i = CARRYOVER_DAYS; i >= 1; i--) {
    const d = C.materialize(store, C.addDays(today, -i))
    for (const a of d.activities) {
      if (a.status === 'done') continue
      items.push(`- ${d.date} ${a.title}（予定 ${a.planEnd - a.planStart}分、${STATUS_LABEL[a.status]}）`)
    }
  }
  return items
}

function brief(date: string, store: Store) {
  const now = C.planningNow()
  const d = C.materialize(store, date)
  const lines: string[] = []
  const wd = (x: string) => WEEKDAY_CHARS[C.weekdayOf(x)]
  lines.push(`現在: ${now.date}(${wd(now.date)}) ${C.fmt12(now.min)}（AM 6:00 までは前日の深夜として扱う）`)
  lines.push(`${date === now.date ? '今日' : date}(${wd(date)}) の予定 — 時間帯 ${C.fmtRange(d.rangeStart, d.rangeEnd)}、状態 ${DAY_STATUS_LABEL[d.status]}`)
  if (!d.activities.length) lines.push('- （予定なし）')
  for (const a of d.activities) {
    const m = C.metrics(a)
    const actual = a.actualStart != null ? ` / 実績 ${C.fmtRange(a.actualStart, a.actualEnd)}（${C.fmtDiff(m.diff)}）` : ''
    lines.push(`- ${C.fmtRange(a.planStart, a.planEnd)} ${a.title}${a.ruleId ? '（繰り返し）' : ''}${actual}${a.status ? ` ${STATUS_LABEL[a.status]}` : ''} [id ${a.id.slice(0, 8)}]`)
  }
  const from = date === now.date ? Math.ceil(now.min / C.STEP) * C.STEP : d.rangeStart
  const free = freeSlots(d, from)
  lines.push(`空き時間: ${free.length ? free.map((f) => `${C.fmtRange(f.start, f.end)}（${f.end - f.start}分）`).join('、') : 'なし'}`)
  if (date === now.date) {
    const t = C.materialize(store, C.addDays(date, 1))
    lines.push(`明日: ${t.activities.length ? t.activities.map((a) => `${C.fmt12(a.planStart)} ${a.title}`).join('、') : '予定なし'}`)
    const carry = carryovers(store, date)
    if (carry.length) lines.push(`持ち越し候補（直近${CARRYOVER_DAYS}日で完了していない予定）:`, ...carry)
  }
  const rules = store.rules ?? []
  if (rules.length) {
    lines.push('繰り返し:', ...rules.map((r) => `- ${r.title} ${C.fmtRange(r.start, r.end)}（${r.weekdays.length === 7 ? '毎日' : r.weekdays.map((w) => WEEKDAY_CHARS[w]).join('')}）`))
  }
  lines.push(`画面: ${UI_URL}（開発サーバーを起動しているとき）`)
  return lines.join('\n')
}

// ---- まとめて入れる（plan） ----

interface PlanItem {
  title: string
  start: string
  end: string
  memo?: string
  repeat?: string
}
interface PlanFile {
  range?: { start: string; end: string }
  replace?: boolean
  activities: PlanItem[]
}

function readPlan(file: string | undefined): PlanFile {
  if (!file) throw new UsageError('--file に計画の JSON を指定してください（- で標準入力）')
  const text = file === '-' ? readFileSync(0, 'utf8') : readFileSync(file, 'utf8')
  const plan = JSON.parse(text)
  if (!Array.isArray(plan?.activities)) throw new UsageError('計画の JSON には activities の配列が必要です（例: {"activities":[{"title":"…","start":"09:00","end":"10:00"}]}）')
  return plan
}

function applyPlan(date: string, plan: PlanFile, dryRun: boolean, replaceFlag: boolean) {
  const replace = replaceFlag || !!plan.replace
  const errors: string[] = []
  const items = plan.activities.map((p, i) => {
    try {
      if (!p.title?.trim()) throw new UsageError('title がありません')
      const start = parseTime(p.start, '開始')
      const end = parseTime(p.end, '終了')
      if (end <= start) throw new UsageError(`終了（${p.end}）が開始（${p.start}）より前です`)
      return { ...p, startMin: start, endMin: end, weekdays: p.repeat ? parseWeekdays(p.repeat) : [] }
    } catch (e) {
      errors.push(`${i + 1}件目「${p.title ?? ''}」: ${(e as Error).message}`)
      return null
    }
  })
  if (errors.length) throw new UsageError(`計画に誤りがあります:\n${errors.join('\n')}`)
  const valid = items.filter((x) => x != null)

  const current = C.materialize(readSaved().store, date)
  // 置き換えるときも、実績を記録済みの予定と繰り返し予定は残す（記録を消さないため）
  const removable = replace ? current.activities.filter((a) => !a.ruleId && a.actualStart == null) : []
  const kept = current.activities.filter((a) => !removable.includes(a))

  const warnings: string[] = []
  const blocks = [...kept.map((a) => ({ title: a.title, s: a.planStart, e: a.planEnd })), ...valid.map((v) => ({ title: v.title, s: v.startMin, e: v.endMin }))]
    .sort((x, y) => x.s - y.s)
  for (let i = 1; i < blocks.length; i++) {
    const prev = blocks[i - 1]
    if (blocks[i].s < prev.e) warnings.push(`時間が重なっています: 「${prev.title}」と「${blocks[i].title}」（${C.fmt12(blocks[i].s)}）`)
  }
  const range = plan.range ? { start: parseTime(plan.range.start, '時間帯の開始'), end: parseTime(plan.range.end, '時間帯の終了') } : null
  const rs = range?.start ?? current.rangeStart
  const re = range?.end ?? current.rangeEnd
  for (const v of valid) if (v.startMin < rs || v.endMin > re) warnings.push(`時間帯（${C.fmtRange(rs, re)}）の外です: 「${v.title}」${C.fmtRange(v.startMin, v.endMin)}`)

  const summary = {
    date,
    dryRun,
    remove: removable.map((a) => `${C.fmtRange(a.planStart, a.planEnd)} ${a.title}`),
    add: valid.map((v) => `${C.fmtRange(v.startMin, v.endMin)} ${v.title}${v.weekdays.length ? `（繰り返し: ${v.weekdays.map((w) => WEEKDAY_CHARS[w]).join('')}）` : ''}`),
    warnings,
  }
  if (dryRun) return summary

  const day = mutateDay(date, (d, s) => {
    if (range) {
      d.rangeStart = range.start
      d.rangeEnd = range.end
      if (d.rangeEnd <= d.rangeStart) throw new UsageError('時間帯の終了は開始より後にしてください')
    }
    for (const a of removable) C.removeActivity(d, a.id)
    for (const v of valid) {
      const act = C.addActivity(d, { title: v.title, memo: v.memo, start: v.startMin, end: v.endMin })
      if (v.weekdays.length) C.setRecurrence(s, d, act.id, v.weekdays)
    }
  })
  return { ...summary, day: describe(day) }
}

// ---- フック ----

// 予定の話をしていそうなときだけ文脈を足す（毎回足すと会話の邪魔になるため）
const PLAN_WORDS = /予定|計画|プラン|スケジュール|段取り|今日|明日|今週|振り返り|実績|やること|タスク|todo|plan|schedule|agenda/i

function readStdin(): Record<string, unknown> {
  try {
    return JSON.parse(readFileSync(0, 'utf8') || '{}')
  } catch {
    return {}
  }
}

function hook(kind: string | undefined) {
  const saved = readSaved()
  const hasData = Object.keys(saved.store.days).length > 0 || (saved.store.rules?.length ?? 0) > 0
  if (!hasData) return
  if (kind === 'prompt') {
    const prompt = String(readStdin().prompt ?? '')
    if (!PLAN_WORDS.test(prompt)) return
  } else if (kind !== 'session-start') {
    throw new UsageError('hook の種類は session-start か prompt です')
  }
  console.log(`日次プランナーの現在の状況（planner スキルで読み書きできます）:\n${brief(C.planningNow().date, saved.store)}`)
}

// ---- コマンド ----

function main(argv: string[]) {
  const { values: o, positionals: p } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      title: { type: 'string' },
      memo: { type: 'string' },
      start: { type: 'string' },
      end: { type: 'string' },
      repeat: { type: 'string' },
      status: { type: 'string' },
      shift: { type: 'boolean' },
      series: { type: 'boolean' },
      file: { type: 'string' },
      'dry-run': { type: 'boolean' },
      replace: { type: 'boolean' },
      from: { type: 'string' },
      to: { type: 'string' },
      help: { type: 'boolean', short: 'h' },
    },
  })
  const [cmd, ...rest] = p
  if (!cmd || o.help) return console.log(HELP)

  switch (cmd) {
    case 'where':
      return out({ data: dataPath(), ui: UI_URL })
    case 'brief':
      return console.log(brief(parseDate(rest[0]), readSaved().store))
    case 'show': {
      const from = parseDate(rest[0])
      const store = readSaved().store
      if (!o.to) return out(describe(C.materialize(store, from)))
      const to = parseDate(o.to)
      const days: ReturnType<typeof describe>[] = []
      for (let d = from; d <= to; d = C.addDays(d, 1)) days.push(describe(C.materialize(store, d)))
      return out(days)
    }
    case 'rules':
      return out((readSaved().store.rules ?? []).map((r) => ({ ...r, start: C.fmt(r.start), end: C.fmt(r.end), weekdays: r.weekdays.map((w) => WEEKDAY_CHARS[w]).join('') })))
    case 'add': {
      const date = parseDate(rest[0])
      if (!o.title) throw new UsageError('--title を指定してください')
      const start = parseTime(o.start, '開始')
      const end = parseTime(o.end, '終了')
      const weekdays = o.repeat ? parseWeekdays(o.repeat) : []
      let id = ''
      const day = mutateDay(date, (d, s) => {
        const a = C.addActivity(d, { title: o.title, memo: o.memo, start, end })
        if (weekdays.length) C.setRecurrence(s, d, a.id, weekdays)
        id = a.id
      })
      return out({ added: id, day: describe(day) })
    }
    case 'update': {
      const date = parseDate(rest[0])
      const day = mutateDay(date, (d, s) => {
        const a = findActivity(d, rest[1])
        C.updateActivity(d, a.id, {
          title: o.title,
          memo: o.memo,
          planStart: o.start != null ? parseTime(o.start, '開始') : undefined,
          planEnd: o.end != null ? parseTime(o.end, '終了') : undefined,
        })
        // 繰り返し予定なら、変更をシリーズ（まだ確定していない日）にも反映する
        const rule = s.rules?.find((r) => r.id === a.ruleId)
        if (rule) C.setRecurrence(s, d, a.id, rule.weekdays)
      })
      return out(describe(day))
    }
    case 'remove': {
      const date = parseDate(rest[0])
      const day = mutateDay(date, (d, s) => {
        const a = findActivity(d, rest[1])
        if (o.series) C.setRecurrence(s, d, a.id, [])
        C.removeActivity(d, a.id)
      })
      return out(describe(day))
    }
    case 'repeat': {
      const date = parseDate(rest[0])
      if (rest[2] == null) throw new UsageError('曜日を指定してください（月水 / 1,3 / daily / weekdays / none）')
      const weekdays = parseWeekdays(rest[2])
      const day = mutateDay(date, (d, s) => C.setRecurrence(s, d, findActivity(d, rest[1]).id, weekdays))
      return out(describe(day))
    }
    case 'actual': {
      const date = parseDate(rest[0])
      const status = (o.status ?? 'done') as Status
      if (!STATUSES.includes(status)) throw new UsageError(`--status は ${STATUSES.join(' / ')} のどれかです`)
      const start = parseTime(o.start, '実績の開始')
      const end = parseTime(o.end, '実績の終了')
      let shifted = 0
      const day = mutateDay(date, (d) => {
        const a = findActivity(d, rest[1])
        // 終了がずれた分（前回の実績、なければ予定から）だけ、後続の実績をずらす
        const delta = end - (a.actualEnd ?? a.planEnd)
        C.updateActivity(d, a.id, { actualStart: start, actualEnd: end, status })
        if (o.shift && delta) shifted = C.shiftFollowing(d, a.id, delta)
      })
      return out({ shiftedFollowing: shifted, day: describe(day) })
    }
    case 'plan': {
      const date = parseDate(rest[0])
      return out(applyPlan(date, readPlan(o.file), !!o['dry-run'], !!o.replace))
    }
    case 'range': {
      const date = parseDate(rest[0])
      const start = parseTime(o.start, '時間帯の開始')
      const end = parseTime(o.end, '時間帯の終了')
      if (end <= start) throw new UsageError('時間帯の終了は開始より後にしてください')
      return out(describe(mutateDay(date, (d) => Object.assign(d, { rangeStart: start, rangeEnd: end }))))
    }
    case 'status': {
      const date = parseDate(rest[0])
      const status = rest[1] as DayStatus
      if (!DAY_STATUSES.includes(status)) throw new UsageError(`状態は ${DAY_STATUSES.join(' / ')} のどれかです`)
      return out(describe(mutateDay(date, (d) => (d.status = status))))
    }
    case 'csv':
      return console.log(C.toCSV(readSaved().store, o.from ? parseDate(o.from) : undefined, o.to ? parseDate(o.to) : undefined))
    case 'hook':
      return hook(rest[0])
    default:
      throw new UsageError(`知らないコマンドです: ${cmd}\n\n${HELP}`)
  }
}

try {
  main(process.argv.slice(2))
} catch (e) {
  // フックは失敗してもユーザーの入力を止めない（終了コード 0 で何も出さない）
  if (process.argv[2] === 'hook') process.exit(0)
  console.error(`エラー: ${(e as Error).message}`)
  process.exit(1)
}
