import LiquidGlass from 'liquid-glass-react'

interface Props {
  width: string // CSS の長さ。中身の大きさから決まらないため、必ず具体的な長さを渡す（% は使えない）
  height: number
  radius: number
  elasticity?: number // カーソルに引かれて伸びる強さ。0 で動かない
  pressable?: boolean // 押したときに縮む表現を付ける（クリックは中の button が受ける）
  className?: string
  children: React.ReactNode
}

// liquid-glass-react の光の屈折するガラス。
// このライブラリは自分の中心を top/left 50% に置き、重ねた層を translate(-50%,-50%) で戻す作りのため、
// 大きさを決めた枠（.lg-slot）の中央に置いて、ふつうのレイアウトの流れに載せる。
// 屈折は Chromium 系のみで、Safari と Firefox ではぼかしだけになる。
export default function Glass({ width, height, radius, elasticity = 0, pressable, className = '', children }: Props) {
  return (
    <div className={`lg-slot ${className}`} style={{ width, height }}>
      <LiquidGlass
        cornerRadius={radius}
        padding="0"
        elasticity={elasticity}
        displacementScale={48}
        blurAmount={0.25}
        saturation={170}
        aberrationIntensity={1.5}
        // 押下の表現（縮む・光る）は onClick があるときだけ付くので、空の関数を渡す
        onClick={pressable ? () => {} : undefined}
        style={{ position: 'absolute', top: '50%', left: '50%' }}
      >
        <div className="lg-content" style={{ width, height, borderRadius: radius }}>
          {children}
        </div>
      </LiquidGlass>
    </div>
  )
}
