import test from 'node:test';
import assert from 'node:assert/strict';
import {taskDestination} from '../src/taskNavigation.ts';
import {LatestRequests} from '../src/useRequestScope.ts';
import {SubmissionDraftCache} from '../src/submissionDrafts.ts';

test('an older run survives results, configuration and monitor round trips',()=>{
 const selected={projectId:'p',page:'run',nodeId:'relax',attemptId:'older-run',runScope:'node' as const};
 const results=taskDestination(selected,{page:'results',nodeId:'relax',attemptId:'older-run'});
 const config=taskDestination(results,{page:'config'});
 assert.equal(taskDestination(config,{page:'run'}).attemptId,'older-run');
 const other=taskDestination(results,{page:'run',nodeId:'scf'});
 assert.equal(other.attemptId,'');assert.equal(other.nodeId,'scf');
 assert.equal(taskDestination(results,{page:'results',attemptId:''}).attemptId,'');
 assert.equal(taskDestination(results,{projectId:'other',page:'run'}).attemptId,'');
});

test('late receipts cannot overwrite a newer selection or a departed page',async()=>{
 const reads=new LatestRequests(),first=reads.start('history');
 let resolveOld!:(value:string)=>void;const old=new Promise<string>(resolve=>{resolveOld=resolve;});
 let displayed='';const late=old.then(value=>{if(first.current())displayed=value;});
 const second=reads.start('history');assert.equal(first.signal.aborted,true);
 displayed='new';resolveOld('old');await late;assert.equal(displayed,'new');
 const inputs=reads.start('inputs');assert.equal(second.current(),true);
 reads.cancel();assert.equal(second.current(),false);assert.equal(inputs.signal.aborted,true);
 const revisit=reads.start('history');assert.equal(revisit.current(),true);
});

test('session drafts evict least recently used entries and release removed projects and nodes',()=>{
 const cache=new SubmissionDraftCache(3,2048);
 cache.set('single:p:n1:config',{alias:'a'});cache.set('single:p:n2:config',{alias:'b'});
 cache.get('single:p:n1:config',null);cache.set('chain:p:selection',['n1']);cache.set('single:q:n3:config',{alias:'c'});
 assert.equal(cache.get('single:p:n2:config',null),null);assert.equal(cache.size,3);
 cache.prune([{id:'p',nodes:[{id:'n1'}]}]);assert.equal(cache.size,2);
 assert.deepEqual(cache.get('single:p:n1:config',null),{alias:'a'});
 cache.prune([{id:'p',nodes:[]}]);assert.equal(cache.size,1);
 cache.set('chain:p:large','x'.repeat(2000));assert.equal(cache.size,1);
 cache.prune([]);assert.equal(cache.size,0);
});
