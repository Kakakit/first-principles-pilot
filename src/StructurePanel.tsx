import StructureEditor from './StructureEditor';
import PoscarEditor from './PoscarEditor';
import SymmetryAnalysis from './SymmetryAnalysis';
import {selectedTheme} from './appearance';
import {useEffect,useRef,useState} from 'react';
import type {Step} from './model';
import {saveBytes,validateStructure,verifySource,currentPoscar} from './structure';
import type {StructureDocument,Vec3} from './structure';

export function CrystalView({structure,projectId}:{structure:StructureDocument;projectId?:string}) {
  const frame=useRef<HTMLIFrameElement>(null);
  const [error,setError]=useState(''),[colors,setColors]=useState<Record<string,string>>({});
  const [wheelZoom,setWheelZoom]=useState(false);
  const axes=['#c43d45','#218457','#346dcc'];
  const send=(message:unknown)=>frame.current?.contentWindow?.postMessage(message,location.origin);
  useEffect(()=>{
    setError('');
    const update=()=>send({type:'crystal-structure',structure:{atoms:structure.atoms,lattice:structure.lattice,lengths:structure.lengths},background:selectedTheme().colors.canvas});
    const receive=(event:MessageEvent)=>{
      if(event.source!==frame.current?.contentWindow||event.origin!==location.origin)return;
      if(event.data?.type==='crystal-ready')update();
      if(event.data?.type==='crystal-colors')setColors(event.data.colors);
      if(event.data?.type==='crystal-error')setError(event.data.message);
    };
    window.addEventListener('message',receive);update();
    return()=>window.removeEventListener('message',receive);
  },[structure]);
  useEffect(()=>{send({type:'crystal-wheel',enabled:wheelZoom});},[wheelZoom,structure]);
  useEffect(()=>{const update=()=>send({type:'crystal-theme',background:selectedTheme().colors.canvas});window.addEventListener('fpilot:appearance-changed',update);return()=>window.removeEventListener('fpilot:appearance-changed',update);},[]);
  return <><div className="crystal-view"><iframe ref={frame} src="/crystal-frame.html" title="晶胞三维视图" style={{width:'100%',height:'100%',border:0}}/></div>{error&&<p role="alert">{error}</p>}<div className="crystal-legend" aria-label="元素颜色与晶格基矢图例">{Object.entries(colors).map(([element,color])=><span key={element}><i style={{background:color}}/>{element} · {structure.atoms.filter(a=>a.element===element).length} 个</span>)}<span>基矢：</span>{axes.map((color,i)=><span key={i} style={{color}}>➜ {['a','b','c'][i]}</span>)}</div><div className="crystal-actions"><span>拖动旋转 · 默认滚轮滚动页面 · 仅显示当前晶胞原子</span><div><button className="button" aria-pressed={wheelZoom} onClick={()=>setWheelZoom(value=>!value)}>{wheelZoom?'关闭滚轮缩放':'开启滚轮缩放'}</button> <button className="button" onClick={()=>{send({type:'crystal-reset'});}}>重置视角</button></div></div><SymmetryAnalysis poscar={structure.poscar} sourceSha={structure.source.sha256} projectId={projectId}/></>;
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
  const [editing,setEditing]=useState<'geometry'|'poscar'|null>(null);
  useEffect(()=>setEditing(null),[candidate]);
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
  return <section className="structure-workspace"><div className="structure-controls"><div><h2>晶体结构</h2></div><label>目标节点<select disabled={busy||Boolean(editing)} value={active?.id||''} onChange={e=>setTarget(e.target.value)}>{structureNodes.map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select></label><label>文件格式<select disabled={busy||Boolean(editing)} value={format} onChange={e=>setFormat(e.target.value as 'cif'|'poscar')}><option value="poscar">POSCAR / CONTCAR</option><option value="cif">CIF</option></select></label><button className="button primary" disabled={busy||Boolean(editing)||!active} onClick={()=>file.current?.click()}>{busy?'正在解析…':doc?'导入并替换当前结构':'导入结构文件'}</button><input ref={file} type="file" hidden onChange={e=>void load(e.target.files?.[0])}/></div>
    {!active&&<p>请先在计算流程中添加一个输入结构节点。</p>}{error&&<p className="structure-error" role="alert">{error} 原有结构保持不变。</p>}
    {doc?<><div className="structure-summary"><strong>{doc.formula}</strong><span>{doc.atoms.length} 个原子</span><span>体积 {doc.volume.toFixed(6)} Å³</span><button className="button" onClick={()=>saveBytes(doc.source.filename,Uint8Array.from(atob(doc.source.base64),c=>c.charCodeAt(0)))}>下载原文件</button><button className="button" onClick={()=>saveBytes('POSCAR',currentPoscar(doc))}>导出当前 POSCAR</button></div><div className="structure-edit-actions"><label>编辑坐标系<select disabled={Boolean(editing)} value={coords} onChange={e=>setCoords(e.target.value as typeof coords)}><option value="fractional">分数坐标（改变晶胞时保持分数位置）</option><option value="cartesian">笛卡尔坐标（改变晶胞时保持空间位置）</option></select></label><button className="button primary" disabled={Boolean(editing)} onClick={()=>setEditing('geometry')}>编辑晶胞与原子</button><button className="button" disabled={Boolean(editing)} onClick={()=>setEditing('poscar')}>编辑 POSCAR 文本</button>{doc.edit&&<span>已编辑 · 原文件保留</span>}</div>{editing==='poscar'?<PoscarEditor key={active!.id} structure={doc} onCancel={()=>setEditing(null)} onApply={s=>{onImport(active!.id,s);setEditing(null);}}/>:editing==='geometry'?<StructureEditor key={active!.id} structure={doc} mode={coords} onCancel={()=>setEditing(null)} onApply={s=>{onImport(active!.id,s);setEditing(null);}}/>:<CrystalView structure={doc} projectId={projectId}/>}<div className="structure-data"><div><h3>晶胞（Å，行向量）</h3><pre>{doc.lattice.map(formatVec).join('\n')}</pre><p>a / b / c：{formatVec(doc.lengths)} Å</p><p>α / β / γ：{formatVec(doc.angles)} °</p><details><summary>来源与解析说明</summary><p>{doc.source.filename} · {doc.parser.name} {doc.parser.version}</p><p className="hash">原文件 SHA-256：{doc.source.sha256}</p>{doc.edit&&<p className="hash">几何 SHA-256：{doc.edit.geometry_sha256}<br/>父几何：{doc.edit.parent_geometry_sha256}</p>}{doc.warnings.map((w,i)=><p key={i}>{w}</p>)}</details></div><div><h3>原子坐标</h3><label>坐标类型<select disabled={Boolean(editing)} value={coords} onChange={e=>setCoords(e.target.value as typeof coords)}><option value="fractional">分数坐标（无量纲）</option><option value="cartesian">笛卡尔坐标（Å）</option></select></label><div className="atoms-scroll"><table><thead><tr><th>编号</th><th>元素</th><th>{coords==='fractional'?'分数坐标':'坐标 / Å'}</th><th>约束 T/F</th></tr></thead><tbody>{doc.atoms.map((a,i)=><tr key={a.id}><td title={a.id}>{i+1}</td><td>{a.element}</td><td>{formatVec(a[coords])}</td><td>{a.selective?a.selective.map(v=>v?'T':'F').join(' '):'未指定'}</td></tr>)}</tbody></table></div></div></div></>:<div className="structure-empty"><h3>尚未导入真实结构</h3><p>支持单结构、完整占位的 CIF，以及含元素名称行的 POSCAR。上限 256 KiB / 1500 原子。</p></div>}
  </section>;
}
