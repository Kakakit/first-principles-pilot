/** Reserved space keeps typing and action controls still during asynchronous feedback. */
export default function Feedback({message='',error='',busy=false,label='正在处理…'}:{message?:string;error?:string;busy?:boolean;label?:string}){
 return <div className="ui-feedback" aria-busy={busy}>{error?<p role="alert">{error}</p>:<p role="status">{busy?label:message||'\u00a0'}</p>}</div>;
}
