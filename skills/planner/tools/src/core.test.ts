import { expect, test } from 'vitest'
import * as C from './core'

test('予定の開始は直前の終了を引き継ぐ', () => {
  const d = C.newDay('2026-11-11')
  C.addActivity(d, { title: '設計作業', end: '10:30' })
  expect(C.nextStart(d)).toBe(630)
  const b = C.addActivity(d, { title: 'レビュー', end: '11:00' })
  expect(C.fmt(b.planStart)).toBe('10:30')
  expect(() => C.addActivity(d, { start: '12:00', end: '11:00' })).toThrow()
})

test('差分: 予定60分→実績90分は+30、45分は−15', () => {
  const d = C.newDay('2026-11-11')
  const a = C.addActivity(d, { start: '09:00', end: '10:00' })
  C.updateActivity(d, a.id, { actualStart: '09:00', actualEnd: '10:30' })
  expect(C.metrics(a).diff).toBe(30)
  C.updateActivity(d, a.id, { actualStart: '09:15', actualEnd: '10:00' })
  expect([C.metrics(a).diff, C.metrics(a).startDelay]).toEqual([-15, 15])
  expect(C.fmtDiff(-15)).toBe('−15分')
  expect(a.planEnd).toBe(600) // 実績を編集しても予定は変わらない
  expect(() => C.updateActivity(d, a.id, { actualEnd: '09:00' })).toThrow()
})

test('後続の一括ずらし (A延長 → B,Cを+30分)', () => {
  const d = C.newDay('2026-11-11')
  const a = C.addActivity(d, { title: 'A', start: '09:00', end: '10:00' })
  const b = C.addActivity(d, { title: 'B', end: '11:00' })
  const c = C.addActivity(d, { title: 'C', end: '12:00' })
  C.updateActivity(d, a.id, { actualStart: '09:00', actualEnd: '10:30' })
  expect(C.shiftFollowing(d, a.id, 30)).toBe(2)
  expect([b, c].map((x) => [C.fmt(x.actualStart), C.fmt(x.actualEnd)])).toEqual([['10:30', '11:30'], ['11:30', '12:30']])
  expect(C.fmt(b.planStart)).toBe('10:00')
})

test('CSV はカンマ・改行・引用符をエスケープし期間で絞る', () => {
  const d = C.newDay('2026-11-11')
  C.addActivity(d, { title: 'a,"b"', memo: '1\n2', start: '09:00', end: '09:15' })
  const csv = C.toCSV({ days: { '2026-11-11': d, '2026-12-01': C.newDay('2026-12-01') } }, '2026-11-01', '2026-11-30')
  expect(csv).toContain('"a,""b"""')
  expect(csv).toContain('"1\n2"')
  expect(csv.split('\r\n')).toHaveLength(2)
})

test('円グラフ配分は同タイトルを合算し実績を優先', () => {
  const d = C.newDay('2026-11-11')
  const a = C.addActivity(d, { title: 'X', start: '09:00', end: '10:00' })
  C.addActivity(d, { title: 'X', end: '10:30' })
  C.updateActivity(d, a.id, { actualStart: '09:00', actualEnd: '09:30' })
  expect(C.distribution(d).map((x) => [x.title, x.minutes])).toEqual([['X', 60]])
  expect(C.conicGradient(C.newDay('x'))).toBeUndefined()
})

test('新しいタイトルには順に別の色を割り当て、既存の色は変えない', () => {
  const d = C.newDay('2026-11-11')
  for (const t of ['設計作業', 'レビュー', 'メール']) C.addActivity(d, { title: t, end: C.nextStart(d) + 30 })
  const colors = C.assignColors({ days: { [d.date]: d }, colors: { メール: 0 } })
  expect(colors).toEqual({ メール: 0, 設計作業: 1, レビュー: 2 })
  expect(new Set(Object.keys(colors).map((t) => C.colorOf(t, colors))).size).toBe(3)
  expect(C.assignColors({ days: { [d.date]: d }, colors: { 設計作業: 0, メール: 275 } })).toEqual({ 設計作業: 0, レビュー: 1, メール: 2 })
})

