import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readJson,errorMessage} from '../src/apiClient.ts';

test('empty or non-JSON API responses show a recoverable service message',async()=>{
  await assert.rejects(readJson(new Response('',{status:200})),/空响应.*重试/);
  await assert.rejects(readJson(new Response('<html>error</html>',{status:200})),/无法读取.*重试/);
  assert.match(errorMessage(new TypeError('Failed to fetch')),/无法连接本机 API/);
});
