import { ask } from './ask'
import * as C from './core'
import type { Activity, DayPlan } from './core'
import type { Mutator } from './App'
import type { MenuItem, MenuState } from './ContextMenu'

// 右クリックの位置を取る。キーボード（ContextMenu キー）で開いたときは座標が 0 なので要素の位置を使う
export function menuAt(e: React.MouseEvent, items: MenuItem[]): MenuState {
  e.preventDefault()
  e.stopPropagation()
  if (e.clientX || e.clientY) return { x: e.clientX, y: e.clientY, items }
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  return { x: r.left + 8, y: r.top + 8, items }
}

type BoundMutate = (fn: Mutator) => DayPlan | null

// 確認してから削除する。series=true なら繰り返しルールごと（確定済みの過去の日の予定は残る）
export async function removeWithConfirm(a: Activity, series: boolean, mutate: BoundMutate) {
  const msg = series ? `「${a.title}」の繰り返しをすべて削除しますか？` : a.ruleId
    ? `この日の「${a.title}」だけを削除しますか？` : `「${a.title}」を削除しますか？`
  if (!(await ask(msg, { ok: '削除', danger: true }))) return false
  return !!mutate((d, s) => {
    if (series) C.setRecurrence(s, d, a.id, [])
    C.removeActivity(d, a.id)
  })
}

// 予定を右クリックしたときのメニュー
export function activityMenu(a: Activity, h: { edit: () => void; open?: () => void; mutate: BoundMutate }): MenuItem[] {
  return [
    { label: '編集', onSelect: h.edit },
    ...(h.open ? [{ label: '日表示で開く', onSelect: h.open }] : []),
    { label: a.ruleId ? 'この日だけ削除' : '削除', danger: true, onSelect: () => void removeWithConfirm(a, false, h.mutate) },
    ...(a.ruleId ? [{ label: '繰り返しを削除', danger: true, onSelect: () => void removeWithConfirm(a, true, h.mutate) }] : []),
  ]
}
