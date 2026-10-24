import { FormEvent, useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, FormControlLabel, Link, List, ListItemButton, ListItemText, MenuItem, Paper, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { api, KvmHost, Network } from "../api";
import { hasRole, useAuth } from "../App";
import HostLoad from "../components/HostLoad";
import { DetailHeader, Facts } from "../components/PageParts";
import { ActionButton, useConfirm } from "../components/Confirm";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import HostNetworks from "./HostNetworks";
import {
  AccessSection, AccountsSection, DashboardSection, DrivesSection, GroupsSection, NicsSection, OsSection, PoolsSection, SECTIONS,
  SectionHeader, sectionsFor, StackSection, useAdminView, VmsSection,
} from "../components/HostSections";

type HostForm = Omit<KvmHost, "id" | "resources">;
interface CheckResult { ok: boolean; remote: boolean; libvirt: string; problems: string[] }
interface PathItem { level: "ok" | "info" | "fix" | "fixed" | "problem"; text: string }
interface PathsResult {
  available: boolean; reason: string; ok: boolean; changes: number;
  paths: { path: string; kind: string; ok: boolean; changes: number; items: PathItem[] }[];
}

// What a host's folders need (VM disks, templates, files), per path, as a list of what is fine, what
// would change (or did) and what can't be fixed from here.
function PathsReport({ result }: { result: PathsResult }) {
  if (!result.available) return <Alert severity="info">{result.reason}</Alert>;
  const mark: Record<PathItem["level"], string> = { ok: "✓", info: "·", fix: "→", fixed: "✓ changed:", problem: "✗" };
  return (
    <Alert severity={result.ok ? (result.changes ? "warning" : "success") : "error"}>
      {result.paths.map((r) => (
        <Box key={r.path} sx={{ mb: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.path}</Typography>
          <Box component="ul" sx={{ m: 0, pl: 2, listStyle: "none" }}>
            {r.items.map((i, n) => <li key={n}>{mark[i.level]} {i.text}</li>)}
          </Box>
        </Box>
      ))}
    </Alert>
  );
}

// A qemu+ssh host (design §22.4): the worker's key to authorize there, and a connection check.
function RemoteAccess({ hostId }: { hostId: number | null }) {
  const [key, setKey] = useState("");
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<{ public_key: string }>("/api/hosts/worker-key").then((k) => setKey(k.public_key), () => setKey(""));
  }, []);
  const run = async () => {
    setBusy(true);
    try { setCheck(await api.post<CheckResult>(`/api/hosts/${hostId}/check`)); } finally { setBusy(false); }
  };
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Remote access over SSH</Typography>
        <Typography variant="body2" color="text.secondary">
          Jobs for this host run there over SSH, as the user in the URI. Install the host with
          {" "}<code>sudo deploy/install-labape.py --profile kvm</code> (libvirt, OpenTofu, Ansible, Packer), then add
          this key to that user&apos;s <code>~/.ssh/authorized_keys</code>:
        </Typography>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Box component="code" sx={{ flex: 1, p: 1, bgcolor: "action.hover", borderRadius: 1, fontSize: 12, wordBreak: "break-all" }}>
            {key || "…"}
          </Box>
          <ActionButton size="small" disabled={!key} tip="Copy the public key to the clipboard"
            onClick={() => navigator.clipboard.writeText(key)}>Copy</ActionButton>
        </Stack>
        {hostId !== null && (
          <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
            <ActionButton variant="outlined" disabled={busy} onClick={run}
              tip="Connect over SSH and libvirt and check the tools and paths jobs need (changes nothing)">
              {busy ? "Checking…" : "Check connection"}
            </ActionButton>
            {check?.libvirt && <Typography variant="body2">{check.libvirt}</Typography>}
          </Stack>
        )}
        {check && (check.ok
          ? <Alert severity="success">The worker reaches the host and everything jobs need is there.</Alert>
          : <Alert severity="error"><Box component="ul" sx={{ m: 0, pl: 2 }}>{check.problems.map((p) => <li key={p}>{p}</li>)}</Box></Alert>)}
      </Stack>
    </Paper>
  );
}

const blank: HostForm = {
  name: "", libvirt_uri: "qemu:///system", vm_storage_path: "/data/VMs/LABaPe",
  template_storage_path: "/data/VMs/LABaPe/templates", bridge: "br0", concurrency_limit: 2, enabled: true,
};

