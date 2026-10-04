import type {EChartsOption} from 'echarts';
import type {Theme} from './appearance';
import {fontStack} from './appearance';
const object=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
/** Changes presentation only: coordinates, data arrays, units and series identities are retained. */
export function themedPlotOption(option:EChartsOption,theme:Theme):EChartsOption {
  const c=theme.colors;
  const axis=(value:unknown)=>{const a=object(value);return {...a,
    axisLabel:{...object(a.axisLabel),color:c.plotText,fontSize:theme.metrics.secondaryFontSize},
    nameTextStyle:{...object(a.nameTextStyle),color:c.plotText,fontSize:theme.metrics.secondaryFontSize},
    axisLine:{...object(a.axisLine),lineStyle:{...object(object(a.axisLine).lineStyle),color:c.plotGrid}},
    axisTick:{...object(a.axisTick),lineStyle:{...object(object(a.axisTick).lineStyle),color:c.plotGrid}},
    splitLine:{...object(a.splitLine),lineStyle:{...object(object(a.splitLine).lineStyle),color:c.plotGrid}},
  };};
  const axes=(value:unknown)=>Array.isArray(value)?value.map(axis):axis(value);
  const rawSeries=Array.isArray(option.series)?option.series:option.series?[option.series]:[];
  const series=rawSeries.map(value=>{
    const s=object(value),mark=object(s.markLine);const changed={...s};
    if(typeof s.name==='string'&&s.name.startsWith('Total '))changed.lineStyle={...object(s.lineStyle),color:c.plotText};
    if(s.markLine)changed.markLine={...mark,label:{...object(mark.label),color:c.plotText},lineStyle:{...object(mark.lineStyle),color:c.plotGrid},
      data:Array.isArray(mark.data)?mark.data.map(item=>{if(Array.isArray(item))return item;const row=object(item);return {...row,...(row.lineStyle?{lineStyle:{...object(row.lineStyle),color:c.plotText}}:{})};}):mark.data};
    return changed;
  });
  return {...option,backgroundColor:c.surface,textStyle:{...option.textStyle,color:c.plotText,fontSize:theme.metrics.secondaryFontSize,fontFamily:fontStack(theme.fonts.english,theme.fonts.chinese)},
    tooltip:{...object(option.tooltip),backgroundColor:c.surface,borderColor:c.border,textStyle:{...object(object(option.tooltip).textStyle),color:c.text}},
    xAxis:axes(option.xAxis),yAxis:axes(option.yAxis),series} as EChartsOption;
}
