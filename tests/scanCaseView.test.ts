import{test}from'node:test';import assert from'node:assert/strict';import{casePage,scanBatchProgress}from'../src/scanCaseView';
const cases=Array.from({length:23},(_,index)=>({id:String(index),index,state:index%2?'FAILED':'COMPLETED',parameters:[{key:'ENCUT',value:String(400+index*20)}]}));
test('case search combines original coordinates and state without changing saved order',()=>{const before=JSON.stringify(cases);assert.deepEqual(casePage(cases,'ENCUT=420','FAILED',0).items.map(c=>c.index),[1]);assert.equal(casePage(cases,'case 23','',0).items[0].index,22);assert.equal(JSON.stringify(cases),before);});
test('pages clamp after filters and empty results remain a usable first page',()=>{assert.deepEqual(casePage(cases,'','',2).items.map(c=>c.index),[20,21,22]);assert.equal(casePage(cases,'420','',2).page,0);assert.equal(casePage(cases,'absent','FAILED',8).pages,1);assert.equal(casePage(cases,'absent','FAILED',8).items.length,0);});
test('scan cards expose waiting and worker failures while paused batches keep their actual state',()=>{
 const waiting={state:'ACTIVE',cases:[{state:'PENDING'},{state:'CANCELLED'}],waiting_reason:'候选节点暂时占用：node01'};
 assert.deepEqual(scanBatchProgress(waiting),{label:'1/2 · 等待派发',hint:waiting.waiting_reason});
 assert.deepEqual(scanBatchProgress({...waiting,last_worker_error:'SSH unavailable'}),{label:'1/2 · 派发待核查',hint:'SSH unavailable'});
 assert.deepEqual(scanBatchProgress({...waiting,state:'PAUSED'}),{label:'1/2 · 已暂停',hint:''});
 assert.deepEqual(scanBatchProgress({...waiting,waiting_reason:''}),{label:'1/2 · 执行中',hint:''});
 assert.deepEqual(scanBatchProgress({...waiting,cases:[{state:'RUNNING'},{state:'PENDING'}]}),{label:'0/2 · 在途 1',hint:waiting.waiting_reason});
});
