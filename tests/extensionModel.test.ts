import test from 'node:test';
import assert from 'node:assert/strict';
import {loadProjectArtifacts,matchingArtifacts} from '../src/extensionProjectArtifact';
import {defaults,analysisLabel,runningAnalysis,preferredLocation,supportedLocation,sourceRuntime,sourceOptionsSchema,readableSource,formAvailability,executionCategories,runtimeChoice,environmentVersion} from '../src/extensionModel';
test('schema defaults retain false/zero and only declared nested fields',()=>{const schema={type:'object',properties:{visible:{type:'boolean',default:false},count:{type:'integer',default:0},missing:{type:'string'},nested:{type:'object',properties:{level:{default:2}}}}};assert.deepEqual(defaults(schema),{visible:false,count:0,nested:{level:2}});const first=defaults(schema);first.nested.level=5;assert.equal(defaults(schema).nested.level,2);});
test('UNKNOWN requires explicit reconciliation and cannot look completed',()=>{assert.equal(runningAnalysis('UNKNOWN'),false);assert.equal(analysisLabel('UNKNOWN'),'状态待核对');assert.equal(runningAnalysis('WAIT_RESOURCE'),true);assert.equal(analysisLabel('COMPLETED'),'分析程序完成');});

test('manifest chooses its preferred location without an extension ID branch',()=>{
 assert.equal(preferredLocation({execution:{locations:['ssh','local'],preferred_location:'ssh',timeout_seconds:300}}),'ssh');
 assert.equal(preferredLocation({execution:{locations:['local','ssh'],timeout_seconds:300}}),'local');
 assert.equal(preferredLocation({execution:{locations:['ssh'],timeout_seconds:300}}),'ssh');
 assert.equal(preferredLocation({execution:{locations:['scheduler','ssh'],preferred_location:'scheduler',timeout_seconds:300}}),'ssh');
});
test('default runtime follows source server and never silently picks another host',()=>{
 assert.equal(sourceRuntime('source-host',{'other':{},'source-host':{}}),'source-host');
 assert.equal(sourceRuntime('missing',{'other':{}}),'');
 assert.equal(sourceRuntime(undefined,{'other':{}}),'other');
});
test('switching packages keeps a supported user location ahead of the package preference',()=>{
 const both={execution:{locations:['local','ssh'],preferred_location:'ssh',timeout_seconds:300}};
 assert.equal(supportedLocation(both,'local'),'local');
 assert.equal(supportedLocation(both,'ssh'),'ssh');
 const remote={execution:{locations:['ssh'],timeout_seconds:300}};
 assert.equal(supportedLocation(remote,'local'),'ssh');
 assert.equal(supportedLocation({execution:{locations:['local'],timeout_seconds:300}},'ssh'),'local');
 assert.equal(supportedLocation({} as any,'local'),'local');
 // A forced fallback must not replace the caller's preference on the next package.
 assert.equal(supportedLocation(both,'local'),'local');
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
test('one package can appear in both local and server categories; broken bundles are safe',()=>{
 assert.deepEqual(executionCategories({execution:{locations:['local','ssh','scheduler'],timeout_seconds:30}}),['local','remote']);
 assert.deepEqual(executionCategories({} as any),[]);assert.equal(preferredLocation({} as any),undefined);
});
test('Python overrides stay scoped to package, location and exact server',()=>{
 const data={local:{python:'/local/default'},runtimes:{a:{python:'/a/default'},b:{python:'/b/default'}},overrides:{one:{local:'/local/custom',remote:{a:'/a/custom'}}}};
 assert.equal(runtimeChoice('one','local','a',data)?.python,'/local/custom');
 assert.equal(runtimeChoice('one','ssh','a',data)?.python,'/a/custom');assert.equal(runtimeChoice('one','scheduler','a',data)?.python,'/a/custom');
 assert.equal(runtimeChoice('one','ssh','b',data)?.python,'/b/default');assert.equal(runtimeChoice('two','local','a',data)?.inherited,true);
 assert.equal(runtimeChoice('one','ssh','missing',data),undefined);
 delete data.overrides.one.local;assert.equal(runtimeChoice('one','local','a',data)?.python,'/local/default');
});
test('environment caption keeps Python and library versions distinct',()=>{
 assert.equal(environmentVersion({ready:true,python_version:'3.11.9',version:'0.9.20',versions:{ase:'3.23'}}),'Python 3.11.9 · 版本 0.9.20 · ase 3.23');
 assert.equal(environmentVersion({ready:false}),'');
});

test('remote output paths use the frozen analysis directory and preserve nested Unicode filenames',async()=>{
 const {remoteOutputPath}=await import('../src/extensionModel');
 assert.equal(remoteOutputPath('/home/user/分析/analyses/id/','figure/能级.png'),'/home/user/分析/analyses/id/outputs/figure/能级.png');
 assert.equal(remoteOutputPath('/home/user/analyses/id',''),'/home/user/analyses/id/outputs');
 assert.equal(remoteOutputPath(undefined,'result.json'),undefined);
 for(const name of ['../result.json','/tmp/file','sub/../../file','sub\\file','bad\nname'])assert.equal(remoteOutputPath('/remote/id',name),undefined);
});

test('project report matching requires the complete source pair regardless of order',()=>{
 const artifacts=[
  {id:'paired',created_at:'2026-11-07',size_bytes:4,sources:[{attempt_id:'down'},{attempt_id:'up'}]},
  {id:'other',created_at:'2026-11-07',size_bytes:4,sources:[{attempt_id:'up'},{attempt_id:'other'}]},
  {id:'single',created_at:'2026-11-07',size_bytes:4,sources:[{attempt_id:'up'}]},
 ];
 const rule={field:'report',producer:'sample-analysis',path:'identity.json',title:'身份报告',match:'same_sources' as const,source_count:2};
 assert.deepEqual(matchingArtifacts(rule,artifacts,['up','down']).map(item=>item.id),['paired']);
 assert.deepEqual(matchingArtifacts(rule,artifacts,['up']),[]);
 assert.equal(matchingArtifacts({...rule,match:'project'},artifacts,['up']).length,3);
});

test('project report lookup uses completed project history when an older host returns 404',async(t)=>{
 const artifact={path:'identity.json',available:true,size_bytes:25};
 const record={id:'report',extension_id:'orbitals',state:'COMPLETED',created_at:'2026-11-07',sources:[{attempt_id:'scf',name:'SCF'}],outputs:[artifact]};
 const request=t.mock.method(globalThis,'fetch',async(url:string)=>url.includes('project-artifacts')
  ?Response.json({detail:'Not Found'},{status:404})
  :Response.json({analyses:[record,{...record,id:'failed',state:'FAILED'},{...record,id:'absent',outputs:[]}]}));
 const result=await loadProjectArtifacts('project_id=project-a&producer=orbitals&name=identity.json',new AbortController().signal);
 assert.deepEqual(result,[{id:'report',created_at:'2026-11-07',size_bytes:25,sources:[{attempt_id:'scf',name:'SCF'}]}]);
 assert.equal(request.mock.calls[1].arguments[0],'/api/extension-host/analyses?project_id=project-a');
});
