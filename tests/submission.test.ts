import {test} from 'node:test';
import assert from 'node:assert/strict';
import {newProject,makeStep,cleanWorkspace} from '../src/model.ts';
import {independentBranch} from '../src/independentBranch.ts';
import {runtimeFor,emptySettings} from '../src/submissionDefaults.ts';
import {effectiveParameters,checkParameters} from '../src/parameters.ts';
import {inputForReview} from '../src/workflowUi.ts';
test('case and planned bundles never enter the base-node single-task selector',()=>{
 const rows=[{id:'case',node_id:'base',execution_scope:'scan',status:'ready-for-execution-review'},{id:'chain',node_id:'base',execution_scope:'base',source_contract:{mode:'planned'},status:'ready-for-execution-review'},{id:'single',node_id:'base',execution_scope:'base',status:'ready-for-execution-review'}];
 assert.equal(inputForReview(rows,'base'),'single');assert.equal(inputForReview(rows,'base','case'),'');assert.equal(inputForReview(rows,'base','chain'),'');
});
test('independent branch copies local drafts with new identities and removes remote lineage',()=>{
 const p=newProject('分支');const n=p.nodes.find(n=>n.data.kind==='scf')||makeStep('scf',{x:0,y:0},2);if(!p.nodes.includes(n))p.nodes.push(n);
 n.data.inputDraft={grid:'4 4 4',path_source_attempt_id:'remote',path_structure_hash:'hash'};n.data.sourceDraft={mode:'self',attempt_id:'remote'};n.data.authorization={};
 const copy=independentBranch(p,n.id);const c=copy.nodes.find(x=>x.data.kind==='scf')!;
 assert.notEqual(copy.id,p.id);assert.notEqual(c.id,n.id);assert.equal((c.data.inputDraft as Record<string,string>).grid,'4 4 4');assert.equal((c.data.inputDraft as Record<string,string>).path_source_attempt_id,undefined);assert.equal((c.data.sourceDraft as {mode:string}).mode,'new');assert.equal(c.data.authorization,undefined);
 assert.equal((n.data.inputDraft as Record<string,string>).path_source_attempt_id,'remote');
 assert.doesNotThrow(()=>cleanWorkspace({activeId:copy.id,projects:[copy]}));
});
test('runtime uses explicit registered SOC path and host-program defaults',()=>{
 const profiles=[{alias:'host',scheduler:'pbs',queue:'q',vasp_executable:'/std/vasp_std',ncl_executable:'/soc/vasp_ncl'}] as Parameters<typeof runtimeFor>[1];
 assert.equal(runtimeFor({alias:'host'},profiles,emptySettings,'host','vasp_ncl').vasp_executable,'/soc/vasp_ncl');
 const settings={...emptySettings,runtimes:{'host:vasp_ncl':{alias:'host',vasp_executable:'/custom/vasp_ncl'}}};
 assert.equal(runtimeFor({alias:'host'},profiles,settings,'host','vasp_ncl').vasp_executable,'/custom/vasp_ncl');
 assert.equal(runtimeFor({alias:'host'},profiles,settings,'host','vasp_std').vasp_executable,'/std/vasp_std');
});
test('copied downstream stages refer to the copied parent and scan templates stay independent',()=>{
 const p=newProject('计算');const r=makeStep('relax',{x:0,y:0},1),c=makeStep('scf',{x:100,y:0},2),d=makeStep('dos',{x:200,y:0},3),scan=makeStep('scan',{x:200,y:100},4);
 p.nodes.push(r,c,d,scan);p.edges.push({id:'rc',source:r.id,target:c.id},{id:'cd',source:c.id,target:d.id},{id:'cs',source:c.id,target:scan.id});
 const copied=independentBranch(p,c.id);
 assert.equal((copied.nodes.find(n=>n.data.kind==='scf')!.data.sourceDraft as {mode:string}).mode,'new');
 assert.equal((copied.nodes.find(n=>n.data.kind==='dos')!.data.sourceDraft as {mode:string}).mode,'upstream');
 assert.equal((copied.nodes.find(n=>n.data.kind==='scan')!.data.sourceDraft as {mode:string}).mode,'new');
 assert.equal(copied.edges.some(e=>e.source===r.id||e.target===c.id),false);
});
test('electronic source governs displayed restart parameters and legacy values do not override it',()=>{
 const p=newProject('来源'),n=makeStep('scf',{x:0,y:0},1);p.nodes.push(n);
 n.data.parameters={values:{ISTART:'0',ICHARG:'2'}};n.data.sourceDraft={mode:'self',files:'both'};
 assert.equal(effectiveParameters(p,n).ISTART.value,'1');assert.equal(effectiveParameters(p,n).ICHARG.value,'1');
 assert.equal(checkParameters(p,n).some(i=>/ISTART|ICHARG/.test(i.message)),false);
 n.data.sourceDraft={mode:'new',files:'charge'};assert.equal(effectiveParameters(p,n).ISTART.value,'0');assert.equal(effectiveParameters(p,n).ICHARG.value,'2');
});
