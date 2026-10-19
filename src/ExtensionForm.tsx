import Form, {getDefaultRegistry} from '@rjsf/core';
import validator from '@rjsf/validator-ajv8';
import type {FieldTemplateProps,ObjectFieldTemplateProps,IconButtonProps,UiSchema} from '@rjsf/utils';
import type {ExtensionManifest} from './extensionModel';
import {formAvailability} from './extensionModel';
const DefaultObject=getDefaultRegistry().templates.ObjectFieldTemplate;
function ObjectLayout(props:ObjectFieldTemplateProps){
 const groups=props.uiSchema?.['ui:groups'] as Array<{title:string;fields:string[];collapsed?:boolean}>|undefined;
 if(!groups)return <DefaultObject {...props}/>;
 const used=new Set(groups.flatMap(g=>g.fields));
 return <>{groups.map(group=>{const fields=group.fields.flatMap(name=>props.properties.filter(p=>p.name===name));return group.collapsed?<details className="extension-advanced" key={group.title}><summary>{group.title}</summary>{fields.map(p=>p.content)}</details>:<section className="extension-common" aria-label={group.title} key={group.title}>{fields.map(p=>p.content)}</section>;})}{props.properties.filter(p=>!used.has(p.name)).map(p=>p.content)}</>;
}
function FieldLayout({id,classNames,hidden,label,required,displayLabel,children,description,rawDescription,errors,help,schema}:FieldTemplateProps){
 if(hidden)return <div hidden>{children}</div>;
 return <div className={classNames+' extension-form-field'}>{displayLabel&&schema.type!=='boolean'&&<label className="control-label" htmlFor={id}>{label}{required?' *':''}</label>}<div className={rawDescription?'extension-field-explained':''}><div>{children}{errors}{help}</div>{rawDescription&&<aside id={id+'__description'}>{description}</aside>}</div></div>;
}
function TextButton({icon,iconType,className,uiSchema,registry,children,...props}:IconButtonProps){
 const label=({'plus':'添加','remove':'删除','arrow-up':'上移','arrow-down':'下移','copy':'复制'} as Record<string,string>)[String(icon)]||String(props.title||'操作');
 return <button {...props} className={'button extension-array-action '+(className||'')} type="button" title={label} aria-label={label}>{label}</button>;
}
const action=(icon:string)=>(props:IconButtonProps)=><TextButton {...props} icon={icon}/>;
export default function ExtensionForm({manifest,value,onChange,onSubmit,onCheck,checkResult,busy,blocked=false,running,label,schema=manifest.options_schema,sourceSpinors=false}:{manifest:ExtensionManifest;value:Record<string,unknown>;onChange:(v:Record<string,unknown>)=>void;onSubmit:()=>void;onCheck:()=>void;checkResult:{status:string;reason:string}|null;busy:boolean;blocked?:boolean;running:boolean;label:string;schema?:Record<string,any>;sourceSpinors?:boolean}){
 const state=formAvailability(busy,blocked,running);
 const uiSchema:UiSchema=structuredClone(manifest.ui_schema);
 for(const [key,ui] of Object.entries(uiSchema)){if(ui?.['ui:options']?.source_enum&&!schema.properties?.[key]?.items?.enum)delete ui['ui:widget'];}
 for(const [name,field] of Object.entries(schema.properties||{}))if((field as any).type==='boolean'&&!uiSchema[name]?.['ui:widget'])uiSchema[name]={...(uiSchema[name]||{}),'ui:widget':'checkbox'};
 const visibility=uiSchema['ui:visibility'] as Record<string,{field:string;equals:unknown;source_spinors?:boolean}>|undefined;
 for(const [name,rule] of Object.entries(visibility||{})){
  const visible=value[rule.field]===rule.equals||(rule.source_spinors&&value[rule.field]==='auto'&&sourceSpinors);
  if(!visible)uiSchema[name]={...(uiSchema[name]||{}),'ui:widget':'hidden','ui:options':{...(uiSchema[name]?.['ui:options']||{}),label:false}};
 }
 return <Form schema={schema} uiSchema={uiSchema} validator={validator} formData={value} disabled={state.editingDisabled} experimental_defaultFormStateBehavior={{arrayMinItems:{populate:'never'}}} templates={{ObjectFieldTemplate:ObjectLayout,FieldTemplate:FieldLayout,ArrayFieldDescriptionTemplate:()=>null,ButtonTemplates:{AddButton:action('plus'),RemoveButton:action('remove'),MoveUpButton:action('arrow-up'),MoveDownButton:action('arrow-down'),CopyButton:action('copy')}}} onChange={event=>onChange(event.formData||{})} onSubmit={()=>{if(!state.submitDisabled)onSubmit();}} transformErrors={errors=>errors.map(e=>({...e,message:e.name==='required'?'请填写此项':e.name==='minimum'?`数值不能小于 ${e.params.limit}`:e.name==='exclusiveMinimum'?`数值须大于 ${e.params.limit}`:e.name==='maximum'?`数值不能大于 ${e.params.limit}`:e.message}))} noHtml5Validate liveValidate showErrorList={false} focusOnFirstError><h4>3. 适用性与运行</h4><div className="extension-check-actions"><button className="button" type="button" disabled={state.actionsDisabled} onClick={onCheck}>检查适用性</button>{checkResult&&<p role="status">{checkResult.status==='APPLICABLE'?'适用':checkResult.status==='PENDING_REMOTE'?'待远端内容检查':'不适用'}：{checkResult.reason}</p>}</div><button className="button primary" type="submit" disabled={state.submitDisabled}>{running?'当前分析执行中…':label}</button></Form>;
}
