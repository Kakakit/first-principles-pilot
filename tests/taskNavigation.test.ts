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

test('a tab reload restores scoped choices and discards transient submission state',async()=>{
 const {RunPreferences}=await import('../src/submissionDrafts.ts');
 const memory=new Map<string,string>();
 const storage={getItem:(k:string)=>memory.get(k)??null,setItem:(k:string,v:string)=>memory.set(k,v)} as Storage;
 const prefs=new RunPreferences('/work/a',storage);
 prefs.set('single:p:n:cores','16');
 prefs.set('chain:p:selection',['n']);
 prefs.set('single:p:n:probe',{ok:true});
 prefs.set('single:p:n:config',{password:'secret'});
 prefs.set('single:p:n:confirmation',true);
 const restored=new RunPreferences('/work/a',storage);
 assert.equal(restored.get('single:p:n:cores',''),'16');
 assert.deepEqual(restored.get('chain:p:selection',[]),['n']);
 assert.equal(restored.get('single:p:n:confirmation',false),false);
 assert.doesNotMatch([...memory.values()].join(''),/secret|probe|confirmation/);
 assert.equal(new RunPreferences('/work/b',storage).get('single:p:n:cores',''),'');
 restored.prune([{id:'p',nodes:[]}]);
 assert.equal(new RunPreferences('/work/a',storage).get('single:p:n:cores',''),'');
});
