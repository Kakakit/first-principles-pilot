import type {Project,Step} from './model';
import type {StructureDocument} from './structure';
import catalog from '../resources/vasp-parameter-catalog.json';
export type ParameterValue=string|null;
export type HubbardRow={l:string;u:string;j:string};
export type ParameterLayer={values:Record<string,ParameterValue>;preset?:'none'|'basic';moments?:Record<string,ParameterValue>;hubbard?:Record<string,HubbardRow>;confirmed_custom_tags?:string[]};
export type ParameterProject={version:1;shared:ParameterLayer;templates?:{name:string;layer:ParameterLayer}[]};
export type Definition={tag:string;label:string;unit?:string;type:'number'|'integer'|'enum'|'ediffg'|'vector3';choices?:[string,string][];positive?:boolean;min?:number;help:string;url:string;applies_to?:string[]};
export const definitions:Definition[]=catalog.definitions as Definition[];
export const catalogRevision=String(catalog.schema)+' · '+String(catalog.retrieved);
export const targetVaspVersion=String(catalog.target_vasp_version);
export const knownTags=new Set<string>(catalog.known_tags);
export const managedTags=new Set<string>(catalog.managed_tags.concat(catalog.contract_locked_tags));
export const singleLineValue=(value:string)=>/^[A-Za-z0-9_.,+*/()\- ]{1,200}$/.test(value)&&!/[\n\r;!#=]/.test(value);
export const emptyLayer=():ParameterLayer=>({values:{}});
export function ionicCriterion(mode:string,magnitude:string):string|null {
 if(mode==='steps')return '0';
 if(!magnitude.trim())return null;
 if(Number.isFinite(Number(magnitude))&&Number(magnitude)===0)return (mode==='force'?'-':'+')+magnitude.replace(/^[+-]/,'');
 return (mode==='force'?'-':'')+magnitude.replace(/^[+-]/,'');
}
export function validateLayer(value:unknown):asserts value is ParameterLayer {
 const p=value as ParameterLayer;const text=(v:unknown)=>v===null||(typeof v==='string'&&v.length<=200);
 if(!p||!p.values||Array.isArray(p.values)||typeof p.values!=='object'||Object.keys(p.values).length>100||!Object.values(p.values).every(text)||p.preset!==undefined&&!['none','basic'].includes(p.preset))throw new Error('参数草稿格式无效。');
 if(p.confirmed_custom_tags&&(!Array.isArray(p.confirmed_custom_tags)||p.confirmed_custom_tags.length>100||!p.confirmed_custom_tags.every(tag=>typeof tag==='string'&&/^[A-Z][A-Z0-9_]*$/.test(tag))))throw new Error('自定义参数确认列表无效。');
 if(p.moments&&(!Object.values(p.moments).every(text)||Object.keys(p.moments).length>1500))throw new Error('磁矩草稿格式无效。');
 if(p.hubbard&&(!Object.entries(p.hubbard).every(([k,v])=>/^[A-Z][a-z]?$/.test(k)&&v&&['l','u','j'].every(x=>typeof v[x as keyof HubbardRow]==='string'&&v[x as keyof HubbardRow].length<=200))))throw new Error('DFT+U 草稿格式无效。');
}
export type Effective={value:ParameterValue;source:'共享设置'|'阶段预设'|'节点覆盖'|'节点执行合同'|'未设置'};
export function effectiveParameters(project:Project,node:Step):Record<string,Effective> {
 const out:Record<string,Effective>={};const shared=project.parameters?.shared.values||{},local=node.data.parameters?.values||{};
 for(const d of definitions)out[d.tag]={value:null,source:'未设置'};
 for(const [tag,value] of Object.entries(shared))out[tag]={value,source:'共享设置'};
 if(node.data.parameters?.preset==='basic'){
   const preset=node.data.kind==='relax'?{IBRION:'2',ISIF:'2'}:{NSW:'0',IBRION:'-1'};
   for(const [tag,value] of Object.entries(preset))out[tag]={value,source:'阶段预设'};
 }
 for(const [tag,value] of Object.entries(local))out[tag]={value,source:'节点覆盖'};
 const source=node.data.sourceDraft as {mode?:string;files?:string}|undefined;
 if(source){
   out.ISTART={value:source.mode!=='new'&&source.files==='both'&&!['bands','dos','vasp-wannier'].includes(node.data.kind)?'1':'0',source:'节点执行合同'};
   out.ICHARG={value:source.mode==='new'?'2':'1',source:'节点执行合同'};
 }
 if(['bands','dos','vasp-wannier'].includes(node.data.kind))out.ICHARG={value:'11',source:'节点执行合同'};
 if(['bands','dos','vasp-wannier'].includes(node.data.kind)&&!Object.hasOwn(shared,'LORBIT')&&!Object.hasOwn(local,'LORBIT'))out.LORBIT={value:'11',source:'节点执行合同'};
 return out;
}
export function upstreamStructure(project:Project,node:Step):StructureDocument|undefined {
 const seen=new Set<string>(),roots=new Map<string,StructureDocument|undefined>();
 const visit=(id:string)=>{if(seen.has(id))return;seen.add(id);const n=project.nodes.find(n=>n.id===id);if(!n)return;if(n.data.kind==='structure'){roots.set(n.id,n.data.structure);return;}project.edges.filter(e=>e.target===id).forEach(e=>visit(e.source));};
 visit(node.id);return roots.size===1?[...roots.values()][0]:undefined;
}
export function parameterTables(project:Project,node:Step) {
 return {moments:{...project.parameters?.shared.moments,...node.data.parameters?.moments},hubbard:{...project.parameters?.shared.hubbard,...node.data.parameters?.hubbard}};
}
export type ParameterIssue={level:'error'|'warning';message:string};
const numeric=(value:unknown)=>typeof value==='string'&&value.trim()!==''&&Number.isFinite(Number(value));
export function checkParameters(project:Project,node:Step):ParameterIssue[] {
 const effective=effectiveParameters(project,node),issues:ParameterIssue[]=[];const value=(tag:string)=>effective[tag]?.value;
 for(const [tag,e] of Object.entries(effective)){
  if(e.value===null)continue;const d=definitions.find(d=>d.tag===tag);
  if(!/^[A-Z][A-Z0-9_]*$/.test(tag)||managedTags.has(tag)&&tag!=='XC_FUNCTIONAL'){issues.push({level:'error',message:`${tag} 名称无效或由工作台管理。`});continue;}
  if(!d){if(!singleLineValue(e.value)){issues.push({level:'error',message:`${tag} 须为安全单行文本。`});continue;}if(knownTags.has(tag))issues.push({level:'warning',message:`${tag} 是官方目录已知参数，以原样文本传递；仍需核对目标 VASP 版本和适用性。`});else if(!(project.parameters?.shared.confirmed_custom_tags||[]).includes(tag)&&!(node.data.parameters?.confirmed_custom_tags||[]).includes(tag))issues.push({level:'error',message:`${tag} 未收录，须显式确认自定义参数。`});else issues.push({level:'warning',message:`${tag} 为未验证的自定义参数。`});continue;}
  if(!singleLineValue(e.value))issues.push({level:'error',message:`${tag} 须为安全单行文本。`});
  if(d.applies_to&&!d.applies_to.includes(node.data.kind))issues.push({level:'error',message:`${tag} 不适用于当前节点类型。`});
  if(tag==='EDIFFG'&&e.value!=='0'&&numeric(e.value)&&Number(e.value)===0)issues.push({level:'error',message:'力或能量阈值必须大于 0；若只按 NSW 停止，请显式选择仅步数（EDIFFG=0）。'});
  if(d.type==='enum'?!d.choices!.some(([v])=>v===e.value):d.type==='vector3'?(()=>{const parts=e.value.trim().split(/\s+/);return parts.length!==3||parts.some(x=>!numeric(x))||parts.every(x=>Number(x)===0);})():(!numeric(e.value)||(d.type==='integer'&&!Number.isInteger(Number(e.value)))||(d.positive&&Number(e.value)<=0)||(d.min!==undefined&&Number(e.value)<d.min)))issues.push({level:'error',message:`${tag} 的类型或取值不符合当前支持范围。`});
 }
 for(const tag of ['XC_FUNCTIONAL','ENCUT','EDIFF','ISPIN'])if(value(tag)===null)issues.push({level:'warning',message:`${tag} 尚未明确，工作台不会补成物理默认值。`});
 if(!node.data.sourceDraft&&value('ISTART')!==null&&value('ISTART')!=='0')issues.push({level:'error',message:'当前 ISTART 锁定为 0；非零值需要 WAVECAR 重启合同。'});
 const expectedCharge=['bands','dos','vasp-wannier'].includes(node.data.kind)?'11':'2';
 if(!node.data.sourceDraft){
 if(value('ICHARG')==='1'&&['relax','scf'].includes(node.data.kind))issues.push({level:'warning',message:'ICHARG=1 需要在计算配置中选择 CHGCAR 来源并重新冻结。'});
 else if(value('ICHARG')!==null&&value('ICHARG')!==expectedCharge)issues.push({level:'error',message:`当前 ICHARG 锁定为 ${expectedCharge}；需核对 CHGCAR 依赖。`});
 }
 if(node.data.kind==='scf'&&value('LCHARG')==='false'&&project.edges.some(e=>e.source===node.id&&['bands','dos','vasp-wannier'].includes(project.nodes.find(n=>n.id===e.target)?.data.kind||'')))issues.push({level:'error',message:'该 SCF 有固定电荷下游，须写出 CHGCAR。'});
 if((value('EMIN')===null)!==(value('EMAX')===null)||value('EMIN')!==null&&value('EMAX')!==null&&Number(value('EMIN'))>=Number(value('EMAX')))issues.push({level:'error',message:'EMIN/EMAX 必须同时设置，且 EMIN < EMAX。'});
 if(node.data.kind==='relax'){
  if(!numeric(value('NSW'))||Number(value('NSW'))<=0)issues.push({level:'error',message:'结构优化需要明确 NSW > 0。'});
  if(!['1','2','3'].includes(value('IBRION')||''))issues.push({level:'error',message:'结构优化需选择本阶段支持的离子优化算法。'});
 }else if(value('NSW')!==null&&Number(value('NSW'))!==0)issues.push({level:'error',message:'静态、能带和 DOS 节点不能执行离子更新；NSW 应为 0。'});
 if(value('ISMEAR')==='-5'&&node.data.kind==='bands')issues.push({level:'error',message:'本能带路径任务不支持四面体积分（ISMEAR=−5）。'});
 if(value('ISMEAR')==='-5')issues.push({level:'warning',message:'四面体法的网格与体系适用性需结合实际 VASP 网格和体系核对。'});
 if(value('ISMEAR')!==null&&value('ISMEAR')!=='-5'&&value('SIGMA')===null)issues.push({level:'warning',message:'展宽方法已选，SIGMA 尚未明确。'});
 const s=upstreamStructure(project,node),tables=parameterTables(project,node);
 const noncollinear=value('LSORBIT')==='true'||value('LNONCOLLINEAR')==='true';
 if(value('LSORBIT')==='true'&&value('LNONCOLLINEAR')==='false')issues.push({level:'error',message:'SOC 会启用非共线框架，不能显式关闭 LNONCOLLINEAR。'});
 if(noncollinear){
  if(value('ISPIN')!=='1')issues.push({level:'error',message:'非共线/SOC 请设置 ISPIN=1；VASP 6.5 起 ISPIN=2 与非共线 MAGMOM 会报错。'});
  if(!s)issues.push({level:'error',message:'非共线磁矩需要唯一、已导入的上游结构。'});
  else if(s.atoms.some(a=>{const v=tables.moments[a.id]?.trim().split(/\s+/)||[];return v.length!==3||v.some(x=>!numeric(x));}))issues.push({level:'error',message:'非共线/SOC 每个原子都需三个 MAGMOM 分量（如 0 0 4）。'});
  if(value('SAXIS')===null)issues.push({level:'warning',message:'SAXIS 未指定；VASP 默认自旋量子化轴为 0 0 1。'});
 }
 if(value('ISPIN')==='2'){
  if(!s)issues.push({level:'error',message:'初始磁矩需要唯一、已导入的上游结构。'});
  else{
   if(s.atoms.some(a=>!numeric(tables.moments[a.id])))issues.push({level:'warning',message:'部分原子的初始 MAGMOM 尚未明确；不使用程序默认磁矩代填。'});
   if(Object.keys(tables.moments).some(id=>!s.atoms.some(a=>a.id===id)))issues.push({level:'warning',message:'保留了其他结构的磁矩记录；不会按位置套用到当前原子。'});
  }
 }
 if(value('LDAU')==='true'){
  if(!['1','2'].includes(value('LDAUTYPE')||''))issues.push({level:'error',message:'启用 DFT+U 后必须明确方法。'});
  if(!s)issues.push({level:'error',message:'DFT+U 元素映射需要唯一、已导入的上游结构。'});
  else for(const symbol of new Set(s.atoms.map(a=>a.element))){const row=tables.hubbard[symbol];
   if(!row||!['-1','0','1','2','3'].includes(row.l)){issues.push({level:'error',message:`${symbol} 尚未明确 LDAUL 轨道（或选择不加 U）。`});continue;}
   if(row.l!=='-1'&&(!numeric(row.u)||!numeric(row.j)||Number(row.u)<0||Number(row.j)<0))issues.push({level:'error',message:`${symbol} 的 U/J 需为明确的非负 eV 数值；本表单不支持负 U/J。`});
   if(value('LDAUTYPE')==='2'&&numeric(row.u)&&numeric(row.j)&&Number(row.u)<Number(row.j))issues.push({level:'warning',message:`${symbol} 的 U−J 为负，请核实模型选择。`});
  }
 }
 return issues;
}
