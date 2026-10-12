import {spawn,spawnSync} from 'node:child_process';
import {createServer as netServer} from 'node:net';
import {createServer} from 'node:http';
import {existsSync,readFileSync,mkdirSync,openSync,closeSync,writeFileSync,renameSync,unlinkSync,statSync,readSync,realpathSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes} from 'node:crypto';

export const root=realpathSync(fileURLToPath(new URL('../',import.meta.url)));
export const sleep=ms=>new Promise(done=>setTimeout(done,ms));
export function configuration(){
  const configPath=resolve(process.env.FPILOT_CONFIG||join(root,'fpilot.local.json'));
  const value=existsSync(configPath)?JSON.parse(readFileSync(configPath,'utf8').replace(/^\uFEFF/,'')):{};
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('本机配置应为 JSON 对象。');
  const ports=[Number(process.env.FPILOT_API_PORT||8000),Number(process.env.FPILOT_DEV_PORT||value.port||5173),Number(process.env.FPILOT_CONTROL_PORT||value.control_port||5174)];
  if(!ports.every(p=>Number.isInteger(p)&&p>=1024&&p<=65535)||new Set(ports).size!==3)throw new Error('API、网页和控制端口须为互不相同的 1024–65535 整数。');
  const [apiPort,devPort,controlPort]=ports;
  const env={...process.env,PYTHONUTF8:'1',FPILOT_CONFIG:configPath,FPILOT_PORT:String(devPort),FPILOT_API_PORT:String(apiPort),FPILOT_CONTROL_URL:`http://127.0.0.1:${controlPort}`};
  for(const [key,name] of [['workspace','FPILOT_WORKSPACE'],['research_root','FPILOT_RESEARCH_ROOT'],['potcar_library','FPILOT_POTCAR_LIBRARY'],['safe_pull','FPILOT_SAFE_PULL']])if(!env[name]&&value[key])env[name]=value[key];
  return {apiPort,devPort,controlPort,configPath,env,python:process.env.FPILOT_PYTHON||value.python||(process.platform==='win32'?'python':'python3'),workspace:resolve(env.FPILOT_WORKSPACE||join(root,'workspace-data')),workerEnabled:value.worker_enabled!==false};
}
export function available(port){
  return new Promise((done,fail)=>{const server=netServer();server.once('error',fail);server.listen(port,'127.0.0.1',()=>server.close(done));});
}
export function logTail(path){
  if(!existsSync(path))return '';
  const fd=openSync(path,'r');try{const size=statSync(path).size,start=Math.max(0,size-16384),buffer=Buffer.alloc(size-start);readSync(fd,buffer,0,buffer.length,start);return buffer.toString('utf8');}finally{closeSync(fd);}
}
export function dispatchingCount(config){
  const database=join(config.workspace,'execution.sqlite3');
  if(!existsSync(database))return 0;
  const result=spawnSync(config.python,['-c',"import sqlite3,sys; from pathlib import Path; c=sqlite3.connect(Path(sys.argv[1]).as_uri()+'?mode=ro',uri=True); print(c.execute(\"SELECT count(*) FROM attempts WHERE state='DISPATCHING'\").fetchone()[0]); c.close()",database],{encoding:'utf8',timeout:5000});
  if(result.status!==0||!/^\d+\s*$/.test(result.stdout))throw new Error('暂不能核对完整本机派发记录，请稍后重试。');
  return Number(result.stdout.trim());
}

