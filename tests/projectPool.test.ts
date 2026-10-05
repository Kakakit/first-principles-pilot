import {test} from 'node:test';
import assert from 'node:assert/strict';
import {withoutProject} from '../src/projectManagement.ts';
import {newProject,parseWorkspace,cleanWorkspace} from '../src/model.ts';
import {fullNodeCandidates,poolNodes,capacityWait} from '../src/candidatePool.ts';
test('removing the last project creates a clean valid draft; original remains recoverable',()=>{
 const p=newProject('删除测试');const w={activeId:p.id,projects:[p]};const after=withoutProject(w,p.id);
 assert.equal(after.projects.length,1);assert.notEqual(after.activeId,p.id);assert.equal(w.projects[0],p);
 assert.equal(after.projects[0].nodes.length,1);assert.equal(after.projects[0].nodes[0].data.structure,undefined);
 assert.doesNotThrow(()=>parseWorkspace(JSON.stringify(cleanWorkspace(after))));
});
test('deleting another project preserves the active project and deleting active switches correctly',()=>{
 const a=newProject('a'),b=newProject('b');const w={activeId:a.id,projects:[a,b]};
 assert.equal(withoutProject(w,b.id).activeId,a.id);assert.equal(withoutProject(w,a.id).activeId,b.id);
 assert.throws(()=>withoutProject(w,'missing'),/刷新/);
});
test('candidate pool reserves whole-node cores and treats occupancy as waiting',()=>{
 const nodes=[{NODE:'node01',CPUS_TOTAL:'24',CPUS_FREE:'0',STATUS:'BUSY'},{NODE:'node02',CPUS_TOTAL:'32',CPUS_FREE:'32',STATUS:'FULLY_IDLE'}];
 assert.deepEqual(fullNodeCandidates(nodes,['node01','node02']),[{node:'node01',cores:24,reason:''},{node:'node02',cores:32,reason:''}]);
 assert.throws(()=>fullNodeCandidates(nodes,['gone']),/重新检测/);
 assert.equal(capacityWait({resource_ready:false,node_status:'BUSY',blockers:['selected node CPUS_FREE must be greater than zero'],script:'s',note:''}),true);
 assert.equal(capacityWait({resource_ready:false,node_status:'BUSY',blockers:['运行时失败'],script:'s',note:''}),false);
 for(const status of ['SUSPECT_LOAD','UNAVAILABLE','SUSPECT_MOM']){
  assert.equal(capacityWait({resource_ready:false,node_status:status,blockers:['selected node CPUS_FREE must be greater than zero'],script:'s',note:''}),status==='SUSPECT_LOAD');
 }
 assert.equal(capacityWait({resource_ready:false,node_status:'SUSPECT_LOAD',blockers:['selected node CPUS_FREE must be greater than zero','walltime 超过队列上限'],script:'s',note:''}),false);
 assert.equal(poolNodes({preflight:{hosts:[{nodes:[...nodes,{NODE:'node06',STATUS:'FULLY_IDLE'}]}]}} as never,'jutai-1').length,2);
});
