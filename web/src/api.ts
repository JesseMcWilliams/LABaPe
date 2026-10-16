// Thin fetch wrapper over the LABaPe API (same origin, session cookie).

export interface FieldProblem {
  field: string;
  label: string;
  message: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public errors: FieldProblem[] = []) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  return (await requestWithHeaders<T>(method, path, body)).data;
}

async function requestWithHeaders<T>(method: string, path: string, body?: unknown): Promise<{ data: T; headers: Headers }> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    let errors: FieldProblem[] = [];
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : "The request was refused.";
      errors = Array.isArray(data.errors) ? data.errors : [];
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail, errors);
  }
  return { data: (await res.json()) as T, headers: res.headers };
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  /** A page of a list the API pages (L1): the rows and the matching total (X-Total-Count). */
  getPage: async <T>(path: string) => {
    const r = await requestWithHeaders<T[]>("GET", path);
    return { rows: r.data, total: Number(r.headers.get("X-Total-Count") ?? r.data.length) };
  },
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export interface Me {
  username: string;
  display_name: string;
  email: string;
  provider: string;
  groups: string[];
  roles: string[];
  breakglass: boolean;
  last_login: string | null;
  previous_login: string | null;
  member_since: string | null;
  session_expires: string | null;
}

export interface Template {
  id: number;
  from_primary?: boolean;     // on the primary file host only: a deploy copies it here
  kvm_host_id: number;
  name: string;
  os_key: string;
  edition: string;
  sku: string;
  disk_gb: number;
  min_cpu: number;
  min_memory_mb: number;
  description: string;
  notes: string;
  source: string;
  updated_at: string;
}

export interface CredentialSection {
  title: string;
  items: { label: string; value: string; secret: boolean }[];
  notes: string[];
}

export interface Provider {
  name: string;
  kind: string;
  display_name: string;
}

export interface KvmHost {
  id: number;
  name: string;
  libvirt_uri: string;
  vm_storage_path: string;
  template_storage_path: string;
  bridge: string;
  concurrency_limit: number;
  enabled: boolean;
  resources?: HostResources;
  // SSH administration (design §28.4)
  ssh_address?: string;
  ssh_port?: number;
  ssh_user?: string;
  access_status?: "none" | "ready" | "failed";
  access_error?: string;
  access_at?: string | null;
  // The host's own logs to the stack's syslog collector (design §29.1)
  log_forward?: boolean;
  log_forward_until?: string | null;
  log_forward_error?: string;
  // Files (design §29.4): the primary holds them all; a host with a share sees them at the same paths
  is_primary?: boolean;
  files_from_share?: boolean;
  // What runs on it (design §30): kvm, stack (a LABaPe web stack only) or both; the stack's address
  role?: "kvm" | "stack" | "both";
  stack_url?: string;
  // F1: its copies of LABaPe's files ("" = beside the VM folder; files_dir is what it uses now) and its
  // free-space alert (null = Settings → Monitoring's)
  files_path?: string;
  files_dir?: string;
  disk_free_pct?: number | null;
}

export interface Network {
  id: number;
  name: string;
  description: string;
  cidr: string;
  gateway: string;
  dns_servers: string[];
  vlan: number | null;
  addressing: ("static" | "dhcp")[];
  static_pools: string[];
  dhcp_ranges: string[];
  reserved: string[];
  allowed_roles: string[];
  allowed_groups: string[];
  enabled: boolean;
  allocated: number;
  bridge?: string; // with ?kvm_host_id=
  is_default?: boolean;
}

export interface HostStatsData {
  cpus: number;
  cores: number;
  memory_total_mb: number;
  memory_available_mb: number;
  load: number[];
  vms_defined: number;
  vms_running: number;
  vcpus_allocated: number;
  vcpus_running: number;
  memory_committed_mb: number;
  memory_running_mb: number;
  storage: Record<"vm" | "templates", { path: string; total_gb: number; free_gb: number }>;
}

export interface HostResources {
  collected_at: string | null;
  error: string;
  stats: HostStatsData | null;
  assessment: {
    vcpu_ratio: number;
    memory_committed_ratio: number;
    disk_free_ratio: number;
    findings: { level: "warning" | "block"; resource: string; message: string }[];
    thresholds: Record<string, number>;
  } | null;
}

export interface HostNetwork {
  network: string;
  bridge: string;
  static_pool: string[];
  is_default: boolean;
}

export interface HostGroup {
  name: string;
  count: number;
  os: string;
  roles: string[];
  image_source?: "iso_direct" | "packer_template";
  template?: string;
  cpu_count: number;
  memory_mb: number;
  disk_gb: number;
  windows_core: boolean;
  windows_datacenter: boolean;
  remote_desktop?: ("xrdp" | "vnc")[];   // E3: desktop sessions on a Linux group's VMs
  network?: string;
  addressing: "static" | "dhcp";
  names?: string[];
  services?: string[];
  applications?: string[];
  packages?: string[];
  // Start order (design §28.3): lower first; absent = DCs 1, others 2.
  start_order?: number | null;
  start_wait?: "agent" | "none";
  start_delay_seconds?: number;
  // Design §45: this group on another KVM host; absent = the environment's host.
  kvm_host_id?: number;
}

export interface Environment {
  id: number;
  name: string;
  status: string;
  kvm_host: string | null;
  spec: { host_groups: HostGroup[]; timezone?: string };
  created_by: string;
  created_at: string;
  updated_at: string;
  is_owner: boolean;
  inventory: string;
  addresses: Record<string, string>;
  lease: Lease;
}

/** An environment's lease (design §40). */
export interface Lease {
  enabled: boolean; expires_at: string | null; phase: "none" | "active" | "expiring" | "ended" | "destroying";
  destroy_at: string | null; max_days: number; state: Record<string, unknown>;
}

export const LEASE_COLOR = { none: "default", active: "success", expiring: "warning", ended: "error", destroying: "error" } as const;

export interface Job {
  id: number;
  type: string;
  state: string;
  environment_id: number | null;
  environment_name?: string | null;
  environment_status?: string | null;
  kvm_host_id: number | null;
  requested_by: string;
  cancel_requested: boolean;
  retry_of: number | null;
  exit_code: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  object_type: string;
  object_id: string;
  outcome: string;
  detail: Record<string, unknown>;
  source_ip: string;
}

export const fmtTime = (s: string | null) => (s ? new Date(s).toLocaleString() : "");
