type Snapshot={upstream_attempt_id?:string;source_attempt_id?:string;kind?:string};
export type LineageAttempt={dependency_snapshot?:Snapshot;chain_source_snapshot?:Snapshot;input_audit?:{dependency_snapshot?:Snapshot}};
export function scfSourceId(attempt?:LineageAttempt):string|undefined {
 const ids=[attempt?.dependency_snapshot,attempt?.chain_source_snapshot,attempt?.input_audit?.dependency_snapshot]
  .filter(s=>s&&(!s.kind||s.kind==='dependency'))
  .map(s=>s!.upstream_attempt_id||s!.source_attempt_id).filter(Boolean);
 return new Set(ids).size===1?ids[0]:undefined;
}
