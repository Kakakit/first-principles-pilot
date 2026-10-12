import test from 'node:test';
import assert from 'node:assert/strict';
import {pollScanBatch,scanCasePhase} from '../src/scanDispatch.ts';

test('initial pending case shows preparation; exact job states keep their meaning',()=>{
 const initial={id:'batch',state:'ACTIVE',initial_dispatch:{state:'IN_PROGRESS' as const,started_at:''}};
 assert.equal(scanCasePhase('PENDING',initial),'正在准备');
 assert.equal(scanCasePhase('UNKNOWN',initial),'状态待核查');
 assert.equal(scanCasePhase('SUBMITTED',initial),'已提交');
});

test('reads initial progress then active status and stops on pause without submitting',async()=>{
 let calls=0;
 await new Promise<void>((resolve,reject)=>{
  const cancel=pollScanBatch(async()=>{
   calls++;
   return {id:'batch',state:calls<3?'ACTIVE':'PAUSED',initial_dispatch:{state:calls===1?'IN_PROGRESS' as const:'FINISHED' as const,started_at:''}};
  },value=>{if(value.state==='PAUSED'){cancel();resolve();}},reject,false,1,1);
 });
 assert.equal(calls,3);
});

test('a lost local read retries the same batch and accepts recovered job evidence',async()=>{
 let reads=0;
 await new Promise<void>((resolve)=>{
  const cancel=pollScanBatch(async()=>{
   reads++;
   if(reads===1)throw new Error('connection');
   return {id:'same',state:'COMPLETE',job_id:'22819'};
  },value=>{assert.equal(value.job_id,'22819');cancel();resolve();},()=>{},false,1,1);
 });
 assert.equal(reads,2);
});

test('switching project or batch discards a delayed response',async()=>{
 let reply!:(value:{id:string;state:string})=>void,received=0;
 const cancel=pollScanBatch(()=>new Promise(resolve=>{reply=resolve;}),()=>received++,()=>assert.fail(),false,1,1);
 cancel();reply({id:'old',state:'ACTIVE'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(received,0);
});
