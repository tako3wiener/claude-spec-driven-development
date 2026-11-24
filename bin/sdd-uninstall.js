#!/usr/bin/env node
'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const result = spawnSync('python3', [path.join(__dirname, '..', 'install.py'), '--uninstall', ...process.argv.slice(2)], { stdio: 'inherit', cwd: process.cwd(), shell: false });
if (result.error) {
  console.error('SDD uninstall requires Python 3.9+ available as python3:', result.error.message);
  process.exitCode = 2;
} else {
  process.exitCode = result.status === null ? 2 : result.status;
}
