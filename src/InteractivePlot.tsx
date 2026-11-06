import {usePageVisible} from './usePageVisible';
import {errorMessage,requestJson,requestTimeouts} from './apiClient';
import {pollSerial} from './pollSerial';
import {useRequestScope} from './useRequestScope';
import {useSubmissionDraft} from './useSubmissionDraft';
import {attemptLabel} from './humanLabels';
import {memo,useEffect,useMemo,useRef,useState} from 'react';
import * as echarts from 'echarts';
import {useAppearance} from './useAppearance';
import {BUILTIN_THEMES} from './appearance';
import type {Theme} from './appearance';
import {themedPlotOption} from './plotAppearance';
import {kPathBounds,projectionColors} from './projectionStyle';
import type {Project} from './model';
import BandEdges from './BandEdges';
import type {BandReference} from './BandEdges';

type Candidate={id:string;node_id?:string;execution_scope?:string;stage:'bands'|'dos';host:string;job_id?:string;path?:string;scf_attempt_id?:string;input_note?:string|null;chain_run_id?:string;config?:{name?:string};created_at?:string;state?:string;parameter_summary?:string;case_index?:number;batch_reason?:string};
type CacheMeta={id:string;remote_parts?:Record<string,unknown>;band_attempt_id?:string;dos_attempt_id?:string;band?:{elements:string[];orbitals:string[];spins:number;energy_reference:BandReference};dos?:{elements:string[];orbitals:string[];spins:number;energy_reference:BandReference;projection_note?:string};sources:Array<{name:string;size_bytes:number;local_path:string}>;compatible?:boolean;compatibility_note?:string};
type Job={id:string;state:string;downloaded_bytes:number;total_bytes:number;available_bytes?:number;current_file?:string;error?:string;metadata?:CacheMeta};
type Trace={element:string;orbital:string;weights?:number[][][];values?:number[][]};
type PlotData={band?:{spins:number;x:number[];breaks:number[];ticks:Array<{x:number;label:string}>;energies:number[][][];traces:Trace[];reference:BandReference};dos?:{spins:number;energy:number[];total:number[][];traces:Trace[];reference:BandReference};compatible:boolean;compatibility_note:string};
type PlotDraft={bandId:string;dosId:string;selected:string[]|null;emin:number;emax:number;pointSize:number;title:string};

const api=(url:string,body?:unknown,signal?:AbortSignal)=>requestJson(url,body,undefined,{signal});
const activePlotStates=['GENERATING','DOWNLOADING','PARSING'];
const emptySelection:string[]=[];
const groups:Record<string,string[]>={s:['s'],p:['px','py','pz'],d:['dxy','dyz','dz2','dxz','dx2-y2']};
const label=(spin:number)=>spin===0?'Spin up':'Spin down';
const bytes=(value:number)=>value<1048576?`${(value/1024).toFixed(1)} KiB`:`${(value/1048576).toFixed(1)} MiB`;
const key=(element:string,orbital:string)=>`${element}|${orbital}`;
const paddedEnergyMax=(emin:number,emax:number)=>emax+Math.max(Math.abs(emax)*0.25,(emax-emin)*0.05);
type LegendGroup={element:string;orbitals:string[]};
function legendLayout(context:CanvasRenderingContext2D,groups:LegendGroup[],width:number,spinNote:boolean,componentColor:(element:string,orbital:string)=>string){
  context.font='26px sans-serif';
  const entries:Array<{x:number;y:number;name:string;swatch?:string}>=[];
  let y=38;
  for(const group of groups){
    let x=32;
    entries.push({x,y,name:group.element});
    x+=context.measureText(group.element).width+28;
    for(const orbital of group.orbitals){
      const name=orbital==='p'?'p-all':orbital==='d'?'d-all':orbital;
      const itemWidth=context.measureText(name).width+52;
      if(x+itemWidth>width-32){y+=42;x=64;}
      entries.push({x,y,name,swatch:componentColor(group.element,orbital)});
      x+=itemWidth+16;
    }
    y+=50;
  }
  return {entries,height:groups.length?y+(spinNote?46:10):0,noteY:spinNote?y+8:0};
}

