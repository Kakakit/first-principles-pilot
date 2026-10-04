import ExtensionPanel from './ExtensionPanel';
export default function SymmetryAnalysis({poscar,sourceSha,projectId}:{poscar:string;sourceSha?:string;projectId?:string}){return <ExtensionPanel projectId={projectId} source={{kind:'structure',poscar,name:'当前结构',original_sha256:sourceSha}} title="结构分析"/>;}
