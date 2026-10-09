import {test} from 'node:test';
import assert from 'node:assert/strict';
import {chainStepPhase,latestChain,pollInitialChain} from '../src/chainDispatch.ts';

test('chain phases distinguish submission, capacity, dependency and unknown receipts',()=>{
 assert.equal(chainStepPhase({state:'PENDING'},true),'正在准备');
 assert.equal(chainStepPhase({state:'PENDING',waiting_kind:'DEPENDENCY'}),'等待上游');
 assert.equal(chainStepPhase({state:'PENDING',waiting_kind:'QUOTA'}),'等待额度');
 assert.equal(chainStepPhase({state:'PENDING',waiting_reason:'GPU busy'}),'等待资源');
 assert.equal(chainStepPhase({state:'PREPARED',attempt_state:'UNKNOWN'}),'状态待核查');
 assert.equal(chainStepPhase({state:'PREPARED',attempt_state:'SUBMITTED',job_id:'12'}),'已提交');
 assert.equal(latestChain({id:'new',revision:3},{id:'old',revision:4})?.id,'new');
 assert.equal(latestChain({id:'same',revision:3},{id:'same',revision:2})?.revision,3);
});

test('initial polling never overlaps, stops at completion and discards late replies',async()=>{
 let calls=0,inflight=0,max=0,received=0;
 const done=new Promise<void>(resolve=>{
  pollInitialChain(async()=>{
   calls++;inflight++;max=Math.max(max,inflight);
   await new Promise(r=>setTimeout(r,5));inflight--;
   return {initial_dispatch:{state:calls===2?'FINISHED' as const:'IN_PROGRESS' as const,started_at:'now'}};
  },()=>received++,resolve,1);
 });
 await done;await new Promise(r=>setTimeout(r,10));
 assert.equal(max,1);assert.equal(calls,2);assert.equal(received,2);
 let reply!:(value:{initial_dispatch?:undefined})=>void;
 const cancel=pollInitialChain(()=>new Promise(resolve=>{reply=resolve;}),()=>received++,()=>assert.fail(),1);
 cancel();reply({});await new Promise(r=>setTimeout(r,5));
 assert.equal(received,2);
});
