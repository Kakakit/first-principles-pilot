import {test} from 'node:test';
import assert from 'node:assert/strict';
import {relativeChange,latticeCsv,latticeStatus,latticeVerified} from '../src/latticeMetrics.ts';
import type {LatticeRow} from '../src/latticeMetrics.ts';

const row:LatticeRow={attempt_id:'A',node_id:'relax',stage:'relax',program_done:true,ediff_reached:true,
 metrics:{lattice_a:{value:4,unit:'Å',source:{file:'OUTCAR',sha256:'hash',host:'fixture',method:'last lattice'}},volume:{value:64,unit:'Å³',source:{file:'OUTCAR'}},ionic_converged:true},
 input:{parameters:{LDAUU:'3 4 0',ENCUT:'500'},files:{}}};
test('lattice deltas use an explicit positive reference and keep missing values empty',()=>{
 assert.equal(relativeChange(4.4,4),10.000000000000009);
 assert.equal(relativeChange(3.6,4),-9.999999999999998);
 for(const [value,reference] of [[null,4],[4,undefined],[4,0],[4,NaN],[Infinity,4]])assert.equal(relativeChange(value,reference),null);
});
test('lattice evidence distinguishes optimization, static cells and incomplete results',()=>{
 assert.equal(latticeStatus(row),'优化已收敛');assert.equal(latticeVerified(row),true);
 assert.equal(latticeStatus({...row,metrics:{...row.metrics,ionic_converged:false}}),'离子收敛未确认');
 assert.equal(latticeVerified({...row,metrics:{...row.metrics,ionic_converged:false}}),false);
 assert.equal(latticeStatus({...row,stage:'scf'}),'非结构优化阶段');
 assert.equal(latticeStatus({...row,metrics:{lattice_error:'missing'}}),'晶格未提取');
 assert.equal(latticeStatus({...row,metrics:null}),'尚未读取');
});
test('CSV retains parameters, reference and output provenance without filling missing cells',()=>{
 const csv=latticeCsv([row,{...row,attempt_id:'B',metrics:null}],'A',r=>r.attempt_id==='A'?'=cell,"reference"':'missing');
 assert.ok(csv.startsWith('\uFEFF'));assert.ok(csv.includes('"a / Å"'));assert.ok(csv.includes('"ΔV / %"'));
 assert.ok(csv.includes('"\'=cell,""reference"""'));assert.ok(csv.includes('"LDAUU"":""3 4 0"'));
 assert.ok(csv.includes('"fixture","OUTCAR","hash"'));assert.ok(csv.includes('"missing","B","relax","尚未读取","","","","","","",""'));
});
