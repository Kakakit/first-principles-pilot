import {test} from 'node:test';
import assert from 'node:assert/strict';
import {attemptLabel,attemptName} from '../src/humanLabels';
import {sourceCaption} from '../src/extensionSourceSelection';

test('attempt caption uses frozen note before IDs and chain job name only as a fallback',()=>{
 const row={id:'abcdef0123456789',input_note:'测试更高截断能',chain_run_id:'chain',config:{name:'chain-job'},state:'UNKNOWN',job_id:'22837.demo'};
 assert.equal(attemptName(row),'测试更高截断能');
 assert.ok(attemptLabel(row).startsWith('测试更高截断能 · '));
 assert.ok(attemptLabel(row).includes('22837.demo'));
 assert.equal(attemptName({...row,input_note:''}),'chain-job');
 assert.equal(attemptName({...row,input_note:null,chain_run_id:undefined}),'未填写备注');
 assert.ok(attemptLabel({id:row.id}).includes('attempt abcdef01'));
});
test('long Chinese captions preserve the full frozen note and handle Unicode without mutation',()=>{
 const note='检查晶胞与电子收敛🧪'.repeat(12),row={id:'x',input_note:note};
 assert.ok(attemptLabel(row).includes('…'));
 assert.ok(attemptLabel(row,undefined,true).includes(note));
 assert.equal(row.input_note,note);
});
test('scan captions remain parameter based; extension source caption uses the same frozen prefix',()=>{
 assert.equal(attemptLabel({parameter_summary:'U=4',case_index:2,batch_reason:'U扫描',job_id:'1.demo'}),'U扫描 · Case 3 · U=4 · 1.demo');
 const value=sourceCaption({id:'id',name:'静态自洽',stage:'scf',state:'COMPLETED',alias:'demo',input_note:'冻结时备注',job_id:'2.demo'});
 assert.ok(value.indexOf('冻结时备注')<value.indexOf('2.demo'));
});
