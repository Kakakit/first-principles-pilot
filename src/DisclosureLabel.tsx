import {ChevronDown} from 'lucide-react';

export default function DisclosureLabel({title}:{title:string}){
 return <summary>
  <span>{title}</span>
  <span className="disclosure-action" aria-hidden="true">
   <span className="when-closed">展开</span><span className="when-open">收起</span><ChevronDown size={15}/>
  </span>
 </summary>;
}
