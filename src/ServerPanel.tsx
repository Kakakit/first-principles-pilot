import {useEffect,useState} from 'react';
import type {ReactNode} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
import {runtimeFor,emptySettings,openSettings} from './submissionDefaults';
import type { ServerConfig, RegisteredProfile, ServerPreview, ServerProbe } from './executionUi';
import RemoteProgress from './RemoteProgress';
import ScriptPicker from './ScriptPicker';

type Props = {
  maintenance?:boolean; project?:Project; scopeId?:string; program?:string; programs?:string[]; onTemplate?:(id:string)=>void;
  config: ServerConfig | null;
  profiles: RegisteredProfile[];
  probe: ServerProbe | null;
  preview: ServerPreview | null;
  node: string;
  cores: string;
  reason: string;
  busy?: boolean;
  canProbe?: boolean;
  status?: string;
  error?: string;
  purpose?: 'single' | 'batch' | 'chain';
  resourceExtra?:ReactNode;
  progressLabel?: string;
  onConfigChange: (config: ServerConfig) => void;
  onNodeChange: (node: string) => void;
  onCoresChange: (cores: string) => void;
  onReasonChange: (reason: string) => void;
  onSaveAndProbe: () => void;
  onProbe: () => void;
  onPreview: () => void;
};

function availableNodes(probe: ServerProbe | null, alias?: string) {
  return (probe?.preflight.hosts.flatMap(host => host.nodes) || [])
    .filter(item => !(alias === 'jutai-1' && item.NODE === 'node06'));
}

