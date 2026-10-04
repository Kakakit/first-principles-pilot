import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inputForReview} from '../src/workflowUi.ts';

const old={id:'old',node_id:'scf',stage:'scf',status:'ready-for-execution-review'};
const current={...old,id:'current'};
const other={...old,id:'dos-input',node_id:'dos',stage:'dos'};
test('freeze-to-review carries the exact version despite multiple historical inputs',()=>{
  assert.equal(inputForReview([old,current,other],'scf','current'),'current');
  assert.equal(inputForReview([old,current,other],'scf'),'');
  assert.equal(inputForReview([old,other],'scf'),'old');
});
test('missing, recycled or cross-node handoff cannot silently select another version',()=>{
  assert.equal(inputForReview([old,current,other],'scf','dos-input'),'');
  assert.equal(inputForReview([old],'scf','current'),'');
  assert.equal(inputForReview([{...current,status:'recycled'},old],'scf','current'),'');
  assert.equal(inputForReview([current],null,'current'),'');
});
test('recipes awaiting upstream output and explicit restart stay reviewable, never submit-ready',()=>{
  assert.equal(inputForReview([{...other,status:'pending-dependencies'}],'dos'),'dos-input');
  assert.equal(inputForReview([{...current,status:'pending-restart'}],'scf'),'current');
  assert.equal(inputForReview([{...current,status:'invalid'}],'scf'),'');
});
