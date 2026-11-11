/**
 * Forward the runtime's existing live assistant frames to a loopback socket, answer the
 * runtime's existing `user-questions` seam over the same authenticated connection, and report
 * the tool surface the runtime actually attached.
 *
 * This plugin adds NO inference, NO agent loop and NO new question model. It uses three
 * documented extension points of the pinned runtime:
 *
 * - `agent/assistant-stream` (process-local start / chunk / end frames) is forwarded so a
 *   non-Node client can render text before settlement;
 * - `user-questions/request` is answered by the plugin, which asks the connected client and
 *   resolves the exact waiting tool invocation with the human's answer. The model-facing
 *   `ask_user_question` tool is the runtime's own `@deepseek-ai/dsh-tool-ask-user`, mounted
 *   by this bundle's patch; this plugin only supplies the answerer.
 * - `ctx.tools.schemas(scope)` — the runtime's documented, read-only view of the tools a scope
 *   can see — is read on request, so a reconnecting client can check that the tool servers it
 *   relies on are really attached instead of trusting that it asked for them. The plugin never
 *   registers, replaces or executes a tool.
 *
 * Wire format, one JSON object per line. Plugin -> client:
 *   {"type":"hello","pid":123,"protocol":1,"features":["questions","tools"]}
 *   {"type":"frame", …}                         live assistant frames (transient)
 *   {"type":"dropped","protocol":1,"frames":N}  frames lost to a slow reader
 *   {"type":"question","sessionId":"…","turn":1,"requestId":"…","questions":[…],"t":…}
 *   {"type":"tools","requestId":"…","available":true,"tools":["…"],"agents":1,"global":N,"t":…}
 *   {"type":"tools","requestId":"…","available":false,"reason":"…","t":…}
 * Client -> plugin (after the token line):
 *   {"type":"answer","requestId":"…","answers":[{"id":"q1","selected":["yes"],"custom":"…"}]}
 *   {"type":"cancel","requestId":"…","reason":"stopped"}
 *   {"type":"tools","requestId":"…"}
 *
 * The client must send {"token":"…"} as its first line. The socket binds the configured
 * host only, accepts one client, and is closed when the plugin unloads.
 *
 * Safety properties this file owns:
 * - a question is only answered while a connected, authenticated client exists; otherwise
 *   the request is delegated so the tool fails visibly instead of hanging;
 * - one answer per question request, matched by a unique id the plugin generated;
 * - an answer can only select labels the asker offered (custom text is passed through);
 * - cancellation, socket loss, disposal and timeout settle the waiter as an error — no
 *   answer is ever invented;
 * - the tool roster is read-only metadata (names only, never definitions or results), is only
 *   sent to the authenticated client, and an unavailable registry is reported as unavailable
 *   rather than as an empty roster;
 * - frames are transient: when the client stops reading, frames are dropped and counted,
 *   never buffered without limit.
 */

import net from 'node:net'
import { randomUUID } from 'node:crypto'

/** Stable Cordis plugin name. */
export const name = 'odessy-stream-bridge'

/**
 * No services are *required*: forwarding frames and answering questions need none, so a
 * runtime without the tools registry still loads this plugin. The registry is read
 * opportunistically (`ctx.get`) when a roster is requested.
 */
export const inject = []

/** Features announced in the greeting; the Python client gates capabilities on these. */
const FEATURES = ['questions', 'tools']

/**
 * Cap on bytes queued for one client but not yet accepted by the kernel. A consumer that
 * stops reading keeps at most this much in this process, plus the frame in flight.
 */
const MAX_QUEUED_BYTES = 1 << 20
/** Longest accepted client line (an answer or a cancel, never anything large). */
const MAX_CLIENT_LINE = 256 * 1024
/** Question request limits, mirrored by the Python side. */
const MAX_QUESTIONS = 8
const MAX_QUESTION_TEXT = 8192
const MAX_OPTIONS = 32
const MAX_LABEL = 1024
const MAX_PENDING = 4
/** Backstop when the client is gone but its socket has not been noticed yet. */
const DEFAULT_WAIT_MS = 300_000
/** Roster bounds: the union is de-duplicated, clipped and capped before it leaves this process. */
const MAX_TOOL_NAMES = 512
const MAX_TOOL_NAME = 200

const isRecord = (value) => typeof value === 'object' && value !== null && !Array.isArray(value)

const clip = (value, limit) => String(value ?? '').slice(0, limit)

/** A rejection the `user-questions` seam restores into its own error type. */
function questionError(message, code) {
  const error = new Error(message)
  error.name = 'UserQuestionError'
  error.code = code
  return error
}

