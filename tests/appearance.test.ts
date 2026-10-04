import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {APPEARANCE_KEY,BUILTIN_THEMES,parseTheme,resolveTheme,restoreAppearance,fontStack,themeTemplate,initializeAppearance,installTheme,chooseTheme,removeTheme,getAppearance,selectedTheme} from '../src/appearance.ts';
const minimal={schema:'fpilot.theme.v1',id:'forest',name:'森林',base:'celadon'};
test('default is bright green and white; five themes include three dark choices',()=>{
 assert.equal(BUILTIN_THEMES[0].colors.sidebar,'#ffffff');assert.equal(BUILTIN_THEMES[0].colors.surface,'#ffffff');assert.equal(BUILTIN_THEMES[0].colors.accent,'#19816b');
 assert.equal(BUILTIN_THEMES.filter(t=>t.appearance==='dark').length,3);assert.ok(BUILTIN_THEMES.find(t=>t.id==='paper')!.fonts.headingChinese.includes('Songti SC'));
});
test('partial import inherits colors, fonts and dimensions from its base',()=>{
 const theme=resolveTheme(parseTheme(JSON.stringify({...minimal,base:'orbit',colors:{accent:'#ffffff'},fonts:{chinese:['Songti SC','serif']},metrics:{fontSize:16}})));
 const base=BUILTIN_THEMES.find(t=>t.id==='orbit')!;assert.equal(theme.id,'custom:forest');assert.equal(theme.appearance,'dark');assert.equal(theme.colors.surface,base.colors.surface);assert.equal(theme.colors.accent,'#ffffff');assert.equal(theme.metrics.secondaryFontSize,13);assert.deepEqual(theme.fonts.chinese,['Songti SC','serif']);
});
test('invalid colors, dimensions, unknown keys and malformed files explain corrections',()=>{
 for(const [change,pattern] of [[{colors:{accent:'green'}},/六位色号/],[{metrics:{secondaryFontSize:9}},/13–16/],[{colors:{surfac:'\u0023ffffff'}},/未知字段/],[{fonts:{english:'Georgia'}},/字体名称数组/],[{base:'missing'},/base/],[{schema:'v2'},/schema/],[{css:'body{}'},/未知字段/],[{fonts:{english:['x; color:red']}},/字体名称数组/]] as const)assert.throws(()=>parseTheme(JSON.stringify({...minimal,...change})),pattern);
 assert.throws(()=>parseTheme('{'),/有效 JSON/);assert.throws(()=>parseTheme(' '.repeat(65537)),/64 KiB/);
});
test('font routing uses named Latin and CJK fonts before generic fallback',()=>{
 assert.equal(fontStack(['Georgia','serif'],['Songti SC','serif']),'"Georgia", "Songti SC", serif');
 assert.equal(fontStack(['Inter','sans-serif'],['PingFang SC','sans-serif']),'"Inter", "PingFang SC", sans-serif');
});
test('builtins can be exported and reimported without losing palette or typography',()=>{
 for(const original of BUILTIN_THEMES){const imported=resolveTheme(parseTheme(JSON.stringify(themeTemplate(original))));assert.deepEqual(imported.colors,original.colors);assert.deepEqual(imported.fonts,original.fonts);assert.deepEqual(imported.metrics,original.metrics);assert.equal(imported.appearance,original.appearance);}
});
test('missing, corrupt, duplicate or removed themes fall back without touching a workspace',()=>{
 assert.equal(restoreAppearance(null).selectedId,'celadon');assert.equal(restoreAppearance('{').selectedId,'celadon');
 assert.equal(restoreAppearance(JSON.stringify({version:1,selectedId:'custom:missing',customThemes:[]})).selectedId,'celadon');
 assert.equal(restoreAppearance(JSON.stringify({version:1,selectedId:'polar',customThemes:[minimal,minimal]})).selectedId,'celadon');
 const valid=restoreAppearance(JSON.stringify({version:1,selectedId:'custom:forest',customThemes:[minimal]}));assert.equal(valid.selectedId,'custom:forest');assert.equal(selectedTheme(valid).name,'森林');
});
function contrast(a:string,b:string){const luminance=(hex:string)=>{const rgb=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;};const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
test('built-in body, auxiliary text and primary buttons have readable contrast',()=>{
 for(const theme of BUILTIN_THEMES){const c=theme.colors;for(const [text,bg] of [[c.text,c.surface],[c.mutedText,c.surface],[c.sidebarText,c.sidebar],[c.sidebarMuted,c.sidebar],[c.accentText,c.accent],[c.accent,c.accentSoft]])assert.ok(contrast(text,bg)>=4.5,`${theme.name} ${text}/${bg}`);}
});
test('theme installation, same-id replacement, invalid import and removal persist independently',()=>{
 const values=new Map<string,string>([['scientific-workspace','original']]);const properties=new Map<string,string>();
 const original={window:globalThis.window,document:globalThis.document,localStorage:globalThis.localStorage};
 Object.assign(globalThis,{window:{addEventListener(){},dispatchEvent(){return true;}},document:{documentElement:{dataset:{},style:{setProperty:(k:string,v:string)=>properties.set(k,v)}}},localStorage:{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value)}});
 try{initializeAppearance();chooseTheme('paper');installTheme(JSON.stringify(minimal));assert.equal(getAppearance().selectedId,'custom:forest');
   installTheme(JSON.stringify({...minimal,name:'森林二版'}));assert.equal(getAppearance().customThemes.length,1);assert.equal(selectedTheme().name,'森林二版');
   const saved=values.get(APPEARANCE_KEY);assert.throws(()=>installTheme('{'),/有效 JSON/);assert.equal(values.get(APPEARANCE_KEY),saved);assert.equal(getAppearance().selectedId,'custom:forest');
   removeTheme('custom:forest');assert.equal(getAppearance().selectedId,'celadon');assert.equal(properties.get('--color-sidebar'),'#ffffff');assert.equal(values.get('scientific-workspace'),'original');
   Object.assign(globalThis,{localStorage:{setItem(){throw new Error('storage unavailable');}}});chooseTheme('polar');assert.equal(getAppearance().selectedId,'polar');assert.match(getAppearance().notice,/未能保存/);
 }finally{Object.assign(globalThis,original);}
});
test('distributed theme template conforms to the same import contract',()=>{const theme=parseTheme(readFileSync(new URL('../public/theme-templates/custom-theme.json',import.meta.url),'utf8'));assert.equal(theme.base,'celadon');assert.deepEqual(resolveTheme(theme).colors,BUILTIN_THEMES[0].colors);});
