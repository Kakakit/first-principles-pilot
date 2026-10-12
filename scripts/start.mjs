// Keep the workbench independent of a terminal; repeated opens reuse its controller.
import {spawn} from 'node:child_process';
import {readFileSync,mkdirSync,openSync,closeSync} from 'node:fs';
import {join} from 'node:path';
import {configuration,root,available,sleep} from './workbench-control.mjs';
const command=process.argv.find(arg=>['start','stop','restart','status','quit'].includes(arg))||'start';
const noBrowser=process.argv.includes('--no-browser');
const config=configuration(),runtime=join(root,'.fpilot-runtime'),recordPath=join(runtime,'controller.json');
const controlURL=`http://127.0.0.1:${config.controlPort}`;
async function existing(){
  try{
    const record=JSON.parse(readFileSync(recordPath,'utf8'));
    if(record.root!==root||record.url!==controlURL)return null;
    const response=await fetch(controlURL+'/status',{signal:AbortSignal.timeout(1500)});
    const state=await response.json();
    return response.ok&&state.instance===record.instance?{record,state}:null;
  }catch{return null;}
}
try{
  let current=await existing();
  if(!current&&['stop','status','quit'].includes(command)){console.log('工作台控制中心未运行。');process.exit(0);}
  if(!current){
    try{await available(config.controlPort);}catch{throw new Error(`控制端口 ${config.controlPort} 被其他程序占用；请在配置中设置 control_port。不会关闭其他程序。`);}
    mkdirSync(runtime,{recursive:true,mode:0o700});
    const log=openSync(join(runtime,'controller.log'),'a',0o600);
    const child=spawn(process.execPath,[join(root,'scripts/workbench-control.mjs'),'--serve'],{cwd:root,env:process.env,detached:true,stdio:['ignore',log,log]});
    child.on('error',error=>console.error(error.message));child.unref();closeSync(log);
    const deadline=Date.now()+15000;
    while(Date.now()<deadline&&!current){await sleep(200);current=await existing();}
    if(!current)throw new Error('控制中心未能启动，请查看 .fpilot-runtime/controller.log。');
  }
  if(command!=='status'){
    const response=await fetch(controlURL+'/'+command,{method:'POST',headers:{'Content-Type':'application/json','X-FPilot-Control':current.record.token},body:'{}',signal:AbortSignal.timeout(5000)});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'操作失败');
  }
  console.log(command==='status'?`工作台状态：${current.state.phase}`:`工作台控制中心：${controlURL}\n重复打开会复用已有服务。`);
  if(!noBrowser&&!['stop','status','quit'].includes(command)){
    const args=process.platform==='darwin'?['open',[controlURL]]:process.platform==='win32'?['rundll32',['url.dll,FileProtocolHandler',controlURL]]:['xdg-open',[controlURL]];
    const browser=spawn(args[0],args[1],{stdio:'ignore'});browser.on('error',()=>console.log('请在浏览器打开上方地址。'));browser.unref();
  }
}catch(error){console.error('无法操作工作台：'+error.message);process.exitCode=1;}
