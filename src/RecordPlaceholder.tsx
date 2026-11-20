export default function RecordPlaceholder({label}:{label:string}){
 return <div className="record-placeholder" role="status" aria-label={label}>
   <span aria-hidden="true"/><span aria-hidden="true"/><span aria-hidden="true"/>
 </div>;
}
