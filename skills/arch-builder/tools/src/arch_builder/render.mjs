// .drawio -> PNG (draw.io Desktop)。Desktop が無ければ vendor/drawio の変換器で SVG を出す。
//   node render.mjs <in.drawio> [out.png]
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const skill = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const vendor = resolve(skill, 'vendor/drawio/scripts')
const [input, output = input.replace(/\.drawio$/, '') + '.png'] = process.argv.slice(2)
if (!input) {
  console.error('usage: node render.mjs <in.drawio> [out.png]')
  process.exit(64)
}

const { detectDrawioDesktop, exportWithDrawioDesktop } = await import(pathToFileURL(resolve(vendor, 'runtime/desktop.js')).href)
if (detectDrawioDesktop()) {
  // scale 2: 批評担当が 48px アイコンのラベルまで読める解像度
  await exportWithDrawioDesktop({ inputFile: resolve(input), outputFile: resolve(output), format: 'png', scale: 2 })
  console.log(`wrote ${output}`)
} else {
  const { drawioToSvg } = await import(pathToFileURL(resolve(vendor, 'svg/drawio-to-svg.js')).href)
  const svgOut = output.replace(/\.png$/, '') + '.svg'
  writeFileSync(svgOut, drawioToSvg(readFileSync(input, 'utf8')))
  console.error(`warn: draw.io Desktop が無いので近似 SVG を出した (${svgOut})。アイコンの見た目は Desktop で最終確認する`)
  console.log(`wrote ${svgOut}`)
}
