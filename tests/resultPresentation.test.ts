import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ResultSummary from '../src/ResultSummary';
import type {ResultAttempt} from '../src/resultWorkspace';
const render=(attempt:ResultAttempt)=>renderToStaticMarkup(createElement(ResultSummary,{attempt,data:null,busy:false,onRead(){},onSection(){},onConfigure(){},onRun(){},onOpenSource(){}}));
test('unknown or failed runs keep their status before any result values',()=>{
 for(const state of ['UNKNOWN','FAILED']){const html=render({id:'test',state} as ResultAttempt);assert.ok(html.indexOf('调度 / 执行状态')<html.indexOf('结果数值'));assert.match(html,/未审核/);}
});
test('completed but unconverged runs keep convergence evidence prominent',()=>{
 const html=render({id:'test',state:'COMPLETED',evidence:{vasp_timing:true,ediff_reached:false}} as ResultAttempt);
 assert.ok(html.indexOf('电子收敛')<html.indexOf('结果数值'));
});
test('completed results lead with values while retaining separate physical evidence',()=>{
 const html=render({id:'test',state:'COMPLETED',evidence:{vasp_timing:true,ediff_reached:true}} as ResultAttempt);
 assert.ok(html.indexOf('结果数值')<html.indexOf('调度 / 执行状态'));assert.match(html,/物理验证/);assert.match(html,/未审核/);
});
