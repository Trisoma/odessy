/**
 * Spike-only: forward the runtime's existing live assistant frames to a loopback
 * socket so a non-Node client can render text before settlement.
 *
 * This plugin adds NO inference, NO loop and NO cancellation logic. It subscribes
 * the documented `agent/assistant-stream` event (process-local `start` / `chunk` /
 * `end` frames) and writes JSON lines to one authenticated TCP client. It is the
 * experiment that decides whether the live-delta gap in
 * docs/restructure/harness-integration-contract.md can be closed without a fork.
 *
 * Wire format, one JSON object per line:
 *   {"type":"hello","pid":123,"protocol":1}
 *   {"type":"frame","sessionId":"…","frame":{"type":"start"|"chunk"|"end", …},"t":1730000000000}
 *
 * The client must send {"token":"…"} as its first line. The socket binds the
 * configured host only, accepts one client, and is closed when the plugin unloads.
 */

import net from 'node:net'

/** Stable Cordis plugin name. */
export const name = 'odessy-stream-bridge'

/** No services are injected: listening for an event needs none. */
export const inject = []

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{host?: string, port?: number, token?: string}} config
 */
export function apply(ctx, config) {
  const host = config?.host ?? '127.0.0.1'
  const port = Number(config?.port ?? 0)
  const token = String(config?.token ?? '')
  if (!token) throw new Error('odessy-stream-bridge requires a non-empty token')
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error('odessy-stream-bridge requires a valid port')
  }

  /** @type {import('node:net').Socket | undefined} */
  let client
  let authenticated = false

  const send = (value) => {
    if (!client || !authenticated || client.destroyed) return
    try {
      client.write(JSON.stringify(value) + '\n')
    } catch {
      client.destroy()
    }
  }

  const server = net.createServer((socket) => {
    if (client !== undefined) {
      socket.destroy()
      return
    }
    client = socket
    authenticated = false
    let buffer = ''
    socket.setEncoding('utf8')
    socket.on('error', () => socket.destroy())
    socket.on('close', () => {
      if (client === socket) {
        client = undefined
        authenticated = false
      }
    })
    socket.on('data', (chunk) => {
      if (authenticated) return
      buffer += chunk
      const newline = buffer.indexOf('\n')
      if (newline < 0) {
        if (buffer.length > 4096) socket.destroy()
        return
      }
      const line = buffer.slice(0, newline)
      try {
        const hello = JSON.parse(line)
        if (hello?.token !== token) {
          socket.destroy()
          return
        }
      } catch {
        socket.destroy()
        return
      }
      authenticated = true
      socket.write(JSON.stringify({ type: 'hello', pid: process.pid, protocol: 1 }) + '\n')
    })
  })
  server.on('error', (error) => {
    ctx.logger?.warn?.('odessy-stream-bridge listen failed: %s', String(error))
  })

  ctx.effect(() => {
    server.listen(port, host)
    return () => {
      client?.destroy()
      client = undefined
      authenticated = false
      server.close()
    }
  })

  ctx.effect(() => ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    if (frame?.type === 'chunk') {
      const chunk = frame.chunk ?? {}
      const kind = String(chunk.type ?? '')
      // Forward visible text and reasoning only; finish/usage frames carry no text.
      if (kind !== 'text-delta' && kind !== 'reasoning-delta') return
      send({
        type: 'frame',
        sessionId: String(agent?.session?.id ?? ''),
        turn: frame.turn,
        step: frame.step,
        index: frame.index,
        chunk: { type: kind, text: chunk.text },
        t: Date.now(),
      })
      return
    }
    if (frame?.type === 'start' || frame?.type === 'end') {
      send({
        type: 'frame',
        sessionId: String(agent?.session?.id ?? ''),
        frame: {
          type: frame.type,
          attempt: frame.attempt,
          turn: frame.turn,
          step: frame.step,
          index: frame.index,
          target: frame.target,
        },
        t: Date.now(),
      })
    }
  }))
}
