// CLI を実際に子プロセスで動かし、一時ファイルのデータで確かめる（利用者の ~/.planner には触れない）
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { beforeEach, expect, test } from 'vitest'
import { ConflictError, readSaved, writeSaved } from './server/storeFile.ts'

let dir = ''
let data = ''
const DATE = '2026-11-16' // 月曜
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'planner-test-'))
  data = join(dir, 'store.json')
})

const cli = (...args: string[]) => {
  const r = spawnSync('node', [join(import.meta.dirname, 'cli.ts'), ...args], { env: { ...process.env, PLANNER_DATA: data }, encoding: 'utf8' })
  return { code: r.status, out: r.stdout, err: r.stderr, json: () => JSON.parse(r.stdout) }
}

test('add と show: ファイルに版が付いて保存される', () => {
  const r = cli('add', DATE, '--title', '設計', '--start', '09:00', '--end', '10:30')
  expect(r.code).toBe(0)
  const show = cli('show', DATE).json()
  expect(show.activities.map((a: { title: string; label: string }) => [a.title, a.label])).toEqual([['設計', 'AM 9:00〜10:30']])
  expect(readSaved(data).rev).toBe(1)
})

test('plan: dry-run は書かずに重なりを知らせ、replace でも実績つきの予定は残す', () => {
  cli('add', DATE, '--title', '朝の作業', '--start', '09:00', '--end', '10:00')
  const recorded = cli('add', DATE, '--title', '記録済み', '--start', '10:00', '--end', '11:00').json().added
  cli('actual', DATE, recorded, '--start', '10:00', '--end', '11:00')
  const plan = join(dir, 'plan.json')
  writeFileSync(plan, JSON.stringify({ replace: true, activities: [
    { title: '資料作成', start: '10:30', end: '12:00' },
    { title: '朝会', start: '09:00', end: '09:15', repeat: '月水' },
  ] }))
  const dry = cli('plan', DATE, '--file', plan, '--dry-run').json()
  expect(dry.remove).toEqual(['AM 9:00〜10:00 朝の作業'])
  expect(dry.warnings.join()).toContain('重なっています')
  expect(readSaved(data).rev).toBe(3) // dry-run は書かない
  const applied = cli('plan', DATE, '--file', plan).json()
  expect(applied.day.activities.map((a: { title: string }) => a.title)).toEqual(['朝会', '記録済み', '資料作成'])
  expect(cli('show', '2026-11-18').json().activities.map((a: { title: string }) => a.title)).toEqual(['朝会']) // 水曜にも繰り返す
})

test('plan: 誤りはまとめて知らせて何も書かない', () => {
  const plan = join(dir, 'bad.json')
  writeFileSync(plan, JSON.stringify({ activities: [{ title: 'A', start: '10:00', end: '09:00' }, { title: '', start: '09:00', end: '10:00' }] }))
  const r = cli('plan', DATE, '--file', plan)
  expect(r.code).toBe(1)
  expect(r.err).toContain('1件目')
  expect(r.err).toContain('2件目')
  expect(readSaved(data).rev).toBe(0)
})

test('actual --shift: 延びた分だけ後続の実績をずらす', () => {
  const a = cli('add', DATE, '--title', 'A', '--start', '09:00', '--end', '10:00').json().added
  cli('add', DATE, '--title', 'B', '--start', '10:00', '--end', '11:00')
  const r = cli('actual', DATE, a.slice(0, 6), '--start', '09:00', '--end', '10:30', '--shift').json()
  expect(r.shiftedFollowing).toBe(1)
  expect(r.day.activities[1].actual).toMatchObject({ start: '10:30', end: '11:30' })
})

test('id が見つからないときは、その日の予定を一覧にして知らせる', () => {
  cli('add', DATE, '--title', '設計', '--start', '09:00', '--end', '10:00')
  const r = cli('remove', DATE, 'zzzz')
  expect(r.code).toBe(1)
  expect(r.err).toContain('AM 9:00〜10:00 設計')
})

test('hook prompt: 予定の話のときだけ文脈を出す。失敗しても入力を止めない', () => {
  cli('add', 'today', '--title', '設計', '--start', '09:00', '--end', '10:00')
  const run = (prompt: string) =>
    execFileSync('node', [join(import.meta.dirname, 'cli.ts'), 'hook', 'prompt'], { input: JSON.stringify({ prompt }), env: { ...process.env, PLANNER_DATA: data }, encoding: 'utf8' })
  expect(run('今日の予定を組み直して')).toContain('設計')
  expect(run('このバグを直して')).toBe('')
  writeFileSync(data, '{broken')
  expect(run('今日の予定は？')).toBe('') // 壊れたデータでも終了コード 0
})

test('古い版からの保存は拒否する（画面と AI の同時書き込みで上書きしない）', () => {
  writeSaved({ days: {} }, 0, data)
  expect(() => writeSaved({ days: {} }, 0, data)).toThrow(ConflictError)
  expect(JSON.parse(readFileSync(data, 'utf8')).rev).toBe(1)
})
