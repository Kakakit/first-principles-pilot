import {test} from 'node:test';
import assert from 'node:assert/strict';
import {taskProject,nextAction,programDone,latestByNode,physicsSignature,inputDifferences,comparisonModel} from '../src/resultWorkspace.ts';
import {trendSegments} from '../src/resultTrend.ts';
import {canConnect,parseWorkspace} from '../src/model.ts';
import type {FrozenInput} from '../src/resultWorkspace.ts';

test('task templates create independent valid drafts without physics or jobs',()=>{
 for(const name of ['scf','relax-scf','electronic']){
  const a=taskProject(name),b=taskProject(name);assert.notEqual(a.id,b.id);
  for(const node of a.nodes){assert.equal(node.data.structure,undefined);assert.equal(node.data.parameters?.values.ENCUT,undefined);assert.equal(node.data.parameters?.values.ISPIN,undefined);if(['scf','bands','dos'].includes(node.data.kind)){assert.equal(node.data.parameters?.values.NSW,'0');assert.equal(node.data.parameters?.values.IBRION,'-1');}assert.equal(node.data.inputDraft,undefined);}
  for(const edge of a.edges)assert.equal(canConnect(a.nodes,a.edges.filter(e=>e!==edge),edge.source,edge.target),true);
  assert.equal(parseWorkspace(JSON.stringify({activeId:a.id,projects:[a]})).projects[0].id,a.id);
 }
});
test('unknown and failed tasks guide to exact result review; prepared tasks to run review',()=>{
 for(const state of ['UNKNOWN','DISPATCHING','FAILED'])assert.equal(nextAction({id:'old',state}).view,'results');
 assert.equal(nextAction({id:'ready',state:'PREPARED'}).view,'run');assert.equal(nextAction().view,'config');
});
test('completion requires explicit program evidence and does not imply physics',()=>{
 assert.equal(programDone({id:'x',state:'COMPLETED',evidence:{vasp_timing:'unknown'}}),false);
 assert.equal(programDone({id:'x',state:'COMPLETED',kind:'wannier90',evidence:{vasp_timing:true}}),false);
 assert.equal(programDone({id:'x',state:'COMPLETED',kind:'wannier90',evidence:{wannier_all_done:true}}),true);
});
test('latest node action respects creation time without losing older unknown records',()=>{
 const rows=[{id:'old',node_id:'n',state:'UNKNOWN',created_at:'2025-01-01'},{id:'new',node_id:'n',state:'COMPLETED',created_at:'2026-01-01'}];
 const copy=structuredClone(rows);assert.equal(latestByNode(rows).n.id,'new');assert.deepEqual(rows,copy);assert.equal(rows[0].state,'UNKNOWN');
});
const input=(saxis='0 0 1'):FrozenInput=>({id:'frozen',stage:'scf',parameters:{GGA:'PE',ISPIN:'2',SAXIS:saxis,MAGMOM:'0 0 4'},files:{POTCAR:{sha256:'P'}},species:['Fe'],geometry_sha256:'G',kpoints:'4 4 4'});
test('comparison describes actual SAXIS, geometry and k mesh differences',()=>{
 const changed={...input('1 1 0'),geometry_sha256:'other',kpoints:'6 6 6'};
 const notes=inputDifferences([input(),changed]);assert.ok(notes.some(s=>s.includes('SAXIS')));assert.ok(notes.some(s=>s.includes('结构不同')));assert.ok(notes.some(s=>s.includes('k 点')));
 assert.notEqual(physicsSignature(input()),physicsSignature(changed));assert.equal(physicsSignature({...changed,warning:'missing'}),'');
});
test('extended model identity breaks misleading lines for spin direction changes',()=>{
 const row=(value:number,i:FrozenInput)=>({stage:'scf',model_sha256:'legacy'+physicsSignature(i),potcar_sha256:'P',program_done:true,ediff_reached:true,metrics:{energy:{value,unit:'eV/cell',source:{sha256:'out'}}}});
 assert.deepEqual(trendSegments([row(-10,input()),row(-9,input('1 1 0'))],'energy'),[]);
 assert.equal(trendSegments([row(-10,input()),row(-9,input())],'energy').length,1);
});
test('different or unknown atom counts do not establish energy comparability',()=>{
 assert.notEqual(comparisonModel(input(),'legacy',2),comparisonModel(input(),'legacy',4));
 assert.equal(comparisonModel(input(),'legacy',undefined),undefined);
 assert.equal(comparisonModel({...input(),warning:'missing'},'legacy',2),undefined);
});
