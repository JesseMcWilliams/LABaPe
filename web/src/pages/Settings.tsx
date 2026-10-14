import { ReactNode, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, Chip, FormControlLabel, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow,
  Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { useAuth } from "../App";
import { ActionButton } from "../components/Confirm";
import { useMaintenance } from "../components/Maintenance";

interface Backup { enabled: boolean; time_of_day: string; retention_days: number; directory: string }
interface Retention { job_logs_days: number; audit_days: number; errors_days: number }
interface Syslog {
  enabled: boolean; host: string; port: number; protocol: "udp" | "tcp" | "tls"; facility: string; app_name: string;
  verify_tls: boolean; client_cert: boolean;
}
interface Forward {
  name: string; enabled: boolean; host: string; port: number; transport: "udp" | "tcp" | "tls"; verify_tls: boolean;
  client_cert: boolean; format: "rfc5424" | "rfc3164"; facilities: string[]; min_severity: string;
}
interface Collector { retention_days: number; require_client_cert: boolean; address: string; forwards: Forward[] }
interface Smtp {
  enabled: boolean; host: string; port: number; security: "none" | "starttls" | "ssl"; username: string;
  from_address: string; notify_jobs: boolean; has_password: boolean;
}
interface Branding { app_name: string }
interface Downloads { allow_http: boolean; proxy: string }
interface MaintenanceWindow { enabled: boolean; weekday: number; time_of_day: string; duration_minutes: number; heads_up_hours: number }
interface VmAccounts { linux_admin_user: string }
interface Templates { keep_versions: number; refresh_enabled: boolean; refresh_day: number; refresh_time: string }
interface Directory { enabled: boolean; api_url: string; verify_tls: boolean; has_token: boolean }
interface StoreBase { enabled: boolean; url: string; verify_tls: boolean; ca_bundle: string }
interface OpenBao extends StoreBase { mount: string; path_prefix: string; auth_mount: string; role_id: string; namespace: string; has_secret_id: boolean }
interface Conjur extends StoreBase { account: string; login: string; authenticator: string; branch: string; has_api_key: boolean }
interface StackBackup { enabled: boolean; time_of_day: string; retention_days: number; directory: string; include_syslog: boolean; include_recordings: boolean; send_to_links: boolean }
interface StackArchive {
  archive: string; size: number; created_at: string; version: string; db_revision: string; file_count: number; parts: string[];
  check: { ok: boolean; problems: string[]; restores: Record<string, string> };
}
interface StackBackupList {
  directory: string; archives: StackArchive[]; agent: { installed: boolean; user: string; home: string };
  status: { state: string; step?: string; message?: string; action?: string; pending?: boolean } | null;
  key_saved: { by: string; at: string } | null; key_file: string; service_user: string;
  sent: { link: string; url: string; archive?: string; at?: string; error?: string; tried_at?: string }[];
  copies: Record<string, { archive: string; size: number; created_at: string | null; received_at: string; host: string; path: string }[]>;
}
interface AllSettings {
  vm_snapshots: { auto_before_deploy: boolean; keep_auto: number };
  leases: { enabled: boolean; default_days: number; max_days: number; remind_days: number[]; grace_days: number; after_grace: string };
  backup: Backup; stack_backup: StackBackup; retention: Retention; syslog: Syslog; smtp: Smtp; directory: Directory; branding: Branding; downloads: Downloads; maintenance_window: MaintenanceWindow; vm_accounts: VmAccounts; templates: Templates;
  store_openbao: OpenBao; store_conjur: Conjur; secret_stores: { default_environment_store: string };
  remote: { record_sessions: boolean; idle_minutes: number; keep_recordings_days: number };
  syslog_collector: Collector;
  file_distribution: { wait_minutes: number };
  monitoring: { email_to: string[]; syslog: boolean; disk_pct: number; temp_c: number; cert_days: number; backup_hours: number; peer_failures: number };
  file_roots: Record<string, string>;
}
interface BackupList {
  directory: string;
  files: { name: string; size: number; created: string }[];
  last_job: { id: number; state: string; finished_at: string | null } | null;
}

const SEVERITIES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"];
const newForward = (): Forward => ({ name: "", enabled: true, host: "", port: 514, transport: "tcp", verify_tls: true,
  client_cert: false, format: "rfc5424", facilities: [], min_severity: "debug" });
const FACILITIES = ["local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7", "auth", "daemon", "user"];
const row = { alignItems: "flex-start", flexWrap: "wrap" } as const;
const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;

/** One settings card: its own Save button, result and field errors. */
function Section({ title, intro, onSave, children, extra }: {
  title: string; intro: string; onSave: () => Promise<string>; children: ReactNode; extra?: ReactNode;
}) {
  const [problem, setProblem] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [ok, setOk] = useState("");
  const save = async () => {
    setProblem({ summary: "", fields: {} });
    setOk("");
    try {
      setOk(await onSave());
    } catch (e) {
      setProblem(fromApiError(e));
    }
  };
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Typography variant="h6">{title}</Typography>
        <Typography variant="body2" color="text.secondary">{intro}</Typography>
        {problem.summary && <ErrorSummary summary={problem.summary} fields={problem.fields} />}
        {ok && <Alert severity="success" onClose={() => setOk("")}>{ok}</Alert>}
        {children}
        <Stack direction="row" spacing={1}>
          <ActionButton variant="contained" onClick={save} tip={`Save the ${title} settings`}
            confirm={{ title: `Save ${title}?`, confirmLabel: "Save", body: `${intro} The new settings apply at once.` }}>
            Save
          </ActionButton>
          {extra}
        </Stack>
      </Stack>
    </Paper>
  );
}

/** A button that runs a test action and shows its result beside the section. */
function TestButton({ label, run, tip, confirmBody }: {
  label: string; run: () => Promise<string>; tip: string; confirmBody?: string;
}) {
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <>
      <ActionButton variant="outlined" tip={tip}
        confirm={confirmBody ? { title: `${label}?`, confirmLabel: label, body: confirmBody } : undefined}
        onClick={() => run().then((t) => setMsg({ ok: true, text: t }),
          (e) => setMsg({ ok: false, text: fromApiError(e).summary }))}>{label}</ActionButton>
      {msg && <Alert severity={msg.ok ? "success" : "error"} sx={{ py: 0 }} onClose={() => setMsg(null)}>{msg.text}</Alert>}
    </>
  );
}

