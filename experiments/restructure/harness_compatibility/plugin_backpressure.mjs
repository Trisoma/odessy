/**
 * Proof that the bridge plugin bounds its own write queue (prompt 05).
 *
 * The plugin runs inside the Harness process, so an unbounded write queue would grow the
 * runtime's memory whenever a client stopped reading. This script loads the *real* plugin
 * module with a stub context, connects a client that does not read, pushes far more frames
 * than the queue bound, and then checks what the client ends up with:
 *
 *   - it received fewer frames than were emitted, and
 *   - a `{"type":"dropped","frames":N}` notice accounts for the difference, and
 *   - the connection survived: a frame emitted after the drain still arrives.
 *
 * Usage (workspace-local Node, no dependencies):
 *
 *   node plugin_backpressure.mjs [--frames 8000] [--json <path>]
 *
 * Exit code is 1 if any check fails. This measures the plugin in isolation; it is not a
 * measurement of the pinned runtime, which `smoke_p05.py` covers.
 */

import net from 'node:net'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { writeFileSync } from 'node:fs'

const HERE = path.dirname(fileURLToPath(import.meta.url))

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback
}

// The plugin under test is the one Odessy ships, resolved from the package rather than
// from a copy under this experiment directory. `--plugin` exists for a deliberate A/B run.
const PLUGIN = arg('plugin', path.join(HERE, '..', '..', '..', 'src', 'odessy',
  'runtime_backends', 'harness', 'bundle', 'odessy-stream-bridge', 'lib', 'index.js'))
const TOKEN = 't0ken-backpressure'
const TEXT = 'x'.repeat(400)

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function freePort() {
  const probe = net.createServer()
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve))
  const port = probe.address().port
  await new Promise((resolve) => probe.close(resolve))
  return port
}

async function connect(port, deadlineMs = 5000) {
  const until = Date.now() + deadlineMs
  for (;;) {
    try {
      return await new Promise((resolve, reject) => {
        const socket = net.connect(port, '127.0.0.1')
        socket.once('connect', () => resolve(socket))
        socket.once('error', reject)
      })
    } catch (error) {
      if (Date.now() > until) throw error
      await sleep(50)
    }
  }
}

async function main() {
  const frames = Number(arg('frames', '8000'))
  const checks = []
  const check = (name, passed, detail) => checks.push({ name, passed, detail: String(detail) })

  const plugin = await import(pathToFileURL(PLUGIN).href)
  const handlers = {}
  const cleanups = []
  const ctx = {
    effect: (fn) => {
      const cleanup = fn()
      if (typeof cleanup === 'function') cleanups.push(cleanup)
      return cleanup
    },
    on: (event, handler) => {
      handlers[event] = handler
      return () => { delete handlers[event] }
    },
    logger: { warn: () => {} },
  }

  const port = await freePort()
  plugin.apply(ctx, { host: '127.0.0.1', port, token: TOKEN })
  check('the plugin registers its listener', Boolean(handlers['agent/assistant-stream']),
        Object.keys(handlers).join(','))

  const client = await connect(port)
  client.setEncoding('utf8')
  const lines = []
  let buffer = ''
  client.on('data', (data) => {
    buffer += data
    let index = buffer.indexOf('\n')
    while (index >= 0) {
      if (buffer.slice(0, index).trim()) lines.push(buffer.slice(0, index))
      buffer = buffer.slice(index + 1)
      index = buffer.indexOf('\n')
    }
  })
  client.write(JSON.stringify({ token: TOKEN }) + '\n')
  const helloDeadline = Date.now() + 5000
  while (!lines.some((line) => line.includes('"hello"')) && Date.now() < helloDeadline) {
    await sleep(20)
  }
  check('the client is greeted', lines.some((line) => line.includes('"hello"')),
        lines.length ? lines[0].slice(0, 80) : 'no greeting')

  // A consumer that stops reading: everything the plugin writes now queues up.
  client.pause()
  const handler = handlers['agent/assistant-stream']
  const emit = (index, text) => handler({
    agent: { session: { id: 'session-1' } },
    frame: { type: 'chunk', turn: 1, step: 0, index, chunk: { type: 'text-delta', text } },
  })
  for (let index = 0; index < frames; index += 1) emit(index, TEXT)
  await sleep(300)                       // the socket buffer fills; drops begin
  client.resume()

  // Let the queue drain, then prove the connection is still usable.
  let previous = -1
  while (lines.length !== previous) {
    previous = lines.length
    await sleep(300)
  }
  emit(frames, 'SENTINEL')
  const sentinelDeadline = Date.now() + 5000
  while (!lines.some((line) => line.includes('SENTINEL')) && Date.now() < sentinelDeadline) {
    await sleep(20)
  }

  const parsed = lines.map((line) => {
    try { return JSON.parse(line) } catch { return { type: 'unparsable' } }
  })
  const received = parsed.filter((entry) => entry.type === 'frame').length
  const notices = parsed.filter((entry) => entry.type === 'dropped')
  const dropped = notices.reduce((total, entry) => total + Number(entry.frames || 0), 0)

  check('the burst exceeded the queue bound', dropped > 0,
        `emitted ${frames}, delivered ${received}, dropped ${dropped}`)
  check('loss is reported to the consumer', notices.length >= 1,
        `${notices.length} notice(s)`)
  check('every emitted frame is either delivered or counted',
        received >= 0 && dropped > 0 && received + dropped === frames + 1,
        `delivered + dropped = ${received + dropped} of ${frames + 1}`)
  check('the connection survives a stalled consumer',
        parsed.some((entry) => entry.type === 'frame' && entry.chunk?.text === 'SENTINEL'),
        `${lines.length} lines seen`)
  check('frames keep their session identity',
        parsed.filter((entry) => entry.type === 'frame')
          .every((entry) => entry.sessionId === 'session-1'), 'session-1')

  client.destroy()
  for (const cleanup of cleanups.reverse()) {
    try { cleanup() } catch { /* the listener is already closed */ }
  }

  const summary = { checks, failed: checks.filter((entry) => !entry.passed).map((e) => e.name) }
  const target = arg('json', '')
  if (target) writeFileSync(target, JSON.stringify(summary, null, 2), 'utf-8')
  process.stdout.write(JSON.stringify(summary, null, 2) + '\n')
  return summary.failed.length === 0 ? 0 : 1
}

main().then((code) => process.exit(code), (error) => {
  process.stderr.write(String(error?.stack || error) + '\n')
  process.exit(2)
})
