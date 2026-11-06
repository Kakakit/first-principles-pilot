import assert from 'node:assert/strict';
import test from 'node:test';
import {locationHash,locationFromHash,nextLocation,normalizeLocation,visitTrail,type Visit} from '../src/navigationHistory';

test('project and settings URLs round-trip the location without draft data',()=>{
 const location=normalizeLocation({projectId:'Fe Cr / 项目',page:'results',nodeId:'scf',attemptId:'older',subpage:'files'});
 assert.deepEqual(locationFromHash(locationHash(location)),location);
 const settings=nextLocation(location,{page:'settings',section:'server',alias:'lab-a',program:'vasp_ncl',focus:'scripts'});
 assert.deepEqual(locationFromHash(locationHash(settings)),settings);
 assert.equal(settings.projectId,'');
 assert.equal(settings.attemptId,'');
 assert.equal(locationFromHash('#/settings/files')?.section,'config');
 assert.equal(locationFromHash('#/projects/%E0%A4/results'),null);
});

test('an asynchronous default replaces its entry and opening a new location truncates only the forward branch',()=>{
 const entry=(position:number,id:string,session='test'):Visit=>({fpilot:1,session,id,position,
  route:normalizeLocation({projectId:'a',page:'run',nodeId:'n',attemptId:id})});
 const a=entry(0,'A'),b=entry(1,'B'),c=entry(2,'C');
 const history=visitTrail([a,b],c,'push');
 const selected=visitTrail(history,b,'pop');
 assert.deepEqual(selected.map(item=>item.id),['A','B','C']);
 const refreshed={...b,route:{...b.route,attemptId:'default'}};
 assert.deepEqual(visitTrail(selected,refreshed,'replace').map(item=>item.id),['A','B','C']);
 assert.deepEqual(visitTrail(selected,entry(2,'D'),'push').map(item=>item.id),['A','B','D']);
 assert.equal(visitTrail(selected,entry(0,'outside','separate'),'pop').length,1);
 let bounded:Visit[]=[];
 for(let n=0;n<130;n++)bounded=visitTrail(bounded,entry(n,String(n)),'push');
 assert.equal(bounded.length,100);
});

test('navigation cannot carry an attempt or node implicitly to another project',()=>{
 const a=normalizeLocation({projectId:'a',page:'results',nodeId:'node-a',attemptId:'run-a',subpage:'files'});
 const b=nextLocation(a,{projectId:'b',page:'results'});
 assert.equal(b.nodeId,null);
 assert.equal(b.attemptId,'');
 assert.equal(b.subpage,'summary');
 const config=nextLocation(a,{page:'config',nodeId:'node-a',subpage:'source'});
 assert.equal(nextLocation(config,{page:'run',nodeId:'node-a'}).attemptId,'run-a');
});
