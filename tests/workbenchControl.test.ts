import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {Workbench,configuration,logTail,dispatchingCount} from '../scripts/workbench-control.mjs';

test('controller rejects shared ports and reads only a bounded log tail',()=>{
  const previous=process.env.FPILOT_CONTROL_PORT;
  try{process.env.FPILOT_CONTROL_PORT=process.env.FPILOT_API_PORT||'8000';assert.throws(configuration,/互不相同/);}
  finally{if(previous===undefined)delete process.env.FPILOT_CONTROL_PORT;else process.env.FPILOT_CONTROL_PORT=previous;}
  const temp=mkdtempSync(join(tmpdir(),'fpilot-log-'));
  try{const path=join(temp,'log');writeFileSync(path,'a'.repeat(40000)+'\n最后一行');const tail=logTail(path);assert.ok(Buffer.byteLength(tail)<=16390);assert.ok(tail.endsWith('最后一行'));}
  finally{rmSync(temp,{recursive:true});}
});

test('dispatch guard blocks stop without signaling a process',async()=>{
  const workbench=new Workbench(tmpdir());workbench.phase='running';
  let stopped=false;workbench.stop=async()=>{stopped=true;};
  workbench.dispatching=async()=>1;await assert.rejects(workbench.request('stop'),/回执/);assert.equal(stopped,false);assert.equal(workbench.busy,false);assert.equal(workbench.phase,'running');
});

test('concurrent restarts are serialized before reading task history',async()=>{
  const workbench=new Workbench(tmpdir());workbench.phase='running';
  let release;workbench.dispatching=()=>new Promise(done=>{release=()=>done(0);});
  workbench.stop=async()=>{workbench.phase='stopped';};workbench.start=async()=>{workbench.phase='running';};
  const first=workbench.request('restart');await assert.rejects(workbench.request('restart'),/上一项/);release();await first;await workbench.operation;assert.equal(workbench.phase,'running');assert.equal(workbench.busy,false);
});

test('dispatch check includes older records beyond the 100-row UI history',()=>{
  const temp=mkdtempSync(join(tmpdir(),'fpilot-dispatch-')),config={...configuration(),workspace:temp};
  try{
    const result=spawnSync(config.python,['-c',"import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('CREATE TABLE attempts(state TEXT)'); c.execute(\"INSERT INTO attempts VALUES ('DISPATCHING')\"); c.executemany('INSERT INTO attempts VALUES (?)',[('COMPLETED',)]*200); c.commit(); c.close()",join(temp,'execution.sqlite3')]);
    assert.equal(result.status,0,result.stderr.toString());assert.equal(dispatchingCount(config),1);
  }finally{rmSync(temp,{recursive:true});}
});

test('repeated start on a ready workbench preserves owned process identities',async()=>{
  const workbench=new Workbench(tmpdir());workbench.phase='running';workbench.children.set('API',{pid:123});
  workbench.start=async()=>{throw Error('must not spawn twice');};await workbench.request('start');assert.equal(workbench.children.get('API').pid,123);
});
