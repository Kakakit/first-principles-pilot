import {DeleteRecord,RecycleBin} from './RecordCleanup';
import { useEffect, useState } from 'react';

export type VerifiedTemplate = {
  key: string;
  alias: string;
  name: string;
  scheduler: string;
  program: string;
  source: string;
  source_hash: string;
  evidence: unknown;
  executable: boolean;
  reason: string;
};

type ManualTemplate = { id: string; name: string; alias: string; scheduler: string; program: string; submission_supported?: boolean };
type ManualPreview = { id: string; original_hash: string; source: string; script_text: string; original_text: string; resource_diff: string; adaptation_diff: string; execution_blockers: string[]; submission_supported: boolean };
type Props = { alias: string; selectedKey: string; onSelect: (id: string) => void; onInvalidate: () => void; disabled?: boolean };

async function request(path: string, body?: unknown, method?: string) {
  const response = await fetch(path, { method: method || (body === undefined ? 'GET' : 'POST'), headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '模板请求失败');
  return data;
}

export default function JobTemplatePanel({ alias, selectedKey, onSelect, onInvalidate, disabled = false }: Props) {
  const [verified, setVerified] = useState<VerifiedTemplate[]>([]);
  const [manual, setManual] = useState<ManualTemplate[]>([]);
  const [manualOpen, setManualOpen] = useState(false);
  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [host, setHost] = useState(alias);
  const [scheduler, setScheduler] = useState('pbs');
  const [program, setProgram] = useState('vasp_std');
  const [source, setSource] = useState('');
  const [parent, setParent] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<ManualPreview | null>(null);
  const [importedName, setImportedName] = useState('');

  useEffect(() => { let alive = true; request('/api/job-templates/verified').then(data => { if (alive) setVerified(Array.isArray(data) ? data : []); }).catch(caught => { if (alive) setError((caught as Error).message); }); return () => { alive = false; }; }, []);
  useEffect(() => { void request('/api/job-templates').then(setManual).catch(caught=>setError((caught as Error).message)); }, []);
  useEffect(() => { setHost(alias); }, [alias]);

  async function importVerified(item: VerifiedTemplate) {
    if (!item.executable) return;
    setBusy(true); setError(''); setPreview(null); onInvalidate();
    try {
      const data = await request(`/api/job-templates/verified/${encodeURIComponent(item.key)}/import`, undefined, 'POST');
      const template = data.template || data.original || {};
      const id = template.id || template.key || item.key;
      onSelect(id);
      setImportedName(template.name || item.name);
    } catch (caught) { setError((caught as Error).message); } finally { setBusy(false); }
  }

  async function loadManual() {
    setManualOpen(true);
    if (manual.length) return;
    try { setManual(await request('/api/job-templates')); } catch (caught) { setError((caught as Error).message); }
  }

  async function saveManual() {
    setBusy(true); setError(''); setPreview(null); onInvalidate();
    try {
      const data = await request('/api/job-templates', { script: text, name, scheduler, alias: host, program, source, parent_id: parent || null });
      setManual(await request('/api/job-templates'));
      const id = data.id || data.key;
      if (id) { onSelect(id); setImportedName(name); }
    } catch (caught) { setError((caught as Error).message); } finally { setBusy(false); }
  }

  async function previewManual(id: string) {
    setBusy(true); setError(''); setPreview(null); onInvalidate();
    try { setPreview(await request(`/api/job-templates/${id}/preview`, { overrides: {}, alias })); } catch (caught) { setError((caught as Error).message); } finally { setBusy(false); }
  }

  return <section className="template-submit-card" aria-label="提交模板选择">
    <div className="submit-section-heading"><div><span className="eyebrow">TEMPLATE · VERIFIED SOURCE</span><h3>模板与运行时绑定</h3></div>{selectedKey && <span className="neutral-badge server-ready">已选择：{importedName || '已验证模板'}</span>}</div>
    <p className="submit-help">当前主机：<strong>{alias || '未选择'}</strong>。优先使用该主机推荐模板；导入会保留原稿和来源证据，必要时由后端生成可追溯副本。</p>
    <div className="verified-template-list">{verified.length ? (verified.filter(item => item.alias === alias).length ? verified.filter(item => item.alias === alias).map(item => <article className={`verified-template ${item.key === selectedKey ? 'selected' : ''}`} key={item.key}><div><strong>{item.name}</strong><span>{item.alias} · {item.scheduler} · {item.program}</span><small>{item.source} · 来源已校验</small><small>证据：{typeof item.evidence === 'string' ? item.evidence : JSON.stringify(item.evidence)}</small></div><button className="button" disabled={disabled || busy || !item.executable} title={!item.executable ? item.reason : ''} onClick={() => void importVerified(item)}>{item.executable ? '导入并使用已验证模板' : '原稿不可读，请重新导入'}</button><p className="template-evidence">{item.executable ? (item.reason || '已通过登记证据，可用于当前主机预览。') : item.reason}</p></article>) : <p>当前主机尚无内置脚本，可在下方导入自己的脚本。</p>) : <p>正在读取已验证模板…</p>}</div>
    {verified.some(item => item.alias !== alias) && <details className="archived-templates"><summary>其他主机的已验证原稿（仅归档查看）</summary>{verified.filter(item => item.alias !== alias).map(item => <p key={item.key}><strong>{item.alias} · {item.name}</strong>　{item.source}　<span>证据：{typeof item.evidence === 'string' ? item.evidence : JSON.stringify(item.evidence)}</span>　{item.reason}</p>)}</details>}
    <div className="verified-template-list"><h4>已导入脚本 · 当前主机</h4>{manual.filter(item=>item.alias===alias).map(item=><p className="history-row" key={item.id}><button className="button" disabled={busy||disabled} onClick={()=>{onSelect(item.id);setImportedName(item.name);}}>{item.name} · {item.scheduler} · {item.program} · 选择</button><DeleteRecord kind="job-templates" id={item.id} label={item.name} onChanged={async()=>{setManual(await request('/api/job-templates'));if(item.id===selectedKey)onInvalidate();}}/><button className="text-button" onClick={()=>void previewManual(item.id)}>预览</button></p>)}</div><RecycleBin kind="job-templates" title="脚本回收站" onChanged={async()=>setManual(await request('/api/job-templates'))}/>{error && <p role="alert" className="structure-error">{error}</p>}
    <details onToggle={event => { if ((event.currentTarget as HTMLDetailsElement).open) void loadManual(); }}><summary>导入脚本与原稿查看</summary>
      <p className="submit-help">导入后即可在上方选择。脚本正文由用户管理；提交前仍按所选主机与节点生成资源预览。</p>
      <fieldset disabled={disabled || busy}>
        <label className="field">模板名称<input value={name} onChange={event => { setName(event.target.value); onInvalidate(); }} /></label>
        <label className="field">来源<input value={source} onChange={event => { setSource(event.target.value); onInvalidate(); }} /></label>
        <label className="field">适用 SSH 别名<input value={host} onChange={event => { setHost(event.target.value); onInvalidate(); }} /></label>
        <label className="field">调度器<select value={scheduler} onChange={event => { setScheduler(event.target.value); onInvalidate(); }}><option value="pbs">PBS/Torque</option><option value="slurm">Slurm</option></select></label>
        <label className="field">程序<select value={program} onChange={event => { setProgram(event.target.value); onInvalidate(); }}><option value="vasp_std">VASP CPU</option><option value="gpu_vasp">VASP GPU</option><option value="wannier90">Wannier90</option><option value="other">其他用户脚本</option></select></label>
        <label className="field">导入脚本<input type="file" accept=".pbs,.slurm,.sh,.txt" onChange={async event => { const file = event.target.files?.[0]; if (!file) return; if (file.size > 65536) { setError('模板最大 64 KiB'); return; } setText(await file.text()); setName(file.name); setSource(file.name); onInvalidate(); }} /></label>
        <label className="field">原始脚本 / 适配副本正文<textarea rows={8} value={text} onChange={event => { setText(event.target.value); onInvalidate(); }} /></label>
        <button className="button" disabled={!text || !name || !source} onClick={() => void saveManual()}>保存原稿并检查</button>
      </fieldset>
      {manual.map(item => <p className="history-row" key={item.id}><button className="button" disabled={busy} onClick={() => void previewManual(item.id)}>{item.name} · {item.alias} · {item.scheduler}</button></p>)}
      {preview && <><p>{preview.submission_supported ? '脚本已可选择；准备时仍会重新核对资源。' : '请先选择主机与资源后预览'}</p>{preview.execution_blockers.map((blocker, index) => <p key={index} className="structure-error">{blocker}</p>)}<p>来源：{preview.source}<br />原稿已校验；完整证据保留在后端。</p><pre className="input-text">{preview.script_text}</pre><details><summary>资源修改差异</summary><pre className="input-text">{preview.resource_diff || '无资源变化'}</pre></details><details><summary>适配副本与父原稿差异</summary><pre className="input-text">{preview.adaptation_diff || '当前为原稿'}</pre></details></>}
    </details>
  </section>;
}
