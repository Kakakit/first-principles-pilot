import {memo,useEffect,useMemo,useRef,useState} from 'react';
import * as echarts from 'echarts';
import {useAppearance} from './useAppearance';
import {BUILTIN_THEMES} from './appearance';
import type {Theme} from './appearance';
import {themedPlotOption} from './plotAppearance';
import type {Project} from './model';

type Candidate={id:string;stage:'bands'|'dos';host:string;job_id?:string;path?:string;scf_attempt_id?:string};
type CacheMeta={id:string;remote_parts?:Record<string,unknown>;band_attempt_id?:string;dos_attempt_id?:string;band?:{elements:string[];orbitals:string[];spins:number;energy_reference:{fermi_eV:number}};dos?:{elements:string[];orbitals:string[];spins:number;energy_reference:{fermi_eV:number};projection_note?:string};sources:Array<{name:string;size_bytes:number;local_path:string}>;compatible?:boolean;compatibility_note?:string};
type Job={id:string;state:string;downloaded_bytes:number;total_bytes:number;available_bytes?:number;current_file?:string;error?:string;metadata?:CacheMeta};
type Trace={element:string;orbital:string;weights?:number[][][];values?:number[][]};
type PlotData={band?:{spins:number;x:number[];breaks:number[];ticks:Array<{x:number;label:string}>;energies:number[][][];traces:Trace[];reference:{fermi_eV:number}};dos?:{spins:number;energy:number[];total:number[][];traces:Trace[];reference:{fermi_eV:number}};compatible:boolean;compatibility_note:string};

async function api(url:string,body?:unknown){const response=await fetch(url,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'绘图请求失败');return value;}
const palette:Record<string,string>={Fe:'#356aa5',Cr:'#d87a34',O:'#248d82'};
const fallback=['#8055a8','#9a7844','#cd5f80','#597c55','#b09426'];
const color=(element:string)=>palette[element]||fallback[Array.from(element).reduce((sum,ch)=>sum+ch.charCodeAt(0),0)%fallback.length];
const componentOrder=['s','p','px','py','pz','d','dxy','dyz','dz2','dxz','dx2-y2'];
const componentColor=(element:string,orbital:string)=>{
  const seed=({Fe:210,Cr:24,O:165} as Record<string,number>)[element]??Array.from(element).reduce((sum,ch)=>sum+ch.charCodeAt(0)*7,0)%360;
  const index=Math.max(0,componentOrder.indexOf(orbital));
  return `hsl(${(seed+index*47)%360} 67% ${index%3===0?39:index%3===1?49:58}%)`;
};
const groups:Record<string,string[]>={s:['s'],p:['px','py','pz'],d:['dxy','dyz','dz2','dxz','dx2-y2']};
const label=(spin:number)=>spin===0?'Spin up':'Spin down';
const bytes=(value:number)=>value<1048576?`${(value/1024).toFixed(1)} KiB`:`${(value/1048576).toFixed(1)} MiB`;
const key=(element:string,orbital:string)=>`${element}|${orbital}`;
const paddedEnergyMax=(emin:number,emax:number)=>emax+Math.max(Math.abs(emax)*0.25,(emax-emin)*0.05);
type LegendGroup={element:string;orbitals:string[]};
function legendLayout(context:CanvasRenderingContext2D,groups:LegendGroup[],width:number,spinNote:boolean){
  context.font='26px sans-serif';
  const entries:Array<{x:number;y:number;name:string;swatch:string;element:boolean}>=[];
  let y=38;
  for(const group of groups){
    let x=32;
    entries.push({x,y,name:group.element,swatch:color(group.element),element:true});
    x+=context.measureText(group.element).width+62;
    for(const orbital of group.orbitals){
      const name=orbital==='p'?'p-all':orbital==='d'?'d-all':orbital;
      const itemWidth=context.measureText(name).width+52;
      if(x+itemWidth>width-32){y+=42;x=64;}
      entries.push({x,y,name,swatch:componentColor(group.element,orbital),element:false});
      x+=itemWidth+16;
    }
    y+=50;
  }
  return {entries,height:groups.length?y+(spinNote?46:10):0,noteY:spinNote?y+8:0};
}