// One KVM host (design §22.2, §28.4): sections on the left (question R11):
// Dashboard, Drives and volumes, NICs, OS, Accounts, Groups, VMs, SSH access,
// Settings. A new host shows only the registration form.
export default function HostDetail() {
  const { id } = useParams();
  if (id === "new") return <HostSettings />;
  return <HostSectionsPage hostId={Number(id)} />;
}

function HostSectionsPage({ hostId }: { hostId: number }) {
  const [params, setParams] = useSearchParams();
  const section = params.get("section") ?? "dashboard";
  const { view, error, load } = useAdminView(hostId);
  const refresh = async () => { await api.post(`/api/hosts/${hostId}/admin/refresh`); load(); };
  const label = SECTIONS.find((x) => x.key === section)?.label ?? "";
  const body = () => {
    if (section === "settings") return <HostSettings embedded />;
    if (!view) return error ? <Alert severity="error">{error}</Alert> : null;
    if (section === "access") return <AccessSection host={view.host} reload={load} />;
    if (section === "vms") return <VmsSection view={view} />;
    if (section === "stack") return <StackSection view={view} />;
    if (view.host.access_status !== "ready" || !view.collected_at) return null;
    if (section === "drives") return <DrivesSection view={view} />;
    if (section === "pools") return <PoolsSection view={view} hostId={hostId} reload={load} />;
    if (section === "nics") return <NicsSection view={view} />;
    if (section === "os") return <OsSection view={view} />;
    if (section === "accounts") return <AccountsSection view={view} hostId={hostId} reload={load} />;
    if (section === "groups") return <GroupsSection view={view} hostId={hostId} reload={load} />;
    return <DashboardSection view={view} />;
  };
  return (
    <Stack spacing={2}>
      <DetailHeader back="/admin/hosts" backLabel="KVM hosts" title={view?.host.name ?? "KVM host"} />
      <Box sx={{ display: "flex", gap: 2, alignItems: "flex-start" }}>
        <Paper variant="outlined" sx={{ width: 190, flexShrink: 0 }}>
          <List dense disablePadding>
            {sectionsFor(view?.host.role, (view?.pools?.length ?? 0) > 0).map((x) => (
              <ListItemButton key={x.key} selected={section === x.key} onClick={() => setParams({ section: x.key }, { replace: true })}>
                <ListItemText primary={x.label} />
              </ListItemButton>
            ))}
          </List>
        </Paper>
        <Stack spacing={2} sx={{ flexGrow: 1, minWidth: 0 }}>
          {section !== "settings" && section !== "access" && <SectionHeader view={view} onRefresh={refresh} title={label} />}
          {body()}
        </Stack>
      </Box>
    </Stack>
  );
}

