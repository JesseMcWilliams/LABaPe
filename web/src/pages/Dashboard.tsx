import { Fragment, ReactNode, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert, Box, Button, Chip, Collapse, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, LinearProgress,
  Link, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import { api, fmtTime } from "../api";
import Sparkline from "../components/Sparkline";
import UpdateDialog from "../components/UpdateDialog";
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
  version: {
    current: string; latest: string; url: string; error: string; checked_at: string | null; update_available: boolean;
    db_revision: string | null; code_revision: string;
  };
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
          <IconButton size="small" aria-label={open ? "Hide hosts" : "Show hosts"} title={open ? "Hide this environment's VMs" : "Show this environment's VMs"} onClick={() => setOpen(!open)}>
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
interface MonAlert {
  id: number; host: string; kvm_host_id: number | null; level: string; message: string; started_at: string;
  cleared_at: string | null; announced_at: string | null; acked_at: string | null; acked_by: string; silenced_until: string | null;
}
interface Silence { id: number; key: string; message: string; reason: string; created_by: string; until: string }
interface Monitoring {
  quiet: boolean; silences: Silence[]; open: MonAlert[]; recent: MonAlert[];
  stacks: { id: number; name: string; self: boolean; stack_url: string; status: { peer?: { ok: boolean; ms: number | null } } }[];
}

const SILENCE_HOURS = [[1, "1 hour"], [4, "4 hours"], [24, "1 day"], [72, "3 days"], [168, "1 week"], [720, "30 days"]] as const;

interface StackSummary {
  name: string; version: string; maintenance: { active: boolean; reason: string };
  hosts: { name: string; role: string; enabled: boolean }[];
  totals: { cpus: number; memory_total_mb: number; memory_used_mb: number; disk_total_gb: number; disk_free_gb: number; vms_running: number; vms_defined: number };
  environments: { name: string; status: string }[]; jobs: { running: number; queued: number; failed_24h: number };
  alerts: { level: string; message: string; host_id?: number | null; disk?: string }[];
}
interface AllStacks {
  stacks: { self: boolean; id: number | null; url: string; status: string; last_ok_at: string | null; error: string; summary: StackSummary | null }[];
  totals: {
    stacks: number; answering: number; hosts: number; cpus: number; memory_total_mb: number; memory_used_mb: number;
    disk_total_gb: number; disk_free_gb: number; vms_running: number; vms_defined: number; environments: number;
    jobs_running: number; jobs_queued: number; alerts_error: number; alerts_warning: number;
  } | null;
}

const STACK_STATUS: Record<string, { color: "success" | "warning" | "error"; label: string }> = {
  ok: { color: "success", label: "answering" }, stale: { color: "warning", label: "stale" },
  down: { color: "error", label: "not answering" }, never: { color: "warning", label: "not reached yet" },
};
const mbAsGb = (mb: number) => `${(mb / 1024).toFixed(0)} GB`;

/** Every linked stack at a glance (design §35): totals, then a card per stack that opens its own dashboard. */
function AllStacksPanel({ d }: { d: AllStacks }) {
  const t = d.totals!;
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }} useFlexGap>
          <Typography variant="h6">All stacks</Typography>
          <Chip size="small" label={`${t.answering} of ${t.stacks} answering`} color={t.answering === t.stacks ? "success" : "warning"} />
          <Typography variant="body2" color="text.secondary">
            {t.hosts} hosts · {t.cpus} CPUs · {mbAsGb(t.memory_used_mb)} of {mbAsGb(t.memory_total_mb)} memory used ·{" "}
            {t.disk_free_gb.toFixed(0)} of {t.disk_total_gb.toFixed(0)} GB VM disk free · {t.vms_running} of {t.vms_defined} VMs running ·{" "}
            {t.environments} environments · {t.jobs_running} jobs running, {t.jobs_queued} queued
          </Typography>
          {(t.alerts_error > 0 || t.alerts_warning > 0) && (
            <Chip size="small" color={t.alerts_error ? "error" : "warning"} label={`${t.alerts_error + t.alerts_warning} open alerts`} />
          )}
        </Stack>
        <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
          {d.stacks.map((s) => {
            const m = s.summary;
            const st = STACK_STATUS[s.status] ?? STACK_STATUS.never;
            const errs = m?.alerts.filter((a) => a.level === "error").length ?? 0;
            return (
              <Paper key={s.url} variant="outlined" sx={{ p: 1.5, flex: "1 1 260px", maxWidth: 420, opacity: s.status === "ok" ? 1 : 0.85 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                  <Link href={s.self ? "/" : `${s.url}/`} target={s.self ? undefined : "_blank"} rel="noreferrer" variant="subtitle1"
                    sx={{ fontWeight: 600 }} title={s.self ? "This stack" : `Open ${s.url} (its own sign-in)`}>
                    {m?.name ?? s.url}
                  </Link>
                  {s.self ? <Chip size="small" label="this stack" /> : <Chip size="small" color={st.color} label={st.label} title={s.error || undefined} />}
                  {m?.maintenance.active && <Chip size="small" label="maintenance" title={m.maintenance.reason} />}
                </Stack>
                {m ? (
                  <Typography variant="body2" component="div" sx={{ mt: 0.5 }}>
                    {m.version} · {m.hosts.length} hosts ({m.hosts.map((h) => h.name).join(", ")})<br />
                    {m.totals.vms_running ?? 0} of {m.totals.vms_defined ?? 0} VMs running · {m.environments.length} environments ·{" "}
                    {m.jobs.running} running, {m.jobs.queued} queued, {m.jobs.failed_24h} failed today
                    {m.alerts.length > 0 && (
                      <Box sx={{ mt: 0.5 }}>
                        <Chip size="small" color={errs ? "error" : "warning"} label={`${m.alerts.length} open alerts`} sx={{ mr: 1 }} />
                        <Typography variant="caption" color="text.secondary" component="span">
                          {m.alerts.slice(0, 2).map((a, k) => (
                            <Fragment key={k}>
                              {k > 0 ? " · " : ""}{a.message}
                              {a.disk && a.host_id ? (
                                <> (<a href={`${s.self ? "" : s.url.replace(/\/$/, "")}/hosts/${a.host_id}/disks/${encodeURIComponent(a.disk)}`}
                                  title="Open the drive's page: SMART attributes and a history of its numbers">drive details</a>)</>
                              ) : null}
                            </Fragment>
                          ))}
                        </Typography>
                      </Box>
                    )}
                  </Typography>
                ) : <Typography variant="body2" color="text.secondary">No summary yet{s.error ? `: ${s.error}` : ""}</Typography>}
                {!s.self && s.status !== "ok" && s.last_ok_at && (
                  <Typography variant="caption" color="text.secondary" component="div">As of {fmtTime(s.last_ok_at)}</Typography>
                )}
              </Paper>
            );
          })}
        </Stack>
      </Stack>
    </Paper>
  );
}

