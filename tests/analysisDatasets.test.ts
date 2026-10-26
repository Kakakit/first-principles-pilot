import test from 'node:test';
import assert from 'node:assert/strict';
import {filterAnalyses,analysisKey,analysisFileUrl,analysisRecycleKind} from '../src/analysisDatasetModel';
import type {AnalysisRecord} from '../src/analysisDatasetModel';
const rows:AnalysisRecord[]=[{source_kind:'v2',source_id:'analysis-one',project_id:'p1',project_name:'Cr2FeO4',source_names:['noSOC 极化'],analysis_name:'形式极化',analysis_version:'2',title:'极化结果',note:'检查分支',summary:'端点',warnings:[],scientific_validation:'INSUFFICIENT',created_at:'2026-10-09'},
 {source_kind:'v1',source_id:'analysis-one',project_id:null,project_name:'未关联项目',source_names:['结构'],analysis_name:'空间对称性',analysis_version:'1',title:'旧结构分析',summary:'空间群',warnings:[],scientific_validation:'NOT_PERFORMED',created_at:'2026-10-08'}];
test('find analysis by project, source name, type and note without task IDs',()=>{
 assert.equal(filterAnalyses(rows,'p1','形式极化','nosoc')[0],rows[0]);
 assert.equal(filterAnalyses(rows,'','','分支')[0],rows[0]);
 assert.equal(filterAnalyses(rows,'p1','空间对称性','').length,0);
 assert.equal(filterAnalyses(rows,'__unlinked','','')[0],rows[1]);
});
test('legacy and v2 identities differ; file URLs preserve safe path segments',()=>{
 assert.notEqual(analysisKey(rows[0]),analysisKey(rows[1]));
 assert.equal(analysisFileUrl('/api/analysis-datasets/key','dir/结果 file.csv'),'/api/analysis-datasets/key/files/dir/%E7%BB%93%E6%9E%9C%20file.csv');
});
test('unregistered history reuses source recycling without requiring a dataset snapshot',()=>{
 assert.deepEqual(['v2','v1','local','plot','comparison'].map(kind=>analysisRecycleKind(kind as AnalysisRecord['source_kind'])),
  ['analyses','local-analyses','local-analyses','plots','comparisons']);
});
