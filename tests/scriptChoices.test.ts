import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fillScriptSelections,scriptFits,resourceFor,withResource,importedChoice,variantLabel} from '../src/scriptChoices.ts';
import {runtimeFor,emptySettings} from '../src/submissionDefaults.ts';
const gpu={mode:'gpu',allocation:'whole_node',mpi_ranks:1,gpus:1} as const;
const script={id:'one',name:'GPU raw',alias:'gpu',scheduler:'pbs',program:'gpu_vasp',source:'fixture',resource_request:gpu};
test('script choices bind alias, scheduler and program with GPU VASP compatibility',()=>{
 assert.equal(scriptFits(script,'gpu','PBS/Torque','vasp_std'),true);
 for(const [alias,scheduler,program] of [['other','pbs','vasp_std'],['gpu','slurm','vasp_std'],['gpu','pbs','vasp_ncl'],['gpu','pbs','wannier90']])assert.equal(scriptFits(script,alias,scheduler,program),false);
});
test('GPU rank requests remain program scoped and changing scripts clears stale requirements',()=>{
 const config=importedChoice({alias:'gpu',scheduler:'pbs'},'vasp_std',script);
 assert.deepEqual(resourceFor(config,'vasp_std'),gpu);assert.equal(resourceFor(config,'vasp_ncl'),undefined);
 const next=withResource(config,'vasp_ncl',{...gpu,mpi_ranks:2});
 assert.equal(resourceFor(next,'vasp_std')?.mpi_ranks,1);assert.equal(resourceFor(next,'vasp_ncl')?.mpi_ranks,2);
 const cpu=importedChoice(next,'vasp_std',{...script,id:'cpu',program:'vasp_std',resource_request:undefined});
 assert.equal(resourceFor(cpu,'vasp_std'),undefined);assert.equal(resourceFor(cpu,'vasp_ncl')?.mpi_ranks,2);
 assert.equal(config.template_id,'one');
});
test('server defaults cannot leak GPU requests or script identities to another server',()=>{
 const config=importedChoice({alias:'gpu'},'vasp_std',script);
 const profiles=[{alias:'cpu',scheduler:'pbs',queue:'q',vasp_executable:'/vasp_std'}] as Parameters<typeof runtimeFor>[1];
 const next=runtimeFor(config,profiles,emptySettings,'cpu','vasp_std');
 assert.equal(next.alias,'cpu');assert.equal(next.resource_request,undefined);assert.equal(next.template_id,undefined);assert.equal(next.runtime_source,'registered');
});
test('same-directory capabilities share the selected script across CPU and SOC chain steps',()=>{
 const compatible={...script,compatible_programs:['vasp_std','vasp_ncl']};
 assert.equal(scriptFits(compatible,'gpu','pbs','vasp_ncl'),true);
 assert.equal(scriptFits(compatible,'other','pbs','vasp_ncl'),false);
 assert.equal(scriptFits({...compatible,program_conflict:'wrong category'},'gpu','pbs','vasp_ncl'),false);
 assert.match(variantLabel(compatible,'vasp_ncl'),/vasp_std → vasp_ncl/);
 const config=importedChoice({alias:'gpu',scheduler:'pbs'},'vasp_std',compatible);
 const shared=fillScriptSelections(config,['vasp_std','vasp_ncl'],[compatible]);
 assert.deepEqual(JSON.parse(shared.script_templates!),{vasp_std:'one',vasp_ncl:'one'});
 const explicit={...config,script_templates:JSON.stringify({vasp_ncl:'custom'})};
 assert.equal(fillScriptSelections(explicit,['vasp_std','vasp_ncl'],[compatible]),explicit);
 const registered={...config,runtime_source:'registered'};
 assert.equal(fillScriptSelections(registered,['vasp_std','vasp_ncl'],[compatible]),registered);
});
test('SOC falls back to the host std default but keeps explicit SOC settings',()=>{
 const profiles=[{alias:'gpu',scheduler:'pbs',queue:'q',vasp_executable:'/vasp_std'}] as Parameters<typeof runtimeFor>[1];
 const settings={...emptySettings,defaults:{'gpu:vasp_std':'one'}};
 const fallback=runtimeFor({alias:'gpu'},profiles,settings,'gpu','vasp_ncl');
 assert.equal(fallback.template_id,'one');assert.equal(fallback.vasp_executable,'vasp_ncl');
 const exact=runtimeFor({alias:'gpu'},profiles,{...settings,defaults:{...settings.defaults,'gpu:vasp_ncl':'ncl'}},'gpu','vasp_ncl');
 assert.equal(exact.template_id,'ncl');
 const registered=runtimeFor({alias:'gpu'},profiles,{...settings,runtimes:{'gpu:vasp_ncl':{runtime_source:'registered',vasp_executable:'/special/vasp_ncl'}}},'gpu','vasp_ncl');
 assert.equal(registered.runtime_source,'registered');assert.equal(registered.template_id,undefined);
});
