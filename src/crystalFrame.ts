// 3Dmol owns window/body listeners. A separate browsing context bounds their lifetime.
import * as mol from '3dmol';
import type {StructureDocument} from './structure';
const holder=document.getElementById('viewer')!;
const viewer=mol.createViewer(holder,{antialias:true});
let wheelZoom=false;
holder.addEventListener('wheel',event=>{if(!wheelZoom)event.stopImmediatePropagation();},{capture:true,passive:true});
const send=(value:unknown)=>parent.postMessage(value,location.origin);
window.addEventListener('message',event=>{
 if(event.source!==parent||event.origin!==location.origin)return;
 const message=event.data;
 try{
  if(message.type==='crystal-structure'){
   const structure=message.structure as Pick<StructureDocument,'atoms'|'lattice'|'lengths'>;
   viewer.clear();viewer.setBackgroundColor(message.background,1);
   viewer.addModel().addAtoms(structure.atoms.map((a,i)=>({elem:a.element,x:a.cartesian[0],y:a.cartesian[1],z:a.cartesian[2],serial:i+1,bonds:[],bondOrder:[]})));
   viewer.setStyle({},{sphere:{scale:0.28,colorscheme:'Jmol'}});
   const point=(bits:number)=>{const p=[0,0,0];structure.lattice.forEach((axis,j)=>{if(bits&(1<<j))axis.forEach((x,k)=>p[k]+=x);});return {x:p[0],y:p[1],z:p[2]};};
   for(let i=0;i<8;i++)for(let j=0;j<3;j++)if(!(i&(1<<j)))viewer.addLine({start:point(i),end:point(i|(1<<j)),color:'#587985'});
   const axes=['#c43d45','#218457','#346dcc'],radius=Math.min(...structure.lengths)*0.012;
   structure.lattice.forEach((axis,j)=>{viewer.addArrow({start:{x:0,y:0,z:0},end:{x:axis[0],y:axis[1],z:axis[2]},radius,color:axes[j]});viewer.addLabel(['a','b','c'][j],{position:{x:axis[0]*1.08,y:axis[1]*1.08,z:axis[2]*1.08},fontColor:axes[j],backgroundOpacity:0,fontSize:18});});
   viewer.zoomTo();viewer.render();
   send({type:'crystal-colors',colors:Object.fromEntries([...new Set(structure.atoms.map(a=>a.element))].map(e=>[e,`#${mol.CC.color(mol.elementColors.Jmol[e]??0xff1493).getHex().toString(16).padStart(6,'0')}`]))});
  }else if(message.type==='crystal-theme'){viewer.setBackgroundColor(message.background,1);viewer.render();}
  else if(message.type==='crystal-wheel')wheelZoom=Boolean(message.enabled);
  else if(message.type==='crystal-reset'){viewer.zoomTo();viewer.render();}
 }catch{send({type:'crystal-error',message:'三维显示未能启动；下方晶胞和坐标仍可查看。'});}
});
window.addEventListener('pagehide',()=>{viewer.clear();for(const canvas of holder.querySelectorAll('canvas')){const gl=canvas.getContext('webgl2')||canvas.getContext('webgl');gl?.getExtension('WEBGL_lose_context')?.loseContext();}},{once:true});
send({type:'crystal-ready'});
