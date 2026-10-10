import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scfSourceId} from '../src/wannierLineage';
test('VASP-Wannier choices accept legacy chain binding and refuse conflicts',()=>{
 assert.equal(scfSourceId({chain_source_snapshot:{source_attempt_id:'scf',kind:'dependency'}}),'scf');
 assert.equal(scfSourceId({input_audit:{dependency_snapshot:{source_attempt_id:'scf'}}}),'scf');
 assert.equal(scfSourceId({dependency_snapshot:{upstream_attempt_id:'soc'},chain_source_snapshot:{source_attempt_id:'scf'}}),undefined);
 assert.equal(scfSourceId({chain_source_snapshot:{source_attempt_id:'self',kind:'restart'}}),undefined);
 assert.equal(scfSourceId(),undefined);
});
