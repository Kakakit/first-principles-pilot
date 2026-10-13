import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {simpleProjectionCount} from '../src/projectionCount.ts';
import {demoWorkspace,parseWorkspace} from '../src/model.ts';
import {copyDraftNode} from '../src/nodeTypes.ts';

test('projection estimate is all or nothing; spinors count spatial projections twice',()=>{
 const structure=JSON.parse(readFileSync(new URL('./fixtures/Si.structure.json',import.meta.url),'utf8'));
 assert.equal(simpleProjectionCount('Si:s\nSi:p',structure),8);
 assert.equal(simpleProjectionCount('begin Projections\nSi:s\nSi:p\nend Projections',structure,true),16);
 for(const text of ['Si:s\nSi:sp3','Si:s\nc=0,0,0:s','Si:s\nSi:dxy','Si:s\nFe:d'])assert.equal(simpleProjectionCount(text,structure),null);
});

test('manual text survives workspace serialization',()=>{
 const w=demoWorkspace(),node=w.projects[0].nodes[1],raw='MAGMOM = 2*4\nCUSTOM_TAG = arbitrary\n';
 node.data.parameters={values:{ENCUT:'400'},manualIncarText:raw};
 const read=parseWorkspace(JSON.stringify(w));
 assert.equal(read.projects[0].nodes[1].data.parameters?.manualIncarText,raw);
 assert.equal(copyDraftNode(read.projects[0],read.projects[0].nodes[1]).data.parameters?.manualIncarText,raw);
});
