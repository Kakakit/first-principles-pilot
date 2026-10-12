import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateStructure,verifySource,currentPoscar} from '../src/structure.ts';
import {demoWorkspace,parseWorkspace,mergeWorkspace} from '../src/model.ts';
const fixture=()=>JSON.parse(readFileSync(new URL('./fixtures/Si.structure.json',import.meta.url),'utf8'));
test('real structure survives workflow backup and independent import',async()=>{
 const s=fixture();validateStructure(s);await verifySource(s);
 const w=demoWorkspace();w.projects[0].nodes[0].data.structure=s;
 const restored=parseWorkspace(JSON.stringify(w));
 assert.deepEqual(restored.projects[0].nodes[0].data.structure,s);
 const imported=mergeWorkspace(w,restored);assert.deepEqual(imported.projects[1].nodes[0].data.structure,s);
});
test('corrupt source bytes and invalid geometry are rejected',async()=>{
 const s=fixture();s.source.base64=btoa('wrong file');await assert.rejects(verifySource(s),/SHA-256/);
 s.source.base64='?';assert.throws(()=>validateStructure(s),/编码/);
 const broken=fixture();broken.atoms[0].fractional[0]=NaN;assert.throws(()=>validateStructure(broken),/结构记录/);
});
test('explicit POSCAR survives project backup and overrides only current export',()=>{
 const s=fixture();s.poscarText='EOS\n1.01\n'+s.poscar.split('\n').slice(2).join('\n');
 const original=s.source.base64;validateStructure(s);assert.equal(currentPoscar(s),s.poscarText);
 const w=demoWorkspace();w.projects[0].nodes[0].data.structure=s;
 const restored=parseWorkspace(JSON.stringify(w)).projects[0].nodes[0].data.structure!;
 assert.equal(restored.poscarText,s.poscarText);assert.equal(restored.source.base64,original);
 delete restored.poscarText;assert.equal(currentPoscar(restored),restored.poscar);
});
test('invalid or oversized explicit POSCAR cannot be loaded as a draft',()=>{
 for(const text of ['',42,'x'.repeat(262145),'中'.repeat(100000)]){
  const s=fixture();s.poscarText=text;assert.throws(()=>validateStructure(s),/POSCAR/);
 }
});
