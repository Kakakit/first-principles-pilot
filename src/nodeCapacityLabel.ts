import type {ProbeNode} from './executionUi';
/** Scheduler slots and gated dispatch capacity are deliberately distinct. */
export function nodeCapacityLabel(node:ProbeNode) {
 const total=Number(node.CPUS_TOTAL),allocated=Number(node.CPUS_ALLOC);
 const unallocated=node.CPUS_UNALLOCATED??(node.CPUS_ALLOC!==undefined&&Number.isFinite(total)&&Number.isFinite(allocated)&&total>=allocated?String(total-allocated):'未知');
 const load=node.LOAD_VALID==='0'||node.LOAD==='unknown'?'待核验':node.LOAD||'未记录';
 return `可派发 ${node.CPUS_FREE||'0'} / ${node.CPUS_TOTAL||'未知'} 核 · 调度未分配 ${unallocated} 核 · 负载 ${load}（阈值 ${node.LOAD_LIMIT||'待刷新'}）`;
}
