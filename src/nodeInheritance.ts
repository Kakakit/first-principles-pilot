import type {Project} from './model';
export type SourcePlanSummary={files:string[];has_parent?:boolean;structure_file?:string|null;parent_id?:string|null;mode?:string;electronic_usage?:string;error?:string};
export function sourceSummaryGraph(project:Project){return {id:project.id,nodes:project.nodes.map(n=>({id:n.id,data:{kind:n.data.kind,scanSpec:n.data.scanSpec,sourceDraft:n.data.sourceDraft}})),edges:project.edges.map(e=>({source:e.source,target:e.target}))};}
export function nodeInheritance(plan:SourcePlanSummary|undefined){
 return {text:plan?.error?'继承：需检查':plan?.files.length?'继承：'+plan.files.join('、'):'',title:plan?.error||''};
}
