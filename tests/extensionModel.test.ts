import test from 'node:test';
import assert from 'node:assert/strict';
import {defaults,analysisLabel,runningAnalysis} from '../src/extensionModel';
test('schema defaults retain false/zero and only declared nested fields',()=>{const schema={type:'object',properties:{visible:{type:'boolean',default:false},count:{type:'integer',default:0},missing:{type:'string'},nested:{type:'object',properties:{level:{default:2}}}}};assert.deepEqual(defaults(schema),{visible:false,count:0,nested:{level:2}});const first=defaults(schema);first.nested.level=5;assert.equal(defaults(schema).nested.level,2);});
test('UNKNOWN requires explicit reconciliation and cannot look completed',()=>{assert.equal(runningAnalysis('UNKNOWN'),false);assert.equal(analysisLabel('UNKNOWN'),'状态待核对');assert.equal(runningAnalysis('WAIT_RESOURCE'),true);assert.equal(analysisLabel('COMPLETED'),'分析程序完成');});
