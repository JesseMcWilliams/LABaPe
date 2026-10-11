// Thin fetch wrapper over the LABaPe API (same origin, session cookie).

export class ApiError extends Error {
  constructor(public status: number, message: string) {
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
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
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
  groups: string[];
  roles: string[];
  breakglass: boolean;
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
