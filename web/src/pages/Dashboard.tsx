import { Fragment, ReactNode, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert, Box, Button, Chip, Collapse, IconButton, LinearProgress, Link, Paper, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Tooltip, Typography,
} from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import { api, fmtTime } from "../api";
import Sparkline from "../components/Sparkline";
import { hasRole, useAuth } from "../App";

type Series = Record<string, (number | null)[]> & { t: string[] };
interface DashHost {
  id: number; name: string; enabled: boolean; collected_at: string | null; error: string;
  cpus: number | null; cpu_pct: number | null; load: number[];
  memory_total_mb: number | null; memory_used_mb: number | null;
  disk_total_gb: number | null; disk_free_gb: number | null;
  disk_read_bps: number | null; disk_write_bps: number | null;
  vms_running: number | null; vms_defined: number | null; series: Series;
}
interface DashVm {
  name: string; state: string; online: boolean; responding: boolean | null; vcpus?: number; cpu_pct?: number | null;
  memory_mb?: number; memory_used_mb?: number | null; disk_total_gb?: number | null; disk_free_gb?: number | null;
  disk_capacity_gb?: number; disk_allocated_gb?: number; disk_read_bps?: number | null; disk_write_bps?: number | null;
}
interface DashEnv {
  id: number; name: string; status: string; kvm_host: string; host_count: number; online: number;
  not_responding: number; cpu_pct: number | null; memory_mb: number | null; memory_used_mb: number | null;
  disk_total_gb: number | null; disk_free_gb: number | null; disk_read_bps: number | null;
  disk_write_bps: number | null; vms: DashVm[]; series: Series;
}
interface Check { name: string; display_name: string; status: "ok" | "error" | "unknown"; message: string }
interface AdminHealth {
  auth: (Check & { enabled: boolean; last_sign_in: string | null })[];
  secrets: Check[];
  notifications: (Check & { enabled: boolean; summary: string; last: { at: string; ok: boolean; error: string } | null })[];
  version: { current: string; latest: string; url: string; error: string; checked_at: string | null; update_available: boolean };
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${v.toFixed(0)}%`);
const gb = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} GB`);
const rate = (v: number | null | undefined) => {
  if (v === null || v === undefined) return "–";
  if (v >= 1048576) return `${(v / 1048576).toFixed(1)} MB/s`;
  if (v >= 1024) return `${(v / 1024).toFixed(0)} kB/s`;
  return `${v.toFixed(0)} B/s`;
};

function Meter({ label, value, max, detail, trend }: {
  label: string; value: number | null; max: number | null; detail: string; trend?: ReactNode;
}) {
  const ratio = value !== null && max ? value / max : null;
  return (
    <Box sx={{ minWidth: 180, flex: 1 }}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="caption">{detail}</Typography>
      </Stack>
      <LinearProgress variant={ratio === null ? "indeterminate" : "determinate"} value={Math.min(100, (ratio ?? 0) * 100)}
        color={ratio !== null && ratio > 0.9 ? "error" : ratio !== null && ratio > 0.75 ? "warning" : "primary"}
        sx={{ height: 6, borderRadius: 1, opacity: ratio === null ? 0.3 : 1 }} aria-label={label} />
      {trend && <Box sx={{ mt: 0.5, color: "primary.main" }}>{trend}</Box>}
    </Box>
  );
}

