import net from 'node:net'
import assert from 'node:assert/strict'
import { apply } from './plugin/odessy-stream-bridge/lib/index.js'
const probe = net.createServer()
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve))
const port = probe.address().port
await new Promise(resolve => probe.close(resolve))
const disposers = []
const ctx = { effect(fn) { const dispose = fn(); if (dispose) disposers.push(dispose) }, on() { return () => {} } }
apply(ctx, {port, token:'literal-test-token'})
const connect = () => new Promise((resolve, reject) => {
  const socket = net.connect(port, '127.0.0.1', () => resolve(socket))
  socket.on('error', reject)
})
const silent = await connect()
const client = await connect()
client.setEncoding('utf8')
const hello = new Promise((resolve, reject) => {
  client.once('data', resolve)
  setTimeout(() => reject(new Error('legitimate client blocked by silent peer')), 1500).unref()
})
client.write(JSON.stringify({token:'literal-test-token'})+'\n')
assert.equal(JSON.parse(await hello).type, 'hello')
await new Promise(resolve => silent.once('close', resolve))
client.destroy()
for (const dispose of disposers.reverse()) dispose()
console.log('PASS silent peer cannot reserve authenticated slot; handshake deadline and disposal work')
