import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateStructure,verifySource} from '../src/structure.ts';
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