function MaintenanceServerPanel({
  config, profiles, probe, preview, node, cores, reason, busy = false, canProbe = true, status = '', error = '', purpose = 'single', progressLabel = '',
  onConfigChange, onNodeChange, onCoresChange, onReasonChange, onSaveAndProbe, onProbe, onPreview, program='vasp_std',
}: Props) {
  const selectedProfile = profiles.find(profile => profile.alias === config?.alias);
  const nodes = availableNodes(probe, config?.alias);
  const chosenNode = nodes.find(item=>item.NODE===node);
  const reasonRequired = Boolean(cores && chosenNode && Number(cores)<Number(chosenNode.CPUS_FREE||0));
  const setField = (key: string, value: string) => onConfigChange({ ...(config || {}), [key]: value });

  return <section className="server-submit-card" aria-label="提交服务器与资源配置">
    <div className="submit-section-heading"><div><span className="eyebrow">SERVER · RESOURCE · RUNTIME</span><h3>服务器运行默认配置</h3></div><span className={`neutral-badge ${selectedProfile?.selectable ? 'server-ready' : ''}`}>{selectedProfile?.selectable ? '登记运行时可选' : '需要选择可用登记项'}</span></div>
    <p className="submit-help">为此服务器与程序保存运行时、工作区及脚本默认值。</p>
    {config && <fieldset disabled={busy}>
      <label className="field">服务器 / SSH 别名
        <select value={config.alias || ''} onChange={event => {
          const profile = profiles.find(item => item.alias === event.target.value);
          if (!profile?.selectable) return;
          onConfigChange({ ...config, alias: profile.alias, scheduler: profile.scheduler.toLowerCase(), queue: profile.queue, mpi_executable: profile.mpi_executable, vasp_executable: profile.vasp_executable, environment_script: profile.environment_script, module: profile.module, launcher: profile.launcher, runtime_environment: profile.runtime_environment, pmi_library: profile.pmi_library, pmi_type: profile.pmi_type });
          onNodeChange('');
        }}>
          {profiles.map(profile => <option key={profile.alias} value={profile.alias} disabled={!profile.selectable}>{profile.alias} · {profile.scheduler || '调度器未登记'} · {profile.selectable ? '可用' : profile.reason}</option>)}
        </select>
      </label>
      <div className="runtime-source"><strong>运行时来源</strong><span>{purpose==='chain'?'按阶段自动选择 vasp_std / vasp_ncl':config.vasp_executable?.endsWith('/vasp_ncl')?selectedProfile?.ncl_runtime_status||'UNREGISTERED':selectedProfile?.runtime_status||selectedProfile?.status||'未登记'}</span><small>{purpose==='chain'?'普通阶段使用 vasp_std，SOC 阶段使用 vasp_ncl；两种程序在同次只读诊断中检查；派发前重新确认容量。':config.vasp_executable?.endsWith('/vasp_ncl')?'vasp_ncl 的登记证据单独显示；实时诊断只确认当前路径存在，不能替代真实运行验证。':selectedProfile?.reason||'来自本机集群登记表；提交前仍需实时诊断。'}</small></div>

      <div className="submit-grid">
        <label className="field">作业名称<input value={config.name || ''} onChange={event => setField('name', event.target.value)} /></label>
        <label className="field">{selectedProfile?.scheduler?.toLowerCase()==='slurm'?'分区':'队列'}<input value={config.queue || ''} onChange={event => setField('queue', event.target.value)} /></label>
        <label className="field">最长运行时间（HH:MM:SS）<input value={config.walltime || ''} onChange={event => setField('walltime', event.target.value)} /></label>
        <label className="field">远端工作区根目录（任务目录的上层）<input value={config.remote_workspace_root || '$HOME/230269265lht'} onChange={event => setField('remote_workspace_root', event.target.value)} /></label><p className="path-layout-note">项目与每次运行将保存在此根目录内的 material-projects 子目录。填写完整工作区路径，不需要再手动追加 material-projects。</p>
      </div>
      {config.runtime_source==='script'?<p>环境加载、MPI 与程序命令使用下方选定的导入脚本，无需重新填写路径。</p>:<details><summary>高级：登记运行时路径</summary><p>填写服务器已有运行方式；也可在下方选择导入脚本。</p><label className="field">程序可执行文件<input value={config.vasp_executable || ''} onChange={event=>setField('vasp_executable',event.target.value)}/></label>{selectedProfile?.scheduler?.toLowerCase()!=='slurm'&&<><label className="field">MPI 可执行文件<input value={config.mpi_executable || ''} onChange={event=>setField('mpi_executable',event.target.value)}/></label><label className="field">环境脚本<input value={config.environment_script || ''} onChange={event=>setField('environment_script',event.target.value)}/></label></>}</details>}
      <div className="submit-actions"><button className="button" disabled={!canProbe} onClick={onProbe}>仅重新诊断当前配置</button><button className="button primary" disabled={!canProbe} onClick={onSaveAndProbe}>保存并诊断一次</button></div>
    </fieldset>}
    {busy && <RemoteProgress label={progressLabel||'正在核对主机与资源'}/>}
    {status && <p className="submit-status" role="status">{status}</p>}
    {error && <p role="alert" className="structure-error">{error}</p>}
    {probe && <div className="probe-result">
      <div className="probe-meta"><strong>实时只读诊断</strong><span>{probe.observed_at}</span></div>
      <p>{probe.runtime_evidence}</p>
      <p>路径检查：{Object.entries(probe.checks).map(([key, value]) => `${key}: ${value}`).join(' · ') || '未返回'}</p>
      <h4>节点资源 <small>jutai-1/node06 已按规则排除</small></h4>
      <div className="table-scroll"><table><thead><tr><th>节点</th><th>状态</th><th>空闲 / 总核</th><th>MOM 年龄 / s</th></tr></thead><tbody>{nodes.map(item => <tr key={item.NODE}><td>{item.NODE}</td><td>{item.STATUS}</td><td>{item.CPUS_FREE || '0'} / {item.CPUS_TOTAL || '未知'}</td><td>{item.MOM_AGE_SEC || '未知'}</td></tr>)}</tbody></table></div>
      <p>{selectedProfile?.scheduler?.toLowerCase()==='slurm'?'分区':'队列'}：{probe.queues.map(queue => selectedProfile?.scheduler?.toLowerCase()==='slurm' ? `${queue.name}（available=${queue.available}, time limit=${queue.time_limit || '未声明'}）` : `${queue.name}（enabled=${queue.enabled}, started=${queue.started}, walltime上限=${queue['resources_max.walltime'] || '未声明'}）`).join('；') || '未返回'}</p>
      <fieldset disabled={busy}>
        {purpose!=='chain'&&<div className="submit-grid">
          <label className="field prominent-node-choice"><strong>本次计算节点：{node || '尚未选择'}</strong><select value={node} onChange={event => onNodeChange(event.target.value)}><option value="">请选择</option>{nodes.map(item => <option key={item.NODE} value={item.NODE}>{item.NODE} · {item.STATUS} · 空闲 {item.CPUS_FREE || 0} 核</option>)}</select></label>
          <label className="field">核数上限（留空使用本次可用核）<input value={cores} onChange={event => onCoresChange(event.target.value)} inputMode="numeric" /></label>
        </div>}
        {purpose!=='chain'&&<><label className="field">少于节点可用核数的理由{reasonRequired?<span className="required-mark">必填</span>:<span>（选填）</span>}<input required={reasonRequired} aria-required={reasonRequired} value={reason} onChange={event => onReasonChange(event.target.value)} /></label>
        <button className="button" disabled={!node} onClick={onPreview}>生成资源与脚本预览</button></>}
      </fieldset>
      <details><summary>只读诊断原始回执</summary><pre className="input-text">{probe.availability.stdout + probe.availability.stderr + probe.diagnostics.stdout + probe.diagnostics.stderr}</pre></details>
    </div>}
    {preview && <div className="server-preview"><h4>{preview.resource_ready ? purpose==='batch' ? '整批资源与脚本预览已就绪' : purpose==='chain' ? '服务器资源预览已就绪 · 请继续逐节点预览' : '资源与路径检查通过 · 仍需输入审查' : '资源或环境条件未满足'}</h4>{preview.blockers.map((blocker, index) => <p className="structure-error" key={index}>{blocker}</p>)}<p>{purpose==='batch'?'case 输入已在冻结时逐项生成并检查；授权后每项派发前会再次审查输入、来源和资源。':purpose==='chain'?'计算链输入已在冻结时逐节点检查；下方仍需预览每个节点的资源和脚本。':preview.note}</p><pre className="input-text">{preview.script}</pre></div>}
  </section>;
}