test('繰り返し: 指定曜日に現れ、この日だけ削除・シリーズ編集・終了ができる', () => {
  const store: C.Store = { days: {} }
  const mon = '2026-11-16' // 月曜
  expect(C.weekdayOf(mon)).toBe(1)
  expect(C.monthCells('2026-11').slice(0, 1)).toEqual(['2026-11-01']) // 11/1 は日曜
  expect(C.monthCells('2026-12').filter(Boolean)).toHaveLength(31)
  expect(C.addMonths('2026-12', 1)).toBe('2027-01')
  const d = C.materialize(store, mon)
  const a = C.addActivity(d, { title: '朝会', start: '09:00', end: '09:15' })
  C.setRecurrence(store, d, a.id, [1, 3]) // 月・水
  store.days[mon] = d

  const wed = C.materialize(store, '2026-11-18')
  expect(wed.activities.map((x) => [x.title, C.fmt(x.planStart), x.ruleId])).toEqual([['朝会', '09:00', a.ruleId]])
  expect(C.materialize(store, '2026-11-17').activities).toHaveLength(0) // 火曜
  expect(C.materialize(store, '2026-11-11').activities).toHaveLength(0) // 開始日より前
  expect(C.materialize(store, mon).activities).toHaveLength(1) // 保存済みの日に重複しない

  // この日だけ削除
  C.removeActivity(wed, wed.activities[0].id)
  store.days[wed.date] = wed
  expect(C.materialize(store, '2026-11-18').activities).toHaveLength(0)
  expect(C.materialize(store, '2026-11-25').activities).toHaveLength(1)

  // シリーズの時刻変更は未保存の日に反映
  C.updateActivity(d, a.id, { planStart: '10:00', planEnd: '10:30' })
  C.setRecurrence(store, d, a.id, [1])
  expect(C.materialize(store, '2026-11-23').activities.map((x) => C.fmt(x.planStart))).toEqual(['10:00'])
  expect(C.materialize(store, '2026-11-25').activities).toHaveLength(0)

  // CSV は期間内の繰り返しも含む
  expect(C.toCSV(store, '2026-11-16', '2026-11-30').split('\r\n')).toHaveLength(4) // 見出し + 16, 23, 30

  // 曜日を空にすると終了し、この日の予定は単発で残る
  C.setRecurrence(store, d, a.id, [])
  expect(store.rules).toEqual([])
  expect(a.ruleId).toBeUndefined()
  expect(C.materialize(store, '2026-11-23').activities).toHaveLength(0)
})

test('1日は AM 6:00〜翌 AM 6:00。深夜をまたぐ予定も扱える', () => {
  expect(C.toMin('06:00')).toBe(360)
  expect(C.toMin('05:59')).toBe(1799) // 翌朝
  expect(C.toMin('00:00')).toBe(1440)
  expect(() => C.toMin('24:00')).toThrow()
  expect([360, 720, 780, 1439, 1500].map(C.fmt12)).toEqual(['AM 6:00', 'PM 12:00', 'PM 1:00', 'PM 11:59', 'AM 1:00'])
  expect(C.fmt(1500)).toBe('01:00')
  expect([C.fmtRange(540, 630), C.fmtRange(690, 780), C.fmtRange(1080, 1650), C.fmtRange(1440, 1500), C.fmtRange(540, 1500)])
    .toEqual(['AM 9:00〜10:30', 'AM 11:30〜PM 1:00', 'PM 6:00〜AM 3:30', 'AM 12:00〜1:00', 'AM 9:00〜AM 1:00'])
  const d = C.newDay('2026-11-11')
  const a = C.addActivity(d, { title: '夜勤', start: '23:00', end: '01:30' })
  expect(C.metrics(a).planDur).toBe(150)
})

test('週表示: 日曜始まりの7日、6時より前は前日、重なる予定は横に並べる', () => {
  expect(C.weekDates('2026-11-11')).toEqual(['2026-11-08', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13', '2026-11-14'])
  expect(C.weekDates('2026-11-29').at(-1)).toBe('2026-12-05')
  expect(C.planningNow(new Date(2026, 10, 12, 1, 30))).toEqual({ date: '2026-11-11', min: 1530 })
  expect(C.planningNow(new Date(2026, 10, 12, 9, 0))).toEqual({ date: '2026-11-12', min: 540 })
  const acts = [{ planStart: 540, planEnd: 600 }, { planStart: 570, planEnd: 630 }, { planStart: 600, planEnd: 660 }]
  const { placed, count } = C.lanes(acts)
  expect([placed.map((p) => p.lane), count]).toEqual([[0, 1, 0], 2])
})
