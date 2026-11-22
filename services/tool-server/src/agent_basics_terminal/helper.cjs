// Odessy-owned protocol adapter. node-pty 1.1.0 is MIT licensed; not vendored.
'use strict';
const readline = require('node:readline');
const pty = require('node-pty');
let terminal;
const send = value => {
  if (!process.stdout.write(JSON.stringify(value) + '\n') && terminal) terminal.pause();
};
process.stdout.on('drain', () => { if (terminal) terminal.resume(); });
const lines = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
lines.on('line', line => {
  let message;
  try {
    if (line.length > 131072) throw new Error('frame too large');
    message = JSON.parse(line);
    if (!terminal) {
      if (message.op !== 'create') throw new Error('create required');
      terminal = pty.spawn(process.env.ComSpec || '/bin/sh',
        process.platform === 'win32' ? ['/d', '/q'] : [],
        {name: 'xterm-256color', cols: message.columns, rows: message.rows,
         cwd: message.cwd, env: {...process.env}});
      terminal.onData(data => send({event: 'output', text: data}));
      terminal.onExit(({exitCode}) => {
        send({event: 'exit', code: exitCode});
        setImmediate(() => process.exit(0));
      });
      send({event: 'ready'});
      return;
    }
    switch (message.op) {
      case 'input': terminal.write(message.text); break;
      case 'resize': terminal.resize(message.columns, message.rows); break;
      case 'interrupt': terminal.write('\x03'); break;
      case 'close': terminal.kill(); break;
      default: throw new Error('unsupported operation');
    }
    send({event: 'ack', id: message.id});
  } catch (_) { send({event: 'error', id: message && message.id}); }
});
lines.on('close', () => { if (terminal) terminal.kill(); else process.exit(0); });
process.on('uncaughtException', () => { if (terminal) terminal.kill(); process.exit(1); });