/** Silence an alert's condition: no syslog or email until it ends; still shown (design §31). */
function SilenceDialog({ alert, onClose }: { alert: MonAlert | null; onClose: (changed: boolean) => void }) {
  const [hours, setHours] = useState(4);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const save = () => api.post("/api/dashboard/monitoring/silences", { alert_id: alert!.id, hours, reason })
    .then(() => { setReason(""); onClose(true); }, (e) => setError(e.message));
  return (
    <Dialog open={!!alert} onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>Silence alert</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2">{alert?.message}</Typography>
          <Typography variant="caption" color="text.secondary">
            No syslog messages or emails for this condition until the silence ends. It stays on the dashboard, and if it's
            still open when the silence ends it's announced then.
          </Typography>
          <TextField select label="For" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {SILENCE_HOURS.map(([h, label]) => <MenuItem key={h} value={h}>{label}</MenuItem>)}
          </TextField>
          <TextField label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. disk replacement ordered" />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)}>Cancel</Button>
        <Button variant="contained" onClick={save}>Silence</Button>
      </DialogActions>
    </Dialog>
  );
}

/** Open alerts and the stacks being watched (design §30); admins acknowledge and silence (§31). */
function AlertsPanel({ m, admin, reload }: { m: Monitoring; admin: boolean; reload: () => void }) {
  const peers = m.stacks.filter((s) => !s.self && s.stack_url);
  const [silencing, setSilencing] = useState<MonAlert | null>(null);
  const ack = (a: MonAlert, on: boolean) => api.post(`/api/dashboard/monitoring/alerts/${a.id}/ack`, { ack: on }).then(reload);
  const endSilence = (s: Silence) => api.del(`/api/dashboard/monitoring/silences/${s.id}`).then(reload);
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography variant="h6">Alerts</Typography>
          <Chip size="small" label={m.open.length ? `${m.open.length} open` : "none open"} color={m.open.some((a) => a.level === "error") ? "error" : m.open.length ? "warning" : "success"} />
          <Box sx={{ flexGrow: 1 }} />
          {peers.map((p) => (
            <Chip key={p.id} size="small" variant="outlined" component={RouterLink} to={`/admin/hosts/${p.id}?section=stack`} clickable
              color={p.status.peer?.ok ? "success" : "error"} label={`${p.name}: ${p.status.peer ? (p.status.peer.ok ? `${p.status.peer.ms} ms` : "not answering") : "not probed yet"}`}
              title={`The stack at ${p.stack_url}`} />
          ))}
        </Stack>
        {m.quiet && (
          <Alert severity="info" icon={false}>
            Maintenance mode is on: new alerts are recorded here but not sent to syslog or email until it ends.
          </Alert>
        )}
        {m.open.map((a) => (
          <Alert key={a.id} severity={a.level === "error" ? "error" : "warning"} variant={a.acked_at ? "outlined" : "standard"}
            action={
              <Stack direction="row" spacing={0.5}>
                {admin && (a.acked_at
                  ? <Button size="small" onClick={() => ack(a, false)} title="Mark it as not acknowledged again">Unack</Button>
                  : <Button size="small" onClick={() => ack(a, true)} title="Mark it as seen: someone is on it">Ack</Button>)}
                {admin && !a.silenced_until && (
                  <Button size="small" onClick={() => setSilencing(a)} title="No syslog or email for this condition for a while">Silence</Button>
                )}
                {a.kvm_host_id && <Button size="small" component={RouterLink} to={`/admin/hosts/${a.kvm_host_id}?section=stack`}>Host</Button>}
              </Stack>
            }>
            {a.message} <Typography component="span" variant="caption" color="text.secondary">since {fmtTime(a.started_at)}</Typography>
            {a.acked_at && <Chip size="small" sx={{ ml: 1, height: 18 }} label={`acknowledged by ${a.acked_by}`} title={fmtTime(a.acked_at)} />}
            {a.silenced_until && <Chip size="small" sx={{ ml: 1, height: 18 }} label={`silenced until ${fmtTime(a.silenced_until)}`} />}
          </Alert>
        ))}
        {m.silences.length > 0 && (
          <Box>
            <Typography variant="caption" color="text.secondary">Silences</Typography>
            {m.silences.map((s) => (
              <Stack key={s.id} direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <Typography variant="body2">
                  {s.message} <Typography component="span" variant="caption" color="text.secondary">
                    until {fmtTime(s.until)} · {s.created_by}{s.reason ? ` · ${s.reason}` : ""}
                  </Typography>
                </Typography>
                {admin && <Button size="small" onClick={() => endSilence(s)} title="End the silence now">End</Button>}
              </Stack>
            ))}
          </Box>
        )}
        <SilenceDialog alert={silencing} onClose={(changed) => { setSilencing(null); if (changed) reload(); }} />
        {m.recent.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            Recently cleared: {m.recent.slice(0, 5).map((a) => `${a.message} (${fmtTime(a.cleared_at)})`).join(" · ")}
          </Typography>
        )}
      </Stack>
    </Paper>
  );
}

