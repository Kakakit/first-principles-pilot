import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inputForReview,chainNodes} from '../src/workflowUi.ts';
import {newProject,makeStep,kinds} from '../src/model.ts';

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
test('chain selection keeps unsupported context visible but sends only selected VASP nodes',()=>{
  const project=newProject('画布选择');
  project.nodes=kinds.map((kind,index)=>makeStep(kind,{x:index*260,y:0},index+1));
  const supported=chainNodes(project);
  assert.deepEqual(supported.map(node=>node.data.kind),['relax','scf','bands','dos','vasp-wannier']);
  const before=JSON.stringify(project);
  const selected=[supported[3].id,project.nodes[0].id,supported[1].id,supported[1].id,'deleted'];
  assert.deepEqual(chainNodes(project,selected).map(node=>node.id),[supported[1].id,supported[3].id]);
  assert.deepEqual(chainNodes(project,[]),[]);
  assert.equal(JSON.stringify(project),before);
  project.nodes=project.nodes.filter(node=>node.id!==supported[1].id);
  assert.deepEqual(chainNodes(project,selected).map(node=>node.id),[supported[3].id]);
});