function HostSettings({ embedded = false }: { embedded?: boolean }) {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [host, setHost] = useState<KvmHost | null>(null);
  const [form, setForm] = useState<HostForm>(blank);
  const [nets, setNets] = useState<Network[]>([]);
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [pathsResult, setPathsResult] = useState<PathsResult | null>(null);
  const [pathsBusy, setPathsBusy] = useState(false);

  useEffect(() => {
    if (isNew) return;
    api.get<KvmHost>(`/api/hosts/${id}`).then((h) => {
      const { id: _id, resources: _r, ...rest } = h;
      setHost(h);
      setForm(rest);
    }, (e) => setErrs({ summary: e.message, fields: {} }));
    api.get<Network[]>(`/api/networks?kvm_host_id=${id}`).then(setNets, () => setNets([]));
  }, [id, isNew]);

  const confirm = useConfirm();
  // The folders as the form has them: what the host would be asked to check or fix.
  const pathsBody = () => ({
    vm_storage_path: form.vm_storage_path, template_storage_path: form.template_storage_path,
    files_path: form.is_primary || form.files_from_share ? "" : (form.files_path || host?.files_dir || ""),
  });
  const pathsChanged = !isNew && !!host && (form.vm_storage_path !== host.vm_storage_path
    || form.template_storage_path !== host.template_storage_path || (form.files_path ?? "") !== (host.files_path ?? ""));
  const inspectPaths = async (fix: boolean) => {
    setPathsBusy(true);
    setErrs({ summary: "", fields: {} });
    try {
      const r = await api.post<PathsResult>(`/api/hosts/${id}/paths`, { ...pathsBody(), fix });
      setPathsResult(r);
      return r;
    } catch (err) {
      setErrs(fromApiError(err));
      return null;
    } finally { setPathsBusy(false); }
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErrs({ summary: "", fields: {} });
    let preview: PathsResult | null = null;
    if (pathsChanged) {                       // what the new folders need on the host, before anything is saved
      preview = await inspectPaths(false);
      if (!preview) return;
      if (!preview.ok) return;                // the report shows what can't be fixed from here
    }
    if (!(await confirm({
      title: isNew ? `Register ${form.name || "this host"}?` : `Save ${form.name}?`, confirmLabel: isNew ? "Register" : "Save",
      body: isNew
        ? `LABaPe starts using ${form.libvirt_uri}: the worker collects its load every minute and lists its templates. Attach networks on the host's page next.`
        : (
          <Stack spacing={1} sx={{ px: 3, pb: 1 }}>
            <Typography variant="body2">New jobs on this host use the changed settings. Running jobs and existing VMs aren&apos;t affected.</Typography>
            {preview?.available && preview.changes > 0 && (
              <>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>Saving also changes these on {form.name}, as the service user needs them:</Typography>
                <PathsReport result={{ ...preview, paths: preview.paths.map((r) => ({ ...r, items: r.items.filter((i) => i.level === "fix") })).filter((r) => r.items.length) }} />
              </>
            )}
          </Stack>
        ),
    }))) return;
    try {
      if (isNew) {
        const created = await api.post<KvmHost>("/api/hosts", form);
        navigate(`/admin/hosts/${created.id}`);
      } else {
        await api.put(`/api/hosts/${id}`, form);
        navigate("/admin/hosts");
      }
    } catch (err) {
      setErrs(fromApiError(err));
    }
  };

  const field = (key: keyof HostForm, label: string, help?: string, type = "text") => (
    <Field label={label} type={type} value={form[key] as string | number} help={help} error={errs.fields[key]}
      onChange={(e) => setForm({ ...form, [key]: type === "number" ? Number(e.target.value) : e.target.value })} />
  );

  const header = embedded ? null : (
    <DetailHeader back="/admin/hosts" backLabel="KVM hosts" title={isNew ? "Register a KVM host" : host?.name ?? "KVM host"} />
  );

  return (
    <Stack spacing={2}>
      {header}
      {isAdmin ? (
        <Paper variant="outlined" sx={{ p: 2 }} component="form" onSubmit={submit} noValidate>
          <Stack spacing={2}>
            <ErrorSummary summary={errs.summary} fields={errs.fields} />
            <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
              {field("name", "Name")}
              {field("libvirt_uri", "libvirt URI", "qemu:///system here, or qemu+ssh://user@host/system")}
              {field("vm_storage_path", "VM storage path")}
              {field("template_storage_path", "Template storage path")}
              {field("bridge", "Fallback bridge", "Used when a network has no bridge here")}
              {field("concurrency_limit", "Concurrent jobs", "Jobs run at once on this host", "number")}
              {!form.is_primary && (
                <Field label="Files path" value={form.files_path ?? ""} error={errs.fields.files_path} sx={{ minWidth: 220 }}
                  placeholder={host?.files_dir || "/data/Files"}
                  help={`ISOs, Installers and Other under it; empty: beside the VM folder${host?.files_dir ? ` (${host.files_dir})` : ""}. A change moves the files.`}
                  onChange={(e) => setForm({ ...form, files_path: e.target.value })} />
              )}
              <Field label="Free-space alert (%)" type="number" value={form.disk_free_pct ?? ""} error={errs.fields.disk_free_pct}
                sx={{ width: 170 }} help="Alert when a drive has less free; empty: Settings → Monitoring's"
                onChange={(e) => setForm({ ...form, disk_free_pct: e.target.value === "" ? null : Number(e.target.value) })} />
              <Field select label="Role" value={form.role ?? "kvm"} sx={{ width: 190 }} help="What runs on it"
                onChange={(e) => setForm({ ...form, role: e.target.value as HostForm["role"] })}>
                <MenuItem value="kvm">KVM host</MenuItem><MenuItem value="stack">LABaPe stack only</MenuItem><MenuItem value="both">KVM host and stack</MenuItem>
              </Field>
              {form.role && form.role !== "kvm" && (
                <Field label="Stack address" value={form.stack_url ?? ""} sx={{ minWidth: 240 }} error={errs.fields.stack_url}
                  help="https://… of its web interface; another stack's is probed every minute"
                  onChange={(e) => setForm({ ...form, stack_url: e.target.value })} />
              )}
              <FormControlLabel label="Enabled" sx={{ mt: 0.5 }}
                control={<Checkbox checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />} />
              <FormControlLabel label="Primary file host" sx={{ mt: 0.5 }}
                title="Holds every file LABaPe uses (ISOs, installers, template images); jobs on other hosts get copies. There is always exactly one."
                control={<Checkbox checked={Boolean(form.is_primary)} disabled={Boolean(host?.is_primary)}
                  onChange={(e) => setForm({ ...form, is_primary: e.target.checked, files_from_share: e.target.checked ? false : form.files_from_share })} />} />
              <FormControlLabel label="Files from a share" sx={{ mt: 0.5 }}
                title="You mounted the primary's file locations on this host at the same paths (NFS or SMB): jobs read them there instead of copying"
                control={<Checkbox checked={Boolean(form.files_from_share)} disabled={Boolean(form.is_primary)}
                  onChange={(e) => setForm({ ...form, files_from_share: e.target.checked })} />} />
            </Stack>
            <Stack direction="row" spacing={2}>
              <ActionButton type="submit" variant="contained"
                tip={isNew ? "Register this KVM host (asks to confirm)" : "Save the changes (asks to confirm)"}>
                {isNew ? "Register" : "Save"}
              </ActionButton>
              <Button component={RouterLink} to="/admin/hosts" title="Leave without saving">Cancel</Button>
              {!isNew && (
                <ActionButton variant="outlined" disabled={pathsBusy} onClick={() => inspectPaths(false)}
                  tip="Look at the VM, template and files folders on the host for the service user and the hypervisor's user (changes nothing)">
                  Check folders
                </ActionButton>
              )}
              {!isNew && pathsResult?.available && pathsResult.changes > 0 && pathsResult.ok && (
                <ActionButton variant="outlined" disabled={pathsBusy} onClick={() => inspectPaths(true)}
                  tip="Create the folders and set their owner, group and mode as listed (never touches what is inside them)">
                  Fix folders
                </ActionButton>
              )}
              {!isNew && host && (
                <ActionButton color="error" variant="outlined" tip="Delete this host from LABaPe (restorable until the retention period ends)"
                  confirm={{ title: `Delete ${host.name}?`, confirmLabel: "Delete", danger: true,
                    body: `${host.name} is hidden from LABaPe now and removed for good after the retention period (Settings → History); until then Admin → KVM hosts → Deleted hosts can restore it. Nothing is changed on the host itself: the worker's key stays authorized there. A host that is the primary file host, runs this stack, has running jobs or is used by an environment can't be deleted.` }}
                  onClick={() => api.del(`/api/hosts/${host.id}`).then(() => navigate("/admin/hosts"), (e) => setErrs(fromApiError(e)))}>
                  Delete host
                </ActionButton>
              )}
            </Stack>
            {pathsResult && <PathsReport result={pathsResult} />}
          </Stack>
        </Paper>
      ) : (
        <>
          {errs.summary && <Alert severity="error">{errs.summary}</Alert>}
          {host && (
            <Facts rows={[
              ["libvirt URI", host.libvirt_uri],
              ["VM storage", host.vm_storage_path],
              ["Templates", host.template_storage_path],
              ["State", host.enabled ? "enabled" : "disabled"],
            ]} />
          )}
        </>
      )}

      {isAdmin && form.libvirt_uri.startsWith("qemu+ssh://") && <RemoteAccess hostId={isNew ? null : Number(id)} />}

      {host && (
        <Box>
          <Typography variant="h6" gutterBottom>Resources and load</Typography>
          <HostLoad resources={host.resources} />
        </Box>
      )}

      {host && isAdmin && <HostNetworks host={host} />}
      {host && !isAdmin && (
        <Box>
          <Typography variant="h6" gutterBottom>Networks you can use here</Typography>
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Network</TableCell><TableCell>Range</TableCell><TableCell>Addressing</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {nets.map((n) => (
                <TableRow key={n.id}>
                  <TableCell>
                    <Link component={RouterLink} to={`/admin/networks/${n.id}`}>{n.name}</Link>{n.is_default ? " (default)" : ""}
                  </TableCell>
                  <TableCell>{n.cidr}</TableCell>
                  <TableCell>{n.addressing.join(", ")}</TableCell>
                </TableRow>
              ))}
              {nets.length === 0 && <TableRow><TableCell colSpan={3}>None.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </Box>
      )}
    </Stack>
  );
}
