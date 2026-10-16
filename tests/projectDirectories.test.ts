import test from 'node:test';
import assert from 'node:assert/strict';
import {newProject,parseWorkspace,mergeWorkspace,cleanWorkspace} from '../src/model';
test('project directories survive save and restore while import as new clears the destination',()=>{
 const project=newProject('Directory test');project.remoteDirectories={'lab':'/scratch/my-project'};
 const workspace={activeId:project.id,projects:[project]};
 const restored=parseWorkspace(JSON.stringify(workspace));
 assert.deepEqual(restored.projects[0].remoteDirectories,project.remoteDirectories);
 const imported=mergeWorkspace({activeId:'',projects:[]},restored);
 assert.deepEqual(imported.projects[0].remoteDirectories,{});
 assert.equal(imported.projects[0].remotePathPolicy,'project-directory-v2');
 assert.equal(restored.projects[0].remoteWorkspaceRoot,'$HOME/fpilot');
 assert.deepEqual(restored.projects[0].remoteDirectories,{'lab':'/scratch/my-project'});
});
test('saving an old project does not change its remote directory policy',()=>{
 const project=newProject('Existing project');delete project.remotePathPolicy;delete project.remoteWorkspaceRoot;
 const workspace={activeId:project.id,projects:[project]};
 assert.equal(cleanWorkspace(parseWorkspace(JSON.stringify(workspace))).projects[0].remotePathPolicy,undefined);
});