export default function ServerPanel(props:Props){
 const {config,profiles,probe,node,cores,reason,busy,project,scopeId,program='vasp_std'}=props;
 const [settingsCache,setSettingsCache]=useState(emptySettings);
 const [bindingRevision,setBindingRevision]=useState(0);
 useEffect(()=>{const update=()=>setBindingRevision(v=>v+1);window.addEventListener('fpilot:binding-changed',update);return()=>window.removeEventListener('fpilot:binding-changed',update);},[]);
 const [binding,setBinding]=useState<{alias?:string;state:string}|null>(null),[bindingError,setBindingError]=useState('');
 useEffect(()=>{if(props.maintenance||!project||!scopeId)return;let active=true;void requestJson('/api/branch-binding',{project,node_id:scopeId}).then(value=>{if(active){setBindingError('');setBinding(value);if(config&&value.alias&&value.alias!==config.alias){void requestJson('/api/workbench-settings').then(settings=>{if(active)props.onConfigChange(runtimeFor(config,profiles,settings,value.alias,program));});}}}).catch(e=>{if(active)setBindingError(errorMessage(e));});return()=>{active=false;};},[project?.id,JSON.stringify(project?.edges),scopeId,config?.alias,bindingRevision]);
 useEffect(()=>{if(props.maintenance||!config)return;let active=true;void requestJson('/api/workbench-settings').then(settings=>{if(active){setSettingsCache(settings);let next=runtimeFor(config,profiles,settings,config.alias,program);if(props.programs&&props.programs.length>1){const choices=Object.fromEntries(props.programs.map(p=>[p,settings.defaults[config.alias+':'+p]||'']).filter(([,id])=>id));if(Object.keys(choices).length)next={...next,runtime_source:'script',program:props.programs[0],vasp_executable:props.programs[0],script_templates:JSON.stringify(choices),template_id:choices[props.programs[0]]||''};}if(JSON.stringify(next)!==JSON.stringify(config))props.onConfigChange(next);props.onTemplate?.(next.template_id||'');}}).catch(e=>{if(active)setBindingError(errorMessage(e));});return()=>{active=false;};},[config?.alias,program,props.programs?.join(','),profiles.length]);
 if(props.maintenance)return <MaintenanceServerPanel {...props}/>;
 const nodes=availableNodes(probe,config?.alias);
 const locked=Boolean(binding?.alias);
 const set=(key:string,value:string)=>{if(config)props.onConfigChange({...config,[key]:value});};
 const localErrors:string[]=[];
 const seconds=(value:string)=>{const parts=value.split('-'),clock=parts.pop()||'';return Number(parts[0]||0)*86400+clock.split(':').reduce((total,n)=>total*60+Number(n),0);};
 if(config){
  if(!/^[A-Za-z0-9_.-]{1,120}$/.test(config.name||''))localErrors.push('作业名称只允许英文、数字、点、横线和下划线');
  if(!/^\d{2,3}:[0-5]\d:[0-5]\d$/.test(config.walltime||'')||seconds(config.walltime)<=0)localErrors.push('运行时间需为正的 HH:MM:SS');
  const queue=probe?.queues.find(q=>q.name===config.queue),maximum=queue?.['resources_max.walltime']||queue?.time_limit;
  if(maximum&&maximum!=='infinite'&&seconds(config.walltime)>seconds(maximum))localErrors.push('运行时间超过已检测队列/分区上限');
  const capacity=nodes.find(n=>n.NODE===node);
  if(cores&&(!/^[1-9]\d*$/.test(cores)||capacity&&Number(cores)>Number(capacity.CPUS_FREE)))localErrors.push('核数需为正整数，且不超过上次检测的可用容量');
 }
 const selectedProfile=profiles.find(p=>p.alias===config?.alias);
 return <><section className="input-submit-card" aria-label="服务器与脚本"><h3>2 · 服务器与脚本</h3>{config&&<><label>分支服务器{locked?<strong className="server-lock">{binding?.alias} · {binding?.state==='RESERVED'?'回执待核查，服务器已预留':'已绑定'}</strong>:<select value={config.alias||''} disabled={busy} onChange={e=>{props.onConfigChange(runtimeFor(config,profiles,settingsCache,e.target.value,program));props.onNodeChange('');}}>{profiles.map(p=><option key={p.alias} value={p.alias} disabled={!p.selectable}>{p.alias}</option>)}</select>}</label>
 <ScriptPicker config={config} programs={props.programs||[program]} disabled={busy} onChange={props.onConfigChange} onSelect={props.onTemplate} registeredExecutable={program==='vasp_ncl'?selectedProfile?.ncl_executable:selectedProfile?.vasp_executable}/>
 <fieldset disabled={busy}><div className="submit-grid"><label>作业名称<input value={config.name||''} onChange={e=>set('name',e.target.value)}/></label><label>{config.scheduler==='slurm'?'分区':'队列'}<input value={config.queue||''} onChange={e=>set('queue',e.target.value)}/></label><label>运行时间（HH:MM:SS）<input value={config.walltime||''} onChange={e=>set('walltime',e.target.value)}/></label></div></fieldset>
 </>}<button className="text-button" onClick={()=>openSettings('server')}>服务器与默认脚本设置</button></section>
 <section className="input-submit-card" aria-label="服务器与资源"><h3>3 · 节点与资源</h3>
 <button className="button" disabled={busy||props.canProbe===false} onClick={props.onProbe}>检测空闲节点</button>{props.resourceExtra}
 {probe&&!props.resourceExtra&&props.purpose!=='chain'&&<div className="submit-grid"><label>计算节点<select value={node} disabled={busy} onChange={e=>props.onNodeChange(e.target.value)}><option value="">请选择</option>{nodes.map(n=><option key={n.NODE} value={n.NODE}>{n.NODE} · 空闲 {n.CPUS_FREE||0} / {n.CPUS_TOTAL||'?'} 核</option>)}</select></label><label>核数（留空使用当前可用核）<input value={cores} disabled={busy} onChange={e=>props.onCoresChange(e.target.value)} inputMode="numeric"/></label></div>}
 {cores&&node&&Number(cores)<Number(nodes.find(n=>n.NODE===node)?.CPUS_FREE||0)&&<label>少用核数的理由<input value={reason} onChange={e=>props.onReasonChange(e.target.value)}/></label>}
 {localErrors.map(error=><p role="alert" className="structure-error" key={error}>{error}</p>)}{probe&&<p role="status">上次节点检测 · {new Date(probe.observed_at).toLocaleTimeString()} · 编辑保留选择，派发前自动实时复核</p>}{binding?.state==='CONFLICT'&&<p role="alert" className="structure-error">历史服务器冲突，请到设置 → 计算流程整理分支归属。</p>}{bindingError&&<p role="alert" className="structure-error">{bindingError}</p>}{props.error&&<p role="alert" className="structure-error">{props.error}</p>}{props.preview?.blockers.map((b,i)=><p role="alert" key={i} className="structure-error">{b}</p>)}{busy&&<RemoteProgress label={props.progressLabel||'正在检测节点'}/>}</section></>;
}
