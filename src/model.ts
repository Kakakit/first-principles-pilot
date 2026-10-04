import {validateLayer} from './parameters';
import type {ParameterLayer,ParameterProject} from './parameters';
import {validateStructure} from './structure';
import type {StructureDocument} from './structure';
import type { Edge, Node } from '@xyflow/react';

export const STORAGE_KEY = 'dft-workbench.workspace';
export const kinds = ['structure', 'relax', 'scf', 'bands', 'dos', 'vasp-wannier', 'wannier90', 'scan'] as const;
export type Kind = typeof kinds[number];
export const catalog: Record<Kind, {label: string; en: string; description: string; color: string}> = {
  structure: { label: '输入结构', en: 'STRUCTURE', description: '定义计算的初始晶体结构', color: '#63758a' },
  relax: { label: '结构优化', en: 'RELAXATION', description: '寻找稳定的原子位置和晶胞', color: '#16816e' },
  scf: { label: '静态自洽', en: 'SELF-CONSISTENT', description: '获得收敛的电子密度', color: '#347ac1' },
  bands: { label: '能带结构', en: 'BAND STRUCTURE', description: '沿高对称路径计算电子能带', color: '#8260b7' },
  dos: { label: '态密度', en: 'DENSITY OF STATES', description: '分析电子态随能量的分布', color: '#bf8142' },
  'vasp-wannier': { label: 'VASP→Wannier 接口', en: 'VASP WANNIER', description: '读取 SCF 电荷并生成 Wannier 接口矩阵', color: '#a05d8d' },
  wannier90: { label: 'Wannier90 局域化', en: 'WANNIER90', description: '读取接口矩阵并运行 Wannier90', color: '#81518e' },
  scan: { label: '参数迭代', en: 'PARAMETER SCAN', description: '迭代 VASP 参数或分阶段测试 Wannier90 能窗', color: '#7058a1' },
};
export type StepData = { kind: Kind; label: string; note: string; params: Record<string, string>; executionMode:'vasp'|'wannier90'|'scan'; stageSequence:number; stageSlug:string; extensionId?:string; structure?:StructureDocument; parameters?:ParameterLayer; [key: string]: unknown };
export type Step = Node<StepData, 'step'>;
export type Project = { parameters?:ParameterProject; sharedPotcar?:{id:string;provenance_id?:string}; recipeRuntime?:unknown; id: string; name: string; remoteSlug?:string; nodes: Step[]; edges: Edge[]; updatedAt: string };
export type Workspace = { activeId: string; projects: Project[] };
export type Issue = { level: 'error' | 'info'; message: string; nodeId?: string };
export const uid = () => crypto.randomUUID();
export function slugify(value:string):string {
  return value.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,64);
}
export function makeStep(kind: Kind, position = { x: 80, y: 80 }, stageSequence=1): Step {
  return { id: uid(), type: 'step', position, data: { kind, label: catalog[kind].label, note: '', executionMode:kind==='scan'?'scan':kind==='wannier90'?'wannier90':'vasp', stageSequence, stageSlug:kind, params: kind === 'structure' ? { material: '未指定' } : { functional: 'PBE', encut: '', kmesh: '', spin: '未设置' } } };
}
export function newProject(name: string, demo = false): Project {
  const nodes = demo ? [makeStep('structure', {x: 40, y: 190},1), makeStep('relax', {x: 310, y: 190},2), makeStep('scf', {x: 580, y: 190},3), makeStep('bands', {x: 850, y: 85},4), makeStep('dos', {x: 850, y: 295},5)] : [makeStep('structure',undefined,1)];
  if (demo) { nodes[0].data.params.material = 'Si · 硅（演示）'; nodes.forEach(n => {n.data.note = n.data.kind === 'structure' ? '可导入真实结构' : '仅展示流程组织，尚未生成计算输入';}); }
  const edges = demo ? [[0,1],[1,2],[2,3],[2,4]].map(([a,b]) => ({id:uid(),source:nodes[a].id,target:nodes[b].id})) : [];
  const id=uid();return { id, name, remoteSlug:slugify(name)||`project-${id.slice(0,8)}`, nodes, edges, updatedAt: new Date().toISOString() };
}
export function demoWorkspace(): Workspace { const p = newProject('Si · 电子结构', true); return { activeId:p.id, projects:[p] }; }
export function initialWorkspace(): Workspace { const p = newProject('新项目'); return { activeId:p.id, projects:[p] }; }
export function cleanWorkspace(w: Workspace): Workspace {
  return { ...w, projects: w.projects.map(p => ({...p, name:p.name.trim() || '未命名项目', remoteSlug:p.remoteSlug||undefined, nodes:p.nodes.map(n => ({id:n.id,type:'step',position:n.position,data:{...n.data,label:n.data.label.trim() || catalog[n.data.kind].label}})), edges:p.edges.map(e => ({id:e.id,source:e.source,target:e.target}))})) };
}

