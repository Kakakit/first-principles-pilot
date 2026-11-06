/** Appearance is local browser preference; it never changes a calculation workspace. */
export const APPEARANCE_KEY = 'fpilot.appearance.v1';
export const THEME_SCHEMA = 'fpilot.theme.v1';
export const MAX_THEME_BYTES = 65536;
export const colorLabels = {
  background:'页面底色', surface:'卡片、表单与弹窗', surfaceRaised:'悬停、次级面板',
  sidebar:'项目侧栏', sidebarText:'侧栏文字', sidebarMuted:'侧栏辅助文字',
  text:'正文', mutedText:'辅助文字', border:'边框与分隔线',
  accent:'主按钮、选中线与链接', accentText:'主按钮文字', accentSoft:'选中背景',
  success:'成功状态文字', successSoft:'成功状态背景', warning:'提醒文字', warningSoft:'提醒背景',
  danger:'错误与删除文字', dangerSoft:'错误背景',
  canvas:'流程画布与结构视图底色', canvasGrid:'画布网格', canvasEdge:'流程连线',
  codeBackground:'输入文件、日志底色', codeText:'输入文件、日志文字',
  plotText:'图表文字', plotGrid:'图表网格',
} as const;
export type ColorKey = keyof typeof colorLabels;
export type ThemeColors = Record<ColorKey,string>;
export const fontLabels = {chinese:'中文正文',english:'英文正文',headingChinese:'中文标题',headingEnglish:'英文标题',monospace:'代码与数值'} as const;
export type FontKey = keyof typeof fontLabels;
export type ThemeFonts = Record<FontKey,string[]>;
export type ThemeMetrics = {fontSize:number;secondaryFontSize:number;radius:number};
export type BuiltinId = 'celadon'|'polar'|'route'|'orbit'|'paper';
export type ThemeDefinition = {
  schema:typeof THEME_SCHEMA;id:string;name:string;description?:string;base:BuiltinId;
  appearance?:'light'|'dark';colors?:Partial<ThemeColors>;fonts?:Partial<ThemeFonts>;metrics?:Partial<ThemeMetrics>;
};
export type Theme = {id:string;name:string;description:string;appearance:'light'|'dark';colors:ThemeColors;fonts:ThemeFonts;metrics:ThemeMetrics;base:BuiltinId};
const fonts:ThemeFonts = {
  chinese:['PingFang SC','Microsoft YaHei','Noto Sans CJK SC','sans-serif'],
  english:['Inter','-apple-system','BlinkMacSystemFont','Segoe UI','sans-serif'],
  headingChinese:['PingFang SC','Microsoft YaHei','Noto Sans CJK SC','sans-serif'],
  headingEnglish:['Inter','-apple-system','BlinkMacSystemFont','Segoe UI','sans-serif'],
  monospace:['SFMono-Regular','Menlo','Consolas','monospace'],
};
const metrics:ThemeMetrics = {fontSize:16,secondaryFontSize:14,radius:4};
const light:ThemeColors = {
  background:'#f4f5f6',surface:'#ffffff',surfaceRaised:'#f5f6f7',
  sidebar:'#ffffff',sidebarText:'#254b41',sidebarMuted:'#53675d',
  text:'#29333b',mutedText:'#58636c',border:'#dce1e5',
  accent:'#19816b',accentText:'#ffffff',accentSoft:'#f2fcf7',
  success:'#24714e',successSoft:'#eaf7ee',warning:'#8b5b16',warningSoft:'#fff4df',
  danger:'#b5414a',dangerSoft:'#fcecef',canvas:'#f5f6f7',canvasGrid:'#dce1e5',canvasEdge:'#82958e',
  codeBackground:'#eef4f0',codeText:'#304b3d',plotText:'#53685f',plotGrid:'#dce7e1',
};
const dark:ThemeColors = {
  background:'#101820',surface:'#18242f',surfaceRaised:'#21313e',
  sidebar:'#131d26',sidebarText:'#e0edf3',sidebarMuted:'#a5b9c8',
  text:'#e4edf4',mutedText:'#a5b9c8',border:'#334857',
  accent:'#70d9cf',accentText:'#102723',accentSoft:'#244440',
  success:'#86deb1',successSoft:'#203f33',warning:'#f3cc85',warningSoft:'#403625',
  danger:'#f4a1ab',dangerSoft:'#442c36',canvas:'#121e28',canvasGrid:'#2d4351',canvasEdge:'#8aa6b8',
  codeBackground:'#111b24',codeText:'#c5dce7',plotText:'#b7cbd7',plotGrid:'#334857',
};
function builtin(id:BuiltinId,name:string,description:string,appearance:'light'|'dark',colors:ThemeColors,themeFonts=fonts):Theme {
  return {id,base:id,name,description,appearance,colors,fonts:themeFonts,metrics};
}
export const BUILTIN_THEMES:Theme[] = [
  builtin('celadon','青瓷 · 绿白','明亮绿白，适合白天办公','light',light),
  builtin('polar','极夜','青蓝光点，安静的深色工作区','dark',dark),
  builtin('route','航线','清晰蓝色，适合夜间处理长流程','dark',{...dark,background:'#111827',surface:'#1b273b',surfaceRaised:'#25344c',sidebar:'#152034',border:'#354964',accent:'#91baff',accentText:'#13243c',accentSoft:'#2a4065',canvas:'#141f32',canvasGrid:'#30425e',canvasEdge:'#95adcf',codeBackground:'#121c2d',codeText:'#d0dff5'}),
  builtin('orbit','星轨','柔和紫色，适合夜间观察与比较','dark',{...dark,background:'#191624',surface:'#262234',surfaceRaised:'#332e43',sidebar:'#201c2d',sidebarText:'#eee7f8',sidebarMuted:'#bdb0cf',text:'#f0eafa',mutedText:'#bdb0cf',border:'#483e5b',accent:'#cfaff7',accentText:'#2a173e',accentSoft:'#443454',canvas:'#201c2c',canvasGrid:'#40364f',canvasEdge:'#aa94c5',codeBackground:'#1b1825',codeText:'#e0d2ef',plotText:'#c6b8d8',plotGrid:'#483e5b'}),
  builtin('paper','纸页','温暖纸色与衬线标题，适合长时间阅读','light',{...light,background:'#f4f1e9',surface:'#fffdf8',surfaceRaised:'#f6f2e9',sidebar:'#faf7ef',sidebarText:'#554d42',sidebarMuted:'#797162',text:'#403c34',mutedText:'#756d5f',border:'#e3dbcc',accent:'#846548',accentSoft:'#f6efe4',canvas:'#f5f1e7',canvasGrid:'#ded5c5',canvasEdge:'#a2947e',codeBackground:'#eee9de',codeText:'#554a3b',plotText:'#756d5f',plotGrid:'#e3dbcc'},{...fonts,headingChinese:['Songti SC','STSong','Noto Serif CJK SC','serif'],headingEnglish:['Georgia','Iowan Old Style','Palatino','serif']}),
];
export function builtinTheme(id:string):Theme {return BUILTIN_THEMES.find(t=>t.id===id)||BUILTIN_THEMES[0];}
const record=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
function assertKeys(value:Record<string,unknown>,keys:string[],label:string) {
  const extra=Object.keys(value).filter(key=>!keys.includes(key));if(extra.length)throw new Error(`${label}包含未知字段：${extra.join('、')}`);
}
export function validateTheme(value:unknown):ThemeDefinition {
  if(!record(value))throw new Error('主题应是一个 JSON 对象');
  assertKeys(value,['schema','id','name','description','base','appearance','colors','fonts','metrics'],'主题');
  if(value.schema!==THEME_SCHEMA)throw new Error(`schema 应为 ${THEME_SCHEMA}`);
  if(typeof value.id!=='string'||!/^[a-z][a-z0-9-]{0,47}$/.test(value.id))throw new Error('id 请使用小写英文开头，最多 48 位英文、数字或短横线');
  if(typeof value.name!=='string'||!value.name.trim()||value.name.length>40)throw new Error('name 请填写 1–40 字的主题名称');
  if(value.description!==undefined&&(typeof value.description!=='string'||value.description.length>160))throw new Error('description 最多 160 字');
  if(!BUILTIN_THEMES.some(theme=>theme.id===value.base))throw new Error('base 请选 celadon、polar、route、orbit 或 paper');
  if(value.appearance!==undefined&&!['light','dark'].includes(value.appearance as string))throw new Error('appearance 请选 light 或 dark');
  if(value.colors!==undefined){
    if(!record(value.colors))throw new Error('colors 应是颜色对象');
    assertKeys(value.colors,Object.keys(colorLabels),'colors');
    for(const [key,color] of Object.entries(value.colors))if(typeof color!=='string'||!/^#[\da-f]{6}$/i.test(color))throw new Error(`${key} 请填写六位色号，例如 #19816b`);
  }
  if(value.fonts!==undefined){
    if(!record(value.fonts))throw new Error('fonts 应是字体对象');assertKeys(value.fonts,Object.keys(fontLabels),'fonts');
    for(const [key,names] of Object.entries(value.fonts))if(!Array.isArray(names)||!names.length||names.length>12||names.some(n=>typeof n!=='string'||!n.trim()||n.length>80||/["'\\;{}<>\r\n]/.test(n)))throw new Error(`${key} 请填写字体名称数组，按优先顺序排列`);
  }
  if(value.metrics!==undefined){
    if(!record(value.metrics))throw new Error('metrics 应是尺寸对象');assertKeys(value.metrics,['fontSize','secondaryFontSize','radius'],'metrics');
    for(const [key,n] of Object.entries(value.metrics)){
      const [min,max]=key==='radius'?[0,20]:key==='fontSize'?[14,18]:[13,16];
      if(typeof n!=='number'||!Number.isFinite(n)||n<min||n>max)throw new Error(`${key} 请填写 ${min}–${max} 的数字`);
    }
  }
  return JSON.parse(JSON.stringify(value)) as ThemeDefinition;
}
export function parseTheme(text:string):ThemeDefinition {
  if(new TextEncoder().encode(text).length>MAX_THEME_BYTES)throw new Error('主题文件最多 64 KiB');
  let value:unknown;try{value=JSON.parse(text);}catch{throw new Error('文件不是有效 JSON，请检查引号和逗号');}return validateTheme(value);
}
export function resolveTheme(definition:ThemeDefinition):Theme {
  const base=builtinTheme(definition.base);
  return {...base,id:`custom:${definition.id}`,base:definition.base,name:definition.name.trim(),description:definition.description||'自定义主题',appearance:definition.appearance||base.appearance,colors:{...base.colors,...definition.colors},fonts:{...base.fonts,...definition.fonts},metrics:{...base.metrics,...definition.metrics}};
}
export function themeTemplate(theme:Theme=BUILTIN_THEMES[0]):ThemeDefinition {
  return {schema:THEME_SCHEMA,id:theme.id.startsWith('custom:')?theme.id.slice(7):'my-theme',name:theme.id.startsWith('custom:')?theme.name:`我的${theme.name}主题`,description:theme.description,base:theme.base,appearance:theme.appearance,colors:{...theme.colors},fonts:{...theme.fonts},metrics:{...theme.metrics}};
}
export function fontStack(english:string[],chinese:string[]):string {
  // Generic families belong last; otherwise Latin sans-serif may claim CJK glyphs first.
  const generic=new Set(['serif','sans-serif','monospace','system-ui']);
  const names=[...new Set([...english.filter(n=>!generic.has(n)),...chinese.filter(n=>!generic.has(n)),...chinese.filter(n=>generic.has(n)),...english.filter(n=>generic.has(n))])];
  return names.map(n=>generic.has(n)||n.startsWith('-')?n:`"${n}"`).join(', ');
}
export type AppearanceState={selectedId:string;customThemes:ThemeDefinition[];notice:string;fontSize?:14|16|18};
const initialState=():AppearanceState=>({selectedId:'celadon',customThemes:[],notice:''});
export function restoreAppearance(raw:string|null):AppearanceState {
  if(!raw)return initialState();
  try{
    const value:unknown=JSON.parse(raw);if(!record(value)||value.version!==1||!Array.isArray(value.customThemes)||value.customThemes.length>20)throw new Error();
    const customThemes=value.customThemes.map(validateTheme);
    if(new Set(customThemes.map(t=>t.id)).size!==customThemes.length)throw new Error();
    const selectedId=typeof value.selectedId==='string'?value.selectedId:'celadon';
    const exists=BUILTIN_THEMES.some(t=>t.id===selectedId)||customThemes.some(t=>`custom:${t.id}`===selectedId);
    const fontSize=value.fontSize===14||value.fontSize===16||value.fontSize===18?value.fontSize:undefined;
    return {selectedId:exists?selectedId:'celadon',customThemes,notice:exists?'':'原主题不可用，已恢复明亮绿白主题',
      ...(fontSize?{fontSize}:{})};
  }catch{return {...initialState(),notice:'外观设置未能读取，已恢复明亮绿白主题；计算草稿不受影响'};}
}
let state=initialState();
const listeners=new Set<()=>void>();
export const getAppearance=()=>state;
export function subscribeAppearance(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener);};}
export function selectedTheme(preferences:AppearanceState=state):Theme {
  const custom=preferences.customThemes.find(t=>`custom:${t.id}`===preferences.selectedId);
  const theme=custom?resolveTheme(custom):builtinTheme(preferences.selectedId);
  const size=preferences.fontSize;
  return size?{...theme,metrics:{...theme.metrics,fontSize:size,secondaryFontSize:size===18?16:size===16?14:13}}:theme;
}
export function applyTheme(theme:Theme,root:HTMLElement=document.documentElement) {
  root.dataset.appearance=theme.appearance;root.dataset.theme=theme.id;root.style.colorScheme=theme.appearance;
  for(const [key,value] of Object.entries(theme.colors))root.style.setProperty(`--color-${key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase())}`,value);
  root.style.setProperty('--font-body',fontStack(theme.fonts.english,theme.fonts.chinese));
  root.style.setProperty('--font-heading',fontStack(theme.fonts.headingEnglish,theme.fonts.headingChinese));
  root.style.setProperty('--font-code',fontStack(theme.fonts.monospace,theme.fonts.chinese.filter(name=>!['serif','sans-serif','monospace','system-ui'].includes(name))));
  root.style.setProperty('--ui-title',theme.metrics.fontSize>=18?'24px':'20px');root.style.setProperty('--ui-caption',theme.metrics.secondaryFontSize>=16?'14px':'12px');
  root.style.setProperty('--font-size',`${theme.metrics.fontSize}px`);root.style.setProperty('--font-secondary',`${theme.metrics.secondaryFontSize}px`);root.style.setProperty('--radius',`${theme.metrics.radius}px`);
}
function notify(){applyTheme(selectedTheme());listeners.forEach(listener=>listener());window.dispatchEvent(new Event('fpilot:appearance-changed'));}
function save(next:AppearanceState) {
  state={...next,notice:''};
  try{localStorage.setItem(APPEARANCE_KEY,JSON.stringify({version:1,selectedId:state.selectedId,customThemes:state.customThemes,fontSize:state.fontSize}));}
  catch{state={...state,notice:'当前主题已应用，但浏览器未能保存外观设置；下次打开可能恢复默认'};}
  notify();
}
export function initializeAppearance(){
  try{state=restoreAppearance(localStorage.getItem(APPEARANCE_KEY));}catch{state={...initialState(),notice:'浏览器无法读取外观设置，当前使用明亮绿白主题'};}
  notify();
  window.addEventListener('storage',event=>{if(event.key===APPEARANCE_KEY){state=restoreAppearance(event.newValue);notify();}});
}
export function chooseTheme(id:string){
  if(!BUILTIN_THEMES.some(t=>t.id===id)&&!state.customThemes.some(t=>`custom:${t.id}`===id))throw new Error('主题不存在');
  save({...state,selectedId:id});
}
export function chooseFontSize(fontSize:AppearanceState['fontSize']){save({...state,fontSize});}
export function installTheme(text:string):Theme {
  const definition=parseTheme(text);const others=state.customThemes.filter(t=>t.id!==definition.id);
  if(others.length>=20)throw new Error('最多保存 20 个自定义主题，请先移除不用的主题');
  const theme=resolveTheme(definition);save({...state,selectedId:theme.id,customThemes:[...others,definition]});return theme;
}
export function removeTheme(id:string){save({...state,selectedId:state.selectedId===id?'celadon':state.selectedId,customThemes:state.customThemes.filter(t=>`custom:${t.id}`!==id)});}
