import type {Project} from './model';
import ProjectRemoteDirectories from './ProjectRemoteDirectories';
import {useSubmissionDraft} from './useSubmissionDraft';

export default function ProjectProperties({project,canEdit,slugLocked,defaultRoot,isSlugTaken,onSave}:{
 project:Project;canEdit:boolean;slugLocked:boolean;defaultRoot:string;isSlugTaken:(value:string)=>boolean;onSave:(patch:Partial<Project>)=>void;
}){
 const [draft,setDraft]=useSubmissionDraft<string|null>('properties:'+project.id+':name',null);
 const name=draft??project.name;
 return <div className="project-properties">
  <header><h2>项目属性 · {project.name}</h2><p>仅作用于此项目</p></header>
  <section className="input-submit-card">
   <h3>基本信息</h3>
   <form onSubmit={event=>{event.preventDefault();if(canEdit&&name.trim()){onSave({name:name.trim()});setDraft(null);}}}>
    <label>项目名称<input disabled={!canEdit} value={name} maxLength={120} onChange={e=>setDraft(e.target.value===project.name?null:e.target.value)}/></label>
    <div className="action-row"><button className="button primary" disabled={!canEdit||name===project.name||!name.trim()}>保存基本信息</button>
    {name!==project.name&&<><button type="button" className="button" onClick={()=>setDraft(null)}>放弃修改</button><span role="status">有未保存修改</span></>}</div>
   </form>
  </section>
  <ProjectRemoteDirectories project={project} canEdit={canEdit} slugLocked={slugLocked} defaultRoot={defaultRoot} isSlugTaken={isSlugTaken} onSave={onSave}/>
 </div>;
}
