import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BUILTIN_THEMES} from '../src/appearance.ts';
import {themedPlotOption} from '../src/plotAppearance.ts';
test('plot themes retain coordinates, energy bounds, axis formatters and orbital colors',()=>{
 const points=[[0,-1],[1,2]],format=(value:number)=>value+' eV';
 const option={animation:false as const,xAxis:{min:0,max:1,axisLabel:{formatter:format}},yAxis:{min:-5,max:8,name:'E − E_F (eV)'},
   series:[{type:'scatter' as const,name:'Fe dxy',data:points,itemStyle:{color:'#123456'}},{type:'line' as const,name:'Total Spin up',data:points,lineStyle:{width:2,color:'#414a51'}}]};
 const before=JSON.stringify(option);
 const changed=themedPlotOption(option,BUILTIN_THEMES[1]);const axes=changed.xAxis as typeof option.xAxis;const series=changed.series as typeof option.series;
 assert.equal(axes.min,0);assert.equal(axes.max,1);assert.equal(axes.axisLabel.formatter,format);assert.equal((changed.yAxis as typeof option.yAxis).max,8);assert.equal((changed.yAxis as typeof option.yAxis).name,'E − E_F (eV)');
 assert.equal(series[0].data,points);assert.equal(series[0].itemStyle!.color,'#123456');assert.equal(series[1].lineStyle!.width,2);assert.equal(series[1].lineStyle!.color,BUILTIN_THEMES[1].colors.plotText);assert.equal(JSON.stringify(option),before);
 const restored=themedPlotOption(option,BUILTIN_THEMES[0]);assert.equal(restored.backgroundColor,'#ffffff');assert.equal((restored.series as typeof option.series)[0].data,points);
});
