// 確認・通知のダイアログ。ネイティブの confirm()/alert() は埋め込みブラウザなどで無効にされ、
// 何も表示せず false を返すことがあるため、<dialog> で出す。
export function ask(message: string, opts: { ok?: string; danger?: boolean; cancel?: boolean } = {}): Promise<boolean> {
  const { ok = 'OK', danger = false, cancel = true } = opts
  return new Promise((resolve) => {
    const d = document.createElement('dialog')
    d.className = 'ask'
    const p = document.createElement('p')
    p.textContent = message
    const row = document.createElement('div')
    row.className = 'row'
    const button = (label: string, value: string, cls = '') => {
      const b = document.createElement('button')
      b.type = 'button'
      b.textContent = label
      b.className = cls
      b.onclick = () => d.close(value)
      return b
    }
    const yes = button(ok, 'ok', danger ? 'danger' : 'primary')
    row.append(...(cancel ? [button('キャンセル', 'cancel')] : []), yes)
    d.append(p, row)
    // Esc で閉じたときは returnValue が空になり、キャンセル扱い
    d.addEventListener('close', () => {
      d.remove()
      resolve(d.returnValue === 'ok')
    })
    document.body.append(d)
    d.showModal()
    yes.focus()
  })
}

export const notify = (message: string) => void ask(message, { cancel: false })
