import test from 'node:test';
import assert from 'node:assert/strict';
import {compactHistory,canRecycle} from '../src/historyModel';
test('compact histories keep newest records, selected old record and unknown monitors',()=>{
 const rows=Array.from({length:8},(_,i)=>({id:String(i),state:i===7?'UNKNOWN':'FAILED'}));
 assert.deepEqual(compactHistory(rows,3,'6').map(r=>r.id),['0','1','2','6','7']);
 assert.equal(rows.length,8);
});
test('frozen plans are compactable while authorized or paused plans remain visible',()=>{
 const rows=Array.from({length:6},(_,i)=>({id:String(i),state:i===5?'PAUSED':'FROZEN'}));
 assert.deepEqual(compactHistory(rows,3,undefined,'chains').map(r=>r.id),['0','1','2','5']);
 assert.equal(canRecycle('FROZEN','chains'),true);assert.equal(canRecycle('ACTIVE','chains'),true);
});
test('all task and analysis states allow user-directed recycling',()=>{
 for(const state of ['RUNNING','UNKNOWN','DISPATCHING','SUBMITTED'])assert.equal(canRecycle(state),true);
 assert.equal(canRecycle('PREPARED'),true);assert.equal(canRecycle('FAILED','analyses'),true);
 assert.equal(canRecycle('PREPARED','analyses'),true);assert.equal(canRecycle(undefined,'saved-plots'),true);
});