function Chart({option,chartRef,height=360}:{option:echarts.EChartsOption;chartRef?:React.MutableRefObject<echarts.ECharts|null>;height?:number}){
  const holder=useRef<HTMLDivElement>(null);
  const {theme}=useAppearance();
  useEffect(()=>{
    if(!holder.current)return;
    const chart=echarts.init(holder.current,undefined,{renderer:'canvas'});
    chartRef&&(chartRef.current=chart);
    const observer=new ResizeObserver(()=>chart.resize());observer.observe(holder.current);
    return ()=>{observer.disconnect();chart.dispose();if(chartRef)chartRef.current=null;};
  },[]);
  useEffect(()=>{if(holder.current){const chart=echarts.getInstanceByDom(holder.current);if(chart){chart.setOption(themedPlotOption(option,theme),true);}}},[option,theme]);
  return <div ref={holder} style={{width:'100%',height}} role="img" aria-label="交互式电子结构图"/>;
}

function DeferredChart({build,height}:{build:()=>echarts.EChartsOption;height:number}){
 const pageVisible=usePageVisible();
 const holder=useRef<HTMLDivElement>(null),[visible,setVisible]=useState(false);
 useEffect(()=>{if(!holder.current)return;const observer=new IntersectionObserver(entries=>setVisible(entries.some(e=>e.isIntersecting)),{rootMargin:'100px'});observer.observe(holder.current);return()=>observer.disconnect();},[]);
 const option=useMemo(()=>visible&&pageVisible?build():null,[visible,pageVisible,build]);
 return <div ref={holder} style={{minHeight:height}}>{option?<Chart option={option} height={height}/>:<p>滚动到此处载入图表</p>}</div>;
}

function bandOption(data:NonNullable<PlotData['band']>,spin:number|undefined,emin:number,emax:number,pointSize:number,componentColor:(element:string,orbital:string)=>string):echarts.EChartsOption{
  const selectedSpins=spin===undefined?Array.from({length:data.spins},(_,i)=>i):[spin];
  const series:echarts.SeriesOption[]=[];
  for(const si of selectedSpins){
    const energy=data.energies[si];
    const nb=energy?.[0]?.length||0;
    for(let band=0;band<nb;band++){
      const points:Array<[number,number|null]>=[];
      for(let k=0;k<data.x.length;k++){
        if(data.breaks?.includes(k))points.push([data.x[k],null]);
        const y=energy[k]?.[band];points.push([data.x[k],Number.isFinite(y)&&y>=emin-0.5&&y<=emax+0.5?y:null]);
      }
      series.push({type:'line',data:points,showSymbol:false,silent:true,animation:false,
                   lineStyle:{width:spin===undefined?0.9:0.65,color:spin===undefined?(si===0?'#8295ad':'#bc9b83'):'#b9c3cc',opacity:0.65}});
    }
    for(const trace of data.traces){
      const points:number[][]=[];
      const weights=trace.weights?.[si];
      if(!weights)continue;
      for(let k=0;k<data.x.length;k++)for(let b=0;b<nb;b++){
        const value=weights[k]?.[b]||0, y=energy[k]?.[b];
        if(value>0.015&&Number.isFinite(y)&&y>=emin&&y<=emax)points.push([data.x[k],y,value]);
      }
      const name=`${trace.element} ${trace.orbital}`;
      series.push({type:'scatter',name,data:points,
                   symbolSize:(point:unknown)=>Math.min(18,pointSize*(1.5+6*Math.sqrt(Math.max(0,(point as number[])[2])))),
                   itemStyle:{color:componentColor(trace.element,trace.orbital),opacity:0.72},
                   symbol:spin===undefined&&si===1?'triangle':'circle',progressive:5000,progressiveThreshold:10000,animation:false});
    }
  }
  series.push({type:'line',data:[],silent:true,markLine:{silent:true,symbol:'none',animation:false,precision:-1,
    label:{show:true,position:'end',formatter:(params:unknown)=>String((params as {name?:string}).name||'')},
    lineStyle:{color:'#d0d7dd',width:1},data:[{yAxis:0,name:'E_F',lineStyle:{color:'#4e5963',type:'dashed'}},...data.ticks.map(tick=>({xAxis:tick.x,name:tick.label,label:{formatter:tick.label}}))]}} as echarts.SeriesOption);
  return {animation:false,grid:{left:58,right:32,top:28,bottom:58},tooltip:{trigger:'item'},
          xAxis:{type:'value',...kPathBounds(data.x),boundaryGap:[0,0],name:'k path (Å⁻¹)',nameLocation:'middle',nameGap:28,axisLabel:{show:false},splitLine:{show:true}},
          yAxis:{type:'value',name:'E − E_F (eV)',min:emin,max:paddedEnergyMax(emin,emax),axisLine:{show:true},
                 splitLine:{show:true,lineStyle:{color:'#e4e9ec'}}},
          series} as echarts.EChartsOption;
}

