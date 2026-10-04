import {useRef,useState} from 'react';
import {Check,Download,Palette,Upload,X} from 'lucide-react';
import {BUILTIN_THEMES,chooseTheme,colorLabels,fontLabels,installTheme,MAX_THEME_BYTES,removeTheme,resolveTheme,themeTemplate} from './appearance';
import type {Theme} from './appearance';
import {useAppearance} from './useAppearance';
export default function AppearancePanel(){
  const {theme,customThemes,notice}=useAppearance();const file=useRef<HTMLInputElement>(null);const [error,setError]=useState('');const [message,setMessage]=useState('');const [exportOpen,setExportOpen]=useState(false);
  const currentJson=JSON.stringify(themeTemplate(theme),null,2)+'\n';
  const copyTheme=async()=>{try{await navigator.clipboard.writeText(currentJson);setMessage('当前主题 JSON 已复制，可保存为 .json 文件后导入');setError('');}catch{setError('复制未完成，请在下方文本框中手动选择和复制。');}};
  const choose=(id:string)=>{chooseTheme(id);setError('');setMessage('主题已应用');};
  const importFile=async(input?:File)=>{
    if(!input)return;setError('');setMessage('');
    try{if(input.size>MAX_THEME_BYTES)throw new Error('主题文件最多 64 KiB');const imported=installTheme(await input.text());setMessage(`已导入并应用「${imported.name}」`);}
    catch(e){setError(`导入失败：${(e as Error).message}。当前主题已保留。`);}
    finally{if(file.current)file.current.value='';}
  };
  const card=(item:Theme,custom=false)=><div className="theme-card-wrap" key={item.id}>
    <button className={`theme-card ${theme.id===item.id?'selected':''}`} aria-pressed={theme.id===item.id} onClick={()=>choose(item.id)}>
      <span className="theme-sample" style={{background:item.colors.background,color:item.colors.text,borderColor:item.colors.border}}>
        <span className="theme-sample-rail" style={{background:item.colors.sidebar,borderColor:item.colors.border}}><i style={{background:item.colors.accent}}/><i style={{background:item.colors.accentSoft}}/><i style={{background:item.colors.border}}/></span>
        <span className="theme-sample-content"><span style={{background:item.colors.surface,borderColor:item.colors.border}}><b style={{color:item.colors.accent}}>Aa</b><i style={{background:item.colors.border}}/></span><span className="theme-sample-dots"><i style={{background:item.colors.accent}}/><i style={{background:item.colors.canvasEdge}}/><i style={{background:item.colors.mutedText}}/></span></span>
      </span>
      <span className="theme-card-name">{item.name}{theme.id===item.id&&<Check size={16} aria-label="当前主题"/>}</span>
      <span className="theme-card-description">{item.description}</span><span className="theme-mode">{custom?'自定义 · ':''}{item.appearance==='dark'?'夜间':'浅色'}</span>
    </button>
    {custom&&<button className="theme-remove" aria-label={`移除主题 ${item.name}`} title="移除自定义主题" onClick={()=>{removeTheme(item.id);setMessage('已移除主题；计算数据和文件不受影响');}}><X size={15}/></button>}
  </div>;
  return <div className="appearance-panel">
    <div className="appearance-heading"><span className="section-kicker"><Palette size={16}/>个人外观</span><h2>让工作台适合你的节奏</h2><p>切换即时生效，保留当前页面、计算草稿和流程画布。</p></div>
    <div className="theme-grid" aria-label="内置主题">{BUILTIN_THEMES.map(t=>card(t))}</div>
    {!!customThemes.length&&<><h3 className="appearance-section-title">我的主题</h3><div className="theme-grid">{customThemes.map(t=>card(resolveTheme(t),true))}</div></>}
    <div className="appearance-preview"><div><span className="section-kicker">字体预览</span><h3>从结构到结果 · Materials research</h3><p>选择来源，配置计算，审阅输入，再运行与比较。</p><p className="appearance-latin">Structure → Calculation → Results</p><code>ENCUT = 520 · E = −10.428 eV</code></div><span className="neutral-badge">{theme.metrics.fontSize} px 正文 · {theme.metrics.secondaryFontSize} px 辅助文字</span></div>
    <div className="appearance-import"><div><h3>自定义颜色与中英文字体</h3><p>下载模板后修改色号和字体名称，再导入 JSON。未填写的项沿用基础主题。</p></div><div className="appearance-actions"><a className="button" href="/theme-templates/custom-theme.json" download="fpilot-custom-theme.json"><Download size={16}/>下载格式模板</a><button className="button" aria-expanded={exportOpen} onClick={()=>setExportOpen(value=>!value)}>查看 / 复制当前主题</button><button className="button primary" onClick={()=>file.current?.click()}><Upload size={16}/>导入并应用</button></div><input ref={file} hidden type="file" accept=".json,application/json" aria-label="导入主题文件" onChange={e=>void importFile(e.target.files?.[0])}/></div>
    {exportOpen&&<div className="theme-export"><label className="field">当前主题 JSON<textarea aria-label="当前主题 JSON" className="input-text" readOnly rows={10} value={currentJson}/></label><button className="button" onClick={()=>void copyTheme()}>复制主题 JSON</button><p>保存为 UTF-8 编码的 .json 文件，即可在另一台电脑导入。基础主题和所有生效颜色、字体都包含在内。</p></div>}
    {error&&<p className="appearance-error" role="alert">{error}</p>}{notice&&<p className="appearance-error" role="alert">{notice}</p>}{message&&<p className="appearance-message" role="status">{message}</p>}
    <details className="appearance-guide"><summary>模板里的色号和字体对应哪里？</summary><p><code>base</code> 指定基础主题。<code>colors</code> 控制以下界面色块；字体按数组顺序使用本机已安装字体，缺少时自动使用后备字体，无需下载字体。</p><dl>{Object.entries(colorLabels).map(([key,label])=><div key={key}><dt><code>{key}</code></dt><dd>{label}</dd></div>)}</dl><p>{Object.entries(fontLabels).map(([key,label])=><span key={key} className="theme-font-key"><code>{key}</code>：{label}</span>)}</p><p>元素颜色、曲线分类颜色和科学数据保持原有含义。<code>metrics</code> 可调正文字号 14–18 px、辅助字号 13–16 px 和圆角 0–20 px。</p></details>
  </div>;
}
