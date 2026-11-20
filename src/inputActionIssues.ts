import type {ActionIssue} from './actionIssues';
export function inputReportIssues(report:{errors:string[];issues?:ActionIssue[]},projectId:string,nodeId:string):ActionIssue[]{
 return report.issues?.length?[...report.issues]:report.errors.map(message=>({code:'input.legacy',message,target:{projectId,nodeId,page:'config',subpage:'files',field:'input.preview'}}));
}
export function prepareIssues({projectId,nodeId,bundle,alias,node,legacySource}:{projectId:string;nodeId:string;bundle:string;alias?:string;node:string;legacySource?:boolean}):ActionIssue[]{
 const issues:ActionIssue[]=[];
 if(!bundle)issues.push({code:'run.input',message:'尚未选择已冻结的输入包',label:'去审查并冻结输入',target:{page:'config',projectId,nodeId,subpage:'files',field:'input.preview'}});
 if(!alias)issues.push({code:'run.server',message:'尚未选择可用服务器',label:'去设置服务器',target:{page:'settings',section:'server',field:'settings.server'}});
 if(!node)issues.push({code:'run.node',message:'尚未选择本次计算节点',label:'去选择节点',target:{page:'run',projectId,nodeId,field:'run.node'}});
 if(legacySource)issues.push({code:'run.legacy-source',message:'旧冻结输入没有来源合同，请选择来源并重新冻结',target:{page:'config',projectId,nodeId,subpage:'source',field:'input.source'}});
 return issues;
}
