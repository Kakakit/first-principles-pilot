import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {demoWorkspace,parseWorkspace,makeStep} from '../src/model.ts';
import {effectiveParameters,ionicCriterion,checkParameters,upstreamStructure,parameterTables} from '../src/parameters.ts';
const fixture=()=>JSON.parse(readFileSync(new URL('./fixtures/Si.structure.json',import.meta.url),'utf8'));
function sample(){const w=demoWorkspace();w.projects[0].nodes[0].data.structure=fixture();return w;}
test('shared physics survives stage preset; node override and explicit unset have provenance',()=>{
 const w=sample(),p=w.projects[0],node=p.nodes[2];p.parameters={version:1,shared:{values:{ENCUT:'520',NSW:'100',ISPIN:'2'}}};node.data.parameters={values:{ENCUT:'600'},preset:'basic'};
 const e=effectiveParameters(p,node);assert.deepEqual(e.ENCUT,{value:'600',source:'节点覆盖'});assert.deepEqual(e.NSW,{value:'0',source:'阶段预设'});assert.equal(e.ISPIN.value,'2');
 node.data.parameters.values.ENCUT=null;assert.deepEqual(effectiveParameters(p,node).ENCUT,{value:null,source:'节点覆盖'});
 delete node.data.parameters.values.ENCUT;assert.deepEqual(effectiveParameters(p,node).ENCUT,{value:'520',source:'共享设置'});
});
test('legacy displayed defaults do not become scientific values; templates roundtrip',()=>{
 const w=sample(),p=w.projects[0],n=p.nodes[1];assert.equal(n.data.params.functional,'PBE');assert.equal(effectiveParameters(p,n).XC_FUNCTIONAL.value,null);
 p.parameters={version:1,shared:{values:{EDIFF:'1e-6'}},templates:[{name:'my precision',layer:{values:{ENCUT:'510'}}}]};
 assert.deepEqual(parseWorkspace(JSON.stringify(w)).projects[0].parameters,p.parameters);
});
test('fractional structure edit retains magnetic identity; another source is not mapped by position',()=>{
 const p=sample().projects[0],n=p.nodes[1],s=p.nodes[0].data.structure!;p.parameters={version:1,shared:{values:{ISPIN:'2'},moments:{[s.atoms[0].id]:'2',[s.atoms[1].id]:'-2'}}};
 const before=parameterTables(p,n);s.atoms[1].fractional[0]=.3;assert.deepEqual(parameterTables(p,n),before);
 s.atoms.forEach(a=>a.id='new:'+a.id);assert.ok(checkParameters(p,n).some(i=>i.message.includes('部分原子')));
 assert.equal(parameterTables(p,n).moments[s.atoms[0].id],undefined);
});
test('multiple upstream structure roots are ambiguous even when one is not imported',()=>{
 const p=sample().projects[0],n=p.nodes[1];assert.equal(upstreamStructure(p,n)?.atoms.length,2);
 const other=makeStep('structure');p.nodes.push(other);p.edges.push({id:'extra',source:other.id,target:n.id});assert.equal(upstreamStructure(p,n),undefined);
});
test('DFT+U requires explicit orbitals and U/J and supports non-spin-polarized +U',()=>{
 const p=sample().projects[0],n=p.nodes[2];p.parameters={version:1,shared:{values:{ISPIN:'1',LDAU:'true',LDAUTYPE:'2'}}};n.data.parameters={preset:'basic',values:{}};
 assert.ok(checkParameters(p,n).some(i=>i.message.includes('LDAUL')));
 p.parameters.shared.hubbard={Si:{l:'1',u:'3',j:'0'}};
 assert.equal(checkParameters(p,n).filter(i=>i.level==='error').length,0);
 p.parameters.shared.hubbard.Si.u='bad';assert.ok(checkParameters(p,n).some(i=>i.message.includes('U/J')));
});
test('method meaning and numerical bounds checked without inventing scientific defaults',()=>{
 const p=sample().projects[0],n=p.nodes[1];n.data.parameters={preset:'basic',values:{NSW:'20',EDIFFG:'-0.02',EDIFF:'0'}};
 assert.equal(checkParameters(p,n).filter(i=>i.level==='error').length,0);
 n.data.parameters.values.NSW='2.5';assert.ok(checkParameters(p,n).some(i=>i.message.includes('NSW')));
 const band=p.nodes[3];band.data.parameters={values:{ISMEAR:'-5'}};assert.ok(checkParameters(p,band).some(i=>i.message.includes('能带路径')));
});
test('parameter resolution does not mutate shared settings or adjacent stages',()=>{
 const p=sample().projects[0];p.parameters={version:1,shared:{values:{ENCUT:'480'}}};const original=structuredClone(p);
 effectiveParameters(p,p.nodes[1]);checkParameters(p,p.nodes[2]);assert.deepEqual(p,original);
});

test('DOS and bands default to LORBIT 11 while explicit overrides survive',()=>{
 const p=sample().projects[0];const band=p.nodes[3];assert.deepEqual(effectiveParameters(p,band).LORBIT,{value:'11',source:'节点执行合同'});
 band.data.parameters={values:{LORBIT:'10'}};assert.equal(effectiveParameters(p,band).LORBIT.value,'10');
});

test('SOC requires vector moments, noncollinear spin setting and nonzero SAXIS',()=>{
 const p=sample().projects[0],n=p.nodes[2],atoms=p.nodes[0].data.structure!.atoms;
 p.parameters={version:1,shared:{values:{ISPIN:'1',LSORBIT:'true',SAXIS:'0 0 1'},moments:Object.fromEntries(atoms.map(a=>[a.id,'0 0 3']))}};
 assert.equal(checkParameters(p,n).filter(i=>i.level==='error').length,0);
 p.parameters.shared.values.ISPIN='2';assert.ok(checkParameters(p,n).some(i=>i.message.includes('ISPIN=1')));
 p.parameters.shared.values.ISPIN='1';p.parameters.shared.moments![atoms[0].id]='3';assert.ok(checkParameters(p,n).some(i=>i.message.includes('三个 MAGMOM')));
 p.parameters.shared.moments![atoms[0].id]='0 0 3';p.parameters.shared.values.SAXIS='0 0 0';assert.ok(checkParameters(p,n).some(i=>i.message.includes('SAXIS')));
});

test('zero force/energy thresholds are invalid drafts; only explicit steps accepts zero',()=>{
 assert.equal(ionicCriterion('steps',''),'0');assert.equal(ionicCriterion('force','0'),'-0');assert.equal(ionicCriterion('energy','0.0'),'+0.0');assert.equal(ionicCriterion('force','0.02'),'-0.02');assert.equal(ionicCriterion('force','0.'),'-0.');
 const p=sample().projects[0],n=p.nodes[1];n.data.parameters={preset:'basic',values:{NSW:'20',EDIFFG:'-0'}};assert.ok(checkParameters(p,n).some(i=>i.level==='error'&&i.message.includes('阈值')));
 n.data.parameters.values.EDIFFG='0';assert.equal(checkParameters(p,n).filter(i=>i.level==='error').length,0);
});