/**
 * Validate one request. Returns `{questions}` or `{error}`.
 * `multiSelect` is refused explicitly: Odessy's clarification surface collects one answer
 * (a choice or free text), and pretending otherwise would silently drop part of a question.
 */
function validateQuestions(raw) {
  if (!Array.isArray(raw) || raw.length === 0) {
    return { error: questionError('ask_user_question requires at least one question', 'EMPTY_QUESTIONS') }
  }
  if (raw.length > MAX_QUESTIONS) {
    return { error: questionError(`at most ${MAX_QUESTIONS} questions may be asked at once`, 'TOO_MANY_QUESTIONS') }
  }
  const seen = new Set()
  const questions = []
  for (const item of raw) {
    if (!isRecord(item)) return { error: questionError('a question was not an object', 'BAD_QUESTION') }
    const id = item.id
    if (typeof id !== 'string' || !id.trim() || id.length > 128 || seen.has(id)) {
      return { error: questionError('each question needs a unique id of at most 128 characters', 'BAD_QUESTION') }
    }
    seen.add(id)
    const text = item.question
    if (typeof text !== 'string' || !text.trim() || text.length > MAX_QUESTION_TEXT) {
      return { error: questionError(`a question must be 1..${MAX_QUESTION_TEXT} characters`, 'BAD_QUESTION') }
    }
    if (item.multiSelect === true) {
      return { error: questionError(
        'Odessy cannot collect a multi-select answer; ask separate yes/no or single-choice questions instead',
        'UNSUPPORTED_MULTI_SELECT',
      ) }
    }
    const labels = []
    if (item.options !== undefined) {
      if (!Array.isArray(item.options) || item.options.length === 0 || item.options.length > MAX_OPTIONS) {
        return { error: questionError(`options must be 1..${MAX_OPTIONS} entries`, 'BAD_QUESTION') }
      }
      for (const option of item.options) {
        if (!isRecord(option) || typeof option.label !== 'string' || !option.label.trim()
          || option.label.length > MAX_LABEL) {
          return { error: questionError(`an option label must be 1..${MAX_LABEL} characters`, 'BAD_QUESTION') }
        }
        if (labels.includes(option.label)) {
          return { error: questionError('option labels must be distinct', 'BAD_QUESTION') }
        }
        labels.push(option.label)
      }
    }
    questions.push({
      id,
      question: text,
      labels,
      header: typeof item.header === 'string' ? clip(item.header, 80) : undefined,
      detail: typeof item.detail === 'string' ? clip(item.detail, MAX_QUESTION_TEXT) : undefined,
    })
  }
  return { questions }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{host?: string, port?: number, token?: string, questionTimeoutMs?: number}} config
 */
