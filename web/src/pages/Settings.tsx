import { ReactNode, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, FormControlLabel, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow,
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
  verify_tls: boolean;
}
interface Smtp {
  enabled: boolean; host: string; port: number; security: "none" | "starttls" | "ssl"; username: string;
  from_address: string; notify_jobs: boolean; has_password: boolean;
}
interface Branding { app_name: string }
interface Downloads { allow_http: boolean; proxy: string }
interface MaintenanceWindow { enabled: boolean; weekday: number; time_of_day: string; duration_minutes: number; heads_up_hours: number }
interface VmAccounts { linux_admin_user: string }
interface Directory { enabled: boolean; api_url: string; verify_tls: boolean; has_token: boolean }
interface StoreBase { enabled: boolean; url: string; verify_tls: boolean; ca_bundle: string }
interface OpenBao extends StoreBase { mount: string; path_prefix: string; auth_mount: string; role_id: string; namespace: string; has_secret_id: boolean }
interface Conjur extends StoreBase { account: string; login: string; authenticator: string; branch: string; has_api_key: boolean }
interface AllSettings {
  backup: Backup; retention: Retention; syslog: Syslog; smtp: Smtp; directory: Directory; branding: Branding; downloads: Downloads; maintenance_window: MaintenanceWindow; vm_accounts: VmAccounts;
  store_openbao: OpenBao; store_conjur: Conjur;
  file_roots: Record<string, string>;
}
interface BackupList {
  directory: string;
  files: { name: string; size: number; created: string }[];
  last_job: { id: number; state: string; finished_at: string | null } | null;
}

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

  const loadBackups = () => api.get<BackupList>("/api/admin/backups").then(setBackups, () => setBackups(null));
  useEffect(() => {
    api.get<AllSettings>("/api/admin/settings").then(setS, (e) => setError(e.message));
    loadBackups();
  }, []);
  if (!s) return error ? <Alert severity="error">{error}</Alert> : null;

  const set = <K extends keyof AllSettings>(k: K, patch: Partial<AllSettings[K]>) =>
    setS({ ...s, [k]: { ...s[k], ...patch } });
  const put = async <K extends "backup" | "retention" | "syslog" | "smtp" | "directory" | "branding" | "downloads" | "maintenance_window" | "vm_accounts" | "store_openbao" | "store_conjur">(k: K, body: object) => {
    const saved = await api.put<AllSettings[K]>(`/api/admin/settings/${k}`, body);
    setS((cur) => (cur ? { ...cur, [k]: saved } : cur));
  };
  const num = (v: string) => Number(v);
  const testStore = (name: string) => () =>
    api.post<{ ok: boolean; message: string }>(`/api/admin/settings/stores/${name}/test`)
      .then((r) => (r.ok ? r.message : Promise.reject(new Error(r.message))));
  const tls = <K extends "store_openbao" | "store_conjur">(k: K) => (
    <>
      <FormControlLabel label="Verify certificate" control={<Checkbox checked={s[k].verify_tls}
        onChange={(e) => set(k, { verify_tls: e.target.checked } as Partial<AllSettings[K]>)} />} />
      <Field label="CA bundle (PEM)" value={s[k].ca_bundle} fullWidth multiline minRows={2}
        help="Blank: trust the LABaPe platform CA, which signs the test stores' certificates"
        onChange={(e) => set(k, { ca_bundle: e.target.value } as Partial<AllSettings[K]>)} />
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

      <Section title="Backups" intro="A daily pg_dump of the LABaPe database (custom format; restore with pg_restore). Times are UTC."
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

      <Section title="Syslog" intro="Send every audit event to a syslog server (RFC 5424, JSON message body)."
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
        </Stack>
      </Section>

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
          {tls("store_openbao")}
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
          {tls("store_conjur")}
        </Stack>
      </Section>
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    </Stack>
  );
}