const chartPresentation=new WeakMap<echarts.ECharts,{option:echarts.EChartsOption;theme:Theme}>();
function exportPlotCanvas(chart:echarts.ECharts){
  const current=chartPresentation.get(chart);if(!current)return chart.getRenderedCanvas({pixelRatio:2});
  try{chart.setOption(themedPlotOption(current.option,BUILTIN_THEMES[0]),true);const rendered=chart.getRenderedCanvas({pixelRatio:2});const snapshot=document.createElement('canvas');snapshot.width=rendered.width;snapshot.height=rendered.height;const context=snapshot.getContext('2d');if(!context)throw new Error('无法导出 Canvas');context.drawImage(rendered,0,0);return snapshot;}
  finally{chart.setOption(themedPlotOption(current.option,current.theme),true);}
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
  useEffect(()=>{if(holder.current){const chart=echarts.getInstanceByDom(holder.current);if(chart){chartPresentation.set(chart,{option,theme});chart.setOption(themedPlotOption(option,theme),true);}}},[option,theme]);
  return <div ref={holder} style={{width:'100%',height}} role="img" aria-label="交互式电子结构图"/>;
}

function bandOption(data:NonNullable<PlotData['band']>,spin:number|undefined,emin:number,emax:number,pointSize:number):echarts.EChartsOption{
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
  series.push({type:'line',data:[],silent:true,markLine:{silent:true,symbol:'none',animation:false,
    label:{show:true,position:'end',formatter:(params:unknown)=>String((params as {name?:string}).name||'')},
    lineStyle:{color:'#d0d7dd',width:1},data:[{yAxis:0,name:'E_F',lineStyle:{color:'#4e5963',type:'dashed'}},...data.ticks.map(tick=>({xAxis:tick.x,name:tick.label}))]}} as echarts.SeriesOption);
  return {animation:false,grid:{left:58,right:20,top:28,bottom:58},tooltip:{trigger:'item'},
          xAxis:{type:'value',name:'k path (Å⁻¹)',nameLocation:'middle',nameGap:28,axisLabel:{show:false},splitLine:{show:true}},
          yAxis:{type:'value',name:'E − E_F (eV)',min:emin,max:paddedEnergyMax(emin,emax),axisLine:{show:true},
                 splitLine:{show:true,lineStyle:{color:'#e4e9ec'}}},
          series} as echarts.EChartsOption;
}

