// One VM of an environment (design §28.6, questions 119-120): sections on the
// left like a KVM host's. What the guest agent reports needs no login; accounts
// and groups are read live (owners and admins); consoles go through the KVM host.
import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  Alert, Box, Chip, List, ListItemButton, ListItemText, Paper, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { DetailHeader, Facts } from "../components/PageParts";
import { ActionButton } from "../components/Confirm";
import { ConnectButton, RdpFileLink } from "../components/Connect";
import { AccountsTable, GroupsTable } from "../components/HostSections";
import Sparkline from "../components/Sparkline";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
interface Consoles { vnc: boolean; serial: boolean; host_ready: boolean; host_id: number | null; virt_viewer: string }
interface VmView {
  name: string; environment: string; environment_id: number; group: string; os: string; roles: string[]; start_order: number;
  address: string; user: string; collected_at: string | null; state: string; online: boolean; responding: boolean | null;
  size: Any; guest: Any; guest_disk: Any; series: Record<string, (number | null)[]> & { t: string[] };
  can_operate: boolean; consoles: Consoles;
}

const SECTIONS = [
  { key: "dashboard", label: "Dashboard" }, { key: "disks", label: "Disks and volumes" }, { key: "nics", label: "NICs" },
  { key: "os", label: "OS" }, { key: "accounts", label: "Accounts" }, { key: "groups", label: "Groups" },
  { key: "console", label: "Console" },
] as const;

const gb = (b?: number | null) => (b == null ? "—" : `${(b / 2 ** 30).toFixed(1)} GB`);
const rate = (v: number) => (v >= 2 ** 20 ? `${(v / 2 ** 20).toFixed(1)} MB/s` : `${(v / 1024).toFixed(0)} KB/s`);
const stateColor = (s: string) => (s === "running" ? "success" : s === "missing" ? "error" : "default");

function NoAgent({ view }: { view: VmView }) {
  return (
    <Alert severity="info">
      {view.state !== "running" ? `${view.name} is ${view.state}: the guest agent reports only while it runs.`
        : "The guest agent hasn't answered yet (it reports within a few minutes of the VM starting)."}
    </Alert>
  );
}

