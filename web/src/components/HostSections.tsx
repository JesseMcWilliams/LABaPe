// A KVM host's administration sections (design §28.4): read over SSH by the
// host's LABaPe access account (questions 113-115), with accounts and groups
// managed by admins (question 120).
import { Fragment, ReactNode, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Paper, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { api, fmtTime, KvmHost, Network } from "../api";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { ActionButton, useConfirm } from "./Confirm";
import { Facts } from "./PageParts";
import Sparkline from "./Sparkline";
import { ConnectButton } from "./Connect";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
export interface AdminView {
  host: KvmHost; collected_at: string | null; smart_at: string | null; error: string;
  facts: Any; smart: Any; latest: Any; warnings: { level: "error" | "warning" | "info"; message: string }[];
  series: Record<string, (number | null)[]> & { t: string[] }; resources: Any; stack?: Any;
}

export const SECTIONS = [
  { key: "dashboard", label: "Dashboard" }, { key: "drives", label: "Drives and volumes" }, { key: "nics", label: "NICs" },
  { key: "os", label: "OS" }, { key: "accounts", label: "Accounts" }, { key: "groups", label: "Groups" },
  { key: "vms", label: "VMs" }, { key: "stack", label: "Stack" }, { key: "access", label: "SSH access" },
  { key: "settings", label: "Settings" },
] as const;

/** The sections a host's role calls for (design §30): VMs for KVM hosts, Stack for stack hosts. */
export const sectionsFor = (role: string | undefined) =>
  SECTIONS.filter((x) => (x.key !== "vms" || role !== "stack") && (x.key !== "stack" || role === "stack" || role === "both"));

const ago2 = (iso?: string | null) => (iso ? fmtTime(iso) : "—");

/** A stack host (design §30): containers, volumes, and this stack's own checks or another stack's probe. */
export function StackSection({ view }: { view: AdminView }) {
  const st = view.stack;
  if (!st) return <Alert severity="info">This host doesn&apos;t run a LABaPe stack (Settings → Role).</Alert>;
  const s = st.status ?? {};
  const containers: Any[] = s.containers ?? [];
  const me = s.self;
  const peer = s.peer;
  return (
    <Stack spacing={2}>
      {(st.alerts ?? []).map((a: Any, i: number) => <Alert key={i} severity={a.level === "error" ? "error" : "warning"}>{a.message} (since {fmtTime(a.started_at)})</Alert>)}
      {st.error && <Alert severity="warning">Last check failed: {st.error}</Alert>}
      <Typography variant="body2" color="text.secondary">
        {st.self ? "The stack this page comes from." : view.host.stack_url ? `Another stack, at ${view.host.stack_url}.` : "A stack host."}
        {" "}Containers are read every 5 minutes through the SSH access account, volumes hourly{st.self ? "; backups and certificates every minute" : "; the stack's health address every minute"}.
      </Typography>
      {peer && (
        <Facts rows={[
          ["Health address", peer.ok ? <Chip key="ok" size="small" color="success" label={`answers (${peer.ms} ms)`} /> : <Chip key="no" size="small" color="error" label={`not answering ×${peer.failures}`} />],
          ["Last probe", ago2(peer.at)], ["Certificate expires", ago2(peer.cert_expires)], ["Problem", peer.error || "—"],
        ]} />
      )}
      {me && (
        <Facts rows={[
          ["Newest backup", me.backup?.enabled ? ago2(me.backup?.newest) : "backups turned off"],
          ...Object.entries(me.certs ?? {}).map(([k, v]: [string, Any]) => [`${k} certificate`, v.expires ? `expires ${fmtTime(v.expires)}` : v.error] as [string, string]),
          ...Object.entries(me.databases ?? {}).map(([k, v]: [string, Any]) => [`${k} database`, gb(v as number)] as [string, string]),
        ]} />
      )}
      {containers.length > 0 ? (
        <Table size="small">
          <TableHead><TableRow><TableCell>Container</TableCell><TableCell>State</TableCell><TableCell>Health</TableCell><TableCell>Restarts</TableCell><TableCell>Image</TableCell></TableRow></TableHead>
          <TableBody>
            {containers.map((c) => (
              <TableRow key={c.name}>
                <TableCell sx={{ fontFamily: "monospace" }}>{c.name}</TableCell>
                <TableCell><Chip size="small" label={c.state} color={c.state === "running" ? "success" : "error"} /></TableCell>
                <TableCell>{c.health || "—"}</TableCell><TableCell>{c.restarts}</TableCell>
                <TableCell sx={{ fontSize: 12 }}>{c.image}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : <Typography variant="body2" color="text.secondary">{view.host.access_status === "ready" ? "Containers not read yet." : "Set up SSH access to read its containers, disks and temperatures."}</Typography>}
      {(s.volumes ?? []).length > 0 && (
        <Table size="small">
          <TableHead><TableRow><TableCell>Volume</TableCell><TableCell>Size</TableCell><TableCell>Used by</TableCell></TableRow></TableHead>
          <TableBody>
            {(s.volumes as Any[]).map((v) => <TableRow key={v.name}><TableCell sx={{ fontFamily: "monospace" }}>{v.name}</TableCell><TableCell>{v.size}</TableCell><TableCell>{v.links}</TableCell></TableRow>)}
          </TableBody>
        </Table>
      )}
    </Stack>
  );
}

const gb = (b?: number | null) => (b == null ? "—" : b >= 2 ** 40 ? `${(b / 2 ** 40).toFixed(2)} TB` : `${(b / 2 ** 30).toFixed(1)} GB`);
const rate = (v: number) => (v >= 2 ** 20 ? `${(v / 2 ** 20).toFixed(1)} MB/s` : `${(v / 1024).toFixed(0)} KB/s`);
const ago = (s?: number) => (s == null ? "—" : s > 86400 ? `${Math.floor(s / 86400)} d` : `${Math.floor(s / 3600)} h`);

export function useAdminView(hostId: number) {
  const [view, setView] = useState<AdminView | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api.get<AdminView>(`/api/hosts/${hostId}/admin`).then((v) => { setView(v); setError(""); }, (e) => setError(e.message));
  }, [hostId]);
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);
  return { view, error, load };
}

function NeedsAccess({ host }: { host: KvmHost }) {
  return (
    <Alert severity="info">
      {host.access_status === "failed"
        ? `LABaPe can't reach this host over SSH: ${host.access_error}. Fix it under SSH access.`
        : "Set up SSH access (left: SSH access) so LABaPe can read this host's disks, NICs, sensors, OS and accounts."}
    </Alert>
  );
}

function Collected({ view, onRefresh }: { view: AdminView; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
      <Typography variant="caption" color="text.secondary">
        Read {view.collected_at ? fmtTime(view.collected_at) : "not yet"}{view.smart_at ? ` · SMART ${fmtTime(view.smart_at)}` : ""}
        {view.error ? ` · last attempt failed: ${view.error}` : ""}
      </Typography>
      <ActionButton size="small" disabled={busy} tip="Read the host again now, SMART included (takes up to a minute)"
        onClick={async () => { setBusy(true); try { await onRefresh(); } finally { setBusy(false); } }}>
        {busy ? "Reading…" : "Refresh now"}
      </ActionButton>
    </Stack>
  );
}

interface OsUpdates { manager: string; packages: string[]; reboot_required: boolean; running_jobs: number; is_stack_host: boolean }

/** Update (apt/dnf upgrade) and Reboot for the host, each after a confirmation that shows what will happen (design §53). */
function PowerActions({ hostId, name }: { hostId: number; name: string }) {
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const go = async (kind: "update" | "reboot") => {
    setError("");
    setBusy(true);
    try {
      const info = await api.get<OsUpdates>(`/api/hosts/${hostId}/os-updates`);
      const shown = info.packages.slice(0, 40);
      const ok = await confirm(kind === "update" ? {
        title: `Update the packages on ${name}?`, confirmLabel: "Update",
        body: (
          <Stack spacing={1}>
            <Typography variant="body2">
              Runs the normal update ({info.manager === "dnf" ? "dnf upgrade" : "apt-get update, then apt-get upgrade"}; no release upgrade) as a job you can follow.
              {info.running_jobs > 0 && ` ${info.running_jobs} job${info.running_jobs === 1 ? " is" : "s are"} running on this host; updates can briefly slow them.`}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {info.packages.length ? `${info.packages.length} package${info.packages.length === 1 ? "" : "s"} waiting by the package lists as they are now (the job refreshes the lists first):` : "The package lists as they are now show nothing waiting; the job refreshes them first."}
            </Typography>
            {shown.length > 0 && <Box component="pre" sx={{ m: 0, maxHeight: 220, overflow: "auto", fontSize: 12 }}>{[...shown, ...(info.packages.length > shown.length ? [`… and ${info.packages.length - shown.length} more`] : [])].map((l) => <div key={l}>{l}</div>)}</Box>}
          </Stack>),
      } : {
        title: `Reboot ${name}?`, confirmLabel: "Reboot", danger: true,
        body: (
          <Stack spacing={1}>
            <Typography variant="body2">
              The host reboots about a minute after the job gets its turn. Its running VMs stop and start again with the host (if they are set to).
              {info.running_jobs > 0 ? ` ${info.running_jobs} job${info.running_jobs === 1 ? " is" : "s are"} running on it: the reboot waits for ${info.running_jobs === 1 ? "it" : "them"} to finish and no new job starts on this host meanwhile.` : " No job is running on it now."}
            </Typography>
            {info.reboot_required && <Alert severity="info">The host says a reboot is needed (for example after a kernel update).</Alert>}
            {info.is_stack_host && <Alert severity="warning">This stack runs on this host: LABaPe is offline until it is back.</Alert>}
          </Stack>),
      });
      if (!ok) return;
      const r = await api.post<{ job_id: number }>(`/api/hosts/${hostId}/${kind === "update" ? "os-update" : "reboot"}`);
      navigate(`/jobs/${r.job_id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1}>
        <Button variant="outlined" size="small" disabled={busy} onClick={() => go("update")}
          title="Update the host's packages (asks to confirm first)">Update</Button>
        <Button variant="outlined" size="small" color="warning" disabled={busy} onClick={() => go("reboot")}
          title="Reboot the host, after its running jobs (asks to confirm first)">Reboot</Button>
      </Stack>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    </Stack>
  );
}

export function DashboardSection({ view }: { view: AdminView }) {
  const os = view.facts?.os ?? {};
  const s = view.series;
  const temps = Object.entries(view.latest?.temps ?? {}) as [string, number][];
  return (
    <Stack spacing={2}>
      {view.warnings.length > 0
        ? view.warnings.map((w, i) => <Alert key={i} severity={w.level}>{w.message}</Alert>)
        : view.collected_at && <Alert severity="success">No problems found.</Alert>}
      <Facts rows={[
        ["OS", `${os.name ?? "—"} · kernel ${os.kernel ?? "—"}`],
        ["CPU", `${os.cpus ?? "—"} × ${os.cpu_model ?? ""}`], ["Memory", os.memory_mb ? `${(os.memory_mb / 1024).toFixed(1)} GB` : "—"],
        ["Up", ago(os.uptime_seconds)], ["Load", (os.load ?? []).join(" · ") || "—"],
        ["Updates", os.updates == null ? "—" : `${os.updates} available${os.reboot_required ? " · reboot required" : ""}`],
      ]} />
      <PowerActions hostId={view.host.id} name={view.host.name} />
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Last 24 hours (every 5 minutes)</Typography>
        <Table size="small">
          <TableBody>
            {([["CPU", "cpu_pct", 100, (v: number) => `${v.toFixed(0)}%`], ["Memory used", "memory_used_pct", 100, (v: number) => `${v.toFixed(0)}%`],
              ["Fullest filesystem", "fs_max_pct", 100, (v: number) => `${v.toFixed(0)}%`], ["Hottest sensor", "temp_max_c", undefined, (v: number) => `${v.toFixed(0)} °C`],
              ["Network in", "rx_bps", undefined, rate], ["Network out", "tx_bps", undefined, rate]] as [string, string, number | undefined, (v: number) => string][])
              .map(([label, key, max, fmt]) => {
                const vals = s[key] ?? [];
                const last = [...vals].reverse().find((v) => v !== null);
                return (
                  <TableRow key={key}>
                    <TableCell>{label}</TableCell>
                    <TableCell><Sparkline values={vals} times={s.t} max={max} label={label} format={fmt} width={260} /></TableCell>
                    <TableCell>{last == null ? "—" : fmt(last)}</TableCell>
                  </TableRow>
                );
              })}
          </TableBody>
        </Table>
      </Paper>
      {temps.length > 0 && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Temperatures</Typography>
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
            {temps.map(([k, v]) => <Chip key={k} size="small" label={`${k}: ${v.toFixed(0)} °C`} color={v >= 85 ? "error" : v >= 70 ? "warning" : "default"} />)}
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}

function DiskRows({ devs, depth = 0 }: { devs: Any[]; depth?: number }): ReactNode {
  return devs.map((d) => (
    <Fragment key={d.path ?? d.name}>
      <TableRow>
        <TableCell sx={{ pl: 2 + depth * 3, fontFamily: "monospace" }}>{d.name}</TableCell>
        <TableCell>{d.type}</TableCell><TableCell>{gb(d.size)}</TableCell>
        <TableCell>{[d.model, d.tran, d.rota === false || d.rota === "0" ? "SSD" : d.type === "disk" ? "HDD" : ""].filter(Boolean).join(" · ")}</TableCell>
        <TableCell>{d.fstype ?? ""}</TableCell><TableCell>{(d.mountpoints ?? []).filter(Boolean).join(", ")}</TableCell>
      </TableRow>
      {d.children && <DiskRows devs={d.children} depth={depth + 1} />}
    </Fragment>
  ));
}

export function DrivesSection({ view }: { view: AdminView }) {
  const f = view.facts ?? {};
  const smart = view.smart ?? {};
  // Loop devices (snaps, mounted images) are hidden unless asked for (D1).
  const [showLoop, setShowLoop] = useState(false);
  const disks: Any[] = f.disks ?? [];
  const loops = disks.filter((d) => d.type === "loop").length;
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <Typography variant="subtitle1">Disks and partitions</Typography>
        {loops > 0 && <FormControlLabel label={`Show loop devices (${loops})`}
          control={<Checkbox size="small" checked={showLoop} onChange={(e) => setShowLoop(e.target.checked)} />} />}
      </Stack>
      <Table size="small">
        <TableHead><TableRow><TableCell>Device</TableCell><TableCell>Type</TableCell><TableCell>Size</TableCell><TableCell>Model</TableCell><TableCell>FS</TableCell><TableCell>Mounted</TableCell></TableRow></TableHead>
        <TableBody><DiskRows devs={showLoop ? disks : disks.filter((d) => d.type !== "loop")} /></TableBody>
      </Table>
      <Typography variant="subtitle1">Disk health (SMART)</Typography>
      {!smart.available ? <Alert severity="info">smartctl isn&apos;t installed on the host (package smartmontools).</Alert> : (
        <Table size="small">
          <TableHead><TableRow><TableCell>Device</TableCell><TableCell>Model</TableCell><TableCell>Health</TableCell><TableCell>Temp</TableCell><TableCell>Power-on</TableCell><TableCell>Reallocated / pending</TableCell><TableCell>Wear</TableCell></TableRow></TableHead>
          <TableBody>
            {(smart.disks ?? []).map((d: Any) => (
              <TableRow key={d.device}>
                <TableCell>{d.device}</TableCell><TableCell>{d.model} <Typography variant="caption" color="text.secondary">{d.serial}</Typography></TableCell>
                <TableCell><Chip size="small" label={d.passed === false ? "FAILED" : d.passed ? "passed" : "unknown"} color={d.passed === false ? "error" : d.passed ? "success" : "default"} /></TableCell>
                <TableCell>{d.temperature != null ? `${d.temperature} °C` : "—"}</TableCell>
                <TableCell>{d.power_on_hours != null ? `${Math.round(d.power_on_hours / 24)} d` : "—"}</TableCell>
                <TableCell>{d.reallocated ?? "—"} / {d.pending ?? "—"}</TableCell>
                <TableCell>{d.wear_pct != null ? `${d.wear_pct}%` : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <Typography variant="subtitle1">Filesystems</Typography>
      <Table size="small">
        <TableHead><TableRow><TableCell>Mounted on</TableCell><TableCell>Device</TableCell><TableCell>Type</TableCell><TableCell>Size</TableCell><TableCell>Used</TableCell><TableCell>Free</TableCell></TableRow></TableHead>
        <TableBody>
          {(f.filesystems ?? []).map((x: Any) => {
            const pct = x.size ? Math.round((x.used * 100) / x.size) : 0;
            return (
              <TableRow key={x.mount}>
                <TableCell>{x.mount}</TableCell><TableCell sx={{ fontFamily: "monospace" }}>{x.device}</TableCell><TableCell>{x.type}</TableCell>
                <TableCell>{gb(x.size)}</TableCell><TableCell><Chip size="small" label={`${pct}%`} color={pct >= 90 ? "error" : pct >= 80 ? "warning" : "default"} /></TableCell><TableCell>{gb(x.avail)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {(f.lvm?.vgs ?? []).length > 0 && (
        <>
          <Typography variant="subtitle1">LVM</Typography>
          <Facts rows={[
            ...(f.lvm.vgs as Any[]).map((v) => [`VG ${v.vg_name}`, `${gb(Number(v.vg_size))}, ${gb(Number(v.vg_free))} free, ${v.pv_count} PV · ${v.lv_count} LV`] as [string, ReactNode]),
            ...((f.lvm.lvs ?? []) as Any[]).map((l) => [`LV ${l.vg_name}/${l.lv_name}`, gb(Number(l.lv_size))] as [string, ReactNode]),
          ]} />
        </>
      )}
      {(f.zfs ?? []).length > 0 && <Facts rows={(f.zfs as Any[]).map((z) => [`ZFS ${z.name}`, `${gb(Number(z.size))}, ${gb(Number(z.free))} free, ${z.health}`] as [string, ReactNode])} />}
      {f.mdraid && /md\d/.test(f.mdraid) && <Paper variant="outlined" sx={{ p: 1 }}><pre style={{ margin: 0, fontSize: 12 }}>{f.mdraid}</pre></Paper>}
    </Stack>
  );
}

export function NicsSection({ view }: { view: AdminView }) {
  const nics: Any[] = view.facts?.nics ?? [];
  return (
    <Table size="small">
      <TableHead><TableRow><TableCell>Interface</TableCell><TableCell>Kind</TableCell><TableCell>State</TableCell><TableCell>Speed</TableCell><TableCell>Addresses</TableCell><TableCell>MAC</TableCell><TableCell>Bridge / members</TableCell><TableCell>Driver</TableCell></TableRow></TableHead>
      <TableBody>
        {nics.map((n) => (
          <TableRow key={n.name}>
            <TableCell sx={{ fontFamily: "monospace" }}>{n.name}</TableCell><TableCell>{n.kind}</TableCell>
            <TableCell><Chip size="small" label={n.state} color={n.state === "UP" ? "success" : "default"} /></TableCell>
            <TableCell>{n.speed_mbps ? `${n.speed_mbps >= 1000 ? `${n.speed_mbps / 1000} Gb/s` : `${n.speed_mbps} Mb/s`}` : "—"}</TableCell>
            <TableCell>{(n.addresses ?? []).join(", ")}</TableCell><TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{n.mac}</TableCell>
            <TableCell>{n.master ? `in ${n.master}` : (n.members ?? []).join(", ")}</TableCell><TableCell>{n.driver}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function OsSection({ view }: { view: AdminView }) {
  const os = view.facts?.os ?? {};
  const ts = os.timesync ?? {};
  return (
    <Facts rows={[
      ["Host name", os.hostname], ["OS", `${os.name ?? "—"} (${os.id ?? ""} ${os.version ?? ""})`], ["Kernel", os.kernel],
      ["Up", ago(os.uptime_seconds)], ["CPU", `${os.cpus} × ${os.cpu_model ?? ""}`], ["Memory", os.memory_mb ? `${os.memory_mb} MB` : "—"],
      ["Updates", os.updates == null ? "—" : String(os.updates)], ["Reboot required", os.reboot_required ? "yes" : "no"],
      ["Time sync", ts.NTPSynchronized === "yes" ? `synchronized (${ts.Timezone ?? ""})` : `not synchronized (${ts.Timezone ?? ""})`],
      ["Failed services", (os.failed_units ?? []).join(", ") || "none"],
      ["Tools", Object.entries(view.facts?.tools ?? {}).map(([k, v]) => `${k}${v ? "" : " (missing)"}`).join(", ")],
    ]} />
  );
}

function ManageDialog({ url, op, name, windows = false, onClose }: { url: string; op: string; name: string; windows?: boolean; onClose: (done: boolean) => void }) {
  const [form, setForm] = useState({ name, comment: "", groups: "", member: "", ssh_key: "", password: "" });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const go = async () => {
    try {
      const groups = (windows ? form.groups.split(",") : form.groups.split(/[\s,]+/)).map((g) => g.trim()).filter(Boolean);
      await api.post(url, { op, ...form, groups });
      onClose(true);
    } catch (e) { setErrs(fromApiError(e)); }
  };
  const titles: Record<string, string> = {
    "user.add": "Add an account", "user.groups": `Groups of ${name}`, "user.password": `Set ${name}'s password`,
    "group.add": "Add a group", "group.member_add": `Add a member to ${name}`,
  };
  return (
    <Dialog open onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>{titles[op] ?? op}</DialogTitle>
      <DialogContent>
        <Stack spacing={1} sx={{ mt: 1 }}>
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          {(op === "user.add" || op === "group.add") && <Field label="Name" value={form.name} error={errs.fields.name} help={windows ? "Letters, digits, spaces, . - and _" : "Lower-case letters, digits, - and _"} onChange={(e) => setForm({ ...form, name: e.target.value })} />}
          {op === "user.add" && <Field label="Full name or note" value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} />}
          {(op === "user.add" || op === "user.groups") && <Field label="Groups" value={form.groups} help="Comma-separated; replaces the account's extra groups" onChange={(e) => setForm({ ...form, groups: e.target.value })} />}
          {op === "user.add" && windows && <Field label="Password" type="password" value={form.password} fullWidth help="Sent once to the machine over WinRM; never stored" onChange={(e) => setForm({ ...form, password: e.target.value })} />}
          {op === "user.add" && !windows && <Field label="SSH public key (optional)" value={form.ssh_key} multiline fullWidth help="Lets the account sign in with that key" onChange={(e) => setForm({ ...form, ssh_key: e.target.value })} />}
          {op === "user.password" && <Field label="New password" type="password" value={form.password} fullWidth help={windows ? "Sent once to the machine over WinRM; never stored" : "Sent once over SSH (chpasswd); never stored"} onChange={(e) => setForm({ ...form, password: e.target.value })} />}
          {op === "group.member_add" && <Field label="Account" value={form.member} onChange={(e) => setForm({ ...form, member: e.target.value })} />}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)} title="Close without changing anything">Cancel</Button>
        <Button variant="contained" onClick={go} title="Apply the change (audited)">Apply</Button>
      </DialogActions>
    </Dialog>
  );
}

export function AccountsSection({ view, hostId, reload }: { view: AdminView; hostId: number; reload: () => void }) {
  return <AccountsTable url={`/api/hosts/${hostId}/accounts`} accounts={view.facts?.accounts ?? []} labape={view.host.ssh_user ?? ""}
    protectedNames={["root", view.host.ssh_user ?? ""]} reload={reload} />;
}

// Accounts on a KVM host or a VM (design §28.4, §28.6): `url` takes the changes.
export function AccountsTable({ url, accounts, labape, protectedNames, windows = false, reload }: {
  url: string; accounts: Any[]; labape: string; protectedNames: string[]; windows?: boolean; reload: () => void;
}) {
  const [dialog, setDialog] = useState<{ op: string; name: string } | null>(null);
  const [error, setError] = useState("");
  const direct = async (op: string, name: string) => {
    setError("");
    try { await api.post(url, { op, name }); reload(); } catch (e) { setError((e as Error).message); }
  };
  return (
    <Stack spacing={1}>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
      <Stack direction="row"><Box sx={{ flexGrow: 1 }} /><Button variant="contained" onClick={() => setDialog({ op: "user.add", name: "" })} title="Create a local account (opens a form)">Add account</Button></Stack>
      <Table size="small">
        <TableHead><TableRow><TableCell>Account</TableCell>{!windows && <TableCell>UID</TableCell>}<TableCell>Groups</TableCell>{!windows && <TableCell>Shell</TableCell>}<TableCell>Flags</TableCell><TableCell /></TableRow></TableHead>
        <TableBody>
          {accounts.map((a) => {
            const protectedAcct = protectedNames.includes(a.name);
            return (
              <TableRow key={a.name}>
                <TableCell><b>{a.name}</b> <Typography variant="caption" color="text.secondary">{a.comment}</Typography></TableCell>
                {!windows && <TableCell>{a.uid}</TableCell>}<TableCell>{(a.groups ?? []).join(", ")}</TableCell>{!windows && <TableCell>{a.shell}</TableCell>}
                <TableCell>{a.admin && <Chip size="small" label="admin" color="warning" sx={{ mr: 0.5 }} />}{a.locked && <Chip size="small" label="locked" />}{a.name === labape && <Chip size="small" label="LABaPe" color="primary" sx={{ ml: 0.5 }} />}</TableCell>
                <TableCell sx={{ whiteSpace: "nowrap" }}>
                  <Button size="small" onClick={() => setDialog({ op: "user.groups", name: a.name })} title="Change the account's groups">Groups</Button>
                  <Button size="small" onClick={() => setDialog({ op: "user.password", name: a.name })} title="Set a new password">Password</Button>
                  {!protectedAcct && (
                    <>
                      <ActionButton size="small" onClick={() => direct(a.locked ? "user.unlock" : "user.lock", a.name)} tip={a.locked ? "Allow sign-in again" : "Block sign-in (password and keys stay)"}
                        confirm={{ title: `${a.locked ? "Unlock" : "Lock"} ${a.name}?`, confirmLabel: a.locked ? "Unlock" : "Lock", body: a.locked ? "The account can sign in again." : "The account can't sign in with its password until it's unlocked." }}>
                        {a.locked ? "Unlock" : "Lock"}
                      </ActionButton>
                      <ActionButton size="small" color="error" onClick={() => direct("user.delete", a.name)} tip={windows ? "Delete the account" : "Delete the account and its home folder"}
                        confirm={{ title: `Delete ${a.name}?`, danger: true, confirmLabel: "Delete", body: windows ? "The account is removed. This can't be undone." : "The account and its home folder are removed. This can't be undone." }}>
                        Delete
                      </ActionButton>
                    </>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {dialog && <ManageDialog url={url} windows={windows} op={dialog.op} name={dialog.name} onClose={(done) => { setDialog(null); if (done) reload(); }} />}
    </Stack>
  );
}

export function GroupsSection({ view, hostId, reload }: { view: AdminView; hostId: number; reload: () => void }) {
  return <GroupsTable url={`/api/hosts/${hostId}/accounts`} groups={view.facts?.groups ?? []} reload={reload} />;
}

// Groups on a KVM host or a VM; system and built-in groups can't be deleted from here.
export function GroupsTable({ url, groups, windows = false, reload }: { url: string; groups: Any[]; windows?: boolean; reload: () => void }) {
  const [dialog, setDialog] = useState<{ op: string; name: string } | null>(null);
  const [error, setError] = useState("");
  const direct = async (op: string, name: string, member?: string) => {
    setError("");
    try { await api.post(url, { op, name, member: member ?? "" }); reload(); } catch (e) { setError((e as Error).message); }
  };
  return (
    <Stack spacing={1}>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
      <Stack direction="row"><Box sx={{ flexGrow: 1 }} /><Button variant="contained" onClick={() => setDialog({ op: "group.add", name: "" })} title="Create a group (opens a form)">Add group</Button></Stack>
      <Table size="small">
        <TableHead><TableRow><TableCell>Group</TableCell>{!windows && <TableCell>GID</TableCell>}<TableCell>Members</TableCell><TableCell /></TableRow></TableHead>
        <TableBody>
          {groups.map((g) => (
            <TableRow key={g.name}>
              <TableCell><b>{g.name}</b>{windows && g.comment && <Typography variant="caption" color="text.secondary"> {g.comment}</Typography>}</TableCell>{!windows && <TableCell>{g.gid}</TableCell>}
              <TableCell>
                {(g.members ?? []).map((m: string) => (
                  <Chip key={m} size="small" label={m} sx={{ mr: 0.5 }} onDelete={() => direct("group.member_remove", g.name, m)} />
                ))}
              </TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                <Button size="small" onClick={() => setDialog({ op: "group.member_add", name: g.name })} title="Add an account to this group">Add member</Button>
                {(windows ? !g.builtin : g.gid >= 1000) && (
                  <ActionButton size="small" color="error" onClick={() => direct("group.delete", g.name)} tip="Delete this group"
                    confirm={{ title: `Delete group ${g.name}?`, danger: true, confirmLabel: "Delete", body: "The group is removed; its members lose what it granted." }}>
                    Delete
                  </ActionButton>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {dialog && <ManageDialog url={url} windows={windows} op={dialog.op} name={dialog.name} onClose={(done) => { setDialog(null); if (done) reload(); }} />}
    </Stack>
  );
}

export function VmsSection({ view }: { view: AdminView }) {
  const vms: Any[] = view.resources?.stats?.vms ?? [];
  return (
    <Table size="small">
      <TableHead><TableRow><TableCell>VM</TableCell><TableCell>State</TableCell><TableCell>vCPU</TableCell><TableCell>Memory</TableCell><TableCell>CPU</TableCell><TableCell>Disk</TableCell></TableRow></TableHead>
      <TableBody>
        {vms.map((v) => (
          <TableRow key={v.name}>
            <TableCell>{v.name}</TableCell><TableCell><Chip size="small" label={v.state} color={v.state === "running" ? "success" : "default"} /></TableCell>
            <TableCell>{v.vcpus}</TableCell><TableCell>{v.memory_mb} MB</TableCell><TableCell>{v.cpu_pct != null ? `${v.cpu_pct}%` : "—"}</TableCell>
            <TableCell>{v.disk_allocated_gb} / {v.disk_capacity_gb} GB</TableCell>
          </TableRow>
        ))}
        {vms.length === 0 && <TableRow><TableCell colSpan={6}>No VMs on this host.</TableCell></TableRow>}
      </TableBody>
    </Table>
  );
}

// One catalog network the host will carry (question 127): only the host-specific parts.
interface NetChoice { on: boolean; Bridge: string; Nic: string; HostAddress: string }

export function AccessSection({ host, reload }: { host: KvmHost; reload: () => void }) {
  const navigate = useNavigate();
  const [form, setForm] = useState({ address: host.ssh_address || "", port: host.ssh_port || 22, account: host.ssh_user || "localadmin01", admin_user: "", admin_password: "" });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [msg, setMsg] = useState("");
  const [install, setInstall] = useState({ KvmHostName: host.name, ServiceUser: "labape", VmStoragePath: host.vm_storage_path, TemplateStoragePath: host.template_storage_path,
    IsoPath: "/data/OS_Images", FilesPath: host.files_dir || "", DefaultNetwork: "", ForwardHostLogs: true, InstallCli: true, CliCheckout: false });
  const [catalog, setCatalog] = useState<Network[]>([]);
  const [choices, setChoices] = useState<Record<number, NetChoice>>({});
  useEffect(() => {
    api.get<Network[]>("/api/networks").then((nets) => setCatalog(nets.filter((n) => n.enabled)), () => setCatalog([]));
  }, []);
  const choice = (id: number): NetChoice => choices[id] ?? { on: false, Bridge: "", Nic: "", HostAddress: "dhcp" };
  const setChoice = (id: number, patch: Partial<NetChoice>) => setChoices({ ...choices, [id]: { ...choice(id), ...patch } });
  const chosen = catalog.filter((n) => choice(n.id).on);
  const [fwdBusy, setFwdBusy] = useState(false);
  const forward = async (enabled: boolean) => {
    setFwdBusy(true); setMsg("");
    try {
      await api.post(`/api/hosts/${host.id}/log-forwarding`, { enabled });
      setMsg(enabled ? "The host's logs now go to the stack's syslog collector (see the Syslog tab)." : "Log forwarding removed.");
      reload();
    } catch (e) { setMsg((e as Error).message); reload(); } finally { setFwdBusy(false); }
  };
  const setup = async () => {
    setErrs({ summary: "", fields: {} });
    try {
      await api.post(`/api/hosts/${host.id}/access`, form);
      setForm({ ...form, admin_password: "" });
      setMsg(`Access set up: LABaPe signs in as ${form.account} with its own key. Facts appear within a minute.`);
      reload();
    } catch (e) { setForm({ ...form, admin_password: "" }); setErrs(fromApiError(e)); }
  };
  const check = async () => {
    try { const h = await api.post<KvmHost>(`/api/hosts/${host.id}/access/check`); setMsg(h.access_status === "ready" ? "SSH access works." : `SSH access failed: ${h.access_error}`); reload(); } catch (e) { setMsg((e as Error).message); }
  };
  const prepare = async () => {
    setErrs({ summary: "", fields: {} });
    try {
      const NetworkChoices = chosen.map((n) => ({ network_id: n.id, Bridge: choice(n.id).Bridge, Nic: choice(n.id).Nic,
        HostAddress: choice(n.id).HostAddress || "dhcp" }));
      const r = await api.post<{ job_id: number }>(`/api/hosts/${host.id}/install`,
        { ...install, DefaultNetwork: install.DefaultNetwork || chosen[0]?.name || "", NetworkChoices });
      navigate(`/jobs/${r.job_id}`);
    } catch (e) { setErrs(fromApiError(e)); }
  };
  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1.5}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>SSH access <Chip size="small" label={host.access_status} color={host.access_status === "ready" ? "success" : host.access_status === "failed" ? "error" : "default"} /></Typography>
          <Typography variant="body2" color="text.secondary">
            LABaPe reads and manages this host as its own account ({form.account}), separate from the service user, signing in with
            a key it keeps. Type an existing admin login once: it creates the account (key-only, passwordless sudo) and isn&apos;t stored.
            {host.access_at ? ` Last checked ${fmtTime(host.access_at)}.` : ""}
          </Typography>
          {msg && <Alert severity="info" onClose={() => setMsg("")}>{msg}</Alert>}
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
            <Field label="Address" value={form.address} error={errs.fields.address} help="Host name or IP" onChange={(e) => setForm({ ...form, address: e.target.value })} />
            <Field label="Port" type="number" value={form.port} sx={{ width: 100 }} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} />
            <Field label="LABaPe account" value={form.account} error={errs.fields.account} help="Created if missing" onChange={(e) => setForm({ ...form, account: e.target.value })} />
            <Field label="Admin user" value={form.admin_user} error={errs.fields.admin_user} help="root, or a user with sudo" autoComplete="off" onChange={(e) => setForm({ ...form, admin_user: e.target.value })} />
            <Field label="Admin password" type="password" value={form.admin_password} error={errs.fields.admin_password} help="Used once, never stored" autoComplete="new-password" onChange={(e) => setForm({ ...form, admin_password: e.target.value })} />
          </Stack>
          <Stack direction="row" spacing={2}>
            <ActionButton variant="contained" onClick={setup} disabled={!form.address || !form.admin_user || !form.admin_password}
              tip="Sign in once with the admin login and create LABaPe's access account"
              confirm={{ title: `Set up access to ${form.address}?`, confirmLabel: "Set up",
                body: `LABaPe signs in as ${form.admin_user}, creates or updates ${form.account} with its key and passwordless sudo, then signs in as ${form.account} to check. The password is used only for this.` }}>
              Set up access
            </ActionButton>
            {host.ssh_address && <ActionButton onClick={check} tip="Sign in as the access account and check sudo (changes nothing)">Check</ActionButton>}
          </Stack>
        </Stack>
      </Paper>
      {host.role !== "stack" && <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1.5}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Prepare this host</Typography>
          <Typography variant="body2" color="text.secondary">
            Installs what a KVM host needs (libvirt and QEMU, bridges, storage folders, Ansible, OpenTofu, Packer) with the installer&apos;s kvm profile,
            as a job, through the access account. The host must run Debian, Rocky or Alma (9 or later). Afterwards the host&apos;s libvirt URI points at it
            and it carries the networks chosen below. Jobs bring their own files, so the host needs no command-line setup unless you want one.
          </Typography>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
            {(["KvmHostName", "ServiceUser", "VmStoragePath", "TemplateStoragePath", "IsoPath", "FilesPath"] as const).map((k) => (
              <Field key={k} label={k} value={install[k]} error={errs.fields[k]} onChange={(e) => setInstall({ ...install, [k]: e.target.value })} />
            ))}
            <FormControlLabel label="Send its logs to the syslog collector" title="Afterwards, forward the host's own logs to the stack's collector over TLS"
              control={<Checkbox checked={install.ForwardHostLogs} onChange={(e) => setInstall({ ...install, ForwardHostLogs: e.target.checked })} />} />
            <FormControlLabel label="OpenTofu and Packer" title="The host's deploy and template jobs run them there"
              control={<Checkbox checked={install.InstallCli} onChange={(e) => setInstall({ ...install, InstallCli: e.target.checked })} />} />
            <FormControlLabel label="Command-line checkout too" title="Also set up the vault, environment.yml and manifests for running LABaPe's command line on this host"
              control={<Checkbox checked={install.CliCheckout} onChange={(e) => setInstall({ ...install, CliCheckout: e.target.checked })} />} />
          </Stack>
          <Typography variant="subtitle2">Networks it carries (from Admin → Networks)</Typography>
          {errs.fields.NetworkChoices && <Alert severity="error">{errs.fields.NetworkChoices.message}</Alert>}
          {catalog.length === 0 && <Typography variant="body2" color="text.secondary">No networks yet: add them in Admin → Networks first.</Typography>}
          {catalog.map((n) => {
            const c = choice(n.id);
            return (
              <Stack key={n.id} direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "flex-start" }}>
                <FormControlLabel sx={{ minWidth: 240 }} label={`${n.name} (${n.cidr})`}
                  control={<Checkbox checked={c.on} onChange={(e) => setChoice(n.id, { on: e.target.checked })} />} />
                {c.on && (
                  <>
                    <Field label="Bridge" value={c.Bridge} sx={{ width: 120 }} help="Created if missing" onChange={(e) => setChoice(n.id, { Bridge: e.target.value })} />
                    <Field label="NIC" value={c.Nic} sx={{ width: 120 }} help="On the new host, e.g. eno2" onChange={(e) => setChoice(n.id, { Nic: e.target.value })} />
                    <Field label="Host address" value={c.HostAddress} sx={{ width: 170 }} help="The bridge's: dhcp or a CIDR" onChange={(e) => setChoice(n.id, { HostAddress: e.target.value })} />
                  </>
                )}
              </Stack>
            );
          })}
          {chosen.length > 1 && (
            <Field select label="Default network" value={install.DefaultNetwork || chosen[0].name} sx={{ width: 240 }}
              help="For host groups that don't name one" onChange={(e) => setInstall({ ...install, DefaultNetwork: e.target.value })}>
              {chosen.map((n) => <MenuItem key={n.id} value={n.name}>{n.name}</MenuItem>)}
            </Field>
          )}
          <Stack direction="row">
            <ActionButton variant="outlined" disabled={host.access_status !== "ready" || chosen.length === 0 || chosen.some((n) => !choice(n.id).Bridge || !choice(n.id).Nic)} onClick={prepare}
              tip={host.access_status === "ready" ? "Run the installer on this host as a job" : "Set up SSH access first"}
              confirm={{ title: `Prepare ${host.name}?`, confirmLabel: "Prepare", danger: true,
                body: "The installer runs as root on the host: it installs packages, creates bridges on the NICs you named (network connections may drop briefly), the service user and the storage folders. Finished steps are skipped on a re-run." }}>
              Prepare this host
            </ActionButton>
          </Stack>
        </Stack>
      </Paper>}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1.5}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            Logs to the syslog collector <Chip size="small" label={host.log_forward ? "on" : "off"} color={host.log_forward ? "success" : "default"} />
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Sends everything the host logs to the stack&apos;s collector (the Syslog tab) over TLS with its own client certificate from the platform CA,
            renewed automatically. Installs rsyslog if needed, through the access account.
            {host.log_forward && host.log_forward_until ? ` Certificate valid until ${fmtTime(host.log_forward_until)}.` : ""}
          </Typography>
          {host.log_forward_error && <Alert severity="warning">Last attempt failed: {host.log_forward_error}</Alert>}
          <Stack direction="row">
            <ActionButton variant="outlined" disabled={host.access_status !== "ready" || fwdBusy} onClick={() => forward(!host.log_forward)}
              tip={host.access_status !== "ready" ? "Set up SSH access first" : host.log_forward ? "Stop sending this host's logs" : "Send this host's logs to the collector"}>
              {fwdBusy ? "Working…" : host.log_forward ? "Stop sending logs" : "Send logs"}
            </ActionButton>
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}

export function SectionHeader({ view, onRefresh, title }: { view: AdminView | null; onRefresh: () => Promise<void>; title: string }) {
  const [err, setErr] = useState("");
  return (
    <Stack spacing={0.5}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h6">{title}</Typography>
        {view?.host.access_status === "ready" && (
          <ConnectButton target={{ target_type: "host", protocol: "ssh", kvm_host_id: view.host.id }} label="Open SSH" onError={setErr} />
        )}
      </Stack>
      {err && <Alert severity="error" onClose={() => setErr("")}>{err}</Alert>}
      {view && view.host.access_status === "ready" && <Collected view={view} onRefresh={onRefresh} />}
      {view && view.host.access_status !== "ready" && <NeedsAccess host={view.host} />}
    </Stack>
  );
}

