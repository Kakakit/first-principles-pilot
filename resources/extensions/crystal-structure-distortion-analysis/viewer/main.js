import {number as fmt,kindName,subscript,imageLabel,siteLabel,ligandLabel,bridgeType,sortSites,bondRows,angleRows,selectBridges,csv} from './model.js';
const $=id=>document.getElementById(id),protocol='fpilot.extension-view.v1',pending=new Map();let seq=0;
window.addEventListener('message',e=>{if(e.source!==parent||e.data?.protocol!==protocol||e.data.kind!=='response')return;const p=pending.get(e.data.id);if(!p)return;pending.delete(e.data.id);e.data.error?p.reject(Error(e.data.error)):p.resolve(e.data.value);});
async function rpc(kind,payload={}){
 if(parent===window){if(kind==='read-file'){const r=await fetch(payload.path);if(!r.ok)throw Error('无法读取冻结数据：'+payload.path);return r.json();}if(kind==='read-state')return {value:{}};if(kind==='save-state')return;}
 const id=String(++seq);return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});parent.postMessage({protocol,kind,id,...payload},'*');});
}
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
function error(e){$('error').hidden=false;$('error').textContent=e.message||String(e);}
function message(s){$('message').textContent=s;}
function download(name,content,media_type='text/csv',encoding='text'){
 if(parent!==window){void rpc('download',{name,content,media_type,encoding}).then(()=>message('已发起下载：'+name)).catch(error);return;}
 const raw=encoding==='base64'?Uint8Array.from(atob(content),c=>c.charCodeAt(0)):content,url=URL.createObjectURL(new Blob([raw],{type:media_type})),a=el('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),4000);
}
let data,sites=[],bridges=[],selected,chosen=[],siteViewer,bridgeViewer,currentBridge,filteredBridges=[],page=0,activeTab='site',saveQueue=Promise.resolve();
const zeros=[0,0,0],add=(a,b)=>a.map((v,i)=>v+b[i]),sub=(a,b)=>a.map((v,i)=>v-b[i]),scale=(v,k)=>v.map(x=>x*k),dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0),norm=v=>Math.hypot(...v),unit=v=>scale(v,1/norm(v)),cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],xyz=v=>({x:v[0],y:v[1],z:v[2]});
const atomColor=e=>({O:'#d54d59',S:'#d1a132',Cr:'#2086a2',Fe:'#dd8744',Co:'#7963ac',Ni:'#539367'}[e]||'#708c9e');
function makeViewer(id){
 try{return window.$3Dmol.createViewer($(id),{backgroundColor:'#edf5f8',antialias:true,keepBuffer:true});}
 catch(e){const host=$(id);host.style.height='auto';host.append(el('p','三维未能初始化，可继续查看全部键长、键角与公式明细。'));message('三维不可用：'+e.message);return null;}
}
function polyhedron(viewer,s,translation=zeros,image=zeros,interactive=false,labelKeys=new Set()){
 const center=add(s.center_cartesian_angstrom,translation),vertices=s.ligand_cartesian_angstrom.map(v=>add(v,translation)),model=viewer.addModel();
 const atoms=[{elem:s.element,...xyz(center),serial:0,bonds:vertices.map((_,i)=>i+1),bondOrder:vertices.map(()=>1)},...vertices.map((v,i)=>({elem:s.ligand,...xyz(v),serial:i+1,bonds:[0],bondOrder:[1]}))];
 model.addAtoms(atoms);model.setStyle({},{sphere:{radius:.18,color:atomColor(s.ligand)},stick:{radius:.045,color:'#8ca6b6'}});model.setStyle({serial:0},{sphere:{radius:.24,color:atomColor(s.element)},stick:{radius:.045,color:'#8ca6b6'}});
 if(interactive){model.setClickable({},true,atom=>{if(atom.serial>0)pick(atom.serial-1);});for(const i of chosen)model.setStyle({serial:i+1},{sphere:{radius:.23,color:'#f1a23c'},stick:{radius:.08,color:'#ce7d20'}});}
 const labels=[[s.site_number,image,center,s.element],...vertices.map((v,i)=>[s.ligand_site_numbers[i],add(s.ligand_images[i],image),v,s.ligand])];
 for(const [id,img,pos,symbol] of labels){const key=id+':'+img.join(',');if(labelKeys.has(key))continue;labelKeys.add(key);viewer.addLabel(symbol+' '+id+(img.some(v=>v)?' ['+img.join(',')+']':''),{position:xyz(pos),fontSize:11,fontColor:'#233e50',backgroundOpacity:.8,backgroundColor:'white',borderThickness:0});}
 const vertexArr=[],normalArr=[],faceArr=[],centroid=scale(vertices.reduce((a,b)=>add(a,b),zeros),1/vertices.length);
 for(const face of s.faces||[]){let points=face.map(i=>vertices[i]),normal=cross(sub(points[1],points[0]),sub(points[2],points[0]));if(dot(normal,sub(points[0],centroid))<0){points=[points[0],points[2],points[1]];normal=scale(normal,-1);}normal=unit(normal);for(const point of points){faceArr.push(vertexArr.length);vertexArr.push(xyz(point));normalArr.push(xyz(normal));}}
 if(vertexArr.length)viewer.addCustom({vertexArr,normalArr,faceArr,color:atomColor(s.element),opacity:.16});
}
function arc(viewer,center,v1,v2,caption){
 const a=unit(v1),b=unit(v2),cos=Math.max(-1,Math.min(1,dot(a,b))),theta=Math.acos(cos);let perpendicular=sub(b,scale(a,cos));
 if(norm(perpendicular)<1e-8)perpendicular=cross(a,Math.abs(a[0])<.8?[1,0,0]:[0,1,0]);perpendicular=unit(perpendicular);
 const radius=Math.min(norm(v1),norm(v2))*.4,points=Array.from({length:25},(_,i)=>xyz(add(center,scale(add(scale(a,Math.cos(theta*i/24)),scale(perpendicular,Math.sin(theta*i/24))),radius))));
 if(theta<1e-8){viewer.addLabel(caption,{position:points[0],fontSize:13,fontColor:'#673f99',backgroundColor:'white'});return;}
 viewer.addCurve({points,radius:.025,color:'#8156b1',smooth:0});viewer.addLabel(caption,{position:points[12],fontSize:13,fontColor:'#673f99',backgroundColor:'white',backgroundOpacity:.9,borderThickness:0});
}
function drawSite(fit=false){
 if(!selected)return;if(!siteViewer)siteViewer=makeViewer('model');if(!siteViewer)return;
 const view=siteViewer.getView();siteViewer.removeAllModels();siteViewer.removeAllShapes();siteViewer.removeAllLabels();polyhedron(siteViewer,selected,zeros,zeros,true);
 if(chosen.length===2){const angle=selected.angles.find(a=>a.ligand_local_indices.every(i=>chosen.includes(i)));arc(siteViewer,selected.center_cartesian_angstrom,...chosen.map(i=>selected.ligand_vectors_angstrom[i]),fmt(angle.angle_degree)+'°');}
 if(fit){siteViewer.zoomTo();siteViewer.zoom(1.45);siteViewer.rotate(24,'y');siteViewer.rotate(12,'x');}else siteViewer.setView(view);siteViewer.render();
}
function pick(i){chosen=chosen.includes(i)?chosen.filter(x=>x!==i):chosen.length===2?[i]:[...chosen,i];selection();drawSite();}
function selection(){
 if(!chosen.length)$('selection').textContent='拖动旋转，滚轮缩放；点击一个配体看键长，点击第二个配体看键角。';
 else if(chosen.length===1){const i=chosen[0];$('selection').textContent=ligandLabel(selected,i)+' · 键长 '+fmt(selected.bond_lengths_angstrom[i])+' Å；再选一个配体查看夹角。';}
 else {const a=selected.angles.find(a=>a.ligand_local_indices.every(i=>chosen.includes(i)));$('selection').textContent=chosen.map(i=>ligandLabel(selected,i)).join(' — '+selected.element+' — ')+' · '+fmt(a.angle_degree)+'° · '+kindName[a.kind];}
 for(const row of $('bonds').tBodies[0].rows)row.classList.toggle('selected',chosen.includes(Number(row.dataset.index)));
 for(const row of $('angles').tBodies[0].rows){const a=selected.angles[Number(row.dataset.index)];row.classList.toggle('selected',chosen.length===2&&a.ligand_local_indices.every(i=>chosen.includes(i)));}
}
function option(host,value,label){const o=el('option',label);o.value=value;host.append(o);}
function renderList(){
 const [key,direction]=$('site-sort').value.split(':'),visible=sortSites(sites,$('type').value,key,direction);$('sites').replaceChildren();$('site-count').textContent=visible.length+' / '+sites.length+' 个中心原子';
 for(const s of visible){const b=el('button',siteLabel(s));b.setAttribute('aria-pressed',String(selected?.site_number===s.site_number));b.onclick=()=>showSite(s);$('sites').append(b);}
 if(visible.length&&!visible.some(s=>s.site_number===selected?.site_number))showSite(visible[0]);
}
function tableRows(id,rows,columns,onClick){
 const body=$(id).tBodies[0];body.replaceChildren();
 for(const r of rows){const tr=el('tr');if(onClick){tr.dataset.click='true';tr.dataset.index=r.index??r.id;tr.tabIndex=0;tr.onclick=()=>onClick(r);tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onClick(r);}};}for(const column of columns){const td=el('td',column(r));tr.append(td);}body.append(tr);}
 if(!rows.length){const tr=el('tr'),td=el('td','当前条件没有记录。可切换类型或清除筛选；本次记录没有被删除。');td.colSpan=columns.length;td.className='empty';tr.append(td);body.append(tr);}
}
function bondChart(){
 const rows=bondRows(selected),max=Math.max(...rows.map(r=>r.length))*1.16,w=650,h=rows.length*36+40,ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.classList.add('chart');svg.setAttribute('role','img');svg.setAttribute('aria-label','配体键长条形图，虚线为平均键长');
 const node=(tag,attrs,text)=>{const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text)e.textContent=text;svg.append(e);return e;};
 for(const r of rows){const y=25+r.index*36;node('text',{x:5,y:y+13,'font-size':12,fill:'#35586b'},selected.ligand+' '+selected.ligand_site_numbers[r.index]+(selected.ligand_images[r.index].some(v=>v)?' ['+selected.ligand_images[r.index].join(',')+']':''));const bar=node('rect',{x:160,y,width:r.length/max*400,height:21,rx:3,fill:'#3898aa'});bar.style.cursor='pointer';bar.onclick=()=>pick(r.index);node('text',{x:168+r.length/max*400,y:y+15,'font-size':12,fill:'#24485b'},fmt(r.length)+' Å');}
 const x=160+selected.mean_bond_length_angstrom/max*400;node('line',{x1:x,x2:x,y1:14,y2:h-16,stroke:'#b16d20','stroke-dasharray':'5 4','stroke-width':2});node('text',{x:160,y:12,'font-size':11,fill:'#8b622a'},'虚线：平均 '+fmt(selected.mean_bond_length_angstrom)+' Å');$('bond-chart').replaceChildren(svg);
}
function renderAngles(){const rows=angleRows(selected,$('angle-kind').value,$('angle-sort').value);$('angle-count').textContent=rows.length+' / '+selected.angles.length+' 个内部角。四面体为 6 角；八面体为 12 顺式角＋3 对向角。';tableRows('angles',rows,[r=>r.label,r=>kindName[r.kind],r=>fmt(r.angle_degree),r=>fmt(r.reference_degree),r=>fmt(r.angle_degree-r.reference_degree)],r=>{chosen=[...r.ligand_local_indices];selection();drawSite();});selection();}
function showSite(s){
 selected=s;chosen=[];$('site-title').textContent=siteLabel(s);$('stats').replaceChildren();
 for(const o of $('angle-kind').options)o.disabled=(s.coordination===4&&['cis','trans'].includes(o.value))||(s.coordination===6&&o.value==='tetrahedral');
 if($('angle-kind').selectedOptions[0]?.disabled)$('angle-kind').value='';
 for(const key of ['mean_bond_length_angstrom','bond_length_distortion_delta','quadratic_elongation','angle_variance_degree2','q_jt_angstrom']){
  if(s[key]===undefined)continue;const m=data.metrics.find(m=>m.key===key),box=el('div');box.className='stat';box.append(el('span',m.label),el('strong',fmt(s[key])),el('small',m.unit));$('stats').append(box);
 }
 $('references').textContent='本位点：n='+s.coordination+'；参考角集合 m='+s.angle_count+'；平均键长 '+fmt(s.mean_bond_length_angstrom)+' Å；等体积理想键长 '+fmt(s.equal_volume_ideal_bond_length_angstrom)+' Å。';
 $('site-warnings').replaceChildren();for(const w of s.site_warnings||[]){const p=el('p',w);p.className='warning';$('site-warnings').append(p);}
 tableRows('bonds',bondRows(s),[r=>r.label,r=>fmt(r.length),r=>fmt(r.deviation)],r=>pick(r.index));bondChart();renderAngles();renderMetrics();selection();renderList();drawSite(true);void save();
}
function renderMetrics(){
 $('metrics').replaceChildren();for(const m of data.metrics){if(selected[m.key]===undefined)continue;const d=el('details');d.className='formula';d.append(el('summary',m.label+' · '+fmt(selected[m.key])+' '+m.unit));const svg=el('div');svg.innerHTML=data.formulas[m.key]||'';d.append(svg,el('code',m.formula),el('p',m.meaning),el('p','参考：'+m.reference));
 let inputs='dᵢ 为本位点逐条键长；n='+selected.coordination+'，d̄='+fmt(selected.mean_bond_length_angstrom)+' Å，d₀='+fmt(selected.equal_volume_ideal_bond_length_angstrom)+' Å；m='+selected.angle_count+'，θ₀='+fmt(selected.ideal_angle_degree)+'°。';
 if(m.key.startsWith('q'))inputs+=' 三组对向键均值 l₁、l₂、l₃ = '+['lx_angstrom','ly_angstrom','lz_angstrom'].map(k=>fmt(selected[k])).join('、')+' Å（按长度排序）。';d.append(el('p',inputs));$('metrics').append(d);
 }
}
function bridgeLabel(b){return b.element_i+' 第 '+b.center_i+' 号 — '+b.ligand+' 第 '+b.ligand_site_number+' 号'+imageLabel(b.ligand_image)+' — '+b.element_j+' 第 '+b.center_j+' 号'+imageLabel(b.center_j_image);}
function renderBridges(){
 filteredBridges=selectBridges(bridges,$('bridge-type').value,$('bridge-center').value,$('bridge-sort').value);page=Math.min(page,Math.max(0,Math.ceil(filteredBridges.length/100)-1));$('bridge-count').textContent=filteredBridges.length+' / '+bridges.length+' 条独立桥键角';$('page').textContent='第 '+(page+1)+' / '+Math.max(1,Math.ceil(filteredBridges.length/100))+' 页';$('previous').disabled=page===0;$('next').disabled=(page+1)*100>=filteredBridges.length;
 tableRows('bridges',filteredBridges.slice(page*100,page*100+100),[bridgeLabel,r=>fmt(r.angle_degree),r=>fmt(r.length_i_angstrom)+' / '+fmt(r.length_j_angstrom),r=>fmt(r.center_distance_angstrom)],r=>showBridge(r,true));
 if(!filteredBridges.some(b=>b.id===currentBridge?.id))currentBridge=filteredBridges[0];
 if(currentBridge&&activeTab==='bridge')showBridge(currentBridge,true);else if(!filteredBridges.length){$('bridge-selected').textContent='当前没有共享配体桥角。确认已填写两端需要的多面体类型，或清除筛选；程序不会额外猜测配位。';bridgeViewer?.removeAllModels();bridgeViewer?.removeAllShapes();bridgeViewer?.removeAllLabels();bridgeViewer?.render();}void save();
}
function showBridge(b,fit=false){
 currentBridge=b;$('bridge-selected').textContent=bridgeLabel(b)+' · '+fmt(b.angle_degree)+'°';for(const r of $('bridges').tBodies[0].rows)r.classList.toggle('selected',Number(r.dataset.index)===b.id);
 if(!bridgeViewer)bridgeViewer=makeViewer('bridge-model');if(!bridgeViewer)return;
 const view=bridgeViewer.getView();bridgeViewer.removeAllModels();bridgeViewer.removeAllShapes();bridgeViewer.removeAllLabels();const a=sites.find(s=>s.site_number===b.center_i),c=sites.find(s=>s.site_number===b.center_j),labels=new Set();
 polyhedron(bridgeViewer,a,zeros,zeros,false,labels);polyhedron(bridgeViewer,c,sub(b.center_j_cartesian_angstrom,c.center_cartesian_angstrom),b.center_j_image,false,labels);
 for(const pos of [b.center_i_cartesian_angstrom,b.center_j_cartesian_angstrom])bridgeViewer.addCylinder({start:xyz(b.ligand_cartesian_angstrom),end:xyz(pos),radius:.085,color:'#c17c24',fromCap:1,toCap:1});
 arc(bridgeViewer,b.ligand_cartesian_angstrom,sub(b.center_i_cartesian_angstrom,b.ligand_cartesian_angstrom),sub(b.center_j_cartesian_angstrom,b.ligand_cartesian_angstrom),fmt(b.angle_degree)+'°');
 if(fit){bridgeViewer.zoomTo();bridgeViewer.rotate(20,'y');bridgeViewer.rotate(12,'x');}else bridgeViewer.setView(view);bridgeViewer.render();
}
function tab(name){activeTab=name;$('site-view').hidden=name!=='site';$('bridge-view').hidden=name!=='bridge';$('site-tab').setAttribute('aria-pressed',String(name==='site'));$('bridge-tab').setAttribute('aria-pressed',String(name==='bridge'));if(name==='bridge')renderBridges();else {siteViewer?.resize();drawSite();}bridgeViewer?.resize();void save();}
function save(){if(!data)return Promise.resolve();const value={selected_site:selected?.site_number,type:$('type').value,sort:$('site-sort').value,tab:activeTab,bridge_type:$('bridge-type').value,bridge_center:$('bridge-center').value,bridge_sort:$('bridge-sort').value};saveQueue=saveQueue.then(()=>rpc('save-state',{value})).catch(e=>message('当前设置未保存：'+e.message));return saveQueue;}
function png(viewer,name){if(!viewer){message('三维不可用，仍可导出全部明细。');return;}viewer.render();download(name,viewer.pngURI().split(',')[1],'image/png','base64');}
async function start(){
 data=await rpc('read-file',{path:'data.json'});if(data.detail_schema!=='fpilot.polyhedra-geometry.v1')throw Error('不支持的几何格式');sites=(await Promise.all(data.site_parts.map(path=>rpc('read-file',{path})))).flat();bridges=(await Promise.all(data.bridge_parts.map(path=>rpc('read-file',{path})))).flat();
 $('overview').textContent=data.structure.formula+' · '+sites.length+' 个中心 · '+bridges.length+' 条桥键角 · 原子编号从 1 开始；距离 Å，角度 °。';for(const w of data.warnings){const p=el('p',w);p.className='warning';$('warnings').append(p);}
 for(const type of [...new Set(sites.map(s=>s.polyhedron))])option($('type'),type,subscript(type));for(const type of [...new Set(bridges.map(bridgeType))].sort())option($('bridge-type'),type,type);for(const s of sites)option($('bridge-center'),String(s.site_number),siteLabel(s));
 const state=(await rpc('read-state')).value||{};for(const [id,key] of [['type','type'],['site-sort','sort'],['bridge-type','bridge_type'],['bridge-center','bridge_center'],['bridge-sort','bridge_sort']])if([...$(id).options].some(o=>o.value===state[key]))$(id).value=state[key];
 selected=sites.find(s=>s.site_number===state.selected_site)||sites[0];showSite(selected);if(state.tab==='bridge')tab('bridge');
 $('type').onchange=()=>{renderList();void save();};$('site-sort').onchange=()=>{renderList();void save();};$('angle-kind').onchange=renderAngles;$('angle-sort').onchange=renderAngles;
 for(const id of ['bridge-type','bridge-center','bridge-sort'])$(id).onchange=()=>{page=0;renderBridges();};$('site-tab').onclick=()=>tab('site');$('bridge-tab').onclick=()=>tab('bridge');
 $('previous').onclick=()=>{page--;renderBridges();};$('next').onclick=()=>{page++;renderBridges();};$('reset-model').onclick=()=>{chosen=[];selection();drawSite(true);};$('bridge-reset').onclick=()=>{if(currentBridge)showBridge(currentBridge,true);};
 $('png').onclick=()=>png(siteViewer,'多面体-'+selected.element+'-'+selected.site_number+'.png');$('bridge-png').onclick=()=>png(bridgeViewer,'共享配体桥角.png');
 $('bond-csv').onclick=()=>download('逐键长度.csv',csv([['label','配体原子与镜像'],['length','键长 / Å'],['deviation','相对平均键长偏差 / %']],bondRows(selected).map(r=>({...r,label:siteLabel(selected)+' — '+r.label}))));
 $('angle-csv').onclick=()=>download('内部键角.csv',csv([['label','配体—中心—配体（含镜像）'],['kind_label','角类型'],['angle_degree','键角 / °'],['reference_degree','参考角 / °']],angleRows(selected,$('angle-kind').value,$('angle-sort').value).map(r=>({...r,kind_label:kindName[r.kind]}))));
 $('bridge-csv').onclick=()=>download('共享配体桥角.csv',csv([['label','中心—配体—中心（含镜像）'],['angle_degree','桥角 / °'],['length_i_angstrom','起点键长 / Å'],['length_j_angstrom','终点键长 / Å'],['center_distance_angstrom','中心间距 / Å']],filteredBridges.map(b=>({...b,label:bridgeLabel(b)}))));
 $('json').onclick=()=>download('多面体完整几何.json',JSON.stringify({...data,site_results:sites,bridges},null,2),'application/json');
 for(const [id,getViewer] of [['model',()=>siteViewer],['bridge-model',()=>bridgeViewer]]){
  $(id).tabIndex=0;$(id).setAttribute('aria-description','方向键旋转，+ 和 - 缩放；也可拖动旋转、滚轮缩放');
  $(id).onkeydown=e=>{const v=getViewer();if(!v)return;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();v.rotate(e.key==='ArrowLeft'||e.key==='ArrowDown'?-15:15,e.key==='ArrowLeft'||e.key==='ArrowRight'?'y':'x');v.render();}else if(e.key==='+'||e.key==='-'){e.preventDefault();v.zoom(e.key==='+'?1.15:1/1.15);v.render();}};
 }
 new ResizeObserver(()=>{siteViewer?.resize();bridgeViewer?.resize();if(parent!==window)parent.postMessage({protocol,kind:'resize',height:document.documentElement.scrollHeight},'*');}).observe(document.body);
 message('使用冻结几何在本页交互；筛选与旋转不连接服务器。');
}
start().catch(error);