function Dashboard({ view }: { view: VmView }) {
  const s = view.series;
  const z = view.size ?? {};
  return (
    <Stack spacing={2}>
      <Facts rows={[
        ["State", <Chip key="s" size="small" label={view.state} color={stateColor(view.state)} />],
        ["Guest agent", !view.online ? "—" : view.responding === true ? "answers" : view.responding === false ? "not answering" : "none"],
        ["OS", view.guest?.os?.["pretty-name"] ?? view.os], ["Address", view.address || "—"],
        ["Size", z.vcpus ? `${z.vcpus} vCPU · ${(z.memory_mb / 1024).toFixed(1)} GB memory · ${z.disk_capacity_gb ?? "—"} GB disk` : "—"],
        ["Group", `${view.group} (start order ${view.start_order})`], ["Roles", view.roles.join(", ") || "—"],
        ["Read", view.collected_at ? fmtTime(view.collected_at) : "not yet"],
      ]} />
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Last 24 hours</Typography>
        <Table size="small">
          <TableBody>
            {([["CPU", "cpu_pct", 100, (v: number) => `${v.toFixed(0)}%`], ["Memory used", "memory_used_mb", z.memory_mb, (v: number) => `${v.toFixed(0)} MB`],
              ["Disk read", "disk_read_bps", undefined, rate], ["Disk write", "disk_write_bps", undefined, rate],
              ["Free disk (guest)", "disk_free_gb", undefined, (v: number) => `${v.toFixed(1)} GB`]] as [string, string, number | undefined, (v: number) => string][])
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
    </Stack>
  );
}

function Disks({ view }: { view: VmView }) {
  const g = view.guest;
  if (!g?.filesystems) return <NoAgent view={view} />;
  return (
    <Stack spacing={2}>
      <Table size="small">
        <TableHead><TableRow><TableCell>Mount</TableCell><TableCell>Device</TableCell><TableCell>Type</TableCell><TableCell>Size</TableCell><TableCell>Used</TableCell></TableRow></TableHead>
        <TableBody>
          {(g.filesystems as Any[]).map((f, i) => {
            const pct = f.total ? Math.round((f.used / f.total) * 100) : null;
            return (
              <TableRow key={i}>
                <TableCell sx={{ fontFamily: "monospace" }}>{f.mount}</TableCell><TableCell>{f.name}</TableCell><TableCell>{f.type}</TableCell>
                <TableCell>{gb(f.total)}</TableCell>
                <TableCell>{pct == null ? "—" : <Chip size="small" label={`${gb(f.used)} (${pct}%)`} color={pct >= 90 ? "error" : pct >= 80 ? "warning" : "default"} />}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {(g.disks ?? []).length > 0 && (
        <Table size="small">
          <TableHead><TableRow><TableCell>Disk (guest)</TableCell><TableCell>Host alias</TableCell><TableCell>Partition</TableCell></TableRow></TableHead>
          <TableBody>
            {(g.disks as Any[]).map((d, i) => (
              <TableRow key={i}><TableCell sx={{ fontFamily: "monospace" }}>{d.name}</TableCell><TableCell>{d.alias ?? "—"}</TableCell><TableCell>{d.partition === "yes" ? "yes" : "no"}</TableCell></TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Stack>
  );
}

function Nics({ view }: { view: VmView }) {
  const ifs: Any[] = view.guest?.interfaces ?? [];
  if (!view.guest?.interfaces) return <NoAgent view={view} />;
  return (
    <Table size="small">
      <TableHead><TableRow><TableCell>Interface</TableCell><TableCell>MAC</TableCell><TableCell>Addresses</TableCell></TableRow></TableHead>
      <TableBody>
        {ifs.map((n) => (
          <TableRow key={n.name}>
            <TableCell sx={{ fontFamily: "monospace" }}>{n.name}</TableCell><TableCell sx={{ fontFamily: "monospace" }}>{n.hwaddr ?? "—"}</TableCell>
            <TableCell>{(n.addresses ?? []).join(", ") || "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Os({ view }: { view: VmView }) {
  const g = view.guest;
  if (!g?.os) return <NoAgent view={view} />;
  return (
    <Stack spacing={2}>
      <Facts rows={[
        ["Host name", g.hostname || "—"], ["OS", g.os["pretty-name"] ?? g.os.name ?? "—"], ["Version", g.os["version-id"] ?? g.os.version ?? "—"],
        ["Kernel", `${g.os["kernel-release"] ?? ""} ${g.os.machine ?? ""}`.trim() || "—"], ["Time zone", g.timezone || "—"],
      ]} />
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>Signed in now</Typography>
        {(g.users ?? []).length === 0 ? <Typography variant="body2">Nobody.</Typography> : (
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
            {(g.users as Any[]).map((u, i) => (
              <Chip key={i} size="small" label={`${u.domain ? `${u.domain}\\` : ""}${u.name}${u.login_time ? ` since ${fmtTime(new Date(Number(u.login_time)).toISOString())}` : ""}`} />
            ))}
          </Stack>
        )}
      </Paper>
    </Stack>
  );
}

function useAccounts(envId: number, vm: string, enabled: boolean) {
  const [data, setData] = useState<Any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    if (!enabled) return;
    setBusy(true);
    api.get<Any>(`/api/environments/${envId}/vms/${vm}/accounts`)
      .then((d) => { setData(d); setError(""); }, (e) => setError(e.message)).finally(() => setBusy(false));
  }, [envId, vm, enabled]);
  useEffect(load, [load]);
  return { data, error, busy, load };
}

function Accounts({ view, which }: { view: VmView; which: "accounts" | "groups" }) {
  const { data, error, busy, load } = useAccounts(view.environment_id, view.name, view.can_operate && view.state === "running");
  if (!view.can_operate) return <Alert severity="info">The environment&apos;s owners and admins see and change its VMs&apos; accounts.</Alert>;
  if (view.state !== "running") return <Alert severity="info">{view.name} is {view.state}: start it to read its accounts.</Alert>;
  const url = `/api/environments/${view.environment_id}/vms/${view.name}/accounts`;
  return (
    <Stack spacing={1}>
      <Typography variant="body2" color="text.secondary">
        Read live {data?.windows ? "over WinRM" : "over SSH"} as the environment&apos;s admin account{busy ? " (reading…)" : ""}. Changes are audited.
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {data?.note && <Alert severity="info">{data.note}</Alert>}
      {data && !data.note && (which === "accounts"
        ? <AccountsTable url={url} accounts={data.accounts} labape={view.user} windows={data.windows}
            protectedNames={data.windows ? ["Administrator", "Guest", "DefaultAccount", "WDAGUtilityAccount"] : ["root", view.user]} reload={load} />
        : <GroupsTable url={url} groups={data.groups} windows={data.windows} reload={load} />)}
    </Stack>
  );
}

function Console({ view, onError }: { view: VmView; onError: (m: string) => void }) {
  const c = view.consoles;
  const target = (protocol: "vnc" | "serial") => ({ target_type: "vm" as const, protocol, environment_id: view.environment_id, vm: view.name });
  const windows = view.os.startsWith("windows");
  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        The VM&apos;s own console, as if you sat at it: it works before the network is up and while the VM boots. It goes through the
        KVM host&apos;s LABaPe access account; sessions are audited and recorded when the admins turned recording on.
      </Typography>
      {!view.can_operate && <Alert severity="info">The environment&apos;s owners and admins open consoles.</Alert>}
      {view.can_operate && !c.host_ready && (
        <Alert severity="info">An admin sets up the KVM host&apos;s SSH access first (Admin → KVM hosts → SSH access); consoles go through it.</Alert>
      )}
      {view.can_operate && c.host_ready && view.state !== "running" && <Alert severity="info">Start {view.name} to open its console.</Alert>}
      {view.can_operate && c.host_ready && view.state === "running" && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1}>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              {c.vnc && <ConnectButton target={target("vnc")} label="Open VNC console" onError={onError} />}
              {c.serial && <ConnectButton target={target("serial")} label="Open serial console" onError={onError} />}
            </Stack>
            {c.serial && <Typography variant="caption" color="text.secondary">Serial console: press Enter for a login prompt; Ctrl+] leaves it.</Typography>}
          </Stack>
        </Paper>
      )}
      {view.can_operate && view.state === "running" && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>Over the network</Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            {windows
              ? <><ConnectButton target={{ ...target("vnc"), protocol: "rdp" }} label="RDP" onError={onError} /><RdpFileLink envId={view.environment_id} vm={view.name} /></>
              : <ConnectButton target={{ ...target("vnc"), protocol: "ssh" }} label="SSH" onError={onError} />}
          </Stack>
        </Paper>
      )}
      {view.can_operate && c.virt_viewer && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>virt-viewer (optional)</Typography>
          <Typography variant="body2" color="text.secondary" gutterBottom>
            On a machine with virt-viewer and an SSH key the KVM host&apos;s access account accepts (not audited by LABaPe):
          </Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Box component="code" sx={{ flex: 1, p: 1, bgcolor: "action.hover", borderRadius: 1, fontSize: 12, wordBreak: "break-all" }}>{c.virt_viewer}</Box>
            <ActionButton size="small" tip="Copy the command" onClick={() => navigator.clipboard.writeText(c.virt_viewer)}>Copy</ActionButton>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}

export default function VmDetail() {
  const { id, vm } = useParams();
  const [params, setParams] = useSearchParams();
  const section = params.get("section") ?? "dashboard";
  const [view, setView] = useState<VmView | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api.get<VmView>(`/api/environments/${id}/vms/${vm}`).then((v) => { setView(v); setError(""); }, (e) => setError(e.message));
  }, [id, vm]);
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);
  const body = () => {
    if (!view) return null;
    if (section === "disks") return <Disks view={view} />;
    if (section === "nics") return <Nics view={view} />;
    if (section === "os") return <Os view={view} />;
    if (section === "accounts" || section === "groups") return <Accounts key={section} view={view} which={section} />;
    if (section === "console") return <Console view={view} onError={setError} />;
    return <Dashboard view={view} />;
  };
  return (
    <Stack spacing={2}>
      <DetailHeader back={`/environments/${id}`} backLabel={view?.environment ?? "Environment"} title={vm}
        actions={view && <Chip size="small" label={view.state} color={stateColor(view.state)} />} />
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
      <Box sx={{ display: "flex", gap: 2, alignItems: "flex-start" }}>
        <Paper variant="outlined" sx={{ width: 190, flexShrink: 0 }}>
          <List dense disablePadding>
            {SECTIONS.map((x) => (
              <ListItemButton key={x.key} selected={section === x.key} onClick={() => setParams({ section: x.key }, { replace: true })}>
                <ListItemText primary={x.label} />
              </ListItemButton>
            ))}
          </List>
        </Paper>
        <Stack spacing={2} sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="h6">{SECTIONS.find((x) => x.key === section)?.label}</Typography>
          {body()}
        </Stack>
      </Box>
    </Stack>
  );
}
