import assert from 'node:assert/strict';
import test from 'node:test';
import {newProject,makeStep,parseWorkspace} from '../src/model';
import {attachContinuation,type ContinuationCreated} from '../src/scanContinuation';
import {copyDraftNode} from '../src/nodeTypes';

test('a returned continuation adds one node and preserves source and existing history fields',()=>{
 const project=newProject('source');const source=makeStep('scan');project.nodes.push(source);
 const node=makeStep('scan');node.data.scanSpec={calculationKind:'scf'};node.data.continuationBatchId='batch';
 const result:ContinuationCreated={project_id:project.id,node,edge:{id:'edge',source:source.id,target:node.id},batch:{id:'batch',state:'FROZEN',case_count:3}};
 const before=structuredClone(project),next=attachContinuation(project,result);
 assert.deepEqual(project,before);assert.equal(next.nodes.length,3);
 assert.equal(attachContinuation(next,result),next);
 assert.doesNotThrow(()=>parseWorkspace(JSON.stringify({activeId:next.id,projects:[next]})));
 assert.throws(()=>attachContinuation(newProject('other'),result),/项目已切换/);
 assert.equal(copyDraftNode(next,node).data.continuationBatchId,undefined);
 const edited=structuredClone(project);edited.nodes[0].data.note='创建期间继续保留的草稿';
 assert.equal(attachContinuation(edited,result).nodes[0].data.note,edited.nodes[0].data.note);
});
