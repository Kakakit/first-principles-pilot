export type ScanCase={id?:string;index:number;state:string;attempt_id?:string;parameters:{key:string;value:string}[]};
export const CASE_PAGE_SIZE=10;
export function casePage(cases:ScanCase[],query:string,state:string,page:number){
 const q=query.trim().toLocaleLowerCase(),filtered=cases.filter(c=>(!state||c.state===state)&&(!q||[`case ${c.index+1}`,...c.parameters.map(p=>`${p.key}=${p.value}`)].join(' ').toLocaleLowerCase().includes(q)));
 const pages=Math.max(1,Math.ceil(filtered.length/CASE_PAGE_SIZE)),index=Math.max(0,Math.min(page,pages-1));
 return {items:filtered.slice(index*CASE_PAGE_SIZE,(index+1)*CASE_PAGE_SIZE),total:filtered.length,pages,page:index};
}
