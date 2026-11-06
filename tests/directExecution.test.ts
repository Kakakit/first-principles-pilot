import assert from 'node:assert/strict';
import test from 'node:test';
import {appendLog,logWindowBytes} from '../src/commandLogs';
import {commandMode,directProcessLabel,type RegisteredProfile} from '../src/executionUi';
import {emptySettings,runtimeFor} from '../src/submissionDefaults';
import {nextAction} from '../src/resultWorkspace';

test('registered command mode wins over per-program settings without changing a frozen attempt',()=>{
 const profile={alias:'lab',scheduler:'custom',command_mode:'direct',runtime_source:'script'} as RegisteredProfile;
 const saved={...emptySettings,runtimes:{'lab:vasp_std':{scheduler:'custom',alias:'lab',command_mode:'opaque'}},
  defaults:{'lab:vasp_std':'regular'}};
 const frozen=runtimeFor({alias:'lab',scheduler:'custom'},[profile],saved,'lab');
 assert.equal(commandMode(frozen),'direct');
 assert.equal(frozen.template_id,'regular');
 assert.equal(runtimeFor(frozen,[profile],saved,'lab','vasp_ncl').template_id,undefined);
 assert.equal(commandMode(runtimeFor(frozen,[{...profile,command_mode:'opaque'}],saved,'lab')),'opaque');
 assert.equal(commandMode(frozen),'direct');
 assert.equal(commandMode({scheduler:'custom'}),'opaque');
 assert.equal(commandMode({scheduler:'pbs',command_mode:'direct'}),null);
 assert.equal(directProcessLabel('RUNNING',{process_pid:123}),'PID 123');
 assert.equal(nextAction({id:'key',state:'COMPLETED',config:frozen}).reason,'执行结束，程序结束证据不足');
});

test('log updates retain bounded UTF-8 and reset on a new stream identity',()=>{
 const old='中🧪'.repeat(60_000);
 const next=appendLog(old,'\n末尾\n',false);
 assert.ok(new TextEncoder().encode(next).length<=logWindowBytes);
 assert.ok(next.endsWith('\n末尾\n'));
 assert.ok(!next.includes('\ufffd'));
 assert.equal(appendLog(next,'new\n',true),'new\n');
});
