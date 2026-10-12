import test from 'node:test';
import assert from 'node:assert/strict';
import {selectedSourceOrder,sourceCaption,retainedSourceFiles} from '../src/extensionSourceSelection';
const catalog=Array.from({length:65},(_,i)=>({id:`run-${i}`,name:`体积 ${i}`,stage:'scf',alias:'server',state:'COMPLETED',job_id:`${i}.node01`,input_reason:'EOS 扫描'}));
test('batch selection retains source order, removes duplicates and excludes the primary',()=>{
 assert.deepEqual(selectedSourceOrder(['run-4','run-2'],['run-3','run-2','run-4','run-3','run-0'],catalog,'run-0',64),['run-4','run-2','run-3']);
 assert.deepEqual(selectedSourceOrder(['run-4'],[],catalog,'run-0',2),[]);
});
test('64 includes the fixed primary; extension-specific limits and vanished records never truncate',()=>{
 assert.equal(selectedSourceOrder([],catalog.slice(0,64).map(s=>s.id),catalog,'run-0',64).length,63);
 assert.throws(()=>selectedSourceOrder([],catalog.map(s=>s.id),catalog,'run-0',64),/最多可选 64/);
 assert.throws(()=>selectedSourceOrder([],['run-1','run-2'],catalog,'run-0',2),/最多可选 2/);
 assert.throws(()=>selectedSourceOrder([],['vanished'],catalog,'run-0',64),/列表已变化/);
});
test('batch captions include node, job, channel and note without exposing identifiers',()=>{
 assert.equal(sourceCaption({...catalog[0],id:'a'.repeat(32),seed:'wannier90.2'}),'体积 0 · 通道 2 · 0.node01 · EOS 扫描');
});
test('changing a batch preserves explicit file choices by source identity',()=>{
 const files={'0':{structure:'CONTCAR'},'1':{outcar:'OUTCAR'},'2':{structure:'POSCAR'}};
 const next=retainedSourceFiles('primary',['one','two'],['two','new'],files);
 assert.deepEqual(next,{'0':{structure:'CONTCAR'},'1':{structure:'POSCAR'}});
 next['0'].structure='POSCAR';assert.equal(files['0'].structure,'CONTCAR');
});
