export type ServerConfig = Record<string, string>;

export type RegisteredProfile = {
  alias: string;
  scheduler: string;
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
  return Boolean(before&&before.alias===after.alias&&scheduler(before.scheduler)===scheduler(after.scheduler));
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
