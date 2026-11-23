import {ChevronDown} from 'lucide-react';
import type {ReactNode} from 'react';

export default function DisclosureLabel({title,children}:{title?:string;children?:ReactNode}){
 return <summary className="disclosure-heading">
  <span>{children ?? title}</span>
  <span className="disclosure-action" aria-hidden="true">
   <span className="when-closed">展开</span><span className="when-open">收起</span><ChevronDown size={15}/>
  </span>
 </summary>;
}
