import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readJson,errorMessage} from '../src/apiClient.ts';

test('empty or non-JSON API responses show a recoverable service message',async()=>{
  await assert.rejects(readJson(new Response('',{status:200})),/空响应.*重试/);
  await assert.rejects(readJson(new Response('<html>error</html>',{status:200})),/响应格式异常.*HTTP 200.*重试/);
  await assert.rejects(readJson(new Response('Internal Server Error',{status:500})),/服务内部错误.*HTTP 500.*填写内容保留/);
  await assert.rejects(readJson(new Response('{"detail":"PyW90 未输出能带路径"}',{status:422})),/PyW90 未输出能带路径/);
  assert.match(errorMessage(new TypeError('Failed to fetch')),/无法连接本机 API/);
});

test('bounded read timeout ends waiting without retrying or claiming a remote stop',async()=>{
 const {requestJson}=await import('../src/apiClient.ts');const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async(_path,options)=>{calls++;return new Promise((_resolve,reject)=>{options?.signal?.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});});};
 try{await assert.rejects(requestJson('/api/wannier90/band-path',{},undefined,{timeoutMs:10}),/等待超时.*没有自动重试.*不代表远端程序已停止/);assert.equal(calls,1);}finally{globalThis.fetch=original;}
});

test('caller cancellation ends the active read without a retry or timeout message',async()=>{
 const {requestJson,requestTimeout}=await import('../src/apiClient.ts');const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async(_path,options)=>{calls++;return new Promise((_resolve,reject)=>{options?.signal?.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});});};
 try{
  const caller=new AbortController(),request=requestJson('/api/execution',undefined,undefined,{signal:caller.signal});caller.abort();
  await assert.rejects(request,{name:'AbortError'});assert.equal(calls,1);
  assert.ok(requestTimeout('/api/server','GET')>0);
  assert.ok(requestTimeout('/api/execution/a/artifacts','GET')>requestTimeout('/api/server','GET'));
  assert.ok(requestTimeout('/api/execution/a/submit','POST')>requestTimeout('/api/server','GET'));
 }finally{globalThis.fetch=original;}
});
