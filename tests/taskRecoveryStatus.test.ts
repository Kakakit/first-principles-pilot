import {test} from 'node:test';
import assert from 'node:assert/strict';
import {recoveryReceiptLabel,recoveryMessage} from '../src/taskRecoveryStatus';

test('unknown receipt cannot be presented as successful submission',()=>{
  const item={state:'UNKNOWN',dispatch_state:'UNCERTAIN'};
  assert.match(recoveryReceiptLabel(item),/回执不明/);
  assert.match(recoveryMessage({state:'FINISHED',items:[item]}),/1 项提交回执不明/);
  assert.doesNotMatch(recoveryMessage({state:'FINISHED',items:[item]}),/成功|提交完毕/);
});
test('pending progress and actual job receipt remain distinguishable',()=>{
  assert.match(recoveryReceiptLabel({state:'FAILED',dispatch_state:'PENDING',progress:{phase:'SUBMITTING',at:''}}),/等待调度器回执/);
  assert.match(recoveryReceiptLabel({state:'SUBMITTED',dispatch_state:'SENT',job_id:'123'}),/作业号 123/);
  assert.match(recoveryReceiptLabel({state:'FAILED',dispatch_state:'FAILED'}),/处理失败/);
  assert.match(recoveryMessage({state:'CONFIRMED',items:[]}),/后台处理/);
  const failed={state:'UNKNOWN',dispatch_state:'FAILED'};
  assert.match(recoveryReceiptLabel(failed),/处理失败/);
  assert.match(recoveryMessage({state:'FINISHED',items:[failed]}),/0 项提交回执不明，1 项处理失败/);
});
