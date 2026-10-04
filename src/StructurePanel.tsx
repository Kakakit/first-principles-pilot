import StructureEditor from './StructureEditor';
import SymmetryAnalysis from './SymmetryAnalysis';
import {selectedTheme} from './appearance';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import type {GLViewer} from '3dmol';
import type {Step} from './model';
import {saveBytes,validateStructure,verifySource} from './structure';
import type {StructureDocument,Vec3} from './structure';

export function CrystalView({structure,projectId}:{structure:StructureDocument;projectId?:string}) {
  const host=useRef<HTMLDivElement>(null), viewer=useRef<GLViewer|null>(null);
  const [error,setError]=useState('');
  const [colors,setColors]=useState<Record<string,string>>({});
  const [wheelZoom,setWheelZoom]=useState(false);
  const axes=['#c43d45','#218457','#346dcc'];
  useLayoutEffect(()=>{
    const element=host.current;if(!element)return;
    const allowPageScroll=(event:WheelEvent)=>{if(!wheelZoom)event.stopImmediatePropagation();};
    element.addEventListener('wheel',allowPageScroll,{capture:true,passive:true});
    return()=>element.removeEventListener('wheel',allowPageScroll,true);
  },[wheelZoom]);
  useEffect(()=>{
    let cancelled=false;let observer:ResizeObserver|undefined;const element=host.current;
    setError('');
    void import('3dmol').then(m=>{
      if(cancelled||!host.current)return;
      const v=m.createViewer(host.current,{backgroundColor:selectedTheme().colors.canvas,antialias:true,rows:1,cols:1,row:0,col:0});viewer.current=v;
      const model=v.addModel();
      model.addAtoms(structure.atoms.map((a,i)=>({elem:a.element,x:a.cartesian[0],y:a.cartesian[1],z:a.cartesian[2],serial:i+1,bonds:[],bondOrder:[]})));
      v.setStyle({},{sphere:{scale:0.28,colorscheme:'Jmol'}});
      setColors(Object.fromEntries([...new Set(structure.atoms.map(a=>a.element))].map(e=>[e,`#${m.CC.color(m.elementColors.Jmol[e]??0xff1493).getHex().toString(16).padStart(6,'0')}`])));
      const point=(bits:number)=>{
        const p=[0,0,0];structure.lattice.forEach((axis,j)=>{if(bits&(1<<j))axis.forEach((x,k)=>p[k]+=x);});return {x:p[0],y:p[1],z:p[2]};
      };
      for(let i=0;i<8;i++)for(let j=0;j<3;j++)if(!(i&(1<<j)))v.addLine({start:point(i),end:point(i|(1<<j)),color:'#587985'});
      const radius=Math.min(...structure.lengths)*0.012;
      structure.lattice.forEach((axis,j)=>{
        v.addArrow({start:{x:0,y:0,z:0},end:{x:axis[0],y:axis[1],z:axis[2]},radius,color:axes[j]});
        v.addLabel(['a','b','c'][j],{position:{x:axis[0]*1.08,y:axis[1]*1.08,z:axis[2]*1.08},fontColor:axes[j],backgroundOpacity:0,fontSize:18});
      });
      v.zoomTo();v.render();observer=new ResizeObserver(()=>{if(!cancelled&&element?.isConnected)v.resize();});observer.observe(host.current);
    }).catch(()=>{if(!cancelled)setError('三维显示未能启动。请检查浏览器 WebGL；下方晶胞和坐标仍可查看。');});
    return ()=>{cancelled=true;observer?.disconnect();const previous=viewer.current;viewer.current=null;try{previous?.clear();}catch(error){console.warn('三维视图释放失败',error);}finally{element?.replaceChildren();}};
  },[structure]);
  useEffect(()=>{const update=()=>{viewer.current?.setBackgroundColor(selectedTheme().colors.canvas,1);viewer.current?.render();};window.addEventListener('fpilot:appearance-changed',update);return()=>window.removeEventListener('fpilot:appearance-changed',update);},[]);
  return <><div className="crystal-view" ref={host} aria-label="晶胞三维视图"/>{error&&<p role="alert">{error}</p>}<div className="crystal-legend" aria-label="元素颜色与晶格基矢图例">{Object.entries(colors).map(([element,color])=><span key={element}><i style={{background:color}}/>{element} · {structure.atoms.filter(a=>a.element===element).length} 个</span>)}<span>基矢：</span>{axes.map((color,i)=><span key={i} style={{color}}>➜ {['a','b','c'][i]}</span>)}<small>箭头从晶胞原点指向实际基矢端点，随结构旋转；a、b、c 不一定正交。</small></div><div className="crystal-actions"><span>拖动旋转 · 默认滚轮滚动页面 · 仅显示当前晶胞原子</span><div><button className="button" aria-pressed={wheelZoom} onClick={()=>setWheelZoom(value=>!value)}>{wheelZoom?'关闭滚轮缩放':'开启滚轮缩放'}</button> <button className="button" onClick={()=>{viewer.current?.zoomTo();viewer.current?.render();}}>重置视角</button></div></div><SymmetryAnalysis poscar={structure.poscar} sourceSha={structure.source.sha256} projectId={projectId}/></>;
}
export default function StructurePanel({nodes,onImport,projectId}:{nodes:Step[];projectId?:string;onImport:(id:string,s:StructureDocument)=>void}) {
  const structureNodes=nodes.filter(n=>n.data.kind==='structure');
  const [target,setTarget]=useState(structureNodes[0]?.id||'');
  const active=structureNodes.find(n=>n.id===target)||structureNodes[0];
  const candidate=active?.data.structure;
  const [verified,setVerified]=useState<StructureDocument|null>(null);
  const doc=verified===candidate?verified:null;
  useEffect(()=>{let cancelled=false;setError('');setVerified(null);if(candidate)void verifySource(candidate).then(()=>{if(!cancelled)setVerified(candidate);}).catch(e=>{if(!cancelled)setError(e.message);});return()=>{cancelled=true;};},[candidate]);
  const [format,setFormat]=useState<'cif'|'poscar'>('poscar'),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [coords,setCoords]=useState<'fractional'|'cartesian'>('fractional');
  const [editing,setEditing]=useState(false);
  useEffect(()=>setEditing(false),[candidate]);
  const file=useRef<HTMLInputElement>(null),alive=useRef(true);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const load=async(f?:File)=>{
    if(!f||!active)return;const nodeId=active.id;setBusy(true);setError('');
    try{
      if(f.size>256*1024)throw new Error('结构文件不能超过 256 KiB。');
      const bytes=new Uint8Array(await f.arrayBuffer());let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
      const response=await fetch('/api/structures/parse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:f.name,format,base64:btoa(binary)})});
      if(!response.ok){let detail='请确认本机后端已启动，且文件格式选择正确。';try{const body=await response.json();if(typeof body.detail==='string')detail=body.detail;}catch{}throw new Error(detail);}
      const result=await response.json();validateStructure(result);await verifySource(result);if(alive.current)onImport(nodeId,result);
    }catch(e){if(alive.current)setError((e as Error).message||'无法连接本机解析服务。');}
    finally{if(alive.current)setBusy(false);if(file.current)file.current.value='';}
  };
  const formatVec=(v:Vec3)=>v.map(n=>n.toFixed(6)).join('  ');
  return <section className="structure-workspace"><div className="structure-controls"><div><h2>晶体结构</h2><p>选择结构节点，导入 CIF 或 POSCAR。原文件随草稿保留。</p></div><label>目标节点<select disabled={busy||editing} value={active?.id||''} onChange={e=>setTarget(e.target.value)}>{structureNodes.map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select></label><label>文件格式<select disabled={busy||editing} value={format} onChange={e=>setFormat(e.target.value as 'cif'|'poscar')}><option value="poscar">POSCAR / CONTCAR</option><option value="cif">CIF</option></select></label><button className="button primary" disabled={busy||editing||!active} onClick={()=>file.current?.click()}>{busy?'正在解析…':doc?'导入并替换当前结构':'导入结构文件'}</button><input ref={file} type="file" hidden onChange={e=>void load(e.target.files?.[0])}/></div>
    {!active&&<p>请先在计算流程中添加一个输入结构节点。</p>}{error&&<p className="structure-error" role="alert">{error} 原有结构保持不变。</p>}
    {doc?<><div className="structure-summary"><strong>{doc.formula}</strong><span>{doc.atoms.length} 个原子</span><span>体积 {doc.volume.toFixed(6)} Å³</span><button className="button" onClick={()=>saveBytes(doc.source.filename,Uint8Array.from(atob(doc.source.base64),c=>c.charCodeAt(0)))}>下载原文件</button><button className="button" onClick={()=>saveBytes('POSCAR.normalized',doc.poscar)}>导出几何 POSCAR</button></div><div className="structure-edit-actions"><label>编辑坐标系<select disabled={editing} value={coords} onChange={e=>setCoords(e.target.value as typeof coords)}><option value="fractional">分数坐标（改变晶胞时保持分数位置）</option><option value="cartesian">笛卡尔坐标（改变晶胞时保持空间位置）</option></select></label><button className="button primary" disabled={editing} onClick={()=>setEditing(true)}>编辑晶胞与原子</button>{doc.edit&&<span>已编辑 · 原文件保留</span>}</div>{editing?<StructureEditor key={active!.id} structure={doc} mode={coords} onCancel={()=>setEditing(false)} onApply={s=>{onImport(active!.id,s);setEditing(false);}}/>:<CrystalView structure={doc} projectId={projectId}/>}<div className="structure-data"><div><h3>晶胞（Å，行向量）</h3><pre>{doc.lattice.map(formatVec).join('\n')}</pre><p>a / b / c：{formatVec(doc.lengths)} Å</p><p>α / β / γ：{formatVec(doc.angles)} °</p><details><summary>来源与解析说明</summary><p>{doc.source.filename} · {doc.parser.name} {doc.parser.version}</p><p className="hash">原文件 SHA-256：{doc.source.sha256}</p>{doc.edit&&<p className="hash">几何 SHA-256：{doc.edit.geometry_sha256}<br/>父几何：{doc.edit.parent_geometry_sha256}</p>}{doc.warnings.map((w,i)=><p key={i}>{w}</p>)}<p>规范化文件只含几何与约束；原文下载保留原始字节。导入与显示不代表输入已适合计算。</p></details></div><div><h3>原子坐标</h3><label>坐标类型<select disabled={editing} value={coords} onChange={e=>setCoords(e.target.value as typeof coords)}><option value="fractional">分数坐标（无量纲）</option><option value="cartesian">笛卡尔坐标（Å）</option></select></label><div className="atoms-scroll"><table><thead><tr><th>编号</th><th>元素</th><th>{coords==='fractional'?'分数坐标':'坐标 / Å'}</th><th>约束 T/F</th></tr></thead><tbody>{doc.atoms.map((a,i)=><tr key={a.id}><td title={a.id}>{i+1}</td><td>{a.element}</td><td>{formatVec(a[coords])}</td><td>{a.selective?a.selective.map(v=>v?'T':'F').join(' '):'未指定'}</td></tr>)}</tbody></table></div></div></div></>:<div className="structure-empty"><h3>尚未导入真实结构</h3><p>支持单结构、完整占位的 CIF，以及含元素名称行的 POSCAR。上限 256 KiB / 1500 原子。</p><p>文件只发送到本机解析服务，不上传计算服务器。导入后可查看晶胞、坐标和原文件。</p></div>}
  </section>;
}
