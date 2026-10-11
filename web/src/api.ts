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
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
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
  kvm_host_id: number;
  name: string;
  os_key: string;
  edition: string;
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
  network?: string;
  addressing: "static" | "dhcp";
  names?: string[];
  services?: string[];
  applications?: string[];
  packages?: string[];
}

export interface Environment {
  id: number;
  name: string;
  status: string;
  kvm_host: string | null;
  spec: { host_groups: HostGroup[] };
  created_by: string;
  created_at: string;
  updated_at: string;
  is_owner: boolean;
  inventory: string;
  addresses: Record<string, string>;
}

export interface Job {
  id: number;
  type: string;
  state: string;
  environment_id: number | null;
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
