// Snapshots are for display while the active page revalidates its own record.
export function recordPresentation<T>() {
  type Entry={project:string;node:string;id:string;value:T};
  const entries:Entry[]=[];
  const clear=()=>{entries.length=0;};
  if(typeof window!=='undefined')window.addEventListener('fpilot:history-changed',clear);
  function forget(project:string,node:string,id?:string){
    for(let i=entries.length-1;i>=0;i--){
      const entry=entries[i];
      if(entry.project===project&&entry.node===node&&(!id||entry.id===id))entries.splice(i,1);
    }
  }
  return {
    read(project:string,node:string,id?:string){
      return entries.find(entry=>entry.project===project&&entry.node===node&&(!id||entry.id===id))?.value;
    },
    save(project:string,node:string,id:string,value:T){
      forget(project,node,id);
      entries.unshift({project,node,id,value});
      if(entries.length>12)entries.pop();
    },
    forget
  };
}
