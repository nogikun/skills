import { useEffect, useRef } from 'react'

export interface MenuItem {
  label: string
  onSelect: () => void
  danger?: boolean
}

export interface MenuState {
  x: number
  y: number
  items: MenuItem[]
}

// 右クリックメニュー。外側のクリック、Esc、スクロールで閉じる。↑↓で項目を移動する
export default function ContextMenu({ menu, onClose }: { menu: MenuState | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })

  useEffect(() => {
    if (!menu) return
    const el = ref.current!
    el.querySelector('button')?.focus()
    const outside = (e: Event) => !el.contains(e.target as Node) && close.current()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current()
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      e.preventDefault()
      const items = [...el.querySelectorAll('button')]
      const i = items.indexOf(document.activeElement as HTMLButtonElement)
      items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus()
    }
    const dismiss = () => close.current()
    document.addEventListener('pointerdown', outside, true)
    window.addEventListener('keydown', key)
    window.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    return () => {
      document.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('keydown', key)
      window.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
    }
  }, [menu])

  if (!menu) return null
  // 画面の端からはみ出さないようにする
  const left = Math.min(menu.x, window.innerWidth - 200)
  const top = Math.min(menu.y, window.innerHeight - menu.items.length * 36 - 16)
  return (
    <div ref={ref} className="ctxmenu" role="menu" style={{ left, top }}>
      {menu.items.map((it) => (
        <button key={it.label} role="menuitem" className={it.danger ? 'danger' : ''}
          onClick={() => {
            close.current()
            it.onSelect()
          }}>
          {it.label}
        </button>
      ))}
    </div>
  )
}
