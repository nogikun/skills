// 予定データのファイル（UI の開発サーバーと AI 用の CLI で共有する）。
// 中身は { rev, store }。rev は保存のたびに 1 増え、古い rev からの保存は拒否して上書き事故を防ぐ。
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import type { Store } from '../src/core.ts'

export interface Saved {
  rev: number
  store: Store
}

// 既定は ~/.planner/store.json。スキルをどこに置いても同じデータを見るよう、スキルのフォルダの外に置く
export const dataPath = () => process.env.PLANNER_DATA || join(homedir(), '.planner', 'store.json')

export function readSaved(path = dataPath()): Saved {
  let text: string
  try {
    text = readFileSync(path, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return { rev: 0, store: { days: {} } }
    throw e
  }
  const data = JSON.parse(text)
  if (!data || typeof data.rev !== 'number' || typeof data.store?.days !== 'object') {
    throw new Error(`予定データの形式が正しくありません: ${path}`)
  }
  return data as Saved
}

export class ConflictError extends Error {
  current: Saved
  constructor(current: Saved) {
    super(`予定データがほかで更新されています（現在の rev ${current.rev}）`)
    this.current = current
  }
}

// baseRev が今のファイルの rev と同じときだけ保存する。途中で落ちても壊れないよう、一時ファイルに書いて置き換える
export function writeSaved(store: Store, baseRev: number, path = dataPath()): Saved {
  const current = readSaved(path)
  if (current.rev !== baseRev) throw new ConflictError(current)
  const next: Saved = { rev: current.rev + 1, store }
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.${process.pid}.tmp`
  writeFileSync(tmp, JSON.stringify(next, null, 2))
  renameSync(tmp, path)
  return next
}