// Admin settings (design §21.5): file locations, backups, history retention,
// syslog output for the audit trail, and SMTP for job emails. Admin only.
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** Maintenance mode now (or at a set time), outside the section/Save pattern. */
function MaintenanceNow() {
  const m = useMaintenance();
  const [reason, setReason] = useState("");
  const [end, setEnd] = useState("");
  const [start, setStart] = useState("");
  const [err, setErr] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [msg, setMsg] = useState("");
  const iso = (v: string) => (v ? new Date(v).toISOString() : null);
  const send = async (active: boolean) => {
    setErr({ summary: "", fields: {} });
    try {
      await api.put("/api/admin/maintenance", { active, reason, expected_end: iso(end), scheduled_start: iso(start) });
      setMsg(active ? (start ? "Maintenance scheduled." : "Maintenance mode is on.") : "Maintenance mode is off.");
    } catch (e) { setErr(fromApiError(e)); }
  };
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Typography variant="h6">Maintenance mode</Typography>
        <Typography variant="body2" color="text.secondary">
          Pauses every change (reads, status and live logs keep working) and lets running jobs finish without starting new ones.
          {m?.active && ` On now: ${m.reason} (${m.running_jobs} job(s) still running).`}
        </Typography>
        {err.summary && <ErrorSummary summary={err.summary} fields={err.fields} />}
        {msg && <Alert severity="success" onClose={() => setMsg("")}>{msg}</Alert>}
        {!m?.active && (
          <Stack direction="row" spacing={2} useFlexGap sx={row}>
            <Field label="Reason" value={reason} sx={{ minWidth: 320 }} error={err.fields.reason} help="Shown in the banner"
              onChange={(e) => setReason(e.target.value)} />
            <Field label="Start" type="datetime-local" value={start} slotProps={{ inputLabel: { shrink: true } }}
              help="Empty: now" onChange={(e) => setStart(e.target.value)} />
            <Field label="Expected end" type="datetime-local" value={end} slotProps={{ inputLabel: { shrink: true } }}
              help="Shown in the banner" onChange={(e) => setEnd(e.target.value)} />
          </Stack>
        )}
        <Stack direction="row" spacing={1}>
          {m?.active ? (
            <ActionButton variant="contained" color="success" allowInMaintenance tip="End maintenance; changes and new jobs resume"
              confirm={{ title: "Turn maintenance mode off?", confirmLabel: "Turn off",
                body: "Changes are accepted again and queued jobs start. Make sure the maintenance work is finished." }}
              onClick={() => send(false)}>
              Turn off
            </ActionButton>
          ) : (
            <ActionButton variant="contained" color="warning" tip={start ? "Schedule maintenance mode" : "Turn maintenance mode on now"}
              confirm={{ title: start ? "Schedule maintenance?" : "Turn maintenance mode on?", danger: !start,
                confirmLabel: start ? "Schedule" : "Turn on",
                body: start ? "At the start time every change pauses and no new jobs start; a banner announces it until then."
                  : "Every change pauses at once for everyone (reads keep working). Running jobs finish; no new ones start until you turn it off." }}
              onClick={() => send(true)}>
              {start ? "Schedule" : "Turn on"}
            </ActionButton>
          )}
        </Stack>
      </Stack>
    </Paper>
  );
}

