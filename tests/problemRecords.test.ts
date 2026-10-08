import {test} from 'node:test';
import assert from 'node:assert/strict';
import {filterProblems} from '../src/problemRecords';
import type {ProblemRecord} from '../src/problemRecords';
test('find old issues by project, node, cause or INCAR tag without an attempt ID',()=>{
 const rows=[{id:'case',project_id:'p',project_name:'尖晶石',node_name:'极化',program:'VASP',server:'server',error_summary:'PEAD error',has_notes:true,notes:{user_note:'并行参数',suspected_cause:'',confirmed_cause:'',confirmation_evidence:''},incar_tags:['NCORE']} as ProblemRecord];
 for(const query of ['尖晶石','极化','并行参数','NCORE'])assert.equal(filterProblems(rows,query,'','','all').length,1);
 assert.equal(filterProblems(rows,'','other','','all').length,0);assert.equal(filterProblems(rows,'','','Wannier90','all').length,0);assert.equal(filterProblems(rows,'','','','no').length,0);
});
