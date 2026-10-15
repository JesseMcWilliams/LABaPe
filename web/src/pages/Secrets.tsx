import { useEffect, useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Link, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Tab, Tabs, Tooltip, Typography,
} from "@mui/material";
import { api, fmtTime, KvmHost } from "../api";
import { ListHeader } from "../components/PageParts";
import StoredSecrets from "../components/StoredSecrets";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";

// Settings > Secrets (design §24): every internal secret, how it's rotated,
// and when. Admin only.
export interface SecretPolicy {
  id: number; name: string; kind: string; label: string; store: string; secret_class: string; interval_hours: number;
  needs_restart: boolean; enabled: boolean; managed_externally: boolean; elevated_ref: string;
  params: Record<string, string>; status: string; last_rotated_at: string | null; next_due_at: string | null;
  last_error: string; notes: string; automatic: boolean; needs_elevated: boolean; rotator_notes: string;
  last_job: { id: number; state: string } | null; secret_name: string | null;
  // Health (S1, question 161).
  check_status: string; check_error: string; last_checked_at: string | null; next_check_at: string | null;
  recoverable: boolean; recovery: string; checkable: boolean; check_every_hours: number | null; revealable?: boolean;
  section: "stack" | "kvm" | "environments"; environment_id: number | null;
}

// Sections (S2): the stack's own secrets, what reaches the KVM hosts, and the environments'.
const SECTIONS = [
  { key: "stack", label: "Stack", help: "The stack's own secrets: its keys, the database, the CA, sign-in, the stores' logins, links." },
  { key: "kvm", label: "KVM hosts", help: "How the stack reaches its KVM hosts: the worker's key (libvirt and jobs) and each host's LABaPe access account." },
  { key: "environments", label: "Environments", help: "Each environment's accounts and passwords, its credentials sheet and inventory." },
] as const;

const HEALTH_COLOR: Record<string, "success" | "error" | "warning" | "default"> = {
  ok: "success", recovered: "success", failed: "error", recovering: "warning",
};

/** Whether the stored value still works, when it was checked, and how often (S1). */
export function HealthCell({ p, onCheck }: { p: SecretPolicy; onCheck?: () => void }) {
  if (!p.checkable) return <Typography variant="caption" color="text.secondary">no check</Typography>;
  const when = p.check_every_hours === null ? "Not checked: it rotates at least daily, which proves it"
    : `Checked every ${interval(p.check_every_hours)}`;
  return (
    <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
      <Tooltip title={<>{p.check_error || (p.check_status === "unchecked" ? "Not checked yet" : "")}<br />{p.last_checked_at ? `Last ${fmtTime(p.last_checked_at)}. ` : ""}{when}.{p.next_check_at ? ` Next ${fmtTime(p.next_check_at)}.` : ""}</>}>
        <Chip size="small" color={HEALTH_COLOR[p.check_status] ?? "default"} label={p.check_status === "none" ? "not in use" : p.check_status} />
      </Tooltip>
      {onCheck && <Button size="small" onClick={onCheck} title="Check now that the stored value works (recovered if it doesn't and can be)">Check now</Button>}
    </Stack>
  );
}

export function RecoverableCell({ p }: { p: SecretPolicy }) {
  return (
    <Tooltip title={p.recovery || "No check for this kind of secret"}>
      <Chip size="small" variant="outlined" color={p.recoverable ? "success" : "warning"} label={p.recoverable ? "Yes" : "No"} />
    </Tooltip>
  );
}

export const interval = (h: number) => (h % 24 === 0 ? `${h / 24} day${h === 24 ? "" : "s"}` : `${h} hours`);

export function StatusChip({ p }: { p: SecretPolicy }) {
  if (p.managed_externally) return <Chip size="small" label="external store" />;
  if (!p.enabled) return <Chip size="small" label="off" />;
  if (!p.automatic) return <Chip size="small" label={p.secret_class === "external" ? "reminder" : "manual"} />;
  if (p.needs_elevated && !p.elevated_ref)
    return <Tooltip title="Rotates once an elevated account is chosen on its page; a reminder until then">
      <Chip size="small" label="reminder" /></Tooltip>;
  const color = p.status === "ok" ? "success" : p.status === "failed" ? "error" : "default";
  return <Chip size="small" color={color} label={p.status} />;
}