export function apply(ctx, config) {
  const host = config?.host ?? '127.0.0.1'
  const port = Number(config?.port ?? 0)
  const token = String(config?.token ?? '')
  const waitMs = Number(config?.questionTimeoutMs ?? DEFAULT_WAIT_MS)
  if (!token) throw new Error('odessy-stream-bridge requires a non-empty token')
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error('odessy-stream-bridge requires a valid port')
  }
  if (!Number.isFinite(waitMs) || waitMs <= 0) {
    throw new Error('odessy-stream-bridge requires a positive questionTimeoutMs')
  }

  /** @type {import('node:net').Socket | undefined} */
  let client
  let authenticated = false
  /** Frames dropped since the last notice, because the client was not reading. */
  let dropped = 0
  const pendingAuth = new Set()
  const flushDropped = () => {
    if (!client || !authenticated || client.destroyed || client.writableLength > MAX_QUEUED_BYTES || dropped === 0) return
    const lost = dropped
    dropped = 0
    try { client.write(JSON.stringify({ type: 'dropped', protocol: 1, frames: lost }) + '\n') }
    catch { client.destroy() }
  }
  /** `requestId -> {resolve, reject, timer, sessionId, turn, questions}`. */
  const pending = new Map()
  /** The runtime's own turn number for each live agent, from `agent/inbox/claimed`. */
  const turns = new WeakMap()

  const send = (value) => {
    if (!client || !authenticated || client.destroyed) return false
    if (client.writableLength > MAX_QUEUED_BYTES) {
      dropped += 1
      return false
    }
    try {
      flushDropped()
      client.write(JSON.stringify(value) + '\n')
      return true
    } catch {
      client.destroy()
      return false
    }
  }

  const settle = (requestId, finish) => {
    const entry = pending.get(requestId)
    if (entry === undefined) return
    pending.delete(requestId)
    if (entry.timer !== null) clearTimeout(entry.timer)
    if (entry.onAbort !== undefined) entry.signal?.removeEventListener?.('abort', entry.onAbort)
    finish(entry)
  }

  const cancelAll = (code, message) => {
    for (const requestId of [...pending.keys()]) {
      settle(requestId, (entry) => entry.reject(questionError(message, code)))
    }
  }

  const answer = (message) => {
    const entry = pending.get(message.requestId)
    if (entry === undefined) return // duplicate, stale, or never asked by us
    const supplied = message.answers
    if (!Array.isArray(supplied) || supplied.length !== entry.questions.length) return
    const byId = new Map(entry.questions.map((question) => [question.id, question]))
    const answers = []
    for (const item of supplied) {
      if (!isRecord(item)) return
      const question = byId.get(item.id)
      if (question === undefined) return
      const selected = Array.isArray(item.selected) ? item.selected : []
      if (!selected.every((label) => typeof label === 'string' && label.length <= MAX_LABEL)) return
      // An answer cannot name an option the asker never offered.
      if (!selected.every((label) => question.labels.includes(label))) return
      if (item.custom !== undefined && typeof item.custom !== 'string') return
      answers.push({
        id: question.id,
        selected: [...selected],
        ...(item.custom !== undefined ? { custom: clip(item.custom, MAX_QUESTION_TEXT) } : {}),
      })
    }
    settle(message.requestId, (record) => record.resolve({ answers }))
  }

  const cancel = (message) => {
    settle(message.requestId, (entry) => entry.reject(questionError(
      `the question was withdrawn (${String(message.reason ?? 'cancelled')})`,
      message.reason === 'stopped' ? 'ASK_ABORTED' : 'ASK_CANCELLED',
    )))
  }

  /**
   * Read a service without requiring it. Cordis exposes services on the context and through
   * `ctx.get`; both are tried, and any failure means "this runtime does not offer it".
   */
  const service = (serviceName) => {
    try {
      const viaGet = typeof ctx.get === 'function' ? ctx.get(serviceName) : undefined
      if (viaGet !== undefined && viaGet !== null) return viaGet
    } catch {
      // A context that refuses the lookup is the same as an absent service here.
    }
    try {
      return ctx[serviceName]
    } catch {
      return undefined
    }
  }

  /**
   * The tool surface this runtime actually attached: names only, from the documented
   * `tools.schemas(scope)` view, unioned across every live agent scope and the global view.
   * Nothing is inferred beyond names, and an absent registry is reported as unavailable —
   * an empty list would read as "this session has no tools", which is a different claim.
   */
  const toolRoster = () => {
    const tools = service('tools')
    if (tools === undefined || typeof tools.schemas !== 'function') {
      return { available: false, reason: 'the runtime exposes no read-only tools registry' }
    }
    const seen = new Set()
    let scopes = 0
    const add = (schemas) => {
      if (!Array.isArray(schemas)) return
      for (const schema of schemas) {
        const name = isRecord(schema) ? schema.name : undefined
        if (typeof name !== 'string' || !name || name.length > MAX_TOOL_NAME) continue
        seen.add(name)
        if (seen.size >= MAX_TOOL_NAMES) return
      }
    }
    const agents = service('agents')
    let handles = []
    try {
      const listed = typeof agents?.list === 'function' ? agents.list() : []
      if (Array.isArray(listed)) handles = listed
    } catch {
      handles = []
    }
    for (const agent of handles) {
      try {
        add(tools.schemas(agent))
        scopes += 1
      } catch {
        // One unreadable scope must not hide the others.
      }
      if (seen.size >= MAX_TOOL_NAMES) break
    }
    let global = 0
    try {
      const all = tools.schemas()
      global = Array.isArray(all) ? all.length : 0
      add(all)
    } catch {
      global = 0
    }
    return {
      available: true,
      tools: [...seen].slice(0, MAX_TOOL_NAMES),
      agents: scopes,
      global,
    }
  }

  const reportTools = (message) => {
    const requestId = message.requestId
    if (typeof requestId !== 'string' || !requestId || requestId.length > 128) return
    let payload
    try {
      payload = toolRoster()
    } catch (error) {
      payload = { available: false, reason: clip(error?.message ?? 'the roster could not be read', 300) }
    }
    send({ type: 'tools', requestId, ...payload, t: Date.now() })
  }

  const receive = (line) => {
    let message
    try {
      message = JSON.parse(line)
    } catch {
      return
    }
    if (!isRecord(message)) return
    if (message.type === 'answer') answer(message)
    else if (message.type === 'cancel') cancel(message)
    else if (message.type === 'tools') reportTools(message)
    // Anything else is not part of this protocol; ignoring it cannot grant authority.
  }

  const server = net.createServer((socket) => {
    if (client !== undefined || pendingAuth.size >= 8) {
      socket.destroy()
      return
    }
    pendingAuth.add(socket)
    let accepted = false
    socket.setTimeout(1000, () => socket.destroy())
    let buffer = ''
    socket.setEncoding('utf8')
    socket.on('error', () => socket.destroy())
    socket.on('drain', flushDropped)
    socket.on('close', () => {
      pendingAuth.delete(socket)
      if (client === socket) {
        client = undefined
        authenticated = false
        cancelAll('BRIDGE_LOST', 'the client connection to the runtime closed')
      }
    })
    socket.on('data', (chunk) => {
      buffer += chunk
      if (!accepted && Buffer.byteLength(buffer, 'utf8') > 4096) { socket.destroy(); return }
      for (;;) {
        const newline = buffer.indexOf('\n')
        if (newline < 0) break
        const line = buffer.slice(0, newline)
        buffer = buffer.slice(newline + 1)
        if (!accepted) {
          let hello
          try {
            hello = JSON.parse(line)
          } catch {
            socket.destroy()
            return
          }
          if (!isRecord(hello) || hello.token !== token) {
            socket.destroy()
            return
          }
          if (client !== undefined) { socket.destroy(); return }
          pendingAuth.delete(socket)
          client = socket
          accepted = authenticated = true
          socket.setTimeout(0)
          socket.write(JSON.stringify({
            type: 'hello', pid: process.pid, protocol: 1, features: FEATURES,
          }) + '\n')
          continue
        }
        if (line.length > MAX_CLIENT_LINE) continue
        if (line.trim()) receive(line)
      }
      if (buffer.length > MAX_CLIENT_LINE) {
        buffer = ''
        if (!authenticated) socket.destroy()
      }
    })
  })
  server.on('error', (error) => {
    ctx.logger?.warn?.('odessy-stream-bridge listen failed: %s', String(error))
  })

  ctx.effect(() => {
    server.listen(port, host)
    return () => {
      for (const socket of pendingAuth) socket.destroy()
      pendingAuth.clear()
      cancelAll('BRIDGE_LOST', 'the runtime is unloading the question bridge')
      client?.destroy()
      client = undefined
      authenticated = false
      server.close()
    }
  })

  ctx.effect(() => ctx.on('agent/inbox/claimed', ({ agent, turn }) => {
    if (agent !== undefined && Number.isInteger(turn)) turns.set(agent, turn)
  }))

  // The answerer. Returning ``next()`` delegates: when no client is connected the tool
  // fails with the seam's own NO_PROVIDER instead of waiting forever.
  ctx.effect(() => ctx.on('user-questions/request', (request, next) => {
    const agent = request?.agent
    const sessionId = String(agent?.session?.id ?? '')
    if (!agent || !sessionId) return next()
    if (!authenticated || client === undefined || client.destroyed) return next()
    if (pending.size >= MAX_PENDING) {
      return Promise.reject(questionError('too many questions are already waiting', 'BUSY'))
    }
    const checked = validateQuestions(request.questions)
    if (checked.error !== undefined) return Promise.reject(checked.error)
    const requestId = randomUUID()
    const turn = turns.get(agent) ?? null
    return new Promise((resolve, reject) => {
      const entry = {
        resolve, reject, timer: null, sessionId, turn, questions: checked.questions,
        signal: request.signal, onAbort: undefined,
      }
      entry.onAbort = () => {
        settle(requestId, (record) => record.reject(questionError(
          'the turn was cancelled before the user answered', 'ASK_ABORTED')))
      }
      request.signal?.addEventListener?.('abort', entry.onAbort, { once: true })
      entry.timer = setTimeout(() => {
        settle(requestId, (record) => record.reject(questionError(
          'the user did not answer in time', 'ASK_TIMEOUT')))
      }, waitMs)
      pending.set(requestId, entry)
      const sent = send({
        type: 'question',
        sessionId,
        turn,
        requestId,
        questions: checked.questions,
        t: Date.now(),
      })
      if (!sent) {
        settle(requestId, (record) => record.reject(questionError(
          'the question could not be delivered to Odessy', 'NO_PROVIDER')))
      }
    })
  }))

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