export class Workbench {
  constructor(runtime){this.runtime=runtime;this.phase='stopped';this.error='';this.children=new Map();this.busy=false;this.startedAt=null;this.workerTimer=null;this.config=configuration();}
  state(){return {phase:this.phase,error:this.error,busy:this.busy,startedAt:this.startedAt,workspace:this.config.workspace,python:this.config.python,workerEnabled:this.config.workerEnabled,url:`http://127.0.0.1:${this.config.devPort}`,processes:[...this.children].map(([name,child])=>({name,pid:child.pid})),logPath:join(this.runtime,'workbench.log')};}
  async dispatching(){return dispatchingCount(this.config);}
  async request(action){
    if(this.busy){if(action==='start')return;throw new Error('正在处理上一项操作，请等待状态更新。');}
    if(action==='start'&&this.phase==='running')return;
    this.busy=true;
    // Preserve an in-progress dispatch receipt. Shutdown only signals children
    // owned by this controller and waits; it never invokes a remote qdel.
    try{if(['stop','restart','quit'].includes(action)&&this.phase==='running'){
      if(await this.dispatching())throw new Error('任务正在上传或提交，等待派发回执落盘后再停止或重启。');
    }}catch(error){this.busy=false;throw error;}
    this.operation=(async()=>{try{if(action!=='start')await this.stop();if(['start','restart'].includes(action))await this.start();}catch(error){this.error=error.message;await this.stop();this.phase='error';}finally{this.busy=false;}})();
  }
  spawn(name,command,args){
    const child=spawn(command,args,{cwd:root,env:{...this.config.env,FPILOT_LAUNCHER_PID:String(process.pid)},stdio:['ignore',this.log,this.log]});
    this.children.set(name,child);
    child.on('error',error=>{this.error=`${name}：${error.message}`;if(!child.pid)this.children.delete(name);});
    child.on('exit',(code,signal)=>{
      this.children.delete(name);
      if(!['running','starting'].includes(this.phase))return;
      if(name==='worker'&&this.phase==='running'){
        this.error='监控进程退出，正在恢复；项目授权保持。';
        this.workerTimer=setTimeout(()=>{if(this.phase==='running')this.spawn('worker',this.config.python,['-m','backend.worker']);},5000);
      }else if(this.phase==='running'){
        this.error=`${name} 已退出（${signal||code}），请查看启动日志。`;
        this.busy=true;this.operation=this.stop().then(()=>{this.phase='error';this.busy=false;});
      }
    });
    return child;
  }
  async start(){
    this.phase='starting';this.error='';this.config=configuration();
    mkdirSync(this.runtime,{recursive:true,mode:0o700});
    for(const port of [this.config.apiPort,this.config.devPort]){
      try{await available(port);}catch{throw new Error(`端口 ${port} 已被其他进程占用。当前控制中心没有该进程的身份，不会强制关闭它。请查看原启动窗口或日志。`);}
    }
    const probe=spawnSync(this.config.python,['-c','import fastapi, uvicorn, jsonschema; from pymatgen.io.vasp import Poscar'],{encoding:'utf8',timeout:20000});
    if(probe.status!==0)throw new Error('已有 Python 无法运行或缺少依赖：'+(probe.stderr||probe.error||'请查看本机配置。'));
    const path=join(this.runtime,'workbench.log');
    if(existsSync(path))renameSync(path,join(this.runtime,`workbench-${Date.now()}.log`));
    this.log=openSync(path,'a',0o600);
    this.spawn('API',this.config.python,['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',String(this.config.apiPort)]);
    if(this.config.workerEnabled)this.spawn('worker',this.config.python,['-m','backend.worker']);
    const deadline=Date.now()+45000;
    // Vite reconnects old tabs as soon as it starts. Wait for the API first,
    // otherwise those tabs load a placeholder workspace after a proxy failure.
    let apiReady=false;
    while(Date.now()<deadline&&!apiReady){
      if(!this.children.has('API')||(this.config.workerEnabled&&!this.children.has('worker')))throw new Error(this.error||'API 或监控在启动期间退出。');
      try{const response=await fetch(`http://127.0.0.1:${this.config.apiPort}/api/health`,{signal:AbortSignal.timeout(1500)});const value=await response.json();apiReady=response.ok&&value.mode==='local-workspace';}catch{}
      if(!apiReady)await sleep(200);
    }
    if(!apiReady)throw new Error('API 启动超时，请查看日志。');
    this.spawn('网页',process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',String(this.config.devPort),'--strictPort']);
    while(Date.now()<deadline){
      if(!this.children.has('API')||!this.children.has('网页')||(this.config.workerEnabled&&!this.children.has('worker')))throw new Error(this.error||'服务在启动期间退出，请查看启动日志。');
      try{
        const [api,page]=await Promise.all([fetch(`http://127.0.0.1:${this.config.apiPort}/api/health`,{signal:AbortSignal.timeout(1500)}),fetch(`http://127.0.0.1:${this.config.devPort}`,{signal:AbortSignal.timeout(1500)})]);
        const info=await api.json();
        let workerReady=!this.config.workerEnabled;
        if(this.config.workerEnabled){const response=await fetch(`http://127.0.0.1:${this.config.apiPort}/api/execution-control`,{signal:AbortSignal.timeout(1500)});const value=await response.json();workerReady=value.worker?.healthy&&value.worker?.pid===this.children.get('worker')?.pid;}
        if(api.ok&&info.mode==='local-workspace'&&page.ok&&workerReady){this.phase='running';this.startedAt=new Date().toISOString();return;}
      }catch{}
      await sleep(200);
    }
    throw new Error('启动超时，请查看日志和现有 Python／本机配置。');
  }
  async stop(){
    this.phase='stopping';clearTimeout(this.workerTimer);
    const children=[...this.children.values()];
    for(const child of children)if(child.exitCode===null&&child.signalCode===null)child.kill('SIGTERM');
    await Promise.all(children.map(child=>!child.pid||child.exitCode!==null||child.signalCode!==null?Promise.resolve():new Promise(done=>child.once('exit',done))));
    this.children.clear();if(this.log!==undefined){closeSync(this.log);this.log=undefined;}
    this.phase='stopped';this.startedAt=null;
  }
}

export async function serve(){
  const config=configuration(),runtime=join(root,'.fpilot-runtime');
  const url=`http://127.0.0.1:${config.controlPort}`,token=randomBytes(32).toString('hex'),instance=randomBytes(16).toString('hex');
  const workbench=new Workbench(runtime),recordPath=join(runtime,'controller.json');let quitting=false;
  const json=(res,code,value)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  const server=createServer(async(req,res)=>{
    if(req.headers.host!==`127.0.0.1:${config.controlPort}`){json(res,403,{error:'控制中心仅接受本机地址。'});return;}
    if(req.method==='GET'&&req.url==='/status'){json(res,200,{...workbench.state(),instance});return;}
    if(req.method==='GET'&&req.url==='/logs'){json(res,200,{text:logTail(join(runtime,'workbench.log'))});return;}
    if(req.method==='GET'&&req.url==='/'){
      const html=readFileSync(join(root,'scripts/workbench-control.html'),'utf8').replace('__CONTROL_TOKEN__',token);
      res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"});res.end(html);return;
    }
    if(req.method!=='POST'||!['/start','/stop','/restart','/quit'].includes(req.url)){json(res,404,{error:'无此操作'});return;}
    if(req.headers['x-fpilot-control']!==token||req.headers['content-type']!=='application/json'||(req.headers.origin&&req.headers.origin!==url)){json(res,403,{error:'请从本机控制页执行操作。'});return;}
    if(quitting){json(res,409,{error:'控制中心正在退出。'});return;}
    try{
      const action=req.url.slice(1);await workbench.request(action);json(res,202,{accepted:true});
      if(action==='quit'){
        quitting=true;await workbench.operation;
        if(workbench.phase==='error'){quitting=false;return;}
        server.close();unlinkSync(recordPath);
      }
    }catch(error){json(res,409,{error:error.message});}
  });
  server.requestTimeout=5000;
  await new Promise((done,fail)=>{server.once('error',fail);server.listen(config.controlPort,'127.0.0.1',done);});
  mkdirSync(runtime,{recursive:true,mode:0o700});
  const temp=recordPath+'.'+instance;writeFileSync(temp,JSON.stringify({root,url,pid:process.pid,token,instance}),{mode:0o600});renameSync(temp,recordPath);
  async function shutdown(){if(quitting)return;quitting=true;if(workbench.operation)await workbench.operation;await workbench.stop();server.close();if(existsSync(recordPath))unlinkSync(recordPath);}
  process.on('SIGINT',shutdown);process.on('SIGTERM',shutdown);
  console.log('工作台控制中心：'+url);
  return {server,workbench};
}
if(process.argv.includes('--serve')&&resolve(process.argv[1])===fileURLToPath(import.meta.url))serve().catch(error=>{console.error(error);process.exitCode=1;});