function dosOption(data:NonNullable<PlotData['dos']>,emin:number,emax:number):echarts.EChartsOption{
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

function InteractivePlot({project,initialBandId='',initialDosId='',compact=false}:{project:Project;initialBandId?:string;initialDosId?:string;compact?:boolean}){
  const projectId=project.id;
  const [candidates,setCandidates]=useState<Candidate[]>([]),[bandId,setBandId]=useState(initialBandId),[dosId,setDosId]=useState(initialDosId);
  const [job,setJob]=useState<Job|null>(null),[data,setData]=useState<PlotData|null>(null);
  const [selected,setSelected]=useState<string[]>([]),[emin,setEmin]=useState(-4),[emax,setEmax]=useState(4);
  const [pointSize,setPointSize]=useState(0.5);
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[title,setTitle]=useState('');
  const charts=useRef<Array<echarts.ECharts|null>>([null,null,null,null]);
  const candidateLabel=(item:Candidate)=>{
    const node=project.nodes.find(row=>item.path?.includes(`/stages/${String(row.data.stageSequence).padStart(2,'0')}-${row.data.stageSlug}/`));
    const attemptTime=item.path?.match(/attempts\/(\d{8})-(\d{6})/);
    const when=attemptTime?`${attemptTime[1].slice(4,6)}/${attemptTime[1].slice(6,8)} ${attemptTime[2].slice(0,2)}:${attemptTime[2].slice(2,4)}`:'';
    return `${node?.data.label||item.stage} · ${item.job_id||'未提交'} · ${item.host}${when?' · '+when:''}`;
  };
  useEffect(()=>{void api(`/api/interactive-plots/candidates?project_id=${encodeURIComponent(projectId)}`).then(value=>setCandidates(value.attempts)).catch(caught=>setError((caught as Error).message));},[projectId]);
  useEffect(()=>{
    let cancelled=false;setData(null);setJob(null);
    if(bandId||dosId){const query=new URLSearchParams({project_id:projectId});if(bandId)query.set('band_attempt_id',bandId);if(dosId)query.set('dos_attempt_id',dosId);
      void api(`/api/interactive-plots/cached?${query}`).then(value=>{if(!cancelled&&value.state==='READY')setJob(value);}).catch(caught=>{if(!cancelled)setError((caught as Error).message);});}
    return ()=>{cancelled=true;};
  },[bandId,dosId,projectId]);
  useEffect(()=>{
    if(!job||!['GENERATING','DOWNLOADING','PARSING'].includes(job.state))return;
    const timer=window.setTimeout(()=>void api(`/api/interactive-plots/${job.id}/status`).then(setJob).catch(caught=>setError((caught as Error).message)),1000);
    return ()=>window.clearTimeout(timer);
  },[job]);
  const meta=job?.metadata;
  const available=useMemo(()=>{
    const elements=[...new Set([...(meta?.band?.elements||[]),...(meta?.dos?.elements||[])])];
    return elements.map(element=>({element,orbitals:[...new Set([...(meta?.band?.orbitals||[]),...(meta?.dos?.orbitals||[])])]}));
  },[meta]);
  const legendGroups=useMemo(()=>available.map(({element})=>({element,orbitals:selected.filter(item=>item.startsWith(`${element}|`)).map(item=>item.split('|')[1])})).filter(group=>group.orbitals.length),[available,selected]);
  useEffect(()=>{
    if(job?.state!=='READY'||!meta)return;
    if(!selected.length){const defaults=[key('Fe','d'),key('Cr','d'),key('O','p')].filter(value=>{const [element,group]=value.split('|');return available.some(item=>item.element===element&&(item.orbitals.includes(group)||groups[group]?.some(name=>item.orbitals.includes(name))))});setSelected(defaults);}
  },[job?.state,meta,available]);
  useEffect(()=>{
    if(job?.state!=='READY'||!Number.isFinite(emin)||!Number.isFinite(emax)||emin>=emax)return;
    let cancelled=false;
    const timer=window.setTimeout(()=>void api('/api/interactive-plots/series',{cache_id:job.id,selected:selected.map(item=>{const [element,orbital]=item.split('|');return {element,orbital};}),emin,emax}).then(value=>{if(!cancelled)setData(value);}).catch(caught=>{if(!cancelled)setError((caught as Error).message);}),120);
    return ()=>{cancelled=true;window.clearTimeout(timer);};
  },[job?.state,job?.id,selected,emin,emax]);
  const prepare=async(force=false)=>{setError('');if(force)setData(null);try{setJob(await api('/api/interactive-plots/prepare',{project_id:projectId,band_attempt_id:bandId||null,dos_attempt_id:dosId||null,force}));}catch(caught){setError((caught as Error).message);}};
  const toggle=(item:string)=>{setData(null);setSelected(old=>old.includes(item)?old.filter(value=>value!==item):[...old,item]);};
  const save=async()=>{
    if(!job||!data)return;
    setError('');setMessage('');
    try{
      const active=charts.current.filter((chart):chart is echarts.ECharts=>!!chart);
      const canvases=active.map(exportPlotCanvas);
      if(!canvases.length)throw new Error('图表尚未完成渲染');
      const image=document.createElement('canvas');image.width=Math.max(...canvases.map(c=>c.width));
      const context=image.getContext('2d');if(!context)throw new Error('无法导出 Canvas');
      const legend=legendLayout(context,legendGroups,image.width,Boolean(data.band?.spins===2||data.dos?.spins===2));
      image.height=canvases.reduce((sum,c)=>sum+c.height,0)+legend.height;
      context.fillStyle='#fff';context.fillRect(0,0,image.width,image.height);
      let offset=0;for(const canvas of canvases){context.drawImage(canvas,0,offset);offset+=canvas.height;}
      context.font='26px sans-serif';context.textBaseline='middle';
      for(const entry of legend.entries){const y=offset+entry.y;context.fillStyle=entry.swatch;context.beginPath();context.arc(entry.x+12,y,entry.element?12:10,0,Math.PI*2);context.fill();context.fillStyle='#34454b';context.fillText(entry.name,entry.x+34,y);}
      if(legend.noteY){context.font='22px sans-serif';context.fillStyle='#5c7078';context.fillText('Spin up: solid / circle    Spin down: dashed / triangle',32,offset+legend.noteY);}
      const png=image.toDataURL('image/png');
      const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${image.width}" height="${image.height}"><image width="100%" height="100%" href="${png}"/></svg>`;
      const result=await api('/api/interactive-plots/save',{cache_id:job.id,project_id:projectId,
        selected:selected.map(item=>{const [element,orbital]=item.split('|');return {element,orbital};}),emin,emax,point_size_scale:pointSize,png_data_url:png,svg,title});
      setMessage(`已保存结果集；可在“数据集与历史交接”重看和下载。`);
    }catch(caught){setError((caught as Error).message);}
  };
  const bandOptions=useMemo(()=>data?.band?Array.from({length:data.band.spins},(_,i)=>bandOption(data.band!,i,emin,emax,pointSize)):[],[data?.band,emin,emax,pointSize]);
  const combinedBandOption=useMemo(()=>data?.band?.spins===2?bandOption(data.band,undefined,emin,emax,pointSize):null,[data?.band,emin,emax,pointSize]);
  const pdosOption=useMemo(()=>data?.dos?dosOption(data.dos,emin,emax):null,[data?.dos,emin,emax]);
  return <section className="input-submit-card interactive-plot-workspace"><h3>内置交互式 fatband／PDOS</h3><p>服务器解析 fatband 与 PDOS，回传完整紧凑数组；重复打开、轨道切换和导出使用本机缓存。</p>
    <div className="parameter-grid"><label className="field">已完成能带 attempt<select value={bandId} onChange={e=>setBandId(e.target.value)}><option value="">不显示 fatband</option>{candidates.filter(item=>item.stage==='bands').map(item=><option key={item.id} value={item.id}>{candidateLabel(item)}</option>)}</select></label><label className="field">已完成 DOS attempt<select value={dosId} onChange={e=>setDosId(e.target.value)}><option value="">不显示 PDOS</option>{candidates.filter(item=>item.stage==='dos').map(item=><option key={item.id} value={item.id}>{candidateLabel(item)}</option>)}</select></label></div>
    {(bandId||dosId)&&<p>来源：{candidates.filter(item=>item.id===bandId||item.id===dosId).map(item=>`${item.stage} ${item.host} · ${item.path} · SCF ${item.scf_attempt_id||'未记录'}`).join('；')}</p>}
    <button className="button primary" disabled={!bandId&&!dosId||job?.state==='GENERATING'||job?.state==='DOWNLOADING'||job?.state==='PARSING'} onClick={()=>void prepare()}>{job?.state==='READY'?'使用本机绘图缓存':'准备本地绘图数据'}</button>
    {job?.state==='READY'&&<button className="button" onClick={()=>void prepare(true)}>刷新远端来源</button>}
    {job&&<p role="status">{job.state} · {bytes(job.downloaded_bytes||0)} / {bytes(job.total_bytes||0)} · 本机可用 {job.available_bytes?bytes(job.available_bytes):'未记录'} {job.current_file||''}{job.error&&` · ${job.error}`}</p>}
    {meta&&<><p>{meta.sources.length} 个来源文件 · 绘图数据已保存到本机</p><p>能带 E_F：{meta.band?.energy_reference.fermi_eV??'未选'} eV；DOS E_F：{meta.dos?.energy_reference.fermi_eV??'未选'} eV。各图按各自来源归零。</p><details><summary>本机文件大小与来源</summary>{meta.sources.map((source,index)=><p key={index}>{source.name} · {bytes(source.size_bytes)} · <code>{source.local_path}</code></p>)}</details>{meta.dos?.projection_note&&<p className="handoff-warning">{meta.dos.projection_note}</p>}{meta.compatibility_note&&<p className="handoff-warning">{meta.compatibility_note}</p>}
      <div className="parameter-grid"><label className="field">能量下界 E−E_F / eV<input type="number" step="0.1" value={emin} onChange={e=>{setData(null);setEmin(Number(e.target.value));}}/></label><label className="field">能量上界 E−E_F / eV<input type="number" step="0.1" value={emax} onChange={e=>{setData(null);setEmax(Number(e.target.value));}}/></label></div>
      {meta.band&&<label className="field fatband-point-size">彩点大小 · {pointSize.toFixed(1)} 倍<input type="range" min="0.1" max="1.5" step="0.1" value={pointSize} onChange={e=>setPointSize(Number(e.target.value))}/></label>}
      <div className="orbital-selector">{available.map(({element,orbitals})=><fieldset key={element}><legend style={{color:color(element)}}>{element}</legend>{Object.entries(groups).map(([group,components])=><div key={group}><label><input type="checkbox" checked={selected.includes(key(element,group))} disabled={!components.some(name=>orbitals.includes(name))&&!orbitals.includes(group)} onChange={()=>toggle(key(element,group))}/>{group}-all</label><div className="orbital-components">{components.filter(name=>group!=='s').map(name=><label key={name}><input type="checkbox" checked={selected.includes(key(element,name))} disabled={!orbitals.includes(name)} onChange={()=>toggle(key(element,name))}/>{name}</label>)}</div></div>)}</fieldset>)}</div>
    </>}
    {job?.metadata?.remote_parts&&<p role="status">完整投影留在服务器；所选轨道首次由服务器处理，之后复用本机缓存。采样点保持完整。</p>}{data?.band&&<div className={`interactive-plot-panels${data.band.spins===1?' single-channel':''}`}>{bandOptions.map((option,i)=><article key={i}><h4>{data.band!.spins===1?'能带与 fatband · 电荷投影':label(i)}</h4><Chart option={option} chartRef={{get current(){return charts.current[i]},set current(value){charts.current[i]=value}} as React.MutableRefObject<echarts.ECharts|null>} height={compact?300:390}/></article>)}{combinedBandOption&&<article><h4>上下自旋叠加</h4><Chart option={combinedBandOption} chartRef={{get current(){return charts.current[2]},set current(value){charts.current[2]=value}} as React.MutableRefObject<echarts.ECharts|null>} height={compact?300:390}/></article>}</div>}
    {data?.dos&&pdosOption&&<article className="interactive-dos-panel"><h4>{data.dos.spins===2?'上下自旋镜像 PDOS':'PDOS'}</h4><Chart option={pdosOption} chartRef={{get current(){return charts.current[3]},set current(value){charts.current[3]=value}} as React.MutableRefObject<echarts.ECharts|null>} height={compact?320:460}/></article>}
    {data&&<div className="plot-color-legend" aria-label="元素与轨道颜色图例">{legendGroups.map(group=><div className="plot-legend-group" key={group.element}><strong><i className="plot-legend-dot" style={{backgroundColor:color(group.element)}}/>{group.element}</strong>{group.orbitals.map(orbital=><span key={orbital}><i className="plot-legend-dot" style={{backgroundColor:componentColor(group.element,orbital)}}/>{orbital==='p'?'p-all':orbital==='d'?'d-all':orbital}</span>)}</div>)}{(data.band?.spins===2||data.dos?.spins===2)&&<small>自旋：实线／圆点为 up，虚线／三角为 down；颜色由元素和轨道决定。</small>}</div>}
    {data&&<><label className="field">图片标题<input value={title} onChange={e=>setTitle(e.target.value)} placeholder="例如 CrFeO 投影能带与 DOS"/></label><button className="button primary" onClick={()=>void save()}>保存当前图与曲线数据</button></>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert" className="structure-error">{error}</p>}
  </section>;
}

// Parent form edits do not change plotting inputs. Keep existing charts intact.
export default memo(InteractivePlot);
