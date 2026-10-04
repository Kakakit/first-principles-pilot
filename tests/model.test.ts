import {test} from 'node:test';
import assert from 'node:assert/strict';
import {demoWorkspace,parseWorkspace,cleanWorkspace,canConnect,inspectFlow,mergeWorkspace,makeStep} from '../src/model.ts';

test('saved workflow round-trips nodes, parameters, positions and connections',()=>{
  const w=demoWorkspace();w.projects[0].nodes[1].data.params.encut='520';w.projects[0].nodes[1].position.x=123;
  assert.deepEqual(parseWorkspace(JSON.stringify(w)),w);
});
test('importing backup appends independent projects without overwriting the source',()=>{
  const w=demoWorkspace(), original=structuredClone(w);const merged=mergeWorkspace(w,w);
  assert.deepEqual(w,original);assert.equal(merged.projects.length,2);
  assert.notEqual(merged.projects[1].id,w.projects[0].id);
  assert.notEqual(merged.projects[1].remoteSlug,w.projects[0].remoteSlug);
  const twice=mergeWorkspace(merged,w);
  assert.equal(new Set(twice.projects.map(p=>p.remoteSlug)).size,3);
  const long=structuredClone(w);long.projects[0].remoteSlug='x'.repeat(64);
  assert.match(mergeWorkspace(w,long).projects[1].remoteSlug!,/^[a-z0-9-]{1,64}$/);
  assert(merged.projects[1].edges.every(e=>merged.projects[1].nodes.some(n=>n.id===e.source)&&merged.projects[1].nodes.some(n=>n.id===e.target)));
  assert.deepEqual(parseWorkspace(JSON.stringify(merged)),merged);
});
test('reject malformed backup, historical shape and dangling edge before import',()=>{
  assert.throws(()=>parseWorkspace('{'));
  assert.throws(()=>parseWorkspace('{"version":2,"projects":[]}'));
  const w=demoWorkspace();w.projects[0].edges[0].target='missing';assert.throws(()=>parseWorkspace(JSON.stringify(w)));
});
test('reject duplicate node identity and unsupported node kind',()=>{
  const w=demoWorkspace();w.projects[0].nodes.push(w.projects[0].nodes[0]);assert.throws(()=>parseWorkspace(JSON.stringify(w)));
  const other=demoWorkspace();(other.projects[0].nodes[0].data as any).kind='execute-shell';assert.throws(()=>parseWorkspace(JSON.stringify(other)));
});
test('prevent self edges, duplicate edges and connections into structure',()=>{
  const p=demoWorkspace().projects[0];assert(!canConnect(p.nodes,p.edges,p.nodes[1].id,p.nodes[1].id));
  assert(!canConnect(p.nodes,p.edges,p.nodes[0].id,p.nodes[1].id));
  assert(!canConnect(p.nodes,p.edges,p.nodes[2].id,p.nodes[0].id));
});
test('prevent longer cycles while allowing a new branch',()=>{
  const p=demoWorkspace().projects[0];assert(!canConnect(p.nodes,p.edges,p.nodes[3].id,p.nodes[1].id));
  const dos=makeStep('dos');p.nodes.push(dos);assert(canConnect(p.nodes,p.edges,p.nodes[2].id,dos.id));
});
test('graph check distinguishes disconnected nodes and does not assert science passed',()=>{
  const p=demoWorkspace().projects[0];const good=inspectFlow(p);assert(good.every(i=>i.level==='info'));assert.match(good[0].message,/尚未检查物理参数/);
  p.edges=[];assert(inspectFlow(p).some(i=>i.level==='error'&&i.nodeId===p.nodes[1].id));
});
test('cycle in an imported document is reported by flow inspection',()=>{
  const p=demoWorkspace().projects[0];p.edges.push({id:'cycle',source:p.nodes[3].id,target:p.nodes[1].id});
  assert(inspectFlow(p).some(i=>i.message.includes('循环')));
});
test('saving a temporarily empty name produces a recoverable document',()=>{
  const w=demoWorkspace();w.projects[0].name='';w.projects[0].nodes[1].data.label='';
  const saved=cleanWorkspace(w);assert.doesNotThrow(()=>parseWorkspace(JSON.stringify(saved)));
  assert.equal(w.projects[0].name,'');
});
test('backup omits transient rendering and selection state',()=>{
  const w=demoWorkspace();w.projects[0].nodes[0].selected=true;w.projects[0].nodes[0].measured={width:200,height:150};
  assert(!('selected' in cleanWorkspace(w).projects[0].nodes[0]));assert(!('measured' in cleanWorkspace(w).projects[0].nodes[0]));
});
test('reject historical workspace document shape',()=>{
  const old=demoWorkspace() as any;old.version=2;
  assert.throws(()=>parseWorkspace(JSON.stringify(old)));
});
test('scan is a container with one relax or SCF parent',()=>{
  const structure=makeStep('structure'),scf=makeStep('scf'),scan=makeStep('scan');
  assert.equal(scan.data.executionMode,'scan');
  assert.equal(canConnect([structure,scf,scan],[],scf.id,scan.id),true);
  assert.equal(canConnect([structure,scf,scan],[],structure.id,scan.id),false);
  assert.equal(canConnect([structure,scf,scan],[],scan.id,scf.id),false);
  assert.equal(parseWorkspace(JSON.stringify({activeId:'p',projects:[{id:'p',name:'P',remoteSlug:'p',nodes:[scan],edges:[],updatedAt:new Date().toISOString()}]})).projects[0].nodes[0].data.kind,'scan');
  scan.data.executionMode='vasp';
  assert.throws(()=>parseWorkspace(JSON.stringify({activeId:'p',projects:[{id:'p',name:'P',remoteSlug:'p',nodes:[scan],edges:[],updatedAt:new Date().toISOString()}]})));
});
