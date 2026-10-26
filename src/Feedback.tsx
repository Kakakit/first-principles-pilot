/** Keep feedback local to the action; idle forms do not reserve an empty panel. */
export default function Feedback({message='',error='',busy=false,label='正在处理…'}:{message?:string;error?:string;busy?:boolean;label?:string}){
 return <div className="ui-feedback" aria-busy={busy}>{error?<p role="alert">{error}</p>:(busy||message)?<p role="status">{busy?label:message}</p>:null}</div>;
}
