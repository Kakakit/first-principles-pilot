import {test} from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
import {newProject,makeStep,uid,parseWorkspace,cleanWorkspace,canConnect,inspectFlow,mergeWorkspace} from '../src/model.ts';
import {addStructureSnapshot,checkedStructureSnapshot} from '../src/structureSnapshot.ts';
import type {StructureSnapshot} from '../src/structureSnapshot.ts';
import {structureOriginEdges} from '../src/structureOrigin.ts';
const structure=JSON.parse(fs.readFileSync(new URL('./fixtures/Si.structure.json',import.meta.url),'utf8'));
function fixture(){const project=newProject('来源分支');project.nodes[0].data.structure=structure;const calculation=makeStep('relax',{x:400,y:80});project.nodes.push(calculation);project.edges=[{id:uid(),source:project.nodes[0].id,target:calculation.id}];const snapshot:StructureSnapshot={structure,origin:{version:1,project_id:project.id,node_id:calculation.id,attempt_id:'a'.repeat(32),job_id:'9.mock',alias:'offline',path:'CONTCAR',sha256:structure.source.sha256,input_note:'冻结备注',state:'FAILED',program_done:false,extracted_at:'2026-10-30T00:00:00Z',observed_at:'',cached:true},warnings:['来源未收敛']};return {project,snapshot};}
test('structure copy keeps original bytes and lineage separate from all executable edges',async()=>{
 const {project,snapshot}=fixture(),before=structuredClone(project);await checkedStructureSnapshot(snapshot);
 const added=addStructureSnapshot(project,snapshot),node=added.project.nodes.find(n=>n.id===added.nodeId)!;
 assert.deepEqual(project,before);assert.notEqual(node.data.structure,snapshot.structure);assert.equal(node.data.label,'CONTCAR 结构');assert.equal((node.data.structureOrigin as any).input_note,'冻结备注');assert.equal(structureOriginEdges(added.project)[0].label,'CONTCAR');
 assert.deepEqual(added.project.edges,project.edges);assert.equal(structureOriginEdges(added.project).length,1);assert.equal(structureOriginEdges(added.project)[0].targetHandle,'structure-origin');
 const next=makeStep('scf',{x:1100,y:80});added.project.nodes.push(next);assert(canConnect(added.project.nodes,added.project.edges,node.id,next.id));assert(!canConnect(added.project.nodes,added.project.edges,project.nodes[1].id,node.id));
 added.project.edges.push({id:uid(),source:node.id,target:next.id});assert(!inspectFlow(added.project).some(i=>i.level==='error'));
 const restored=parseWorkspace(JSON.stringify(cleanWorkspace({activeId:project.id,projects:[added.project]}))).projects[0];assert.deepEqual(restored.nodes.find(n=>n.id===node.id)?.data.structureOrigin,snapshot.origin);assert.equal(structureOriginEdges(restored).length,1);
});
test('repeated extraction focuses the same unedited version; changed file and edited copy remain separate',()=>{
 const {project,snapshot}=fixture(),first=addStructureSnapshot(project,snapshot),again=addStructureSnapshot(first.project,snapshot);assert(again.reused);assert.equal(first.nodeId,again.nodeId);
 const edited=structuredClone(first.project);edited.nodes.find(n=>n.id===first.nodeId)!.data.structure!.edit={id:'edit',parent_geometry_sha256:'a',geometry_sha256:'b',coordinate_mode:'fractional',at:'now',operation:'edit',parent_edit_id:null};
 assert(!addStructureSnapshot(edited,snapshot).reused);
 const later=structuredClone(snapshot);later.origin.attempt_id='b'.repeat(32);assert(!addStructureSnapshot(first.project,later).reused);
 const imported=mergeWorkspace({activeId:project.id,projects:[project]},{activeId:project.id,projects:[first.project]}).projects.at(-1)!;assert.equal(structureOriginEdges(imported).length,1);assert.deepEqual(imported.edges.length,first.project.edges.length);assert.deepEqual(imported.nodes.find(n=>n.data.structureOrigin)?.data.structureOrigin,snapshot.origin);
 assert.deepEqual(structureOriginEdges({...first.project,nodes:first.project.nodes.filter(n=>n.id!==snapshot.origin.node_id)}),[]);
});
test('wrong project and corrupt original identity cannot attach a structure',async()=>{
 const {project,snapshot}=fixture(),wrong=structuredClone(snapshot);wrong.origin.project_id='other';assert.throws(()=>addStructureSnapshot(project,wrong),/当前项目/);
 const corrupt=structuredClone(snapshot);corrupt.origin.sha256='0'.repeat(64);assert.throws(()=>addStructureSnapshot(project,corrupt),/不一致/);
 const badBytes=structuredClone(snapshot);badBytes.structure.source.base64=btoa('wrong');await assert.rejects(checkedStructureSnapshot(badBytes),/SHA-256/);
});
