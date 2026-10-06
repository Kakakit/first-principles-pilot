import test from 'node:test';
import assert from 'node:assert/strict';
import {defaults,analysisLabel,runningAnalysis,preferredLocation,sourceRuntime,sourceOptionsSchema,readableSource,formAvailability} from '../src/extensionModel';
test('schema defaults retain false/zero and only declared nested fields',()=>{const schema={type:'object',properties:{visible:{type:'boolean',default:false},count:{type:'integer',default:0},missing:{type:'string'},nested:{type:'object',properties:{level:{default:2}}}}};assert.deepEqual(defaults(schema),{visible:false,count:0,nested:{level:2}});const first=defaults(schema);first.nested.level=5;assert.equal(defaults(schema).nested.level,2);});
test('UNKNOWN requires explicit reconciliation and cannot look completed',()=>{assert.equal(runningAnalysis('UNKNOWN'),false);assert.equal(analysisLabel('UNKNOWN'),'状态待核对');assert.equal(runningAnalysis('WAIT_RESOURCE'),true);assert.equal(analysisLabel('COMPLETED'),'分析程序完成');});

test('manifest chooses its preferred location without an extension ID branch',()=>{
 assert.equal(preferredLocation({execution:{locations:['ssh','local'],preferred_location:'ssh',timeout_seconds:300}}),'ssh');
 assert.equal(preferredLocation({execution:{locations:['local','ssh'],timeout_seconds:300}}),'local');
 assert.equal(preferredLocation({execution:{locations:['ssh'],timeout_seconds:300}}),'ssh');
});
test('default runtime follows source server and never silently picks another host',()=>{
 assert.equal(sourceRuntime('source-host',{'other':{},'source-host':{}}),'source-host');
 assert.equal(sourceRuntime('missing',{'other':{}}),'');
 assert.equal(sourceRuntime(undefined,{'other':{}}),'other');
});

test('missing runtime or files blocks actions while the options stay editable',()=>{
 assert.deepEqual(formAvailability(false,true,false),{editingDisabled:false,actionsDisabled:true,submitDisabled:true});
 assert.equal(formAvailability(true,false,false).editingDisabled,true);
 assert.equal(formAvailability(false,false,true).submitDisabled,true);
});
test('source element choices are declared by the package and do not select magnetic elements automatically',()=>{
 const manifest={options_schema:{type:'object',properties:{magnetic_elements:{type:'array',items:{type:'string'},default:[]},other:{type:'array',items:{type:'string'}}}},ui_schema:{magnetic_elements:{'ui:options':{source_enum:'elements'}}}};
 const source={id:'source',name:'run',stage:'wannier90',kind:'attempt',state:'COMPLETED',files:[],input_summary:{elements:['Cr','Fe','O']}};
 const schema=sourceOptionsSchema(manifest,[source,source]);
 assert.deepEqual(schema.properties.magnetic_elements.items.enum,['Cr','Fe','O']);
 assert.deepEqual(schema.properties.magnetic_elements.default,[]);assert.equal(schema.properties.other.items.enum,undefined);
 assert.equal((manifest.options_schema.properties.magnetic_elements.items as any).enum,undefined);
});
test('source captions use meaningful names instead of internal fingerprints',()=>{
 assert.equal(readableSource('a'.repeat(32),'已完成运行'),'已完成运行');assert.equal(readableSource('25262.node01'),'25262.node01');
});
