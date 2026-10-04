import { useEffect, useState } from 'react';

type Props = { bundleId: string; alias: string; root: string; disabled?: boolean; onRootChange?: (root: string) => void };
type Result = { remote_path_absolute: string; note: string; alias?: string; remote_workspace_root?: string };

export default function RemotePathPreview({ bundleId, alias, root, disabled = false, onRootChange }: Props) {
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { setResult(null); setError(''); }, [bundleId, alias, root]);
  async function preview() {
    setBusy(true); setError(''); setResult(null);
    try {
      const response = await fetch('/api/execution/path-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bundle_id: bundleId, alias, remote_workspace_root: root }) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '路径核对失败');
      setResult(data);
    } catch (caught) { setError((caught as Error).message); } finally { setBusy(false); }
  }
  return <><p className="path-layout-note"><strong>本次工作区根目录：</strong><code>{root}</code><br/>任务将建在此目录内的 <code>material-projects/项目/flows/main/stages/阶段/attempts/本次运行</code>。每次运行独立保存，避免覆盖已有结果；更改根目录仅影响之后新准备的任务。</p><details className="path-preview"><summary>核对冻结输入的远端阶段路径</summary>
    <p>路径预览与本次提交使用同一服务器别名和工作区根目录。它是只读核对，不创建目录、不上传文件。</p>
    <fieldset disabled={disabled || busy}>
      <p><strong>绑定主机：</strong>{alias || '未选择'}　<strong>工作区根目录：</strong>{root || '未设置'}</p>
      {onRootChange && <label className="field">工作区根目录<input value={root} onChange={event => onRootChange(event.target.value)} /></label>}
      <button className="button" disabled={!bundleId || !alias || !root} onClick={() => void preview()}>只读核对路径</button>
    </fieldset>
    {busy && <p role="status">正在核对目录…</p>}
    {error && <p role="alert" className="structure-error">{error}</p>}
    {result && <div><p>主机：{result.alias || alias}</p><pre className="input-text">{result.remote_path_absolute}</pre><p>{result.note}</p></div>}
  </details></>;
}
