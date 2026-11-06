import {test} from 'node:test';
import assert from 'node:assert/strict';
import {identityMapping} from '../resources/extensions/tb2j/viewer/identity.js';
const bindings=['up','down'].map(channel=>({channel,hr_sha256:channel,win_sha256:channel}));
const basis=[{atom_index:3,orbital_index:1,wannier_index:1}];
const fixture=()=>({schema:'fpilot.wf-identity.v1',models:bindings.map((b,i)=>({channel:b.channel,binding:b,num_wann:1,orbitals:[{wf:1,atom:3,label:i?'混合 WF':'dxy型 WF',status:'MIXED',coverage:1,home_fraction:.9,owner_fraction:1,dominant_fraction:.6,components:[{l:2,label:'dxy',relative_weight:.6}]}]}))});
test('identity uses frozen model and channel, retains mixtures and never auto groups',()=>{const r=identityMapping(fixture(),bindings,basis);assert.equal(r.rows.length,2);assert.match(r.automatic[0].label,/自旋成分不同/);assert.equal(r.automatic[0].group,'');assert.equal(r.rows[0].wannier_index,1);assert.equal(r.rows[0].atom_index,3);assert.equal(Object.keys(r.automatic[0].components_by_channel).length,2);});
test('wrong frozen model, duplicate channel and nonfinite weights are rejected',()=>{const r=fixture();r.models[0].binding={...bindings[0],hr_sha256:'wrong'};assert.throws(()=>identityMapping(r,bindings,basis));const n=fixture();n.models[0].orbitals[0].coverage=NaN;assert.throws(()=>identityMapping(n,bindings,basis));assert.throws(()=>identityMapping(fixture(),[],basis));});
test('conflicting atom ownership stays explicit',()=>{const r=fixture();r.models[0].orbitals[0].atom=2;assert.equal(identityMapping(r,bindings,basis).rows[0].status,'ATOM_ASSIGNMENT_CONFLICT');});
test('missing native orb-to-WF indices and duplicate target channels are rejected',()=>{assert.throws(()=>identityMapping(fixture(),bindings,[]));assert.throws(()=>identityMapping(fixture(),[bindings[0],bindings[0]],basis));});

test('unverified packed-window reports cannot silently reuse old identities',()=>{const r=fixture();r.models[0].binding={...r.models[0].binding,udis_sha256:'present'};assert.throws(()=>identityMapping(r,bindings,basis),/eig/);});

test('VASP local-coordinate reference points to the correct export button', () => {
 assert.throws(
  () => identityMapping({schema:'fpilot.local-orbital-reference.v1'}, bindings, basis),
  /VASP 局域坐标参考.*下载给 TB2J 用的文件.*wf-identity\.json/
 );
});
