// Form edits cover ordinary scalar assignments. Quoted blocks and continuations
// stay in the full-file editor, where their original spelling is preserved.
export function simpleIncar(text:string){
 if(/["{}\\]/.test(text.replace(/[#!][^\n]*/g,'')))return null;
 const values:Record<string,string>={};
 for(const line of text.split(/\r?\n/)){
  for(const part of line.split(/[#!]/,1)[0].split(';')){
   const match=part.match(/^\s*([\w/]+)\s*=\s*(.*?)\s*$/);
   if(match)values[match[1].toUpperCase()]=match[2];
  }
 }
 return values;
}

export function changeAssignment(text:string,tag:string,value:string|null){
 const name=tag==='XC_FUNCTIONAL'?'GGA':tag;
 const written=value==='true'?'.TRUE.':value==='false'?'.FALSE.':tag==='XC_FUNCTIONAL'&&value==='PBE'?'PE':tag==='XC_FUNCTIONAL'&&value==='PBEsol'?'PS':tag==='XC_FUNCTIONAL'&&value==='LDA'?null:value;
 let found=false;
 const result=text.split(/(\r?\n)/).map(line=>{
  const comment=line.search(/[#!]/),end=comment<0?line.length:comment;
  return line.slice(0,end).split(';').map(part=>{
   const match=part.match(/^(\s*([\w/]+)\s*=\s*)(.*?)(\s*)$/);
   if(match?.[2].toUpperCase()!==name)return part;
   found=true;
   return written===null?'':match[1]+written+match[4];
  }).join(';')+line.slice(end);
 }).join('');
 return found||written===null?result:result+(result&&!result.endsWith('\n')?'\n':'')+name+' = '+written+'\n';
}
