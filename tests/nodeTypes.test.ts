import {test} from 'node:test';
import assert from 'node:assert/strict';
import {newProject,makeStep,mergeWorkspace,parseWorkspace} from '../src/model.ts';
import {STAGE_SEQUENCES,nextStageSequence} from '../src/stagePolicy.ts';
import {copyDraftNode,typeSwitchPreview} from '../src/nodeTypes.ts';
import {effectiveParameters,checkParameters} from '../src/parameters.ts';
import {taskProject} from '../src/resultWorkspace.ts';

test('new type sequences survive deletion, repeated creation and independent import',()=>{
  const p=newProject('new');const scf=makeStep('scf');p.nodes.push(scf,makeStep('bands'));
  assert.equal(nextStageSequence(p,'scf'),3);p.nodes=p.nodes.filter(n=>n.id!==scf.id);
  assert.equal(nextStageSequence(p,'scf'),3);
  assert.equal(copyDraftNode(p,makeStep('scf')).data.stageSequence,3);
  assert.deepEqual(taskProject('electronic').nodes.map(n=>n.data.stageSequence),[1,3,4,5]);
  const legacy=structuredClone(p);delete legacy.stagePolicy;legacy.nodes[1].data.stageSequence=23;
  const w={activeId:legacy.id,projects:[legacy]};
  assert.deepEqual(parseWorkspace(JSON.stringify(w)),w);assert.equal(nextStageSequence(legacy,'scf'),24);
  const imported=mergeWorkspace(w,w).projects[1];assert.equal(imported.stagePolicy,'kind-fixed-v1');
  for(const n of imported.nodes)assert.equal(n.data.stageSequence,STAGE_SEQUENCES[n.data.kind]);
  assert.equal(legacy.nodes[1].data.stageSequence,23);
});

test('relax copy becomes SCF without sharing history or changing scientific settings',()=>{
  const p=newProject('test'),relax=makeStep('relax');p.nodes.push(relax);
  relax.data.parameters={values:{NSW:'200',IBRION:'2',ENCUT:'520',LSORBIT:'true',LDAU:'true'},moments:{atom:'0 0 4'},hubbard:{Cr:{l:'2',u:'3',j:'0'}},confirmed_custom_tags:['MYTAG']};
  relax.data.inputDraft={grid:'6 6 6',center:'Gamma',potcar_id:'potcar',path:'saved path',path_source_attempt_id:'old',path_structure_hash:'old'};
  relax.data.sourceDraft={mode:'self',attempt_id:'old'};relax.data.authorization={approved:true};
  const before=structuredClone(relax),copy=copyDraftNode(p,relax);p.nodes.push(copy);
  const result=typeSwitchPreview(p,copy,'scf');
  assert.deepEqual(relax,before);assert.notEqual(copy.id,relax.id);assert.equal(result.node.data.stageSequence,3);
  assert.deepEqual(result.node.data.parameters?.values,{NSW:'0',IBRION:'-1',ENCUT:'520',LSORBIT:'true',LDAU:'true'});
  assert.deepEqual(result.node.data.parameters?.moments,relax.data.parameters.moments);
  assert.deepEqual(result.node.data.parameters?.hubbard,relax.data.parameters.hubbard);
  assert.deepEqual(result.node.data.inputDraft,{grid:'6 6 6',center:'Gamma',potcar_id:'potcar',path:'saved path'});
  assert.equal(result.node.data.sourceDraft,undefined);assert.equal(result.node.data.authorization,undefined);
  assert.equal(result.node.data.parameters?.confirmed_custom_tags,undefined);
});

test('legacy switches retain sequence/custom suffix and warn about affected edges',()=>{
  const p=newProject('test');delete p.stagePolicy;
  const n=makeStep('relax',undefined,17);n.data.stageSlug='my-study';p.nodes.push(n);
  const band=makeStep('bands',undefined,18);p.nodes.push(band);p.edges.push({id:'edge',source:n.id,target:band.id});
  const result=typeSwitchPreview(p,n,'dos');assert.equal(result.node.data.stageSequence,17);assert.equal(result.node.data.stageSlug,'my-study');
  assert(result.issues.some(i=>i.nodeId===band.id));assert.equal(p.edges.length,1);
  assert.throws(()=>typeSwitchPreview(p,n,'wannier90'));
});

test('static to relax requires NSW, preserves usable values and respects explicit edits',()=>{
  const p=newProject('test'),n=makeStep('scf');p.nodes.push(n);
  const result=typeSwitchPreview(p,n,'relax');assert.equal(result.node.data.parameters?.values.NSW,null);assert.equal(result.node.data.parameters?.values.IBRION,'2');
  n.data.parameters!.values.NSW='80';n.data.parameters!.values.IBRION='1';
  assert.equal(typeSwitchPreview(p,n,'relax').node.data.parameters?.values.NSW,'80');
  assert.equal(typeSwitchPreview(p,n,'relax').node.data.parameters?.values.IBRION,'1');
  assert(checkParameters(p,n).some(i=>i.message.includes('NSW')));assert(checkParameters(p,n).some(i=>i.message.includes('IBRION')));
  n.data.parameters!.values.NSW=null;assert.equal(effectiveParameters(p,n).NSW.value,null);
});

test('interface effective parallel values match execution contract without modifying old draft',()=>{
  const p=newProject('test'),n=makeStep('vasp-wannier');p.nodes.push(n);
  assert.deepEqual(n.data.parameters?.values,{NSW:'0',IBRION:'-1',NCORE:'1'});
  n.data.parameters!.values.NCORE='6';n.data.parameters!.values.KPAR='2';const before=structuredClone(n);
  const e=effectiveParameters(p,n);assert.equal(e.NCORE.value,'1');assert.equal(e.KPAR.value,'1');assert.equal(e.NCORE.source,'节点执行合同');assert.deepEqual(n,before);
});
