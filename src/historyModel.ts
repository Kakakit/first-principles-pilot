export type HistoryRecord = {id:string;state?:string};
export function canRecycle(_state?:string,_kind='attempts') {return true;}
function needsAttention(state:string,kind:string){return !(kind==='chains'||kind==='scans'?['FROZEN','COMPLETE']:['COMPLETED','FAILED','NOT_APPLICABLE','PREPARED']).includes(state);}
export function compactHistory<T extends HistoryRecord>(rows:T[],limit:number,pinnedId?:string,kind='attempts') {
 const keep=new Set(rows.slice(0,limit).map(r=>r.id));
 for(const row of rows)if(row.id===pinnedId||(row.state&&needsAttention(row.state,kind)))keep.add(row.id);
 return rows.filter(r=>keep.has(r.id));
}