export default function Dashboard() {
  const { me } = useAuth();
  const [data, setData] = useState<{ hosts: DashHost[]; environments: DashEnv[]; is_admin: boolean } | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [mon, setMon] = useState<Monitoring | null>(null);
  const [all, setAll] = useState<AllStacks | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);
  const [updateOpen, setUpdateOpen] = useState(false);

  const load = () => {
    api.get<{ hosts: DashHost[]; environments: DashEnv[]; is_admin: boolean }>("/api/dashboard").then((d) => {
      setData(d);
      if (d.is_admin) api.get<AdminHealth>("/api/dashboard/admin").then(setHealth, () => setHealth(null));
      if (d.is_admin || hasRole(me, "auditor")) {
        api.get<Monitoring>("/api/dashboard/monitoring").then(setMon, () => setMon(null));
        api.get<AllStacks>("/api/dashboard/stacks").then(setAll, () => setAll(null));
      }
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
      setUpdateOpen(true);       // the choices: release, pre-release, roll back (design §23.2)
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

      <UpdateDialog open={updateOpen} onClose={() => setUpdateOpen(false)} />
      {v && (
        <Alert severity={v.update_available ? "info" : "success"} icon={false}
          action={<Button size="small" disabled={checking} onClick={checkNow} title="Check GitHub for newer releases and pre-releases, and see the update and rollback choices">Check now</Button>}>
          LABaPe <b>{v.current}</b>
          {v.update_available ? (
            <> · <Link href={v.url} target="_blank" rel="noreferrer">{v.latest}</Link> is available</>
          ) : v.latest ? " · up to date" : ""}
          {v.error && ` · ${v.error}`}
          {v.checked_at && <Typography variant="caption" color="text.secondary"> (checked {fmtTime(v.checked_at)})</Typography>}
          <Typography variant="caption" color="text.secondary" component="div">
            Database layout {v.db_revision ?? "not stamped"}{v.db_revision && v.db_revision !== v.code_revision ? ` (this version expects ${v.code_revision})` : ""}
          </Typography>
        </Alert>
      )}

      {all?.totals && <AllStacksPanel d={all} />}
      {mon && <AlertsPanel m={mon} admin={data.is_admin} reload={load} />}

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
            <Button component={RouterLink} to="/environments/new" variant="outlined" size="small"
              title="New environment: opens the form; nothing is created until you confirm there">New environment</Button>
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
            <CheckList title="Secrets and certificates" items={health.secrets} extra={(c) => c.name === "platform_ca" && c.status === "ok"
              ? <Link href="/api/ca/root.crt" variant="caption">Download the root certificate (trust it in browsers)</Link>
              : null} />
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
