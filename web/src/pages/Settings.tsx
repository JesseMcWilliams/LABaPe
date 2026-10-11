import { ReactNode, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, FormControlLabel, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow,
  Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";

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
interface Directory { enabled: boolean; api_url: string; verify_tls: boolean; has_token: boolean }
interface AllSettings {
  backup: Backup; retention: Retention; syslog: Syslog; smtp: Smtp; directory: Directory;
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
          <Button variant="contained" onClick={save}>Save</Button>
          {extra}
        </Stack>
      </Stack>
    </Paper>
  );
}

/** A button that runs a test action and shows its result beside the section. */
function TestButton({ label, run }: { label: string; run: () => Promise<string> }) {
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <>
      <Button variant="outlined" onClick={() => run().then((t) => setMsg({ ok: true, text: t }),
        (e) => setMsg({ ok: false, text: fromApiError(e).summary }))}>{label}</Button>
      {msg && <Alert severity={msg.ok ? "success" : "error"} sx={{ py: 0 }} onClose={() => setMsg(null)}>{msg.text}</Alert>}
    </>
  );
}

// Admin settings (design §21.5): file locations, backups, history retention,
// syslog output for the audit trail, and SMTP for job emails. Admin only.
export default function Settings() {
  const [s, setS] = useState<AllSettings | null>(null);
  const [error, setError] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [testTo, setTestTo] = useState("");
  const [dirToken, setDirToken] = useState("");
  const [backups, setBackups] = useState<BackupList | null>(null);

  const loadBackups = () => api.get<BackupList>("/api/admin/backups").then(setBackups, () => setBackups(null));
  useEffect(() => {
    api.get<AllSettings>("/api/admin/settings").then(setS, (e) => setError(e.message));
    loadBackups();
  }, []);
  if (!s) return error ? <Alert severity="error">{error}</Alert> : null;

  const set = <K extends keyof AllSettings>(k: K, patch: Partial<AllSettings[K]>) =>
    setS({ ...s, [k]: { ...s[k], ...patch } });
  const put = async <K extends "backup" | "retention" | "syslog" | "smtp" | "directory">(k: K, body: object) => {
    const saved = await api.put<AllSettings[K]>(`/api/admin/settings/${k}`, body);
    setS((cur) => (cur ? { ...cur, [k]: saved } : cur));
  };
  const num = (v: string) => Number(v);

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Settings</Typography>

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

      <Section title="Backups" intro="A daily pg_dump of the LABaPe database (custom format; restore with pg_restore). Times are UTC."
        onSave={async () => { await put("backup", s.backup); return "Backup settings saved."; }}
        extra={<Button variant="outlined" onClick={() => api.post("/api/admin/backups").then(loadBackups,
          (e) => setError(fromApiError(e).summary))}>Back up now</Button>}>
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
            <Button size="small" onClick={loadBackups}>Refresh</Button>
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
        extra={<TestButton label="Send test message" run={() =>
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
          <TestButton label="Send test email" run={() =>
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
        extra={<TestButton label="Test search" run={() =>
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
      {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    </Stack>
  );
}
