import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Paper, Stack, Typography } from "@mui/material";
import { api, fmtTime } from "../api";
import { ActionButton } from "../components/Confirm";
import { DetailHeader, Facts } from "../components/PageParts";
import { ErrorSummary, Field, FieldErrors, fromApiError } from "../forms";
import { interval, SecretPolicy, StatusChip } from "./Secrets";

// One secret's rotation (design §24). Admin only.
export default function SecretDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [p, setP] = useState<(SecretPolicy & { elevated: string[]; stores: string[] }) | null>(null);
  const [form, setForm] = useState({ interval_hours: 24, enabled: true, managed_externally: false, store: "builtin",
    elevated_ref: "", params: {} as Record<string, string>, notes: "" });
  const [errs, setErrs] = useState<{ summary: string; fields: FieldErrors }>({ summary: "", fields: {} });
  const [saved, setSaved] = useState("");

  const load = () => api.get<SecretPolicy & { elevated: string[]; stores: string[] }>(`/api/admin/secrets/${id}`).then((x) => {
    setP(x);
    setForm({ interval_hours: x.interval_hours, enabled: x.enabled, managed_externally: x.managed_externally,
      store: x.store, elevated_ref: x.elevated_ref, params: x.params ?? {}, notes: x.notes });
  }, (e) => setErrs({ summary: e.message, fields: {} }));
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setSaved("");
    try {
      await api.put(`/api/admin/secrets/${id}`, form);
      await load();
      setSaved("Saved.");
      setErrs({ summary: "", fields: {} });
    } catch (e) { setErrs(fromApiError(e)); }
  };
  const rotate = async () => {
    try {
      const r = await api.post<{ job_id: number }>(`/api/admin/secrets/${id}/rotate`);
      navigate(`/jobs/${r.job_id}`);
    } catch (e) { setErrs(fromApiError(e)); }
  };

  if (!p) return errs.summary ? <Alert severity="error">{errs.summary}</Alert> : null;
  const canRotate = p.automatic && !p.managed_externally && p.enabled;
  return (
    <Stack spacing={2}>
      <DetailHeader back="/admin/secrets" backLabel="Secrets" title={<>{p.label} <StatusChip p={p} /></>}
        actions={
          <ActionButton variant="outlined" disabled={!canRotate} onClick={rotate}
            tip={canRotate ? "Rotate this secret now, as a job" : "LABaPe doesn't rotate this secret (see the notes)"}
            confirm={{ title: `Rotate ${p.label} now?`, confirmLabel: "Rotate",
              body: `${p.rotator_notes} ${p.needs_restart ? "This one restarts a service: maintenance mode is on while it runs." : "No service restarts."} If anything fails, the old secret stays and it retries in an hour.` }}>
            Rotate now
          </ActionButton>
        } />
      {saved && <Alert severity="success" onClose={() => setSaved("")}>{saved}</Alert>}
      {p.status === "failed" && <Alert severity="error">Last rotation failed: {p.last_error}</Alert>}
      {p.needs_restart && (
        <Alert severity="info">
          Rotating this secret restarts a service, so it rotates at most weekly, inside the maintenance window
          (Settings → Maintenance window), with maintenance mode on while it runs.
        </Alert>
      )}
      <Facts rows={[
        ["How it rotates", p.rotator_notes],
        ["Class", p.secret_class.replace("_", "-")],
        ["Last rotated", fmtTime(p.last_rotated_at) || "never"],
        ["Next rotation", canRotate ? fmtTime(p.next_due_at) : "—"],
        ["Last job", p.last_job ? <Button size="small" component={RouterLink} to={`/jobs/${p.last_job.id}`}>#{p.last_job.id} {p.last_job.state}</Button> : "none"],
        ["Policy name", p.name],
      ]} />
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1}>
          <ErrorSummary summary={errs.summary} fields={errs.fields} />
          <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
            <Field label="Rotate every (hours)" type="number" value={form.interval_hours} sx={{ width: 190 }}
              error={errs.fields.interval_hours} help={interval(form.interval_hours)}
              onChange={(e) => setForm({ ...form, interval_hours: Number(e.target.value) })} />
            <Field select label="Store" value={form.store} sx={{ width: 160 }} error={errs.fields.store}
              help={p.secret_name ? "Saving a new store moves the secret" : "Not in the catalog: it stays where it is"}
              disabled={!p.secret_name}
              onChange={(e) => setForm({ ...form, store: e.target.value })}>
              {[...new Set([p.store, ...p.stores])].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
            </Field>
            <Field select label="Elevated account" value={form.elevated_ref} sx={{ minWidth: 220 }} error={errs.fields.elevated_ref}
              help={p.needs_elevated ? "Needed: this secret can't rotate itself" : "Not needed"}
              onChange={(e) => setForm({ ...form, elevated_ref: e.target.value })}>
              <MenuItem value="">(none)</MenuItem>
              {p.elevated.map((n) => <MenuItem key={n} value={n}>{n}</MenuItem>)}
            </Field>
            {p.kind === "directory_token" && (
              <Field label="Authentik token identifier" value={form.params.identifier ?? ""} sx={{ minWidth: 220 }}
                onChange={(e) => setForm({ ...form, params: { ...form.params, identifier: e.target.value } })} />
            )}
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap" }}>
            <FormControlLabel label="Rotate on schedule" control={<Checkbox checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />} />
            <FormControlLabel label="The secret store rotates it (LABaPe only reads it)" control={<Checkbox checked={form.managed_externally}
              onChange={(e) => setForm({ ...form, managed_externally: e.target.checked })} />} />
          </Stack>
          <Field label="Notes" value={form.notes} fullWidth multiline minRows={2} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          <Box>
            <ActionButton variant="contained" onClick={save} tip="Save this secret's rotation settings"
              confirm={{ title: `Save the rotation settings for ${p.label}?`, confirmLabel: "Save",
                body: "The next rotation is scheduled from these settings. Nothing rotates now." }}>
              Save
            </ActionButton>
          </Box>
        </Stack>
      </Paper>
      <Typography variant="caption" color="text.secondary">Every rotation and every change here is audited.</Typography>
    </Stack>
  );
}
