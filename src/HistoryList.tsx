import {useEffect,useState} from 'react';
import type {ReactNode} from 'react';
import {compactHistory} from './historyModel';
import type {HistoryRecord} from './historyModel';
export default function HistoryList<T extends HistoryRecord>({rows,render,limit,pinnedId,context='',kind='attempts'}:{rows:T[];render:(row:T,index:number)=>ReactNode;limit?:number;pinnedId?:string;context?:string;kind?:string}) {
 const [expanded,setExpanded]=useState(false);
 useEffect(()=>setExpanded(false),[context]);
 const chosenLimit=limit??Number(localStorage.getItem('fpilot-history-limit')||5);
 const compact=compactHistory(rows,chosenLimit,pinnedId,kind),visible=expanded?rows:compact;
 return <div className="history-list"><div className="history-rows">{visible.map(render)}</div>{rows.length>compact.length&&<button className="text-button history-more" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded?'收起历史记录':`查看更多（另 ${rows.length-compact.length} 条）`}</button>}</div>;
}