export default function Settings() {
  const { setAppName } = useAuth();
  const [s, setS] = useState<AllSettings | null>(null);
  const [error, setError] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [testTo, setTestTo] = useState("");
  const [dirToken, setDirToken] = useState("");
  const [baoSecretId, setBaoSecretId] = useState("");
  const [conjurKey, setConjurKey] = useState("");
  const [backups, setBackups] = useState<BackupList | null>(null);
  const [stackBackups, setStackBackups] = useState<StackBackupList | null>(null);

  const loadBackups = () => api.get<BackupList>("/api/admin/backups").then(setBackups, () => setBackups(null));
  const loadStackBackups = () => api.get<StackBackupList>("/api/admin/backups/stack").then(setStackBackups, () => setStackBackups(null));
  useEffect(() => {
    api.get<AllSettings>("/api/admin/settings").then(setS, (e) => setError(e.message));
    loadBackups();
    loadStackBackups();
  }, []);
  if (!s) return error ? <Alert severity="error">{error}</Alert> : null;

  const set = <K extends keyof AllSettings>(k: K, patch: Partial<AllSettings[K]>) =>
    setS({ ...s, [k]: { ...s[k], ...patch } });
  const put = async <K extends "backup" | "stack_backup" | "vm_snapshots" | "leases" | "retention" | "syslog" | "smtp" | "directory" | "branding" | "downloads" | "maintenance_window" | "vm_accounts" | "store_openbao" | "store_conjur" | "secret_stores" | "templates" | "remote" | "syslog_collector" | "file_distribution" | "monitoring">(k: K, body: object) => {
    const saved = await api.put<AllSettings[K]>(`/api/admin/settings/${k}`, body);
    setS((cur) => (cur ? { ...cur, [k]: saved } : cur));
  };
  const num = (v: string) => Number(v);
  const testStore = (name: string) => () =>
    api.post<{ ok: boolean; message: string }>(`/api/admin/settings/stores/${name}/test`)
      .then((r) => (r.ok ? r.message : Promise.reject(new Error(r.message))));
  const tls = (k: "store_openbao" | "store_conjur", cfg: StoreBase) => (
    <>
      <FormControlLabel label="Verify certificate" control={<Checkbox checked={cfg.verify_tls}
        onChange={(e) => set(k, { verify_tls: e.target.checked })} />} />
      <Field label="CA bundle (PEM)" value={cfg.ca_bundle} fullWidth multiline minRows={2}
        help="Blank: trust the LABaPe platform CA, which signs the test stores' certificates"
        onChange={(e) => set(k, { ca_bundle: e.target.value })} />
    </>
  );

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Settings</Typography>

      <MaintenanceNow />

      <Section title="Maintenance window" intro="A weekly window (UTC) for work that restarts services, such as some secret rotations. A banner gives notice before it starts."
        onSave={async () => { await put("maintenance_window", s.maintenance_window); return "Maintenance window saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Use a window" control={<Checkbox checked={s.maintenance_window.enabled}
            onChange={(e) => set("maintenance_window", { enabled: e.target.checked })} />} />
          <Field select label="Day" value={s.maintenance_window.weekday} sx={{ width: 150 }}
            onChange={(e) => set("maintenance_window", { weekday: Number(e.target.value) })}>
            {DAYS.map((d, i) => <MenuItem key={d} value={i}>{d}</MenuItem>)}
          </Field>
          <Field label="Start (UTC)" value={s.maintenance_window.time_of_day} sx={{ width: 120 }} help="HH:MM"
            onChange={(e) => set("maintenance_window", { time_of_day: e.target.value })} />
          <Field label="Length (minutes)" type="number" value={s.maintenance_window.duration_minutes} sx={{ width: 150 }}
            onChange={(e) => set("maintenance_window", { duration_minutes: Number(e.target.value) })} />
          <Field label="Notice (hours)" type="number" value={s.maintenance_window.heads_up_hours} sx={{ width: 140 }}
            help="Banner before it starts" onChange={(e) => set("maintenance_window", { heads_up_hours: Number(e.target.value) })} />
        </Stack>
      </Section>

      <Section title="VM accounts" intro="The admin account LABaPe makes on new Linux VMs: SSH key only (the VM key on the Secrets page), passwordless sudo. Each environment keeps the name it was created with, so a change applies to new environments only. Templates keep their build account, labape."
        onSave={async () => { await put("vm_accounts", s.vm_accounts); return "VM accounts saved."; }}>
        <Field label="Linux admin user" value={s.vm_accounts.linux_admin_user} sx={{ maxWidth: 320 }}
          help="Lower case letters, digits, _ and -; up to 32 characters"
          onChange={(e) => set("vm_accounts", { linux_admin_user: e.target.value })} />
      </Section>

      <Section title="Monitoring" intro="Alerts about hosts and stacks (stack hosts' containers, disks, temperatures, certificates, backups; other stacks that stop answering): on the dashboard, and when one starts or clears, to syslog and by email."
        onSave={async () => { await put("monitoring", s.monitoring); return "Monitoring settings saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <Field label="Email alerts to" value={s.monitoring.email_to.join(", ")} sx={{ minWidth: 300 }} help="Comma-separated; uses Email (SMTP) below"
            onChange={(e) => set("monitoring", { email_to: e.target.value.split(/[\s,;]+/).filter(Boolean) })} />
          <FormControlLabel label="Send to syslog" control={<Checkbox checked={s.monitoring.syslog} onChange={(e) => set("monitoring", { syslog: e.target.checked })} />} />
          {([["disk_pct", "Disk full at (%)"], ["temp_c", "Hot at (°C)"], ["cert_days", "Certificate days left"], ["backup_hours", "Backup older than (hours)"],
            ["peer_failures", "Missed probes"]] as const).map(([k, label]) => (
            <Field key={k} label={label} type="number" value={s.monitoring[k]} sx={{ width: 170 }}
              onChange={(e) => set("monitoring", { [k]: Number(e.target.value) })} />
          ))}
        </Stack>
      </Section>

      <Section title="File distribution" intro="The primary file host (Admin → KVM hosts) holds every file; a job on another host gets copies of the ones it needs. While the primary can't be reached, a job waits this long before it fails (a host that already has the file doesn't wait)."
        onSave={async () => { await put("file_distribution", s.file_distribution); return "File distribution settings saved."; }}>
        <Field label="Wait for the primary (minutes)" type="number" value={s.file_distribution.wait_minutes} sx={{ width: 240 }} help="0-1440"
          onChange={(e) => set("file_distribution", { wait_minutes: Number(e.target.value) })} />
      </Section>

      <Section title="Remote access" intro="Browser SSH and RDP sessions to VMs (owners and admins) and KVM hosts (admins), through guacd. Each session is in Admin → Remote sessions. Recording saves the screen for the auditors (not keystrokes); it's announced on the session page."
        onSave={async () => { await put("remote", s.remote); return "Remote access settings saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Record every session" control={<Checkbox checked={s.remote.record_sessions}
            onChange={(e) => set("remote", { record_sessions: e.target.checked })} />} />
          <Field label="Close after idle (minutes)" type="number" value={s.remote.idle_minutes} sx={{ width: 200 }} help="5-480; no keyboard or mouse input"
            onChange={(e) => set("remote", { idle_minutes: Number(e.target.value) })} />
          <Field label="Keep recordings (days)" type="number" value={s.remote.keep_recordings_days} sx={{ width: 200 }} help="1-3650"
            onChange={(e) => set("remote", { keep_recordings_days: Number(e.target.value) })} />
        </Stack>
      </Section>

      <Section title="Templates" intro="Templates named <name>-YYYY.MM are dated versions. Once a month the worker refreshes each template's newest version into this month's (every OS update applied), and versions beyond the newest few are deleted once no environment or VM uses them. Times are UTC."
        onSave={async () => { await put("templates", s.templates); return "Template settings saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <Field label="Versions to keep" type="number" value={s.templates.keep_versions} sx={{ width: 150 }}
            help="Per template; 0 keeps all" onChange={(e) => set("templates", { keep_versions: Number(e.target.value) })} />
          <FormControlLabel label="Monthly refresh" control={<Checkbox checked={s.templates.refresh_enabled}
            onChange={(e) => set("templates", { refresh_enabled: e.target.checked })} />} />
          <Field label="Day of the month" type="number" value={s.templates.refresh_day} sx={{ width: 150 }}
            help="1-28" onChange={(e) => set("templates", { refresh_day: Number(e.target.value) })} />
          <Field label="Time (UTC)" value={s.templates.refresh_time} sx={{ width: 120 }} help="HH:MM"
            onChange={(e) => set("templates", { refresh_time: e.target.value })} />
        </Stack>
      </Section>

      <Section title="Branding" intro="The name in the top bar, the browser tab, the sign-in page and emails. A logo comes later."
        onSave={async () => { await put("branding", s.branding); setAppName(s.branding.app_name); return "Name saved."; }}>
        <Field label="App name" value={s.branding.app_name} sx={{ maxWidth: 320 }} help="Up to 40 characters"
          onChange={(e) => set("branding", { app_name: e.target.value })} />
      </Section>

      <Section title="File locations" intro="Where the Files page keeps ISOs, application installers and other files (paths inside the containers)."
        onSave={async () => {
          const r = await api.put<Record<string, string>>("/api/files/roots", s.file_roots);
          setS({ ...s, file_roots: r });
          return "File locations saved.";
        }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          {(["isos", "installers", "files"] as const).map((k) => (
            <Field key={k} label={k === "isos" ? "ISOs" : k === "installers" ? "App installers" : "Other files"}
              value={s.file_roots[k] ?? ""} sx={{ minWidth: 260 }} help="Absolute path"
              onChange={(e) => setS({ ...s, file_roots: { ...s.file_roots, [k]: e.target.value } })} />
          ))}
        </Stack>
      </Section>

      <Section title="Downloads" intro="Files downloaded from a URL on the Files page. HTTPS only unless HTTP is allowed; link-local addresses are always refused."
        onSave={async () => { await put("downloads", s.downloads); return "Download settings saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Allow plain HTTP" control={<Checkbox checked={s.downloads.allow_http}
            onChange={(e) => set("downloads", { allow_http: e.target.checked })} />} />
          <Field label="Proxy" value={s.downloads.proxy} sx={{ minWidth: 320 }} help="e.g. http://proxy.example:3128; empty: direct"
            onChange={(e) => set("downloads", { proxy: e.target.value })} />
        </Stack>
      </Section>

      <Section title="Environment leases" intro="Environments expire unless their owners extend them: reminder emails before the end, the VMs shut down at the end, and after the grace period the environment is destroyed (or kept shut down). New environments get the default lease; existing ones have none until someone sets it."
        onSave={async () => { await put("leases", s.leases); return "Lease settings saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Leases on" control={<Checkbox checked={s.leases.enabled}
            onChange={(e) => set("leases", { enabled: e.target.checked })} />} />
          <Field label="New environments (days)" type="number" value={s.leases.default_days} sx={{ width: 190 }}
            onChange={(e) => set("leases", { default_days: num(e.target.value) })} />
          <Field label="Longest from now (days)" type="number" value={s.leases.max_days} sx={{ width: 190 }} help="How far owners can extend"
            onChange={(e) => set("leases", { max_days: num(e.target.value) })} />
          <Field label="Remind (days before)" value={s.leases.remind_days.join(", ")} sx={{ width: 180 }} help="e.g. 7, 1"
            onChange={(e) => set("leases", { remind_days: e.target.value.split(",").map((x) => Number(x.trim())).filter((x) => x > 0) })} />
          <Field label="Grace (days)" type="number" value={s.leases.grace_days} sx={{ width: 130 }} help="Shut down, then this long"
            onChange={(e) => set("leases", { grace_days: num(e.target.value) })} />
          <Field select label="After the grace" value={s.leases.after_grace} sx={{ width: 190 }}
            onChange={(e) => set("leases", { after_grace: e.target.value })}>
            <MenuItem value="destroy">Destroy it</MenuItem>
            <MenuItem value="keep">Keep it shut down</MenuItem>
          </Field>
        </Stack>
      </Section>

      <Section title="VM snapshots" intro="Snapshots of an environment's VMs before a deploy changes them (a re-deploy, added applications or services). Owners take, revert and delete snapshots on each VM's page."
        onSave={async () => { await put("vm_snapshots", s.vm_snapshots); return "VM snapshot settings saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Snapshot every VM before a deploy changes it" control={<Checkbox checked={s.vm_snapshots.auto_before_deploy}
            onChange={(e) => set("vm_snapshots", { auto_before_deploy: e.target.checked })} />} />
          <Field label="Keep (automatic, per VM)" type="number" value={s.vm_snapshots.keep_auto} sx={{ width: 200 }} help="1-20; older ones are deleted"
            onChange={(e) => set("vm_snapshots", { keep_auto: num(e.target.value) })} />
        </Stack>
      </Section>

      <Section title="Full stack backups" intro="The whole stack in one encrypted archive, taken on the host by the update agent: every database (LABaPe, Authentik, the syslog collector, Conjur), OpenBao, the platform CA, the configuration and the bootstrap secrets. Each archive is opened again afterwards and its LABaPe and Authentik databases are test-loaded. Rebuilding a stack from one needs the backup key. Times are UTC."
        onSave={async () => { await put("stack_backup", s.stack_backup); return "Full backup settings saved."; }}
        extra={<ActionButton variant="outlined" tip="Ask the update agent for a full backup now"
          disabled={!stackBackups?.agent.installed}
          confirm={{ title: "Back up the whole stack now?", confirmLabel: "Back up",
            body: `The update agent writes an encrypted archive of the whole stack to ${s.stack_backup.directory} and checks it. Nothing stops while it runs.` }}
          onClick={() => api.post("/api/admin/backups/stack").then(loadStackBackups, (e) => setError(fromApiError(e).summary))}>Back up now</ActionButton>}>
        {stackBackups && !stackBackups.agent.installed && (
          <Alert severity="warning">The update agent isn't installed on the stack host, and it takes the full backups.</Alert>
        )}
        {stackBackups && stackBackups.key_file && !stackBackups.key_saved && (
          <Alert severity="warning" action={<Button size="small" title="Record that the key is kept somewhere other than this host"
            onClick={() => api.post("/api/admin/backups/stack/key-saved").then(loadStackBackups)}>I saved it</Button>}>
            Copy the backup key somewhere other than this host (a password manager): without it no archive can be opened.
            On the stack host run <code>sudo -u {stackBackups.service_user || "<service user>"} cat {stackBackups.key_file}</code>
          </Alert>
        )}
        {stackBackups?.key_saved && (
          <Typography variant="caption" color="text.secondary">
            Backup key copy recorded by {stackBackups.key_saved.by} on {fmtTime(stackBackups.key_saved.at)}.
          </Typography>
        )}
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Daily full backup" control={<Checkbox checked={s.stack_backup.enabled}
            onChange={(e) => set("stack_backup", { enabled: e.target.checked })} />} />
          <Field label="Time (UTC)" value={s.stack_backup.time_of_day} sx={{ width: 120 }} help="HH:MM"
            onChange={(e) => set("stack_backup", { time_of_day: e.target.value })} />
          <Field label="Keep (days)" type="number" value={s.stack_backup.retention_days} sx={{ width: 130 }} help="1-3650; the newest always stays"
            onChange={(e) => set("stack_backup", { retention_days: num(e.target.value) })} />
          <Field label="Directory" value={s.stack_backup.directory} sx={{ minWidth: 300 }} help="Absolute path on the host, under the data root"
            onChange={(e) => set("stack_backup", { directory: e.target.value })} />
          <FormControlLabel label="Syslog messages" title="The syslog collector's stored messages (can be large)" control={<Checkbox checked={s.stack_backup.include_syslog}
            onChange={(e) => set("stack_backup", { include_syslog: e.target.checked })} />} />
          <FormControlLabel label="Session recordings" title="Recorded SSH/RDP sessions (can be very large)" control={<Checkbox checked={s.stack_backup.include_recordings}
            onChange={(e) => set("stack_backup", { include_recordings: e.target.checked })} />} />
          <FormControlLabel label="Copy to linked stacks" title="Send the newest archive to every linked stack, which keeps it on its primary file host" control={<Checkbox checked={s.stack_backup.send_to_links}
            onChange={(e) => set("stack_backup", { send_to_links: e.target.checked })} />} />
        </Stack>
        {stackBackups?.status && (
          <Alert severity={stackBackups.status.state === "succeeded" ? "success" : stackBackups.status.state === "running" || stackBackups.status.pending ? "info" : "error"}
            action={<Button size="small" onClick={loadStackBackups} title="Reload the backup list">Refresh</Button>}>
            {stackBackups.status.pending && stackBackups.status.state !== "running" ? "Waiting for the update agent to start…" : stackBackups.status.message}
          </Alert>
        )}
        {stackBackups && (
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Archive</TableCell><TableCell>Size</TableCell><TableCell>Taken</TableCell><TableCell>Version</TableCell><TableCell>Check</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {stackBackups.archives.map((a) => (
                <TableRow key={a.archive}>
                  <TableCell><a href={`/api/admin/backups/stack/${a.archive}`}>{a.archive}</a></TableCell>
                  <TableCell>{mb(a.size)}</TableCell>
                  <TableCell>{fmtTime(a.created_at)}</TableCell>
                  <TableCell>{a.version}</TableCell>
                  <TableCell title={[...Object.entries(a.check?.restores ?? {}).map(([k, v]) => `${k}: ${v}`), ...(a.check?.problems ?? [])].join("\n")}>
                    {a.check?.ok ? <Chip size="small" color="success" label={`ok · ${a.file_count} files`} /> : <Chip size="small" color="error" label="failed" />}
                  </TableCell>
                </TableRow>
              ))}
              {stackBackups.archives.length === 0 && <TableRow><TableCell colSpan={5}>No full backups yet.</TableCell></TableRow>}
            </TableBody>
          </Table>
        )}
        {stackBackups && stackBackups.sent.length > 0 && (
          <Typography variant="body2" component="div">
            Sent to linked stacks:{" "}
            {stackBackups.sent.map((x) => (
              <Chip key={x.url} size="small" sx={{ mr: 1 }} color={x.error ? "error" : x.archive ? "success" : "default"}
                title={x.error || (x.at ? `sent ${fmtTime(x.at)}` : "not sent yet")}
                label={`${x.link}: ${x.error ? "failed, will retry" : x.archive ? x.archive.replace(/^labape-stack-/, "") : "nothing yet"}`} />
            ))}
          </Typography>
        )}
        {stackBackups && Object.keys(stackBackups.copies).length > 0 && (
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Kept for</TableCell><TableCell>Archive</TableCell><TableCell>Size</TableCell><TableCell>Taken</TableCell><TableCell>Where</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(stackBackups.copies).flatMap(([peer, list]) => list.map((c) => (
                <TableRow key={c.path}>
                  <TableCell>{peer}</TableCell><TableCell>{c.archive}</TableCell><TableCell>{mb(c.size)}</TableCell>
                  <TableCell>{c.created_at ? fmtTime(c.created_at) : "—"}</TableCell>
                  <TableCell title="Sealed with the other stack's backup key; to rebuild it, copy this file to the new machine (design §33)"><code>{c.host}:{c.path}</code></TableCell>
                </TableRow>
              )))}
            </TableBody>
          </Table>
        )}
      </Section>

      <Section title="Database backups" intro="A daily pg_dump of the LABaPe database only (custom format; restore with pg_restore), also taken before every update. Times are UTC."
        onSave={async () => { await put("backup", s.backup); return "Backup settings saved."; }}
        extra={<ActionButton variant="outlined" tip="Dump the database now, as a job"
          confirm={{ title: "Back up now?", confirmLabel: "Back up",
            body: `A job dumps the LABaPe database to ${s.backup.directory}. Dumps older than ${s.backup.retention_days} days are removed afterwards.` }}
          onClick={() => api.post("/api/admin/backups").then(loadBackups, (e) => setError(fromApiError(e).summary))}>Back up now</ActionButton>}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Daily backup" control={<Checkbox checked={s.backup.enabled}
            onChange={(e) => set("backup", { enabled: e.target.checked })} />} />
          <Field label="Time (UTC)" value={s.backup.time_of_day} sx={{ width: 120 }} help="HH:MM"
            onChange={(e) => set("backup", { time_of_day: e.target.value })} />
          <Field label="Keep (days)" type="number" value={s.backup.retention_days} sx={{ width: 130 }} help="1-3650"
            onChange={(e) => set("backup", { retention_days: num(e.target.value) })} />
          <Field label="Directory" value={s.backup.directory} sx={{ minWidth: 300 }} help="Absolute path in the containers"
            onChange={(e) => set("backup", { directory: e.target.value })} />
        </Stack>
        {backups && (
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Backup</TableCell><TableCell>Size</TableCell><TableCell>Taken</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {backups.files.map((f) => (
                <TableRow key={f.name}>
                  <TableCell><a href={`/api/admin/backups/${f.name}`}>{f.name}</a></TableCell>
                  <TableCell>{mb(f.size)}</TableCell>
                  <TableCell>{fmtTime(f.created)}</TableCell>
                </TableRow>
              ))}
              {backups.files.length === 0 && <TableRow><TableCell colSpan={3}>No backups yet.</TableCell></TableRow>}
            </TableBody>
          </Table>
        )}
        {backups?.last_job && (
          <Typography variant="caption" color="text.secondary">
            Last backup job: <a href={`/jobs/${backups.last_job.id}`}>#{backups.last_job.id}</a> {backups.last_job.state}
            {backups.last_job.finished_at ? ` at ${fmtTime(backups.last_job.finished_at)}` : ""}.{" "}
            <Button size="small" onClick={loadBackups} title="Reload the backup list">Refresh</Button>
          </Typography>
        )}
      </Section>

      <Section title="History" intro="How long to keep job logs, the audit trail and user errors. The worker purges once a day."
        onSave={async () => { await put("retention", s.retention); return "Retention saved."; }}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <Field label="Job logs (days)" type="number" value={s.retention.job_logs_days} sx={{ width: 160 }} help="1-3650"
            onChange={(e) => set("retention", { job_logs_days: num(e.target.value) })} />
          <Field label="Audit trail (days)" type="number" value={s.retention.audit_days} sx={{ width: 160 }} help="0 keeps it forever"
            onChange={(e) => set("retention", { audit_days: num(e.target.value) })} />
          <Field label="User errors (days)" type="number" value={s.retention.errors_days} sx={{ width: 160 }} help="1-3650"
            onChange={(e) => set("retention", { errors_days: num(e.target.value) })} />
        </Stack>
      </Section>

      <Section title="Syslog (audit trail)" intro="Send every audit event to a syslog server (RFC 5424, JSON message body). The stack's own collector is labape-syslog, port 6514, TLS, with LABaPe's client certificate."
        onSave={async () => { await put("syslog", s.syslog); return "Syslog settings saved."; }}
        extra={<TestButton label="Send test message" tip="Send one test event to the syslog server"
          confirmBody={`One test event goes to ${s.syslog.host || "the saved server"}:${s.syslog.port} over ${s.syslog.protocol.toUpperCase()}, using the saved settings (save first if you changed them).`} run={() =>
          api.post<{ message: string }>("/api/admin/settings/syslog/test").then((r) => r.message)} />}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Enabled" control={<Checkbox checked={s.syslog.enabled}
            onChange={(e) => set("syslog", { enabled: e.target.checked })} />} />
          <Field label="Server" value={s.syslog.host} sx={{ minWidth: 220 }} help="Host name or address"
            onChange={(e) => set("syslog", { host: e.target.value })} />
          <Field label="Port" type="number" value={s.syslog.port} sx={{ width: 110 }}
            onChange={(e) => set("syslog", { port: num(e.target.value) })} />
          <Field select label="Protocol" value={s.syslog.protocol} sx={{ width: 120 }}
            onChange={(e) => set("syslog", { protocol: e.target.value as Syslog["protocol"] })}>
            <MenuItem value="udp">UDP</MenuItem><MenuItem value="tcp">TCP</MenuItem><MenuItem value="tls">TLS</MenuItem>
          </Field>
          <Field select label="Facility" value={s.syslog.facility} sx={{ width: 130 }}
            onChange={(e) => set("syslog", { facility: e.target.value })}>
            {FACILITIES.map((f) => <MenuItem key={f} value={f}>{f}</MenuItem>)}
          </Field>
          <Field label="App name" value={s.syslog.app_name} sx={{ width: 150 }}
            onChange={(e) => set("syslog", { app_name: e.target.value })} />
          {s.syslog.protocol === "tls" && (
            <FormControlLabel label="Verify certificate" control={<Checkbox checked={s.syslog.verify_tls}
              onChange={(e) => set("syslog", { verify_tls: e.target.checked })} />} />
          )}
          {s.syslog.protocol === "tls" && (
            <FormControlLabel label="Present LABaPe's client certificate" control={<Checkbox checked={s.syslog.client_cert}
              onChange={(e) => set("syslog", { client_cert: e.target.checked })} />} />
          )}
        </Stack>
      </Section>

      <CollectorSection value={s.syslog_collector} onChange={(v) => set("syslog_collector", v)}
        onSave={async () => { await put("syslog_collector", s.syslog_collector); return "Syslog collector settings saved; the collector restarts within a minute."; }} />

      <Section title="Email (SMTP)" intro="Used to email people when their jobs finish. The password is kept in the encrypted secrets store."
        onSave={async () => {
          await put("smtp", { ...s.smtp, ...(smtpPassword ? { password: smtpPassword } : {}) });
          setSmtpPassword("");
          return "Email settings saved.";
        }}
        extra={<>
          <Field label="Test recipient" value={testTo} sx={{ minWidth: 220 }} onChange={(e) => setTestTo(e.target.value)} />
          <TestButton label="Send test email" tip="Send a test email to the recipient beside it"
            confirmBody={`A test email goes to ${testTo || "(no recipient yet)"} through ${s.smtp.host || "the saved server"}, using the saved settings (save first if you changed them).`} run={() =>
            api.post<{ message: string }>("/api/admin/settings/smtp/test", { to: testTo }).then((r) => r.message)} />
        </>}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Enabled" control={<Checkbox checked={s.smtp.enabled}
            onChange={(e) => set("smtp", { enabled: e.target.checked })} />} />
          <Field label="Server" value={s.smtp.host} sx={{ minWidth: 220 }}
            onChange={(e) => set("smtp", { host: e.target.value })} />
          <Field label="Port" type="number" value={s.smtp.port} sx={{ width: 110 }}
            onChange={(e) => set("smtp", { port: num(e.target.value) })} />
          <Field select label="Security" value={s.smtp.security} sx={{ width: 140 }}
            onChange={(e) => set("smtp", { security: e.target.value as Smtp["security"] })}>
            <MenuItem value="none">None</MenuItem><MenuItem value="starttls">STARTTLS</MenuItem>
            <MenuItem value="ssl">SSL/TLS</MenuItem>
          </Field>
          <Field label="From address" value={s.smtp.from_address} sx={{ minWidth: 220 }}
            onChange={(e) => set("smtp", { from_address: e.target.value })} />
        </Stack>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <Field label="User name" value={s.smtp.username} sx={{ minWidth: 220 }} help="Blank: no sign-in"
            onChange={(e) => set("smtp", { username: e.target.value })} />
          <Field label="Password" type="password" value={smtpPassword} sx={{ minWidth: 220 }} autoComplete="new-password"
            help={s.smtp.has_password ? "Saved. Type a new one to replace it." : "Not set"}
            onChange={(e) => setSmtpPassword(e.target.value)} />
          <FormControlLabel label="Email people when their jobs finish" control={<Checkbox checked={s.smtp.notify_jobs}
            onChange={(e) => set("smtp", { notify_jobs: e.target.checked })} />} />
        </Stack>
      </Section>

      <Section title="Directory (group search)"
        intro="Lets group fields search Authentik as you type. Use a token that can only view groups. Without one, suggestions come from the groups LABaPe has seen at sign-in."
        onSave={async () => {
          await put("directory", { ...s.directory, ...(dirToken ? { token: dirToken } : {}) });
          setDirToken("");
          return "Directory settings saved.";
        }}
        extra={<TestButton label="Test search" tip="Ask Authentik for its groups with the saved token (changes nothing)" run={() =>
          api.post<{ message: string }>("/api/admin/settings/directory/test").then((r) => r.message)} />}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Search Authentik" control={<Checkbox checked={s.directory.enabled}
            onChange={(e) => set("directory", { enabled: e.target.checked })} />} />
          <Field label="Authentik URL" value={s.directory.api_url} sx={{ minWidth: 300 }}
            help="e.g. https://auth.example.com. Blank: the OIDC issuer's address"
            onChange={(e) => set("directory", { api_url: e.target.value })} />
          <Field label="API token" type="password" value={dirToken} sx={{ minWidth: 260 }} autoComplete="new-password"
            help={s.directory.has_token ? "Saved. Type a new one to replace it." : "Not set"}
            onChange={(e) => setDirToken(e.target.value)} />
          <FormControlLabel label="Verify certificate" control={<Checkbox checked={s.directory.verify_tls}
            onChange={(e) => set("directory", { verify_tls: e.target.checked })} />} />
        </Stack>
      </Section>
      <Typography variant="h6" sx={{ pt: 1 }}>Secret stores</Typography>
      <Typography variant="body2" color="text.secondary">
        External stores that can hold LABaPe's secrets besides the built-in, encrypted database store. Each store's own
        login stays in the built-in store and is never shown again.
      </Typography>

      <Section title="Default store"
        intro="Where new environment secrets (inventories, credential sheets, rotated passwords) are kept. Existing secrets stay where they are; move them on the Secrets page."
        onSave={async () => { await put("secret_stores", s.secret_stores); return "Default store saved."; }}>
        <Field select label="New environment secrets" value={s.secret_stores.default_environment_store} sx={{ width: 300 }}
          help="A store must be turned on and pass its test first"
          onChange={(e) => set("secret_stores", { default_environment_store: e.target.value })}>
          <MenuItem value="builtin">Built-in store (database, encrypted)</MenuItem>
          <MenuItem value="openbao">OpenBao / Vault</MenuItem>
          <MenuItem value="conjur">CyberArk Conjur</MenuItem>
        </Field>
      </Section>

      <Section title="OpenBao / HashiCorp Vault"
        intro="KV version 2 with an AppRole login. LABaPe's role needs read, write, list and delete on its own mount only."
        onSave={async () => {
          await put("store_openbao", { ...s.store_openbao, ...(baoSecretId ? { secret_id: baoSecretId } : {}) });
          setBaoSecretId("");
          return "OpenBao settings saved.";
        }}
        extra={<TestButton label="Test connection" tip="Sign in with the saved AppRole and list the mount (changes nothing)"
          run={testStore("openbao")} />}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Use OpenBao / Vault" control={<Checkbox checked={s.store_openbao.enabled}
            onChange={(e) => set("store_openbao", { enabled: e.target.checked })} />} />
          <Field label="Address" value={s.store_openbao.url} sx={{ minWidth: 300 }} help="e.g. https://bao.example.com:8200"
            onChange={(e) => set("store_openbao", { url: e.target.value })} />
          <Field label="KV mount" value={s.store_openbao.mount} sx={{ width: 160 }}
            onChange={(e) => set("store_openbao", { mount: e.target.value })} />
          <Field label="Path prefix" value={s.store_openbao.path_prefix} sx={{ width: 180 }} help="Optional folder in the mount"
            onChange={(e) => set("store_openbao", { path_prefix: e.target.value })} />
          <Field label="AppRole mount" value={s.store_openbao.auth_mount} sx={{ width: 160 }}
            onChange={(e) => set("store_openbao", { auth_mount: e.target.value })} />
          <Field label="Role ID" value={s.store_openbao.role_id} sx={{ minWidth: 300 }}
            onChange={(e) => set("store_openbao", { role_id: e.target.value })} />
          <Field label="Secret ID" type="password" value={baoSecretId} sx={{ minWidth: 260 }} autoComplete="new-password"
            help={s.store_openbao.has_secret_id ? "Saved. Type a new one to replace it." : "Not set"}
            onChange={(e) => setBaoSecretId(e.target.value)} />
          <Field label="Namespace" value={s.store_openbao.namespace} sx={{ width: 180 }} help="Vault Enterprise only"
            onChange={(e) => set("store_openbao", { namespace: e.target.value })} />
          {tls("store_openbao", s.store_openbao)}
        </Stack>
      </Section>

      <Section title="CyberArk Conjur"
        intro="A Conjur host signs in with its API key. The host must own the policy branch its variables live in."
        onSave={async () => {
          await put("store_conjur", { ...s.store_conjur, ...(conjurKey ? { api_key: conjurKey } : {}) });
          setConjurKey("");
          return "Conjur settings saved.";
        }}
        extra={<TestButton label="Test connection" tip="Sign in as the host and look up its policy branch (changes nothing)"
          run={testStore("conjur")} />}>
        <Stack direction="row" spacing={2} useFlexGap sx={row}>
          <FormControlLabel label="Use Conjur" control={<Checkbox checked={s.store_conjur.enabled}
            onChange={(e) => set("store_conjur", { enabled: e.target.checked })} />} />
          <Field label="Address" value={s.store_conjur.url} sx={{ minWidth: 300 }} help="e.g. https://conjur.example.com"
            onChange={(e) => set("store_conjur", { url: e.target.value })} />
          <Field label="Account" value={s.store_conjur.account} sx={{ width: 160 }}
            onChange={(e) => set("store_conjur", { account: e.target.value })} />
          <Field label="Login" value={s.store_conjur.login} sx={{ minWidth: 220 }} help="e.g. host/labape/app"
            onChange={(e) => set("store_conjur", { login: e.target.value })} />
          <Field label="Authenticator" value={s.store_conjur.authenticator} sx={{ width: 160 }}
            onChange={(e) => set("store_conjur", { authenticator: e.target.value })} />
          <Field label="Policy branch" value={s.store_conjur.branch} sx={{ minWidth: 200 }} help="Where its variables are declared"
            onChange={(e) => set("store_conjur", { branch: e.target.value })} />
          <Field label="API key" type="password" value={conjurKey} sx={{ minWidth: 260 }} autoComplete="new-password"
            help={s.store_conjur.has_api_key ? "Saved. Type a new one to replace it." : "Not set"}
            onChange={(e) => setConjurKey(e.target.value)} />
          {tls("store_conjur", s.store_conjur)}
        </Stack>
      </Section>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    </Stack>
  );
}


/** The stack's syslog collector (design §29.1): retention, client certificates, and forward targets. */
function CollectorSection({ value: c, onChange, onSave }: {
  value: Collector; onChange: (v: Partial<Collector>) => void; onSave: () => Promise<string>;
}) {
  const setF = (i: number, patch: Partial<Forward>) =>
    onChange({ forwards: c.forwards.map((f, j) => (j === i ? { ...f, ...patch } : f)) });
  return (
    <Section title="Syslog collector" onSave={onSave}
      intro="The stack's own collector receives on UDP and TCP 514 and TLS 6514, keeps messages in its database (the Syslog tab) and can forward a copy to a SIEM. Forwards queue on disk while their target is down.">
      <Stack direction="row" spacing={2} useFlexGap sx={row}>
        <Field label="Keep messages (days)" type="number" value={c.retention_days} sx={{ width: 190 }} help="1-3650; the worker purges daily"
          onChange={(e) => onChange({ retention_days: Number(e.target.value) })} />
        <Field label="Address hosts send to" value={c.address} sx={{ minWidth: 240 }}
          help="Blank: the web interface's host name. Also in the collector's certificate"
          onChange={(e) => onChange({ address: e.target.value })} />
        <FormControlLabel label="TLS clients must present a certificate" control={<Checkbox checked={c.require_client_cert}
          onChange={(e) => onChange({ require_client_cert: e.target.checked })} />} />
      </Stack>
      <Typography variant="subtitle2" sx={{ mt: 1 }}>Forward to</Typography>
      {c.forwards.length === 0 && <Typography variant="body2" color="text.secondary">Nowhere: messages stay in the collector.</Typography>}
      {c.forwards.map((f, i) => (
        <Paper key={i} variant="outlined" sx={{ p: 1.5 }}>
          <Stack direction="row" spacing={2} useFlexGap sx={row}>
            <FormControlLabel label="On" control={<Checkbox checked={f.enabled} onChange={(e) => setF(i, { enabled: e.target.checked })} />} />
            <Field label="Name" value={f.name} sx={{ width: 150 }} onChange={(e) => setF(i, { name: e.target.value })} />
            <Field label="Server" value={f.host} sx={{ minWidth: 200 }} help="Host name or address" onChange={(e) => setF(i, { host: e.target.value })} />
            <Field label="Port" type="number" value={f.port} sx={{ width: 100 }} onChange={(e) => setF(i, { port: Number(e.target.value) })} />
            <Field select label="Transport" value={f.transport} sx={{ width: 120 }}
              onChange={(e) => setF(i, { transport: e.target.value as Forward["transport"] })}>
              <MenuItem value="udp">UDP</MenuItem><MenuItem value="tcp">TCP</MenuItem><MenuItem value="tls">TLS</MenuItem>
            </Field>
            <Field select label="Format" value={f.format} sx={{ width: 140 }}
              onChange={(e) => setF(i, { format: e.target.value as Forward["format"] })}>
              <MenuItem value="rfc5424">RFC 5424</MenuItem><MenuItem value="rfc3164">RFC 3164</MenuItem>
            </Field>
            <Field label="Facilities" value={f.facilities.join(", ")} sx={{ width: 200 }} help="Blank: all; e.g. auth, local0"
              onChange={(e) => setF(i, { facilities: e.target.value.split(/[\s,]+/).filter(Boolean) })} />
            <Field select label="Minimum severity" value={f.min_severity} sx={{ width: 160 }}
              onChange={(e) => setF(i, { min_severity: e.target.value })}>
              {SEVERITIES.map((v) => <MenuItem key={v} value={v}>{v === "debug" ? "debug (all)" : v}</MenuItem>)}
            </Field>
            {f.transport === "tls" && (
              <>
                <FormControlLabel label="Verify certificate" control={<Checkbox checked={f.verify_tls} onChange={(e) => setF(i, { verify_tls: e.target.checked })} />} />
                <FormControlLabel label="Client certificate" control={<Checkbox checked={f.client_cert} onChange={(e) => setF(i, { client_cert: e.target.checked })} />} />
              </>
            )}
            <Button size="small" color="error" onClick={() => onChange({ forwards: c.forwards.filter((_, j) => j !== i) })}
              title="Remove this target (takes effect when you save)">Remove</Button>
          </Stack>
        </Paper>
      ))}
      <Stack direction="row">
        <Button size="small" onClick={() => onChange({ forwards: [...c.forwards, newForward()] })} disabled={c.forwards.length >= 10}
          title="Add a forward target, e.g. a SIEM (up to 10)">Add target</Button>
      </Stack>
    </Section>
  );
}
