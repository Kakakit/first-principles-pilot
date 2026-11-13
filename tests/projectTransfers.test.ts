import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createProjectTransfers,type TransferJob} from '../src/projectTransfers.ts';

const job=(state:string):TransferJob=>({id:'job',producer_id:'analysis',producer_kind:'analysis',name:'large.bin',state,bytes_transferred:0,size_bytes:100});
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return {promise,resolve};}

test('many results share one project read and completed history keeps no polling timer',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 let calls=0;
 const store=createProjectTransfers(async()=>{calls++;return [job('COMPLETED')];});
 const stops=Array.from({length:20},()=>store.subscribe('project',()=>{}));
 await flush();assert.equal(calls,1);assert.equal(store.getSnapshot('project').jobs[0].state,'COMPLETED');
 t.mock.timers.tick(60_000);await flush();assert.equal(calls,1);
 const stop=store.subscribe('project',()=>{});await flush();assert.equal(calls,1);
 stop();stops.forEach(stop=>stop());
});

test('active project polling is serial, stops at terminal state, and retry restarts it',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 let calls=0,read=deferred<TransferJob[]>();
 const store=createProjectTransfers(()=>{calls++;return read.promise;});
 const stop=store.subscribe('project',()=>{});await flush();assert.equal(calls,1);
 t.mock.timers.tick(10_000);await flush();assert.equal(calls,1,'slow initial read must not overlap');
 read.resolve([job('RUNNING')]);await flush();
 read=deferred<TransferJob[]>();t.mock.timers.tick(2000);await flush();assert.equal(calls,2);
 t.mock.timers.tick(10_000);await flush();assert.equal(calls,2,'slow poll must not overlap');
 read.resolve([job('COMPLETED')]);await flush();t.mock.timers.tick(60_000);await flush();assert.equal(calls,2);
 read=deferred<TransferJob[]>();const retried=store.refresh('project');await flush();assert.equal(calls,3);
 read.resolve([job('PENDING')]);await retried;
 read=deferred<TransferJob[]>();t.mock.timers.tick(2000);await flush();assert.equal(calls,4);
 read.resolve([job('FAILED')]);await flush();t.mock.timers.tick(60_000);await flush();assert.equal(calls,4);
 stop();
});

test('create or retry during an old read requests fresh state once for all subscribers',async()=>{
 const reads=[deferred<TransferJob[]>(),deferred<TransferJob[]>()];let calls=0;
 const store=createProjectTransfers(()=>reads[calls++].promise);
 const stops=[store.subscribe('p',()=>{}),store.subscribe('p',()=>{})];await flush();
 const first=store.refresh('p'),second=store.refresh('p');assert.equal(first,second);
 reads[0].resolve([]);await flush();assert.equal(calls,2);
 reads[1].resolve([job('FAILED')]);await first;
 assert.equal(store.getSnapshot('p').jobs[0].state,'FAILED');assert.equal(calls,2);
 stops.forEach(stop=>stop());
});

test('last result unmount aborts its read and late data cannot enter a remounted project',async()=>{
 const old=deferred<TransferJob[]>();let signal:AbortSignal|undefined,calls=0,notifications=0;
 const store=createProjectTransfers(async(_project,current)=>{signal=current;calls++;return calls===1?old.promise:[];});
 const first=store.subscribe('p',()=>notifications++),other=store.subscribe('p',()=>notifications++);
 await flush();first();assert.equal(signal?.aborted,false);other();assert.equal(signal?.aborted,true);
 const stop=store.subscribe('p',()=>notifications++);await flush();assert.equal(calls,2);
 old.resolve([job('COMPLETED')]);await flush();assert.deepEqual(store.getSnapshot('p').jobs,[]);assert.equal(notifications,1);
 stop();
});

test('project lists and active timers remain isolated',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});const reads:string[]=[];
 const store=createProjectTransfers(async project=>{reads.push(project);return [job(project==='active'?'RUNNING':'CANCELLED')];});
 const activeStop=store.subscribe('active',()=>{}),closedStop=store.subscribe('closed',()=>{});await flush();
 t.mock.timers.tick(2000);await flush();assert.deepEqual(reads,['active','closed','active']);
 assert.equal(store.getSnapshot('closed').jobs[0].state,'CANCELLED');
 activeStop();t.mock.timers.tick(10_000);await flush();assert.equal(reads.length,3);closedStop();
});

test('read failures keep active progress and terminal errors can be refreshed explicitly',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let calls=0;
 const store=createProjectTransfers(async()=>{calls++;if(calls===1)return [job('RUNNING')];if(calls===2)throw Error('offline');return [job('COMPLETED')];});
 const stop=store.subscribe('p',()=>{});await flush();t.mock.timers.tick(2000);await flush();
 assert.equal(store.getSnapshot('p').error,'offline');assert.equal(store.getSnapshot('p').jobs[0].state,'RUNNING');
 t.mock.timers.tick(2000);await flush();assert.equal(store.getSnapshot('p').error,'');
 t.mock.timers.tick(60_000);await flush();assert.equal(calls,3);stop();
 let offline=true,emptyReads=0;
 const empty=createProjectTransfers(async()=>{emptyReads++;if(offline)throw Error('offline');return [];});
 const end=empty.subscribe('p',()=>{});
 await flush();t.mock.timers.tick(60_000);await flush();assert.equal(empty.getSnapshot('p').error,'offline');assert.equal(emptyReads,1);
 offline=false;await empty.refresh('p');assert.equal(empty.getSnapshot('p').error,'');assert.equal(emptyReads,2);
 end();
});
