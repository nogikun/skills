// 開発サーバーに予定データの API を足す Vite プラグイン。
//   GET /api/store            → { rev, store }
//   PUT /api/store {baseRev, store} → { rev }。baseRev が古ければ 409 と最新の { rev, store }
// ファイルが変わったら（CLI や AI が書いたときも）ブラウザに 'planner:changed' を送り、画面を最新にする。
import type { IncomingMessage, ServerResponse } from 'node:http'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import type { Plugin } from 'vite'
import { ConflictError, dataPath, readSaved, writeSaved } from './storeFile.ts'

const MAX_BODY = 5 * 1024 * 1024 // 5MB。1年分の予定でも数百KB程度なので、それを大きく超える送信は拒否する

function readBody(req: IncomingMessage) {
  return new Promise<string>((ok, ng) => {
    let size = 0
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => {
      size += c.length
      if (size > MAX_BODY) {
        ng(new Error('送信されたデータが大きすぎます'))
        req.destroy()
      } else chunks.push(c)
    })
    req.on('end', () => ok(Buffer.concat(chunks).toString('utf8')))
    req.on('error', ng)
  })
}

function send(res: ServerResponse, status: number, body: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.end(JSON.stringify(body))
}

async function handle(req: IncomingMessage, res: ServerResponse) {
  try {
    if (req.method === 'GET') return send(res, 200, readSaved())
    if (req.method !== 'PUT') return send(res, 405, { error: 'GET か PUT だけを受け付けます' })
    const body = JSON.parse(await readBody(req))
    if (typeof body?.baseRev !== 'number' || typeof body?.store?.days !== 'object' || body.store.days === null) {
      return send(res, 400, { error: '{ baseRev: number, store: { days: {...} } } の形で送ってください' })
    }
    return send(res, 200, { rev: writeSaved(body.store, body.baseRev).rev })
  } catch (e) {
    if (e instanceof ConflictError) return send(res, 409, e.current)
    return send(res, 500, { error: (e as Error).message })
  }
}

export default function plannerApi(): Plugin {
  return {
    name: 'planner-api',
    configureServer(server) {
      server.middlewares.use('/api/store', (req, res) => void handle(req, res))
      const file = resolve(dataPath())
      mkdirSync(dirname(file), { recursive: true })
      server.watcher.add(file)
      const notify = (changed: string) => {
        if (resolve(changed) !== file) return
        try {
          server.ws.send({ type: 'custom', event: 'planner:changed', data: { rev: readSaved().rev } })
        } catch {
          // 書き込みの途中で読んだときなど。次の変更の通知で追いつく
        }
      }
      server.watcher.on('add', notify)
      server.watcher.on('change', notify)
    },
    configurePreviewServer(server) {
      server.middlewares.use('/api/store', (req, res) => void handle(req, res))
    },
  }
}
