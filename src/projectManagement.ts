import {newProject} from './model';
import type {Workspace} from './model';
export function withoutProject(workspace:Workspace,id:string):Workspace {
 if(!workspace.projects.some(p=>p.id===id))throw new Error('项目已不在当前工作区，请刷新后重试');
 const projects=workspace.projects.filter(p=>p.id!==id);
 if(!projects.length)projects.push(newProject('新项目'));
 return {projects,activeId:projects.some(p=>p.id===workspace.activeId)?workspace.activeId:projects[0].id};
}
