import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import WannierComparisonResult,{type Comparison} from '../src/WannierComparisonResult';
import type {Project} from '../src/model';

const project={id:'p',name:'只读比较',nodes:[],edges:[]} as unknown as Project;
const record:Comparison={id:'comparison',wannier_attempt_id:'w',band_attempt_id:'d',seed:'wannier90.1',
 fermi_eV:5,local_path:'/isolated',created_at:'2026-10-24',note:'',diagnostics:{
  status:'PARTIAL',revision:'fpilot.band-metrics.v2',dft_band_first:21,dft_band_last:74,
  matched_bands:50,expected_bands:54,metric_window_relative_eV:[-9,5],reference_values:15000,
  reference:{id:'ref',selection:'first_common_kpoint_lower_edge_match'},
  mae_meV:null,rmse_meV:null,max_abs_diff_meV:null,paired_values:0,
  partial_metrics:{mae_meV:.805846,rmse_meV:4.388517,max_abs_diff_meV:99.913469,paired_values:15000},
  groups:[{wannier_first:49,wannier_last:54,dft_first:69,dft_last:74,matched_bands:2,expected_bands:6,
    mae_meV:1,rmse_meV:2,max_abs_diff_meV:3,paired_values:600}]},
 pyw90_diagnostic:{status:'COMPLETED',quality_value:777.113}};
const render=(value:Comparison)=>renderToStaticMarkup(createElement(WannierComparisonResult,
 {record:value,project,bandLabel:'参考能带',busy:false,onDiagnose:()=>{throw Error('read only');}}));

test('partial comparison displays covered statistics and suppresses a misleading complete native score',()=>{
 const html=render(record);
 assert.match(html,/参考覆盖 50\/54/);
 assert.match(html,/DFT 带 21–70/);
 assert.match(html,/缺少 4 条参考/);
 assert.match(html,/局部 MAE/);
 assert.match(html,/>0\.806<|>0\.806<!--/);
 assert.match(html,/>4\.389<|>4\.389<!--/);
 assert.match(html,/>2\/6<|>2<!--/);
 const visibleScore=html.match(/<p class="pyw90-status"[^>]*>(.*?)<\/p>/)?.[1];
 assert.match(visibleScore||'',/参考不完整，不计算总分/);
 assert.doesNotMatch(visibleScore||'',/777/);
 assert.doesNotMatch(html,/补充 PyW90 评分/);
});

test('old automatic comparisons remain identifiable and complete new scores declare the fixed reference',()=>{
 const old={...record,diagnostics:{...record.diagnostics!,status:'MEASURED',revision:'fpilot.band-metrics.v1'}};
 assert.match(render(old),/旧版自动匹配记录/);
 const next={...old,diagnostics:{...old.diagnostics,revision:'fpilot.band-metrics.v2'},
  pyw90_diagnostic:{status:'COMPLETED',quality_value:1,band_matching:'caller_fixed_reference',dft_band_first:21,dft_band_last:74}};
 const html=render(next);
 assert.match(html,/固定 DFT 带 21–74/);
 assert.doesNotMatch(html,/旧版自动匹配记录/);
});