function HostCard({ h }: { h: DashHost }) {
  const diskUsed = h.disk_total_gb !== null && h.disk_free_gb !== null ? h.disk_total_gb - h.disk_free_gb : null;
  return (
    <Paper variant="outlined" sx={{ p: 2, flex: "1 1 420px" }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{h.name}</Typography>
        {!h.enabled && <Chip size="small" label="disabled" />}
        <Box sx={{ flexGrow: 1 }} />
        <Typography variant="caption" color="text.secondary">
          {h.vms_running ?? "–"} of {h.vms_defined ?? "–"} VMs running{h.load.length ? ` · load ${h.load[0].toFixed(1)}` : ""}
        </Typography>
      </Stack>
      {h.error && <Alert severity="warning" sx={{ mb: 1 }}>Not collected: {h.error}</Alert>}
      {!h.collected_at && !h.error && <Typography variant="body2" color="text.secondary">Waiting for the first sample (every minute).</Typography>}
      <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
        <Meter label="CPU" value={h.cpu_pct} max={100} detail={`${pct(h.cpu_pct)} of ${h.cpus ?? "–"} CPUs`}
          trend={<Sparkline label="CPU" values={h.series.cpu_pct ?? []} times={h.series.t} max={100} format={(v) => `${v.toFixed(0)}%`} />} />
        <Meter label="RAM" value={h.memory_used_mb} max={h.memory_total_mb}
          detail={`${gb(h.memory_used_mb !== null ? h.memory_used_mb / 1024 : null)} of ${gb(h.memory_total_mb !== null ? h.memory_total_mb / 1024 : null)}`}
          trend={<Sparkline label="RAM used" values={h.series.memory_used_pct ?? []} times={h.series.t} max={100} format={(v) => `${v.toFixed(0)}%`} />} />
        <Meter label="VM storage" value={diskUsed} max={h.disk_total_gb} detail={`${gb(h.disk_free_gb)} free`}
          trend={<Sparkline label="Free space" values={h.series.disk_free_gb ?? []} times={h.series.t} format={(v) => `${v.toFixed(0)} GB`} />} />
        <Box sx={{ minWidth: 180, flex: 1 }}>
          <Typography variant="caption" color="text.secondary">Disk activity</Typography>
          <Typography variant="body2">read {rate(h.disk_read_bps)} · write {rate(h.disk_write_bps)}</Typography>
          <Box sx={{ color: "primary.main" }}>
            <Sparkline label="Disk writes" values={h.series.write_bps ?? []} times={h.series.t} format={rate} />
          </Box>
        </Box>
      </Stack>
    </Paper>
  );
}

function VmState({ v }: { v: DashVm }) {
  if (v.state === "missing") return <Chip size="small" label="not found" variant="outlined" />;
  if (!v.online) return <Chip size="small" label="offline" />;
  if (v.responding === false) {
    return <Tooltip title="Running, but the guest agent doesn't answer"><Chip size="small" color="warning" label="not responding" /></Tooltip>;
  }
  return <Chip size="small" color="success" label="online" />;
}

function vmDisk(v: DashVm) {
  if (v.disk_free_gb !== null && v.disk_free_gb !== undefined) return `${gb(v.disk_free_gb)} free of ${gb(v.disk_total_gb)}`;
  if (v.disk_capacity_gb) {
    return <Tooltip title="No guest agent: this is the disk file's allocation, not free space inside the guest">
      <span>{gb(v.disk_allocated_gb)} of {gb(v.disk_capacity_gb)} allocated</span>
    </Tooltip>;
  }
  return "–";
}

function EnvRows({ e }: { e: DashEnv }) {
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <TableRow hover>
        <TableCell padding="checkbox">
          <IconButton size="small" aria-label={open ? "Hide hosts" : "Show hosts"} onClick={() => setOpen(!open)}>
            {open ? <KeyboardArrowDownIcon /> : <KeyboardArrowRightIcon />}
          </IconButton>
        </TableCell>
        <TableCell><Link component={RouterLink} to={`/environments/${e.id}`}>{e.name}</Link></TableCell>
        <TableCell>{e.kvm_host}</TableCell>
        <TableCell>
          <Stack direction="row" spacing={0.5}>
            <Chip size="small" color={e.online === e.host_count ? "success" : e.online ? "warning" : "default"}
              label={`${e.online}/${e.host_count} online`} />
            {e.not_responding > 0 && <Chip size="small" color="warning" label={`${e.not_responding} not responding`} />}
          </Stack>
        </TableCell>
        <TableCell>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <span>{pct(e.cpu_pct)}</span>
            <Box sx={{ color: "primary.main" }}><Sparkline label="CPU" values={e.series.cpu_pct ?? []} times={e.series.t} max={100} width={80} format={(v) => `${v.toFixed(0)}%`} /></Box>
          </Stack>
        </TableCell>
        <TableCell>{e.memory_used_mb !== null ? `${gb(e.memory_used_mb / 1024)} of ` : ""}{gb(e.memory_mb !== null ? e.memory_mb / 1024 : null)}</TableCell>
        <TableCell>{e.disk_free_gb !== null ? `${gb(e.disk_free_gb)} free` : "–"}</TableCell>
        <TableCell>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <span>{rate((e.disk_read_bps ?? 0) + (e.disk_write_bps ?? 0))}</span>
            <Box sx={{ color: "primary.main" }}><Sparkline label="Disk writes" values={e.series.disk_write_bps ?? []} times={e.series.t} width={80} format={rate} /></Box>
          </Stack>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={8} sx={{ py: 0, borderBottom: open ? undefined : "none" }}>
          <Collapse in={open} unmountOnExit>
            <Table size="small" sx={{ my: 1 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Host</TableCell><TableCell>State</TableCell><TableCell>CPU</TableCell>
                  <TableCell>RAM</TableCell><TableCell>Disk</TableCell><TableCell>Disk activity</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {e.vms.map((v) => (
                  <TableRow key={v.name}>
                    <TableCell>{v.name}</TableCell>
                    <TableCell><VmState v={v} /></TableCell>
                    <TableCell>{v.online ? `${pct(v.cpu_pct)} of ${v.vcpus ?? "–"} vCPU` : "–"}</TableCell>
                    <TableCell>{v.memory_used_mb != null ? `${gb(v.memory_used_mb / 1024)} of ` : ""}{v.memory_mb ? gb(v.memory_mb / 1024) : "–"}</TableCell>
                    <TableCell>{vmDisk(v)}</TableCell>
                    <TableCell>{v.online ? `read ${rate(v.disk_read_bps)} · write ${rate(v.disk_write_bps)}` : "–"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Collapse>
        </TableCell>
      </TableRow>
    </Fragment>
  );
}

function StatusIcon({ status }: { status: Check["status"] }) {
  if (status === "ok") return <CheckCircleIcon color="success" fontSize="small" />;
  if (status === "error") return <ErrorIcon color="error" fontSize="small" />;
  return <HelpOutlineIcon color="disabled" fontSize="small" />;
}

function CheckList({ title, items, extra }: {
  title: string; items: (Check & { enabled?: boolean })[]; extra?: (c: Check & Record<string, unknown>) => ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2, flex: "1 1 320px" }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>{title}</Typography>
      <Stack spacing={1}>
        {items.map((c) => (
          <Stack key={c.name} direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
            <Box sx={{ mt: "2px" }}><StatusIcon status={c.status} /></Box>
            <Box>
              <Typography variant="body2">
                {c.display_name}
                {c.enabled === false && <Chip size="small" label="off" sx={{ ml: 1, height: 18 }} />}
              </Typography>
              <Typography variant="caption" color="text.secondary" component="div">{c.message}</Typography>
              {extra?.(c as Check & Record<string, unknown>)}
            </Box>
          </Stack>
        ))}
      </Stack>
    </Paper>
  );
}

// The landing page (design §22.1): KVM host load and the user's environments,
// filtered to what they may see; admins also get platform health.
export default function Dashboard() {
  const { me } = useAuth();
  const [data, setData] = useState<{ hosts: DashHost[]; environments: DashEnv[]; is_admin: boolean } | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);

  const load = () => {
    api.get<{ hosts: DashHost[]; environments: DashEnv[]; is_admin: boolean }>("/api/dashboard").then((d) => {
      setData(d);
      if (d.is_admin) api.get<AdminHealth>("/api/dashboard/admin").then(setHealth, () => setHealth(null));
    }, (e) => setError(e.message));
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const checkNow = async () => {
    setChecking(true);
    try {
      const v = await api.post<AdminHealth["version"]>("/api/dashboard/admin/check-updates");
      setHealth((h) => (h ? { ...h, version: v } : h));
    } finally {
      setChecking(false);
    }
  };

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <LinearProgress />;
  const v = health?.version;
  return (
    <Stack spacing={3}>
      <Stack direction="row" sx={{ alignItems: "center" }}>
        <Typography variant="h5">Dashboard</Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Typography variant="caption" color="text.secondary">Refreshes every minute</Typography>
      </Stack>

      {v && (
        <Alert severity={v.update_available ? "info" : "success"} icon={false}
          action={<Button size="small" disabled={checking} onClick={checkNow}>Check now</Button>}>
          LABaPe <b>{v.current}</b>
          {v.update_available ? (
            <> · <Link href={v.url} target="_blank" rel="noreferrer">{v.latest}</Link> is available</>
          ) : v.latest ? " · up to date" : ""}
          {v.error && ` · ${v.error}`}
          {v.checked_at && <Typography variant="caption" color="text.secondary"> (checked {fmtTime(v.checked_at)})</Typography>}
        </Alert>
      )}

      <Box>
        <Typography variant="h6" sx={{ mb: 1 }}>KVM hosts</Typography>
        <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
          {data.hosts.map((h) => <HostCard key={h.id} h={h} />)}
          {data.hosts.length === 0 && <Typography variant="body2" color="text.secondary">No KVM hosts you can use.</Typography>}
        </Stack>
      </Box>

      <Box>
        <Stack direction="row" sx={{ alignItems: "center", mb: 1 }}>
          <Typography variant="h6">{data.is_admin ? "All environments" : "Your environments"}</Typography>
          <Box sx={{ flexGrow: 1 }} />
          {hasRole(me, "deployer", "template_editor") && (
            <Button component={RouterLink} to="/environments/new" variant="outlined" size="small">New environment</Button>
          )}
        </Stack>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell padding="checkbox" /><TableCell>Environment</TableCell><TableCell>KVM host</TableCell>
              <TableCell>Hosts</TableCell><TableCell>CPU</TableCell><TableCell>RAM</TableCell>
              <TableCell>Disk free</TableCell><TableCell>Disk activity</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.environments.map((e) => <EnvRows key={e.id} e={e} />)}
            {data.environments.length === 0 && (
              <TableRow><TableCell colSpan={8}>No environments yet.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </Box>

      {health && (
        <Box>
          <Typography variant="h6" sx={{ mb: 1 }}>Platform</Typography>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
            <CheckList title="Sign-in" items={health.auth} extra={(c) => c.last_sign_in
              ? <Typography variant="caption" color="text.secondary" component="div">Last sign-in {fmtTime(c.last_sign_in as string)}</Typography>
              : null} />
            <CheckList title="Secrets" items={health.secrets} />
            <CheckList title="Notifications" items={health.notifications} extra={(c) => {
              const last = c.last as { at: string; ok: boolean; error: string } | null;
              return <>
                {c.summary ? <Typography variant="caption" component="div">{c.summary as string}</Typography> : null}
                {last && <Typography variant="caption" color={last.ok ? "text.secondary" : "error"} component="div">
                  Last sent {fmtTime(last.at)}: {last.ok ? "ok" : last.error}
                </Typography>}
              </>;
            }} />
          </Stack>
        </Box>
      )}
    </Stack>
  );
}
