export type ServerConfig = Record<string, string>;

type ExecutionConfig = {scheduler?:string;command_mode?:string};
export function commandMode(config?:ExecutionConfig):'direct'|'opaque'|null {
  return ['custom','other'].includes(config?.scheduler?.toLowerCase()||'')
    ? config?.command_mode==='direct'?'direct':'opaque' : null;
}

export type DirectRuntime = {process_pid?:number;exit_code?:number|null;termination_reason?:string};
export function directProcessLabel(state:string,runtime?:DirectRuntime) {
  if (runtime?.process_pid) return 'PID '+runtime.process_pid;
  return state==='PREPARED'?'尚未启动':state==='UNKNOWN'?'进程待核查':'直接运行';
}

export type RegisteredProfile = {
  alias: string;
  scheduler: string;
  command_mode?: 'direct'|'opaque';
  direct_limit?: number;
  queue: string;
  variant: string;
  status: string;
  runtime_status: string;
  ncl_runtime_status?: string;
  ncl_executable?: string;
  runtime_complete: boolean;
  selectable: boolean;
  reason: string;
  mpi_executable: string;
  vasp_executable: string;
  environment_script: string;
  module: string;
  launcher: string;
  runtime_environment: string;
  pmi_library: string;
  pmi_type: string;
  runtime_source?: string;
  remote_workspace_root?: string;
  pyw90_python?:string;
  pyw90_package?:string;
};

export type ProbeNode = Record<string, string> & { NODE: string; STATUS: string };

export type ServerProbe = {
  id: string;
  observed_at: string;
  config_hash?: string;
  checks: Record<string, string>;
  queues: Record<string, string>[];
  runtime_evidence: string;
  preflight: { hosts: { nodes: ProbeNode[] }[] };
  availability: { stdout: string; stderr: string; exit_code?: number | null };
  diagnostics: { stdout: string; stderr: string; exit_code?: number | null };
};

export type ServerPreview = {
  script: string;
  program_adaptation?: {source_program:string;target_program:string;target_command:string} | null;
  program_diff?: string;
  script_diff?: string;
  script_warnings?: string[];
  blockers: string[];
  node_status?: string;
  capacity_wait?: boolean;
  resource_ready: boolean;
  note: string;
  probe_id?: string;
  profile?: Record<string, unknown>;
};

export type PreparedBinding = {
  alias: string;
  root: string;
  node: string;
  bundleId: string;
  templateId: string;
  cores?: string;
  reason?: string;
  config: ServerConfig;
};

export function sameCapacityTarget(before:ServerConfig|null,after:ServerConfig):boolean {
  const scheduler=(value?:string)=>(value||'pbs').toLowerCase().replace('pbs/torque','pbs').replace('torque','pbs');
  return Boolean(before&&before.alias===after.alias&&scheduler(before.scheduler)===scheduler(after.scheduler)&&commandMode(before)===commandMode(after)&&(before.resource_policy||'wait_idle')===(after.resource_policy||'wait_idle'));
}

export function bindingFingerprint(binding: PreparedBinding | null): string {
  if (!binding) return '';
  return JSON.stringify({
    alias: binding.alias,
    root: binding.root,
    node: binding.node,
    bundleId: binding.bundleId,
    templateId: binding.templateId,
    cores: binding.cores || '',
    reason: binding.reason || '',
    config: binding.config,
  });
}

export function configFingerprint(config: ServerConfig | null): string {
  if (!config) return '';
  return JSON.stringify(Object.keys(config).sort().reduce<Record<string, string>>((out, key) => {
    out[key] = config[key];
    return out;
  }, {}));
}
