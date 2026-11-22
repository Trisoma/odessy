'use strict';
// tsc does not copy CommonJS/native helper resources.
const fs = require('node:fs');
const path = require('node:path');
const target = path.join(__dirname, 'dist/src/agent_basics_terminal');
fs.mkdirSync(target, {recursive: true});
for (const name of ['helper.cjs', 'dependency-receipt.json']) {
  fs.copyFileSync(path.join(__dirname, 'src/agent_basics_terminal', name),
                  path.join(target, name));
}