function dosOption(data:NonNullable<PlotData['dos']>,emin:number,emax:number,componentColor:(element:string,orbital:string)=>string):echarts.EChartsOption{
  const series:echarts.SeriesOption[]=[];
  let visibleMax=0;
  for(const values of [...data.total,...data.traces.flatMap(trace=>trace.values||[])]){
    for(let i=0;i<values.length;i++)if(data.energy[i]>=emin&&data.energy[i]<=emax&&Number.isFinite(values[i]))visibleMax=Math.max(visibleMax,Math.abs(values[i]));
  }
  const intensityMax=Math.max(1,visibleMax*1.25);
  for(let spin=0;spin<data.spins;spin++){
    const sign=data.spins===2&&spin===1?-1:1;
    series.push({type:'line',name:`Total ${data.spins===2?label(spin):''}`,
                 data:data.energy.map((energy,i)=>[sign*(data.total[spin]?.[i]||0),energy]),showSymbol:false,animation:false,
                 lineStyle:{color:'#414a51',width:1.8,type:spin?'dashed':'solid'}});
    for(const trace of data.traces){const name=`${trace.element} ${trace.orbital}`;
      series.push({type:'line',name,
          data:data.energy.map((energy,i)=>[sign*(trace.values?.[spin]?.[i]||0),energy]),showSymbol:false,animation:false,
          lineStyle:{color:componentColor(trace.element,trace.orbital),width:1.7,type:spin?'dashed':'solid'}});}
  }
  return {animation:false,grid:{left:46,right:20,top:28,bottom:58},tooltip:{trigger:'axis'},
          xAxis:{type:'value',name:'DOS (states/eV)',nameLocation:'middle',nameGap:28,
                 min:data.spins===2?-intensityMax:0,max:intensityMax,splitLine:{show:true}},
          yAxis:{type:'value',name:'E − E_F',min:emin,max:paddedEnergyMax(emin,emax),axisLine:{show:true},splitLine:{show:true}},series} as echarts.EChartsOption;
}

