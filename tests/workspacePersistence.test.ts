import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WorkspacePersistence,WorkspaceRequestError,workspaceRequest,workspaceSerializer,autosaveDelay,AUTOSAVE_IDLE_MS} from '../src/workspacePersistence.ts';
import {demoWorkspace,cleanWorkspace} from '../src/model.ts';
import {pollSerial} from '../src/pollSerial.ts';

const initial=()=>JSON.stringify(cleanWorkspace(demoWorkspace()));
const changed=(body:string,name:string)=>{const value=JSON.parse(body);value.projects[0].name=name;return JSON.stringify(value);};

test('autosave waits for 30 seconds of idle editing and continuous edits cannot postpone it past two minutes',()=>{
 assert.equal(AUTOSAVE_IDLE_MS,30_000);
 assert.equal(autosaveDelay(0,0),30_000);
 assert.equal(autosaveDelay(0,110_000),10_000);
 assert.equal(autosaveDelay(0,120_000),0);
 assert.equal(autosaveDelay(0,150_000),0);
});

test('lost successful save receipt is reconciled before newer edits are saved',async()=>{
 const base=initial(),first=changed(base,'first'),second=changed(base,'second');
 let disk=base,revision=1,lose=true;const writes:number[]=[];
 const persistence=new WorkspacePersistence(async init=>{
  if(!init){const value=JSON.parse(disk);return {workspace:{projects:value.projects,activeId:value.activeId},revision};}
  const body=JSON.parse(init.body as string);writes.push(body.expected_revision);
  assert.equal(body.expected_revision,revision);disk=JSON.stringify(body.workspace);revision++;
  if(lose){lose=false;throw new WorkspaceRequestError('lost',true);}
  return {revision};
 });
 persistence.initialize(base,1);
 await assert.rejects(persistence.save(first),/lost/);
 await persistence.save(second);
 assert.deepEqual(writes,[1,2]);assert.equal(disk,second);assert.equal(persistence.saved,second);
});

test('retry after uncommitted failure keeps the revision, and a real conflict never writes',async()=>{
 const base=initial(),draft=changed(base,'draft');let disk=base,puts=0,fail=true;
 const persistence=new WorkspacePersistence(async init=>{
  if(!init)return {workspace:JSON.parse(disk),revision:1};
  puts++;if(fail)throw new WorkspaceRequestError('offline',true);
  return {revision:2};
 });
 persistence.initialize(base,1);await assert.rejects(persistence.save(draft));
 disk=changed(base,'another window');await assert.rejects(persistence.save(draft),/其他窗口/);assert.equal(puts,1);
 disk=base;fail=false;await persistence.save(draft);assert.equal(puts,2);assert.equal(persistence.saved,draft);
});

test('reverting while PUT is uncertain restores the old content after acknowledging the lost save',async()=>{
 const base=initial(),draft=changed(base,'draft');let disk=draft;
 const persistence=new WorkspacePersistence(async init=>{
  if(!init)return {workspace:JSON.parse(disk),revision:2};
  const body=JSON.parse(init.body as string);assert.equal(body.expected_revision,2);disk=JSON.stringify(body.workspace);return {revision:3};
 });
 persistence.initialize(base,1);persistence.pendingBody=draft;
 await persistence.save(base);assert.equal(disk,base);assert.equal(persistence.revision,3);
});

test('a timed-out request releases waiting and 422 errors do not auto-retry',async()=>{
 const original=globalThis.fetch;
 try{
  globalThis.fetch=async(_path,init)=>new Promise((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError'))));
  await assert.rejects(workspaceRequest(undefined,5),(error:WorkspaceRequestError)=>error.retryable);
  globalThis.fetch=async()=>new Response(JSON.stringify({detail:'invalid input'}),{status:422});
  await assert.rejects(workspaceRequest(),(error:WorkspaceRequestError)=>!error.retryable&&error.status===422);
 }finally{globalThis.fetch=original;}
});

test('workspace serialization ignores visual selection and preserves changed drafts',()=>{
 const serialize=workspaceSerializer(),workspace=demoWorkspace();const base=serialize(workspace);
 const next=structuredClone(workspace);next.projects[0].nodes[0].selected=true;
 assert.equal(serialize(next),base);
 const edited=structuredClone(next);edited.projects[0].name='edited';assert.notEqual(serialize(edited),base);
 assert.equal(serialize(workspace),base);
});

test('slow polling never overlaps and cancellation prevents another read',async()=>{
 let reads=0,release!:()=>void;
 const cancel=pollSerial(()=>{reads++;return new Promise<void>(resolve=>{release=resolve;});},()=>assert.fail(),2);
 await new Promise(resolve=>setTimeout(resolve,20));assert.equal(reads,1);
 cancel();release();await new Promise(resolve=>setTimeout(resolve,10));assert.equal(reads,1);
});

test('serial polling continues after a temporary status read failure', {timeout:1000}, async()=>{
 const errors:unknown[]=[];
 const failure=new Error('temporarily unavailable');
 let reads=0;
 let stop=()=>{};
 await new Promise<void>(resolve=>{
  stop=pollSerial(async()=>{
   if(++reads===1)throw failure;
   stop();
   resolve();
  },error=>errors.push(error),2,0);
 });
 assert.equal(reads,2);
 assert.deepEqual(errors,[failure]);
});
