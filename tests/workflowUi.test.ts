import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inputForReview,chainNodes} from '../src/workflowUi.ts';
import {newProject,makeStep,kinds,canConnect} from '../src/model.ts';

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

test('program inputs accept calculated sources and reject cycles and incompatible scientific targets',()=>{
  const nodes=(['scf','program','program','structure'] as const).map((kind,i)=>makeStep(kind,{x:i*220,y:0},i+1));
  const [scf,analysis,consumer,structure]=nodes.map(n=>n.id);
  assert.ok(canConnect(nodes,[],scf,analysis));
  assert.ok(canConnect(nodes,[],analysis,consumer));
  assert.equal(canConnect(nodes,[{id:'one',source:analysis,target:consumer}],consumer,analysis),false);
  assert.equal(canConnect(nodes,[],analysis,scf),false);
  assert.equal(canConnect(nodes,[],structure,analysis),false);
});

test('only omitted immediate calculation parents need a historical source',async()=>{
 const {chainUpstreamCandidates}=await import('../src/workflowUi.ts');
 const p=newProject('Si',true);
 const [,relax,scf,bands,dos]=p.nodes;
 assert.deepEqual([...chainUpstreamCandidates(p,[bands.id,dos.id])],[scf.id]);
 assert.deepEqual([...chainUpstreamCandidates(p,[scf.id,bands.id])],[relax.id]);
 assert.deepEqual([...chainUpstreamCandidates(p,[relax.id])],[]);
});

test('freeze receipt and run history identify separately frozen versions with the same reason',async()=>{
 const {inputVersionLabel}=await import('../src/humanLabels.ts');
 assert.equal(inputVersionLabel('0123456789',' 复核\nU 值 '),'复核 U 值 · 01234567');
 assert.notEqual(inputVersionLabel('0123456789','SCF'),inputVersionLabel('abcd56789','SCF'));
 assert.equal(inputVersionLabel('0123456789',null),'冻结输入 · 01234567');
});
