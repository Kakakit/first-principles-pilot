import {test} from 'node:test';
import assert from 'node:assert/strict';
import {chainResourceIssues} from '../src/chainResources.ts';
import type {ServerPreview} from '../src/executionUi.ts';
test('chain shows exact stage/node reasons, retains capacity wait and rejects missing previews',()=>{
 const steps=[{node_id:'scf',label:'自洽'},{node_id:'dos',label:'态密度'}];
 const ready:ServerPreview={resource_ready:true,script:'PBS',profile:{ppn:96},blockers:[],note:''};
 const waiting={...ready,resource_ready:false,node_status:'BUSY',capacity_wait:true,blockers:['selected node CPUS_FREE must be greater than zero']};
 assert.deepEqual(chainResourceIssues(steps,['n1'],{scf:{n1:waiting},dos:{n1:ready}}),[]);
 const previews={scf:{n1:ready},dos:{n1:{...ready,resource_ready:false,blockers:['第16行进入固定/未知目录']}}};
 assert.deepEqual(chainResourceIssues(steps,['n1'],previews),[{nodeId:'dos',label:'态密度',node:'n1',reasons:['第16行进入固定/未知目录']}]);
 assert.equal(chainResourceIssues(steps,['n1'],{scf:{n1:ready}})[0].nodeId,'dos');
 assert.equal(chainResourceIssues([steps[0]],['n1'],{scf:{n1:{...waiting,capacity_wait:false,blockers:['walltime 超过队列上限']}}}).length,1);
});
