export type Vec3 = [number,number,number];
export type StructureDocument = {
  edit?:{id:string;parent_geometry_sha256:string;geometry_sha256:string;coordinate_mode:string;at:string;operation:string;parent_edit_id:string|null};
  version:1; formula:string; lattice:Vec3[]; lengths:Vec3; angles:Vec3; volume:number;
  atoms:{id:string;element:string;fractional:Vec3;cartesian:Vec3;selective:boolean[]|null}[];
  warnings:string[]; source:{filename:string;format:'cif'|'poscar';sha256:string;base64:string};
  parser:{name:string;version:string}; poscar:string;
};
export function validateStructure(value: unknown): asserts value is StructureDocument {
  const s=value as StructureDocument;
  const vec=(v:unknown)=>Array.isArray(v)&&v.length===3&&v.every(n=>typeof n==='number'&&Number.isFinite(n));
  if(!s||s.version!==1||typeof s.formula!=='string'||!Array.isArray(s.lattice)||s.lattice.length!==3||!s.lattice.every(vec)||!vec(s.lengths)||!vec(s.angles)||!Number.isFinite(s.volume)||s.volume<=0||!Array.isArray(s.atoms)||!s.atoms.length||s.atoms.length>1500||!s.atoms.every(a=>a&&typeof a.id==='string'&&/^[A-Z][a-z]?$/.test(a.element)&&vec(a.fractional)&&vec(a.cartesian)&&(a.selective===null||(Array.isArray(a.selective)&&a.selective.length===3&&a.selective.every(v=>typeof v==='boolean'))))||new Set(s.atoms.map(a=>a.id)).size!==s.atoms.length||!s.source||typeof s.source.filename!=='string'||!['cif','poscar'].includes(s.source.format)||!/^[a-f0-9]{64}$/.test(s.source.sha256)||typeof s.source.base64!=='string'||s.source.base64.length>349528||!Array.isArray(s.warnings)||!s.warnings.every(w=>typeof w==='string')||typeof s.poscar!=='string'||!s.parser||typeof s.parser.version!=='string') throw new Error('结构记录无效，请重新导入原文件。');
  try {if(!atob(s.source.base64).length)throw new Error();}catch {throw new Error('原文件备份编码损坏。');}
}
export function saveBytes(name:string, bytes:BlobPart) {
  const url=URL.createObjectURL(new Blob([bytes])); const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export async function verifySource(s:StructureDocument) {
  let bytes:Uint8Array;
  try {bytes=Uint8Array.from(atob(s.source.base64),c=>c.charCodeAt(0));}
  catch {throw new Error('原文件备份编码损坏，请重新导入原文件。');}
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(n=>n.toString(16).padStart(2,'0')).join('');
  if(hash!==s.source.sha256)throw new Error('原文件备份与 SHA-256 不一致，请重新导入原文件。');
}
