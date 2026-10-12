import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Link, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Tooltip,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { ListHeader } from "../components/PageParts";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";

// Settings > Secrets (design §24): every internal secret, how it's rotated,
// and when. Admin only.
export interface SecretPolicy {
  id: number; name: string; kind: string; label: string; store: string; secret_class: string; interval_hours: number;
  needs_restart: boolean; enabled: boolean; managed_externally: boolean; elevated_ref: string;
  params: Record<string, string>; status: string; last_rotated_at: string | null; next_due_at: string | null;
  last_error: string; notes: string; automatic: boolean; needs_elevated: boolean; rotator_notes: string;
  last_job: { id: number; state: string } | null;
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
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Secret</TableCell><TableCell>Class</TableCell><TableCell>Store</TableCell>
            <TableCell>Every</TableCell><TableCell>Last rotated</TableCell><TableCell>Next</TableCell>
            <TableCell>Status</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {data?.policies.map((p) => (
            <TableRow key={p.id} hover sx={{ opacity: p.enabled ? 1 : 0.5 }}>
              <TableCell>
                <Link component={RouterLink} to={`/secrets/${p.id}`}>{p.label}</Link>
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
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Stack>
  );
}
