#!/usr/bin/env node
/*
 * 常驻 REA MCP 会话客户端
 * =======================
 * 每条 `rea <cmd>` CLI 调用都会重新跑一遍 Ghidra 全量分析（约 2.5 分钟）。
 * 本脚本直接拉起 REA 的 MCP stdio server，保持会话，按批次发 tools/call，
 * 只付一次分析代价。
 *
 * 用法:
 *   node tools/rea_session.mjs <plan.json> <outdir>
 *
 * plan.json: [{ "tool": "...", "args": {...}, "out": "name" }, ...]
 * 每个结果写到 <outdir>/<name>.json
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const planPath = process.argv[2];
const outdir = process.argv[3] || 'build/rea/out';
const plan = JSON.parse(fs.readFileSync(planPath, 'utf8'));
fs.mkdirSync(outdir, { recursive: true });

const REA_JS = process.env.REA_JS ||
  'C:/Users/Maplu/AppData/Local/npm-cache/_npx/a1722018f28f9df2/node_modules/rea-agents/scripts/rea.mjs';
const CMD = process.env.REA_CMD || process.execPath;
const ARGS = process.env.REA_ARGS
  ? process.env.REA_ARGS.split(' ')
  : [REA_JS, 'mcp'];

const child = spawn(CMD, ARGS, {
  stdio: ['pipe', 'pipe', 'pipe'],
  shell: false,
  env: process.env,
});

let buf = '';
let nextId = 1;
const pending = new Map();

child.stdout.on('data', (chunk) => {
  buf += chunk.toString('utf8');
  let idx;
  while ((idx = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      process.stderr.write('[non-json] ' + line.slice(0, 300) + '\n');
      continue;
    }
    if (msg.id != null && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
    }
  }
});

child.stderr.on('data', (d) => process.stderr.write('[srv] ' + d.toString()));

function rpc(method, params, timeoutMs = 3600000) {
  const id = nextId++;
  const payload = JSON.stringify({ jsonrpc: '2.0', id, method, params });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`timeout ${method} id=${id}`));
    }, timeoutMs);
    pending.set(id, {
      resolve: (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      reject: (e) => {
        clearTimeout(timer);
        reject(e);
      },
    });
    child.stdin.write(payload + '\n');
  });
}

function notify(method, params) {
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method, params }) + '\n');
}

async function main() {
  const init = await rpc('initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'wite2-translation-analyst', version: '1.0.0' },
  });
  notify('notifications/initialized', {});
  const server = init.serverInfo || {};
  console.log(`[session] connected: ${server.name || '?'} ${server.version || ''}`);

  const tools = await rpc('tools/list', {});
  const names = (tools.tools || []).map((t) => t.name);
  console.log(`[session] ${names.length} tools available`);
  fs.writeFileSync(
    path.join(outdir, '_tools.json'),
    JSON.stringify(names.sort(), null, 2)
  );

  for (const step of plan) {
    const t0 = Date.now();
    process.stdout.write(`[run] ${step.out || step.tool} ... `);
    try {
      const res = await rpc('tools/call', {
        name: step.tool,
        arguments: step.args || {},
      });
      const dt = ((Date.now() - t0) / 1000).toFixed(1);
      const text = (res.content || [])
        .filter((c) => c.type === 'text')
        .map((c) => c.text)
        .join('\n');
      const file = path.join(outdir, (step.out || step.tool) + '.json');
      fs.writeFileSync(file, text);
      console.log(`ok ${dt}s -> ${file} (${(text.length / 1024).toFixed(0)} KB)`);
    } catch (e) {
      console.log('FAILED ' + e.message);
      fs.writeFileSync(
        path.join(outdir, (step.out || step.tool) + '.error.txt'),
        e.message
      );
    }
  }

  child.stdin.end();
  child.kill();
}

main().catch((e) => {
  console.error('fatal:', e);
  try {
    child.kill();
  } catch {}
  process.exit(1);
});
