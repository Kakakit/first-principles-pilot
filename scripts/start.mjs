import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const configPath = process.env.FPILOT_CONFIG || join(root, 'fpilot.local.json');
const config = existsSync(configPath) ? JSON.parse(readFileSync(configPath, 'utf8').replace(/^\uFEFF/, '')) : {};
const python = process.env.FPILOT_PYTHON || config.python || (process.platform === 'win32' ? 'python' : 'python3');
const apiPort = Number(process.env.FPILOT_API_PORT || 8000);
const devPort = Number(process.env.FPILOT_DEV_PORT || config.port || 5173);
if (![apiPort, devPort].every(p => Number.isInteger(p) && p >= 1024 && p <= 65535) || apiPort === devPort) {
  console.error('API 和网页端口需为不同的 1024–65535 整数。'); process.exit(1);
}
Object.assign(process.env, { PYTHONUTF8: '1', FPILOT_CONFIG: configPath, FPILOT_PORT: String(devPort), FPILOT_API_PORT: String(apiPort) });
if (config.safe_pull && !process.env.FPILOT_SAFE_PULL) process.env.FPILOT_SAFE_PULL = config.safe_pull;
for (const [key, env] of [['workspace', 'FPILOT_WORKSPACE'], ['research_root', 'FPILOT_RESEARCH_ROOT'], ['potcar_library', 'FPILOT_POTCAR_LIBRARY']])
  if (!process.env[env] && config[key]) process.env[env] = config[key];
const probe = spawnSync(python, ['-c', 'import fastapi, uvicorn, jsonschema; from pymatgen.io.cif import CifParser; from pymatgen.io.vasp import Poscar'], { encoding: 'utf8' });
if (probe.status !== 0) {
  console.error('现有 Python 缺少依赖或无法运行。请设置 FPILOT_PYTHON，参见 README.md。\n' + (probe.stderr || probe.error || ''));
  process.exit(1);
}
for (const port of [apiPort, devPort]) {
  await new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  }).catch(error => { console.error(`端口 ${port} 不可用：${error.message}。请先停止已有工作台。`); process.exit(1); });
}
const children = [];
let stopping = false;
let workerTimer;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  clearTimeout(workerTimer);
  for (const child of children) child.kill();
  process.exitCode = code;
}
// Worker refreshes attempts and dispatches only explicitly authorized chains/batches.
function startWorker() {
  if (stopping) return;
  const child = spawn(python, ['-m', 'backend.worker'], { cwd: root, stdio: 'inherit', env: { ...process.env, FPILOT_LAUNCHER_PID: String(process.pid) } });
  children.push(child);
  child.on('error', error => console.error('worker:', error.message));
  child.on('exit', () => {
    children.splice(children.indexOf(child), 1);
    if (!stopping) workerTimer = setTimeout(startWorker, 5000);
  });
}
for (const [command, args] of [
  [python, ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(apiPort)]],
  [process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(devPort), '--strictPort']],
]) {
  const child = spawn(command, args, { cwd: root, stdio: 'inherit' });
  children.push(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => { if (!stopping) stop(code || 1); });
}
if (config.worker_enabled !== false) startWorker();
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
console.log(`工作台：http://127.0.0.1:${devPort} （等候 Vite ready）；Ctrl+C 同时停止前后端。`);
