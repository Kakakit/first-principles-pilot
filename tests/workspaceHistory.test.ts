import test from 'node:test';
import assert from 'node:assert/strict';
import {initialWorkspace} from '../src/model';
import {rememberWorkspace} from '../src/workspaceHistory';
test('undo retains original immutable state and shares structure references',()=>{
 const original=initialWorkspace(),history=rememberWorkspace([],original);
 const next={...original,projects:original.projects.map((p,i)=>i? p:{...p,name:'edited'})};
 assert.equal(history[0].workspace,original);
 assert.notEqual(history[0].workspace.projects[0].name,next.projects[0].name);
 assert.equal(history[0].workspace.projects[0].nodes,original.projects[0].nodes);
});
test('large undo history is bounded without mutating or deleting workspace data',()=>{
 const original=initialWorkspace();original.projects[0].name='a'.repeat(9*1024*1024);
 const first=rememberWorkspace([],original),next={...original};
 const kept=rememberWorkspace(first,next);
 assert.equal(kept.length,1);assert.equal(kept[0].workspace,next);
 assert.equal(original.projects[0].name.length,9*1024*1024);
});