function InteractivePlot({project,initialBandId='',initialDosId='',compact=false,draftNodeId}:{project:Project;initialBandId?:string;initialDosId?:string;compact?:boolean;draftNodeId?:string}){
  const visible=usePageVisible();
  const projectId=project.id;
  const draftKey=draftNodeId?`plot:${projectId}:${draftNodeId}:${initialBandId}:${initialDosId}`:null;
  const [draft,setDraft]=useSubmissionDraft<PlotDraft>(draftKey,{bandId:initialBandId,dosId:initialDosId,selected:null,emin:-4,emax:4,pointSize:0.5,title:''});
  const {bandId,dosId,emin,emax,pointSize,title}=draft;
  const selected=draft.selected??emptySelection;
  const updateDraft=(patch:Partial<PlotDraft>)=>setDraft(current=>({...current,...patch}));
  const requests=useRequestScope(JSON.stringify([projectId,bandId,dosId]));
  const [candidates,setCandidates]=useState<Candidate[]>([]);
  const [job,setJob]=useState<Job|null>(null),[data,setData]=useState<PlotData|null>(null);
  const [error,setError]=useState(''),[message,setMessage]=useState('');
  const [pollError,setPollError]=useState(''),[pollRevision,setPollRevision]=useState(0),[preparing,setPreparing]=useState(false);
  const jobId=job?.id;
  const running=activePlotStates.includes(job?.state||'');
  const exporting=useRef(false),[saving,setSaving]=useState(false);
  const seriesQueue=useRef<{running:boolean;pending?:()=>Promise<void>;controller?:AbortController}>({running:false});
  useEffect(()=>{
    const queue=seriesQueue.current;
    return ()=>{queue.pending=undefined;queue.controller?.abort();};
  },[]);

  const candidateLabel=(item:Candidate)=>{
    if('input_note' in item)return attemptLabel(item,project)+' · '+item.host;
    const node=project.nodes.find(row=>row.id===(item.node_id||item.execution_scope));
    const attemptTime=item.path?.match(/attempts\/(\d{8})-(\d{6})/);
    const when=attemptTime?`${attemptTime[1].slice(4,6)}/${attemptTime[1].slice(6,8)} ${attemptTime[2].slice(0,2)}:${attemptTime[2].slice(2,4)}`:'';
    return `${node?.data.label||item.stage} · ${item.job_id||'未提交'} · ${item.host}${when?' · '+when:''}`;
  };
  useEffect(()=>{const controller=new AbortController();void api(`/api/interactive-plots/candidates?project_id=${encodeURIComponent(projectId)}`,undefined,controller.signal).then(value=>{if(!controller.signal.aborted)setCandidates(value.attempts);}).catch(caught=>{if(!controller.signal.aborted)setError((caught as Error).message);});return()=>controller.abort();},[projectId]);
  useEffect(()=>{
    setData(null);
    setJob(null);
    setError('');
    setPollError('');
    setPollRevision(0);
    setPreparing(false);
    if(!bandId&&!dosId)return;
    const request=requests.start('selection');
    const query=new URLSearchParams({project_id:projectId});
    if(bandId)query.set('band_attempt_id',bandId);
    if(dosId)query.set('dos_attempt_id',dosId);
    void api(`/api/interactive-plots/cached?${query}`,undefined,request.signal)
      .then(value=>{if(request.current()&&value.state!=='ABSENT')setJob(value);})
      .catch(caught=>{if(request.current())setError(errorMessage(caught));});
  },[requests]);
  useEffect(()=>{
    if(!visible||!jobId||!running)return;
    const request=requests.start('status');
    const stop=pollSerial(async()=>{
      const value:Job=await api(`/api/interactive-plots/${encodeURIComponent(jobId)}/status`,undefined,request.signal);
      if(!request.current())return;
      setJob(value);
      setPollError('');
      if(!activePlotStates.includes(value.state))stop();
    },caught=>{
      if(!request.current())return;
      const message=errorMessage(caught);
      setPollError(message.startsWith('本次请求等待超时')?'等待状态响应超时。':message);
    },1000,pollRevision?0:1000);
    return ()=>{stop();requests.cancel('status');};
  },[requests,jobId,running,visible,pollRevision]);
  const meta=job?.metadata;
  const available=useMemo(()=>{
    const elements=[...new Set([...(meta?.band?.elements||[]),...(meta?.dos?.elements||[])])];
    return elements.map(element=>({element,orbitals:[...new Set([...(meta?.band?.orbitals||[]),...(meta?.dos?.orbitals||[])])]}));
  },[meta]);
  const componentColor=useMemo(()=>projectionColors(available.map(item=>item.element)),[available]);
  const legendGroups=useMemo(()=>available.map(({element})=>({element,orbitals:selected.filter(item=>item.startsWith(`${element}|`)).map(item=>item.split('|')[1])})).filter(group=>group.orbitals.length),[available,selected]);
  useEffect(()=>{
    if(job?.state!=='READY'||!meta)return;
    if(draft.selected===null){const defaults=[key('Fe','d'),key('Cr','d'),key('O','p')].filter(value=>{const [element,group]=value.split('|');return available.some(item=>item.element===element&&(item.orbitals.includes(group)||groups[group]?.some(name=>item.orbitals.includes(name))))});updateDraft({selected:defaults});}
  },[job?.state,meta,available]);
  useEffect(()=>{
    if(!visible||job?.state!=='READY'||!Number.isFinite(emin)||!Number.isFinite(emax)||emin>=emax)return;
    let cancelled=false;const controller=new AbortController();
    const remote=Boolean(meta?.remote_parts&&Object.keys(meta.remote_parts).length);
    const run=async()=>{
      const queue=seriesQueue.current;
      queue.pending=async()=>{
        if(cancelled)return;
        queue.controller=controller;
        try{
          const value=await api('/api/interactive-plots/series',{cache_id:job.id,selected:selected.map(item=>{const [element,orbital]=item.split('|');return {element,orbital};}),emin,emax},controller.signal);
          if(!cancelled)setData(value);
        }catch(caught){if(!cancelled)setError(errorMessage(caught));}
        finally{queue.controller=undefined;}
      };
      if(queue.running)return;queue.running=true;
      try{while(queue.pending){const request=queue.pending;queue.pending=undefined;await request();}}finally{queue.running=false;}
    };
    const timer=window.setTimeout(()=>void run(),remote?500:120);
    return ()=>{cancelled=true;if(!remote)controller.abort();window.clearTimeout(timer);};
  },[visible,job?.state,job?.id,selected,emin,emax]);
  const prepare=async(force=false)=>{
    const request=requests.start('selection');
    setPreparing(true);
    setError('');
    setPollError('');
    if(force)setData(null);
    try{
      const value=await requestJson('/api/interactive-plots/prepare',{project_id:projectId,band_attempt_id:bandId||null,dos_attempt_id:dosId||null,force},undefined,{signal:request.signal,timeoutMs:requestTimeouts.remote});
      if(request.current())setJob(value);
    }catch(caught){if(request.current())setError(errorMessage(caught));}
    finally{if(request.current())setPreparing(false);}
  };
  const selectSource=(patch:Pick<PlotDraft,'bandId'>|Pick<PlotDraft,'dosId'>)=>{
    requests.cancel();
    setJob(null);
    setData(null);
    updateDraft(patch);
  };
  const toggle=(item:string)=>{
    setData(null);
    setDraft(current=>{
      const items=current.selected??emptySelection;
      return {...current,selected:items.includes(item)?items.filter(value=>value!==item):[...items,item]};
    });
  };
  const save=async()=>{
    if(!job||!data||exporting.current)return;
    exporting.current=true;setSaving(true);
    setError('');setMessage('');
    try{
      const width=Math.max(400,Math.min(1000,document.querySelector('.interactive-plot-workspace')?.clientWidth||720));
      const image=document.createElement('canvas');image.width=Math.round(width*2);
      const context=image.getContext('2d');if(!context)throw new Error('无法导出 Canvas');
      const legend=legendLayout(context,legendGroups,image.width,Boolean(data.band?.spins===2||data.dos?.spins===2),componentColor);
      image.height=plotBuilders.reduce((sum,c)=>sum+c.height*2,0)+legend.height;
      context.fillStyle='#fff';context.fillRect(0,0,image.width,image.height);
      let offset=0;
      for(const plot of plotBuilders){
        const holder=document.createElement('div');holder.style.cssText=`position:fixed;left:-20000px;top:0;width:${width}px;height:${plot.height}px`;document.body.append(holder);
        const chart=echarts.init(holder,undefined,{renderer:'canvas'});
        try{chart.setOption(themedPlotOption(plot.build(),BUILTIN_THEMES[0]),true);const canvas=chart.getRenderedCanvas({pixelRatio:2});context.drawImage(canvas,0,offset);offset+=canvas.height;}
        finally{chart.dispose();holder.remove();}
        await new Promise<void>(resolve=>setTimeout(resolve,0));
      }
      context.font='26px sans-serif';context.textBaseline='middle';
      for(const entry of legend.entries){const y=offset+entry.y;if(entry.swatch){context.fillStyle=entry.swatch;context.beginPath();context.arc(entry.x+12,y,10,0,Math.PI*2);context.fill();}context.fillStyle='#34454b';context.fillText(entry.name,entry.x+(entry.swatch?34:0),y);}
      if(legend.noteY){context.font='22px sans-serif';context.fillStyle='#5c7078';context.fillText('Spin up: solid / circle    Spin down: dashed / triangle',32,offset+legend.noteY);}
      const png=await new Promise<Blob>((resolve,reject)=>image.toBlob(blob=>blob?resolve(blob):reject(new Error('无法导出图片')),'image/png'));
      image.width=0;image.height=0;
      const metadata={cache_id:job.id,project_id:projectId,selected:selected.map(item=>{const [element,orbital]=item.split('|');return {element,orbital};}),emin,emax,point_size_scale:pointSize,title:title.slice(0,160)};
      const response=await fetch('/api/interactive-plots/save-binary?metadata='+encodeURIComponent(JSON.stringify(metadata)),{method:'POST',headers:{'Content-Type':'image/png'},body:png});
      const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'保存图片失败');
      setMessage(`已保存结果集；可在“数据集与历史交接”重看和下载。`);
    }catch(caught){setError((caught as Error).message);}
    finally{exporting.current=false;setSaving(false);}
  };
  const plotBuilders=useMemo(()=>{
    const items:Array<{id:string;title:string;height:number;build:()=>echarts.EChartsOption}>=[];
    if(data?.band){const band=data.band;for(let i=0;i<band.spins;i++)items.push({id:'spin'+i,title:band.spins===1?'能带与 fatband · 电荷投影':label(i),height:compact?300:390,build:()=>bandOption(band,i,emin,emax,pointSize,componentColor)});
      if(band.spins===2)items.push({id:'combined',title:'上下自旋叠加',height:compact?300:390,build:()=>bandOption(band,undefined,emin,emax,pointSize,componentColor)});}
    if(data?.dos){const dos=data.dos;items.push({id:'dos',title:dos.spins===2?'上下自旋镜像 PDOS':'PDOS',height:compact?320:460,build:()=>dosOption(dos,emin,emax,componentColor)});}
    return items;
  },[data,emin,emax,pointSize,componentColor,compact]);
  return <section className="input-submit-card interactive-plot-workspace"><h3>内置交互式 fatband／PDOS</h3>
    <div className="parameter-grid"><label className="field">已完成能带 attempt<select value={bandId} onChange={e=>selectSource({bandId:e.target.value})}><option value="">不显示 fatband</option>{candidates.filter(item=>item.stage==='bands').map(item=><option key={item.id} value={item.id} title={attemptLabel(item,project,true)}>{candidateLabel(item)}</option>)}</select></label><label className="field">已完成 DOS attempt<select value={dosId} onChange={e=>selectSource({dosId:e.target.value})}><option value="">不显示 PDOS</option>{candidates.filter(item=>item.stage==='dos').map(item=><option key={item.id} value={item.id} title={attemptLabel(item,project,true)}>{candidateLabel(item)}</option>)}</select></label></div>
    {(bandId||dosId)&&<p>来源：{candidates.filter(item=>item.id===bandId||item.id===dosId).map(item=>`${candidateLabel(item)} · ${item.scf_attempt_id?'使用相连 SCF 参考':'使用本任务参考'}`).join('；')}</p>}
    <button className="button primary" disabled={!bandId&&!dosId||preparing||running} onClick={()=>void prepare()}>{preparing?'正在准备绘图数据…':job?.state==='READY'?'使用本机绘图缓存':'准备本地绘图数据'}</button>
    {job?.state==='READY'&&<button className="button" disabled={preparing} onClick={()=>void prepare(true)}>刷新远端来源</button>}
    {job&&<p role="status">{job.state} · {bytes(job.downloaded_bytes||0)} / {bytes(job.total_bytes||0)} · 本机可用 {job.available_bytes?bytes(job.available_bytes):'未记录'} {job.current_file||''}{job.error&&` · ${job.error}`}</p>}
    {pollError&&<div><p role="alert" className="structure-error">绘图状态暂时读取失败，稍后会继续查询。{pollError}</p><button className="button" type="button" onClick={()=>setPollRevision(value=>value+1)}>重新读取状态</button></div>}
    {meta&&<><p>{meta.sources.length} 个来源文件 · 绘图数据已保存到本机</p><p>能带 E_F：{meta.band?.energy_reference.fermi_eV??'未选'} eV；DOS E_F：{meta.dos?.energy_reference.fermi_eV??'未选'} eV。各图按各自来源归零。</p><details><summary>本机文件大小与来源</summary>{meta.sources.map((source,index)=><p key={index}>{source.name} · {bytes(source.size_bytes)} · <code>{source.local_path}</code></p>)}</details>{meta.dos?.projection_note&&<p className="handoff-warning">{meta.dos.projection_note}</p>}{meta.compatibility_note&&<p className="handoff-warning">{meta.compatibility_note}</p>}
      {meta.band&&<BandEdges reference={meta.band.energy_reference}/>}<div className="parameter-grid"><label className="field">能量下界 E−E_F / eV<input type="number" step="0.1" value={emin} onChange={e=>{setData(null);updateDraft({emin:Number(e.target.value)});}}/></label><label className="field">能量上界 E−E_F / eV<input type="number" step="0.1" value={emax} onChange={e=>{setData(null);updateDraft({emax:Number(e.target.value)});}}/></label></div>
      {meta.band&&<label className="field fatband-point-size">彩点大小 · {pointSize.toFixed(1)} 倍<input type="range" min="0.1" max="1.5" step="0.1" value={pointSize} onChange={e=>updateDraft({pointSize:Number(e.target.value)})}/></label>}
      <div className="orbital-selector">{available.map(({element,orbitals})=><fieldset key={element}><legend>{element}</legend>{Object.entries(groups).map(([group,components])=><div key={group}><label><input type="checkbox" checked={selected.includes(key(element,group))} disabled={!components.some(name=>orbitals.includes(name))&&!orbitals.includes(group)} onChange={()=>toggle(key(element,group))}/>{group}-all</label><div className="orbital-components">{components.filter(name=>group!=='s').map(name=><label key={name}><input type="checkbox" checked={selected.includes(key(element,name))} disabled={!orbitals.includes(name)} onChange={()=>toggle(key(element,name))}/>{name}</label>)}</div></div>)}</fieldset>)}</div>
    </>}
    {data&&<div className={`interactive-plot-panels${data.band?.spins===1?' single-channel':''}`}>{plotBuilders.map(plot=><article key={plot.id} className={plot.id==='dos'?'interactive-dos-panel':''}><h4>{plot.title}</h4><DeferredChart build={plot.build} height={plot.height}/></article>)}</div>}
    {data&&<div className="plot-color-legend" aria-label="元素与轨道颜色图例">{legendGroups.map(group=><div className="plot-legend-group" key={group.element}><strong>{group.element}</strong>{group.orbitals.map(orbital=><span key={orbital}><i className="plot-legend-dot" style={{backgroundColor:componentColor(group.element,orbital)}}/>{orbital==='p'?'p-all':orbital==='d'?'d-all':orbital}</span>)}</div>)}{(data.band?.spins===2||data.dos?.spins===2)&&<small>自旋：实线／圆点为 up，虚线／三角为 down；颜色由元素和轨道决定。</small>}</div>}
    {data&&<><label className="field">图片标题<input value={title} onChange={e=>updateDraft({title:e.target.value})} placeholder="例如 CrFeO 投影能带与 DOS"/></label><button className="button primary" disabled={saving} onClick={()=>void save()}>{saving?'正在保存…':'保存当前图与曲线数据'}</button></>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert" className="structure-error">{error}</p>}
  </section>;
}

// Parent form edits do not change plotting inputs. Keep existing charts intact.
export default memo(InteractivePlot);
