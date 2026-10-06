import Form from '@rjsf/core';
import validator from '@rjsf/validator-ajv8';
import type {ExtensionManifest} from './extensionModel';
import {formAvailability} from './extensionModel';
export default function ExtensionForm({manifest,value,onChange,onSubmit,onCheck,checkResult,busy,blocked=false,running,label,schema=manifest.options_schema}:{manifest:ExtensionManifest;value:Record<string,unknown>;onChange:(v:Record<string,unknown>)=>void;onSubmit:()=>void;onCheck:()=>void;checkResult:{status:string;reason:string}|null;busy:boolean;blocked?:boolean;running:boolean;label:string;schema?:Record<string,any>}){
 const state=formAvailability(busy,blocked,running);
 const uiSchema=structuredClone(manifest.ui_schema);
 for(const [key,ui] of Object.entries(uiSchema)){if(ui?.['ui:options']?.source_enum&&!schema.properties?.[key]?.items?.enum)delete ui['ui:widget'];}
 return <Form schema={schema} uiSchema={uiSchema} validator={validator} formData={value} disabled={state.editingDisabled} onChange={event=>onChange(event.formData||{})} onSubmit={()=>{if(!state.submitDisabled)onSubmit();}} transformErrors={errors=>errors.map(e=>({...e,message:e.name==='required'?'请填写此项':e.name==='minimum'?`数值不能小于 ${e.params.limit}`:e.name==='exclusiveMinimum'?`数值须大于 ${e.params.limit}`:e.name==='maximum'?`数值不能大于 ${e.params.limit}`:e.message}))} noHtml5Validate liveValidate showErrorList={false} focusOnFirstError><h4>3. 适用性与运行</h4><div className="extension-check-actions"><button className="button" type="button" disabled={state.actionsDisabled} onClick={onCheck}>检查适用性</button>{checkResult&&<p role="status">{checkResult.status==='APPLICABLE'?'适用':checkResult.status==='PENDING_REMOTE'?'待远端内容检查':'不适用'}：{checkResult.reason}</p>}</div><button className="button primary" type="submit" disabled={state.submitDisabled}>{running?'当前分析执行中…':label}</button></Form>;
}
