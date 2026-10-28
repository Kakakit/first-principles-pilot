import test from 'node:test';
import assert from 'node:assert/strict';
import {makeStep,newProject,canConnect,parseWorkspace} from '../src/model';
import {calculationKind,calculationDefaults,upgradeScan} from '../src/scanNodes';

test('independent scan retains canvas identity and validates physical connections',()=>{
 const p=newProject('scan'),s=p.nodes[0],scan=makeStep('scan'),dos=makeStep('dos'),iface=makeStep('vasp-wannier');p.nodes.push(scan,dos,iface);
 assert.equal(calculationKind(scan,p),'scf');assert.ok(canConnect(p.nodes,p.edges,s.id,scan.id));assert.ok(canConnect(p.nodes,p.edges,scan.id,dos.id));
 const w90=calculationDefaults(scan,'wannier90');p.nodes=p.nodes.map(n=>n.id===scan.id?w90:n);
 assert.equal(w90.data.kind,'scan');assert.equal(w90.data.stageSequence,8);assert.ok(canConnect(p.nodes,p.edges,iface.id,w90.id));assert.equal(canConnect(p.nodes,p.edges,s.id,w90.id),false);
 assert.equal(parseWorkspace(JSON.stringify({activeId:p.id,projects:[p]})).projects[0].nodes[1].data.kind,'scan');
});
test('explicit legacy upgrade copies settings and reconnects upstream without changing base',()=>{
 const p=newProject('old'),s=p.nodes[0],base=makeStep('relax'),scan=makeStep('scan');delete scan.data.scanSpec;base.data.parameters={values:{ENCUT:'520',NSW:'100'}};
 p.nodes.push(base,scan);p.edges=[{id:'a',source:s.id,target:base.id},{id:'b',source:base.id,target:scan.id}];const original=structuredClone(p);
 const result=upgradeScan(p,scan),changed=result.nodes.find(n=>n.id===scan.id)!;
 assert.deepEqual(p,original);assert.equal(calculationKind(changed,result),'relax');assert.equal(result.edges.find(e=>e.target===scan.id)!.source,s.id);
 assert.equal(changed.data.parameters!.values.ENCUT,'520');assert.equal(changed.data.stageSequence,scan.data.stageSequence);assert.deepEqual(result.nodes.find(n=>n.id===base.id),base);
});

import {splitScanValues} from '../src/scanNodes.ts';
test('scan value lists preserve vectors, VASP quotes and incomplete editing text',()=>{
 assert.deepEqual(splitScanValues('Normal, Fast'),['Normal',' Fast']);
 assert.deepEqual(splitScanValues('0 0 1, 1 0 0'),['0 0 1',' 1 0 0']);
 assert.deepEqual(splitScanValues('"a,b", "c,d"'),['"a,b"',' "c,d"']);
 assert.deepEqual(splitScanValues('400,'),['400','']);
 assert.deepEqual(splitScanValues('"incomplete,'),['"incomplete,']);
});
