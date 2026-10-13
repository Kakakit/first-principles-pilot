import type {StructureDocument} from './structure';

/** A complete estimate only for the deliberately small species:s/p/d/f subset. */
export function simpleProjectionCount(text:string,structure:StructureDocument|undefined,spinors=false):number|null {
 if(!structure)return null;
 text=text.replace(/^\s*begin\s+projections\s*\n([\s\S]*?)\n\s*end\s+projections\s*$/i,'$1');
 let count=0,found=false;
 for(const raw of text.split('\n')){
  const line=raw.split(/[!#]/)[0].trim();if(!line)continue;
  const m=line.match(/^([A-Z][a-z]?)\s*:\s*([spdf])$/);if(!m)return null;
  const sites=structure.atoms.filter(a=>a.element===m[1]).length;if(!sites)return null;
  count+=sites*({s:1,p:3,d:5,f:7}[m[2] as 's'|'p'|'d'|'f']);found=true;
 }
 return found?count*(spinors?2:1):null;
}
