import {useEffect,useRef} from 'react';
import {ArrowLeft,ArrowRight,ChevronDown} from 'lucide-react';
import type {Visit} from './navigationHistory';
import type {TaskNavigation} from './taskNavigation';

type Props={nav:{entry:Visit;back?:Visit;forward?:Visit;recent:Visit[];goBack:()=>void;goForward:()=>void};
 describe:(entry:Visit)=>string;onOpen:(route:TaskNavigation)=>void};

export default function NavigationBar({nav,describe,onOpen}:Props){
 const menu=useRef<HTMLDetailsElement>(null);
 useEffect(()=>{if(menu.current)menu.current.open=false;},[nav.entry.id]);
 return <div className="page-navigation" aria-label="页面历史">
  <div className="page-history-buttons">
   <button className="icon-btn" aria-label="后退" title={nav.back?'后退到 '+describe(nav.back):'没有上一页'} disabled={!nav.back} onClick={nav.goBack}><ArrowLeft size={16}/></button>
   <button className="icon-btn" aria-label="前进" title={nav.forward?'前进到 '+describe(nav.forward):'没有下一页'} disabled={!nav.forward} onClick={nav.goForward}><ArrowRight size={16}/></button>
   <details className="recent-visits" ref={menu} onKeyDown={event=>{if(event.key==='Escape')event.currentTarget.open=false;}}>
    <summary>最近访问<ChevronDown size={14}/></summary>
    <div>{nav.recent.length?nav.recent.map(visit=><button key={visit.id} onClick={()=>{if(menu.current)menu.current.open=false;onOpen(visit.route);}}>{describe(visit)}</button>):<p>访问其他页面后会显示在这里。</p>}</div>
   </details>
  </div>
  <strong className="current-page-label" title={describe(nav.entry)}>{describe(nav.entry)}</strong>
 </div>;
}