// One editor document layout; no historical workspace conversion.
export function parseWorkspace(raw: string): Workspace {
  const w = JSON.parse(raw);
  const text = (v: unknown, max = 2000): v is string => typeof v === 'string' && v.length <= max;
  const nonempty = (v: unknown) => text(v, 200) && Boolean((v as string).trim());
  if (!w || Object.prototype.hasOwnProperty.call(w, 'version') || !Array.isArray(w.projects) || w.projects.length === 0 || w.projects.length > 100) throw new Error('不是支持的工作台备份。');
  const projectIds = new Set();
  for (const p of w.projects) {
    if (!p || !nonempty(p.id) || projectIds.has(p.id) || !nonempty(p.name) || !text(p.updatedAt, 100) || !Array.isArray(p.nodes) || !Array.isArray(p.edges) || p.nodes.length > 500 || p.edges.length > 2000) throw new Error('项目内容或大小无效。');
    if(!text(p.remoteSlug,64)||!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(p.remoteSlug))throw new Error('远程项目标识无效。');
    if(p.sharedPotcar!==undefined&&(!p.sharedPotcar||!/^[a-f0-9]{64}$/.test(p.sharedPotcar.id)||p.sharedPotcar.provenance_id!==undefined&&!/^[a-f0-9]{64}$/.test(p.sharedPotcar.provenance_id)))throw new Error('项目共享 POTCAR 无效。');
    if(p.parameters!==undefined){if(p.parameters.version!==1)throw new Error('不支持的参数版本。');validateLayer(p.parameters.shared);if(p.parameters.templates!==undefined){if(!Array.isArray(p.parameters.templates)||p.parameters.templates.length>20)throw new Error('参数模板过多。');for(const t of p.parameters.templates){if(!nonempty(t.name))throw new Error('模板名称无效。');validateLayer(t.layer);}}}
    projectIds.add(p.id);
    const nodeIds = new Set();
    for (const n of p.nodes) {
      if (!n || !nonempty(n.id) || nodeIds.has(n.id) || n.type !== 'step' || !n.position || !Number.isFinite(n.position.x) || !Number.isFinite(n.position.y) || Math.abs(n.position.x)>1e6 || Math.abs(n.position.y)>1e6 || !n.data || !kinds.includes(n.data.kind) || !nonempty(n.data.label) || !text(n.data.note) || !n.data.params || typeof n.data.params !== 'object' || Array.isArray(n.data.params) || Object.keys(n.data.params).length > 30 || !Object.values(n.data.params).every(v=>text(v,200))) throw new Error('流程节点格式无效。');
      if(!Number.isInteger(n.data.stageSequence)||n.data.stageSequence<1||n.data.stageSequence>999||!text(n.data.stageSlug,48)||!/^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$/.test(n.data.stageSlug)||!['vasp','wannier90','scan'].includes(n.data.executionMode)||((n.data.kind==='scan')!==(n.data.executionMode==='scan'))||((n.data.kind==='wannier90')!==(n.data.executionMode==='wannier90')))throw new Error('节点执行类型或远程阶段标识无效。');
      if(n.data.structure!==undefined) {if(n.data.kind!=='structure')throw new Error('结构只能绑定输入结构节点。');validateStructure(n.data.structure);}
      if(n.data.parameters!==undefined)validateLayer(n.data.parameters);
      if(n.data.inputDraft!==undefined){const d=n.data.inputDraft;if(!d||typeof d!=='object'||Array.isArray(d)||!Object.entries(d).every(([k,v])=>['grid','center','path','line_points','potcar_id','potcar_provenance_id','reason','projections','num_wann','path_structure_hash','path_source_attempt_id'].includes(k)&&typeof v==='string'&&v.length<=20000))throw new Error('输入文件配置格式无效。');}
      nodeIds.add(n.id);
    }
    const edgeIds = new Set();
    for (const e of p.edges) {
      if (!e || !nonempty(e.id) || edgeIds.has(e.id) || !nodeIds.has(e.source) || !nodeIds.has(e.target) || e.source === e.target) throw new Error('流程连接无效。');
      edgeIds.add(e.id);
    }
  }
  if (!projectIds.has(w.activeId)) throw new Error('当前项目不存在。');
  return cleanWorkspace(w as Workspace);
}
export function canConnect(nodes: Step[], edges: Edge[], source: string, target: string): boolean {
  if (source === target || !nodes.some(n=>n.id===source) || !nodes.some(n=>n.id===target) || nodes.find(n=>n.id===target)?.data.kind === 'structure' || edges.some(e=>e.source===source && e.target===target)) return false;
  if(nodes.find(n=>n.id===target)?.data.kind==='scan'&&!['relax','scf','wannier90'].includes(nodes.find(n=>n.id===source)?.data.kind||''))return false;
  if(nodes.find(n=>n.id===source)?.data.kind==='scan')return false;
  if(nodes.find(n=>n.id===target)?.data.kind==='wannier90'&&nodes.find(n=>n.id===source)?.data.kind!=='vasp-wannier')return false;
  const seen = new Set<string>();
  const visit = (id: string): boolean => { if(id === source) return true; if(seen.has(id)) return false; seen.add(id); return edges.filter(e=>e.source===id).some(e=>visit(e.target)); };
  return !visit(target);
}
export function inspectFlow(p: Project): Issue[] {
  const out: Issue[] = [];
  if (!p.nodes.length) out.push({level:'error',message:'流程为空，请添加输入结构和计算节点。'});
  if (!p.nodes.some(n=>n.data.kind === 'structure')) out.push({level:'error',message:'缺少输入结构节点。'});
  for (const n of p.nodes) {
    const parents = p.edges.filter(e=>e.target===n.id).map(e=>p.nodes.find(x=>x.id===e.source)).filter(Boolean) as Step[];
    if (n.data.kind !== 'structure' && !parents.length) out.push({level:'error',nodeId:n.id,message:`「${n.data.label}」尚未连接上游节点。`});
    if (['bands','dos','vasp-wannier'].includes(n.data.kind) && !parents.some(x=>x.data.kind==='scf')) out.push({level:'error',nodeId:n.id,message:`「${n.data.label}」需要连接静态自洽节点。`});
    if (n.data.kind==='wannier90' && (parents.length!==1 || parents[0].data.kind!=='vasp-wannier')) out.push({level:'error',nodeId:n.id,message:`「${n.data.label}」需要直接连接一个 VASP→Wannier 接口节点。`});
    if(n.data.kind==='scan'&&(parents.length!==1||!['relax','scf','wannier90'].includes(parents[0].data.kind)))out.push({level:'error',nodeId:n.id,message:`「${n.data.label}」需要直接连接一个 relax、SCF 或 Wannier90 节点。`});
    if (n.data.kind === 'structure' && parents.length) out.push({level:'error',nodeId:n.id,message:'输入结构不能接收上游连接。'});
  }
  const visiting = new Set<string>(), visited = new Set<string>();
  const cycle = (id: string): boolean => { if(visiting.has(id)) return true; if(visited.has(id)) return false; visiting.add(id); if(p.edges.filter(e=>e.source===id).some(e=>cycle(e.target))) return true; visiting.delete(id); visited.add(id); return false; };
  if (p.nodes.some(n=>cycle(n.id))) out.push({level:'error',message:'流程存在循环连接，请删除形成回路的连线。'});
  if (!out.length) out.push({level:'info',message:'流程连接检查通过。尚未检查物理参数或生成计算输入。'});
  return out;
}
export function mergeWorkspace(current: Workspace, imported: Workspace): Workspace {
  const projects = imported.projects.map(p=>{
    const ids = new Map(p.nodes.map(n=>[n.id,uid()]));
    return {...p,id:uid(),name:`${p.name.slice(0,190)} · 导入`,nodes:p.nodes.map(n=>({...n,id:ids.get(n.id)!})),edges:p.edges.map(e=>({...e,id:uid(),source:ids.get(e.source)!,target:ids.get(e.target)!}))};
  });
  if(current.projects.length+projects.length>100) throw new Error('最多保存 100 个项目，请先导出备份并整理。');
  return {...current,activeId:projects[0].id,projects:[...current.projects,...projects]};
}
