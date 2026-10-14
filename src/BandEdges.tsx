export type BandReference={fermi_eV:number;vbm_eV?:number|null;cbm_eV?:number|null;scf_attempt_id?:string;fermi_source?:string};
export function bandEdges(reference:BandReference){
 const {fermi_eV,vbm_eV,cbm_eV}=reference;
 if(typeof vbm_eV!=='number'||typeof cbm_eV!=='number'||![fermi_eV,vbm_eV,cbm_eV].every(Number.isFinite)||cbm_eV<=vbm_eV)return null;
 return {vbm:vbm_eV-fermi_eV,cbm:cbm_eV-fermi_eV,gap:cbm_eV-vbm_eV};
}
export default function BandEdges({reference}:{reference:BandReference}){
 const edges=bandEdges(reference),source=reference.scf_attempt_id?'相连 SCF 的 OUTCAR · SCF 网格':'所选能带任务的 OUTCAR · 能带路径';
 return <section className="band-edge-summary" aria-label="VBM 与 CBM 信息"><h4>VBM / CBM · {source}</h4>{edges?<><div className="band-edge-values"><div><span>VBM · E−E_F / eV</span><strong>{edges.vbm.toFixed(4)}</strong></div><div><span>CBM · E−E_F / eV</span><strong>{edges.cbm.toFixed(4)}</strong></div><div><span>CBM−VBM / eV</span><strong>{edges.gap.toFixed(4)}</strong></div></div><p>复用 OUTCAR 的带边报告，与当前图采用同一费米参考。仅覆盖该来源采样的 k 点；路径采样不保证找到整个布里渊区的极值。</p><details><summary>绝对能量核对</summary><p>VBM：{reference.vbm_eV} eV；CBM：{reference.cbm_eV} eV；E_F：{reference.fermi_eV} eV。</p></details></>:<p>该 OUTCAR 未提供可识别的正带隙边界。不能据此判断金属性，也未按当前绘图能量范围推测 VBM/CBM。</p>}</section>;
}
