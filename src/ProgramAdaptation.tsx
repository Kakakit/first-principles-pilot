export type ProgramChange={source_program:string;target_program:string;target_command:string};
export default function ProgramAdaptation({change,diff}:{change?:ProgramChange|null;diff?:string}){
 if(!change)return null;
 return <><p>本次程序：{change.source_program} → {change.target_program}，使用同目录程序。原始脚本保留。</p>{diff&&<details><summary>本次程序切换差异</summary><pre className="input-text">{diff}</pre></details>}</>;
}
