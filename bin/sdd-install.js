#!/usr/bin/env node
'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const args = process.argv.slice(2);
const legacy = args.some(a => a === '--legacy-project' || a === '--target' || a.startsWith('--target=') || a === '--baseline' || a.startsWith('--baseline='));
const script = legacy ? 'install.py' : 'standalone.py';
const action = legacy ? [] : ['install'];
const result = spawnSync('python3', [path.join(__dirname, '..', script), ...action, ...args.filter(a => a !== '--legacy-project')], { stdio: 'inherit', cwd: process.cwd(), shell: false });
if (result.error) {
  console.error('SDD install requires Python 3.9+ available as python3:', result.error.message);
  process.exitCode = 2;
} else {
  process.exitCode = result.status === null ? 2 : result.status;
}