/** Show a stored platform secret people need to use (the Authentik administrator's password); audited, never kept in the page. */
function RevealButton({ p, onError }: { p: SecretPolicy; onError: (m: string) => void }) {
  const [value, setValue] = useState<string | null>(null);
  const show = () => api.post<{ value: string }>(`/api/admin/secrets/${p.id}/reveal`).then((r) => setValue(r.value), (e) => onError(e.message));
  return (
    <>
      <Button size="small" sx={{ ml: 1 }} onClick={show} title="Show the stored value to you (audited, without the value)">Reveal</Button>
      <Dialog open={value !== null} onClose={() => setValue(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{p.label}</DialogTitle>
        <DialogContent>
          <Stack spacing={1} sx={{ mt: 1 }}>
            <Typography variant="body2">The stored value. It is shown once here and not kept in the page; the reveal is in the audit log.</Typography>
            <Typography sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>{value}</Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => navigator.clipboard.writeText(value ?? "")} title="Copy it to the clipboard">Copy</Button>
          <Button variant="contained" onClick={() => setValue(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

function AddElevated({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", value: "", description: "" });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const save = async () => {
    try {
      await api.post("/api/admin/secrets/elevated", form);
      setOpen(false);
      setForm({ name: "", value: "", description: "" });
      onDone();
    } catch (e) { setErrs(fromApiError(e)); }
  };
  return (
    <>
      <Button variant="outlined" onClick={() => setOpen(true)}
        title="Store the credential of an account allowed to rotate secrets that can't rotate themselves (opens a form)">
        Add elevated account
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Add an elevated account</DialogTitle>
        <DialogContent>
          <Stack spacing={1} sx={{ mt: 1 }}>
            <ErrorSummary summary={errs.summary} fields={errs.fields} />
            <Field label="Name" value={form.name} error={errs.fields.name} help="e.g. authentik-admin"
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <Field label="Credential (token or password)" type="password" value={form.value} error={errs.fields.value}
              autoComplete="new-password" help="Kept encrypted in the built-in store; never shown again"
              onChange={(e) => setForm({ ...form, value: e.target.value })} />
            <Field label="What it is" value={form.description} fullWidth
              help="e.g. Authentik token with permission to set the directory token's key"
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} title="Close without saving">Cancel</Button>
          <Button variant="contained" onClick={save} title="Store the credential (audited)">Save</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

export default function Secrets() {
  const [data, setData] = useState<{ policies: SecretPolicy[]; elevated: string[] } | null>(null);
  const [error, setError] = useState("");
  const [params, setParams] = useSearchParams();
  const section = (SECTIONS.find((s) => s.key === params.get("section"))?.key ?? "stack") as SecretPolicy["section"];
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  useEffect(() => { if (section === "kvm") api.get<KvmHost[]>("/api/hosts").then(setHosts, () => setHosts([])); }, [section]);
  const load = () => api.get<{ policies: SecretPolicy[]; elevated: string[] }>("/api/admin/secrets")
    .then(setData, (e) => setError(e.message));
  useEffect(() => { load(); }, []);
  return (
    <Stack spacing={2}>
      <ListHeader title="Secrets"
        intro="Every internal secret, where it's kept and how often it rotates. Secrets whose rotation restarts a service rotate weekly in the maintenance window.">
        <AddElevated onDone={load} />
      </ListHeader>
      {error && <Alert severity="error">{error}</Alert>}
      {data && data.elevated.length > 0 && (
        <Alert severity="info" icon={false}>Elevated accounts: {data.elevated.join(", ")}</Alert>
      )}
      <Tabs value={section} onChange={(_, v) => setParams({ section: v }, { replace: true })}>
        {SECTIONS.map((s) => <Tab key={s.key} value={s.key} label={s.label} />)}
      </Tabs>
      <Typography variant="body2" color="text.secondary">{SECTIONS.find((s) => s.key === section)?.help}</Typography>
      <Typography variant="h6">Rotation and health</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Secret</TableCell><TableCell>Class</TableCell><TableCell>Store</TableCell>
            <TableCell>Every</TableCell><TableCell>Last rotated</TableCell><TableCell>Next</TableCell>
            <TableCell>Status</TableCell><TableCell>Recoverable</TableCell><TableCell>Health</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {data?.policies.filter((p) => p.section === section).map((p) => (
            <TableRow key={p.id} hover sx={{ opacity: p.enabled ? 1 : 0.5 }}>
              <TableCell>
                <Link component={RouterLink} to={`/admin/secrets/${p.id}`}>{p.label}</Link>
                {p.revealable && <RevealButton p={p} onError={setError} />}
                {p.needs_restart && <Tooltip title="Restarts a service: rotates in the maintenance window"><Chip size="small" label="restart" sx={{ ml: 1 }} /></Tooltip>}
              </TableCell>
              <TableCell>{p.secret_class.replace("_", "-")}</TableCell>
              <TableCell>{p.store}</TableCell>
              <TableCell>{interval(p.interval_hours)}</TableCell>
              <TableCell>{fmtTime(p.last_rotated_at) || "never"}</TableCell>
              <TableCell>{p.automatic && p.enabled && !p.managed_externally ? fmtTime(p.next_due_at) : "—"}</TableCell>
              <TableCell>
                <StatusChip p={p} />
                {p.status === "failed" && <Tooltip title={p.last_error}><Chip size="small" variant="outlined" color="error" label="why?" sx={{ ml: 0.5 }} /></Tooltip>}
              </TableCell>
              <TableCell><RecoverableCell p={p} /></TableCell>
              <TableCell><HealthCell p={p} onCheck={() => api.post(`/api/admin/secrets/${p.id}/check`).then(load, (e) => setError(e.message))} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {section === "kvm" && (
        <>
          <Typography variant="h6">Host access accounts</Typography>
          <Typography variant="body2" color="text.secondary">
            Each KVM host's LABaPe access account signs in with the stack's host-admin key (no password); it reads the host,
            manages its accounts, opens consoles and puts the worker's key back if a check finds it gone.
          </Typography>
          <Table size="small">
            <TableHead><TableRow><TableCell>Host</TableCell><TableCell>Account</TableCell><TableCell>Access</TableCell></TableRow></TableHead>
            <TableBody>
              {hosts.map((h) => (
                <TableRow key={h.id}>
                  <TableCell><Link component={RouterLink} to={`/admin/hosts/${h.id}`}>{h.name}</Link></TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{h.access_status === "none" ? "—" : `${h.ssh_user}@${h.ssh_address}`}</TableCell>
                  <TableCell><Chip size="small" label={h.access_status} color={h.access_status === "ready" ? "success" : "default"} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
      <StoredSecrets onMoved={load} section={section} />
    </Stack>
  );
}
