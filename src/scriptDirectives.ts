// Only edit an unambiguous, literal directive in the scheduler header.
const slurmFields=[
 ['任务名称',/^([ \t]*#SBATCH[ \t]+(?:--job-name(?:=|[ \t]+)|-J[ \t]+))([\w.-]+)([ \t]*)$/],
 ['MPI 进程数',/^([ \t]*#SBATCH[ \t]+(?:--ntasks(?:=|[ \t]+)|-n[ \t]+))([1-9]\d*)([ \t]*)$/],
 ['每进程 CPU 核数',/^([ \t]*#SBATCH[ \t]+(?:--cpus-per-task(?:=|[ \t]+)|-c[ \t]+))([1-9]\d*)([ \t]*)$/],
 ['GPU 数量',/^([ \t]*#SBATCH[ \t]+--gpus(?:=|[ \t]+))([1-9]\d*)([ \t]*)$/],
] as const;
export function literalSlurmFields(body:string){
 const lines=body.split(/\r?\n/);
 const end=lines.findIndex(line=>line.trim()&&!line.trimStart().startsWith('#'));
 const header=end===-1?lines:lines.slice(0,end);
 return slurmFields.flatMap(([label,pattern])=>{
  const matches=header.flatMap((line,index)=>{const match=pattern.exec(line);return match?[{label,index,value:match[2],prefix:match[1],suffix:match[3]}]:[];});
  return matches.length===1?matches:[];
 });
}
