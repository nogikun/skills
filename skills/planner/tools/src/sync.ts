// 予定データのファイル（~/.planner/store.json）との同期。AI 用の CLI も同じファイルを読み書きする。
// 開発サーバーの /api/store が使えないとき（静的に開いたときなど）は null を返し、画面は localStorage だけで動く。
import { notify } from './ask'
import type { Store } from './core'

interface Saved {
  rev: number
  store: Store
}

type OnRemote = (store: Store) => void

let rev = 0 // 最後にサーバーと一致していた版
let inflight = false
let pending: Store | null = null
let onRemote: OnRemote = () => {}
let connecting: Promise<((store: Store) => void) | null> | null = null

async function fetchSaved(): Promise<Saved | null> {
  try {
    const r = await fetch('/api/store')
    return r.ok ? await r.json() : null
  } catch {
    return null
  }
}

async function put(store: Store) {
  inflight = true
  try {
    const r = await fetch('/api/store', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ baseRev: rev, store }),
    })
    const body = await r.json()
    if (r.status === 409) {
      // ほかで先に書かれていた。最新を表示し、上書きはしない
      rev = body.rev
      onRemote(body.store)
      notify('ほかの場所（AI など）で予定が更新されていたため、最新の内容を表示しました。直前の変更は保存されていません。')
    } else if (!r.ok) {
      notify(`予定を保存できませんでした: ${body.error ?? r.status}`)
    } else {
      rev = body.rev
    }
  } catch (e) {
    notify(`予定を保存できませんでした（開発サーバーが止まっているかもしれません）: ${(e as Error).message}`)
  } finally {
    inflight = false
    if (pending) {
      const next = pending
      pending = null
      await put(next)
    }
  }
}

// 保存は1つずつ順番に送る（同時に送ると同じ版からの保存がぶつかるため）。待っている間は最新の1件だけを残す
function save(store: Store) {
  if (inflight) pending = store
  else void put(store)
}

// 接続して、保存用の関数を返す。React の StrictMode で2回呼ばれても接続は1回にする
export function connect(local: Store, remote: OnRemote) {
  onRemote = remote
  connecting ??= (async () => {
    const saved = await fetchSaved()
    if (!saved) return null
    rev = saved.rev
    const serverEmpty = !Object.keys(saved.store.days).length && !saved.store.rules?.length
    // 初めてファイルを使うときは、これまでブラウザに保存していた予定をファイルへ移す
    if (rev === 0 && serverEmpty && Object.keys(local.days).length) await put(local)
    else onRemote(saved.store)
    // ファイルが変わった（AI が予定を入れた）ら、最新を読み込む
    import.meta.hot?.on('planner:changed', async (d: { rev: number }) => {
      if (d.rev === rev || inflight) return
      const next = await fetchSaved()
      if (next && next.rev !== rev) {
        rev = next.rev
        onRemote(next.store)
      }
    })
    return save
  })()
  return connecting
}
