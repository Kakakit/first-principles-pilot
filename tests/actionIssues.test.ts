import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ApiError,readJson} from '../src/apiClient.ts';
import {issueRoute,issueFromError,registerActionCheck,getActionCheck,readActionIssues,runtimeIssueField,readRuntimeIssueField} from '../src/actionIssues.ts';
import {inputReportIssues,prepareIssues} from '../src/inputActionIssues.ts';
import {locationFromHash,locationHash,normalizeLocation} from '../src/navigationHistory.ts';

test('structured API errors preserve stable issue targets and legacy errors keep their text',async()=>{
 const issue={code:'input.potcar',message:'请补充赝势',target:{page:'config',subpage:'files',field:'input.potcar',projectId:'p',nodeId:'n'}};
 await assert.rejects(readJson(new Response(JSON.stringify({detail:{message:'输入未就绪',issues:[issue]}}),{status:422})),error=>{assert.ok(error instanceof ApiError);assert.equal(error.message,'输入未就绪');assert.deepEqual(issueFromError(error),[issue]);return true;});
 await assert.rejects(readJson(new Response(JSON.stringify({detail:'original failure'}),{status:422})),/original failure/);
 assert.deepEqual(readActionIssues([{message:'bad'},null,42]),[]);
});
test('precise field navigation preserves the correct project/node and survives URL restore',()=>{
 const origin=normalizeLocation({projectId:'p',nodeId:'n',page:'run',attemptId:'old'});
 const target=normalizeLocation(issueRoute({code:'input',message:'missing',target:{page:'config',subpage:'files',field:'input.potcar'}},origin));
 assert.equal(target.projectId,'p');assert.equal(target.nodeId,'n');assert.equal(target.focus,'issue:input.potcar');
 assert.deepEqual(locationFromHash(locationHash(target)),target);
 const upstream=normalizeLocation(issueRoute({code:'upstream',message:'pending',action:'inspect',target:{page:'run',nodeId:'parent',attemptId:'exact-run',field:'run.history'}},origin));
 assert.deepEqual(readRuntimeIssueField(runtimeIssueField('sample-extension','ssh','server-alias')),{id:'sample-extension',location:'ssh',alias:'server-alias'});assert.equal(readRuntimeIssueField('extension.runtime:invalid'),null);
 assert.equal(upstream.nodeId,'parent');assert.equal(upstream.attemptId,'exact-run');
});
test('missing preparation settings explain each original prerequisite without submitting',()=>{
 const issues=prepareIssues({projectId:'p',nodeId:'n',bundle:'',node:'',alias:''});
 assert.deepEqual(issues.map(issue=>issue.code),['run.input','run.server','run.node']);
 assert.equal(issues[0].target?.field,'input.preview');assert.equal(issues[2].target?.field,'run.node');
 assert.deepEqual(prepareIssues({projectId:'p',nodeId:'n',bundle:'frozen',node:'cpu',alias:'school'}),[]);
});
test('warnings do not become report blockers; adding local prerequisites cannot mutate report issues',()=>{
 const source={code:'input.grid',message:'bad grid',target:{page:'config',field:'input.grid'}};
 const report={errors:['bad grid'],issues:[source],warnings:['advice']};const issues=inputReportIssues(report,'p','n');
 issues.push({code:'local',message:'review'});assert.equal(report.issues.length,1);
 assert.equal(inputReportIssues({errors:[]},'p','n').length,0);
 const legacy=inputReportIssues({errors:['unmapped original']},'p','n');assert.equal(legacy[0].message,'unmapped original');assert.equal(legacy[0].target?.field,'input.preview');
});
test('return checks use the remounted current action and cleanup cannot delete a newer registration',async()=>{
 const old=registerActionCheck('input',()=>[{code:'old',message:'old draft'}]);
 const fresh=registerActionCheck('input',()=>[]);old();assert.deepEqual(await getActionCheck('input')?.(),[]);
 fresh();assert.equal(getActionCheck('input'),undefined);
});
