import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scriptFits,resourceFor,withResource,importedChoice} from '../src/scriptChoices.ts';
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
