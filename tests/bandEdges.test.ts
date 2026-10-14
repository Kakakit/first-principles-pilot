import {test} from 'node:test';
import assert from 'node:assert/strict';
import {bandEdges} from '../src/BandEdges.tsx';

test('OUTCAR edges use exactly the plot reference without range filtering',()=>{
 const result=bandEdges({fermi_eV:6.179381397,vbm_eV:5.8460696248,cbm_eV:7.4056836755,scf_attempt_id:'scf'});
 assert.ok(result);assert.ok(Math.abs(result.vbm+0.3333117722)<1e-10);assert.ok(Math.abs(result.cbm-1.2263022785)<1e-10);
 assert.ok(Math.abs(result.gap-1.5596140507)<1e-10);
});
test('missing or invalid reports never invent metal or band edges',()=>{
 for(const ref of [{fermi_eV:2},{fermi_eV:2,vbm_eV:3,cbm_eV:3},{fermi_eV:NaN,vbm_eV:1,cbm_eV:3},{fermi_eV:2,vbm_eV:NaN,cbm_eV:3}])assert.equal(bandEdges(ref),null);
});
