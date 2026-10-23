// The Fix button on an admin check (app/labape/fixes.py): asks for the credentials the repair needs, runs it and
// reports. What is typed here is sent once and never stored.
import { useState } from "react";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { api } from "../api";

export interface FixField {
  name: string; label: string; secret: boolean; required: boolean; default: string; help: string; kind: string; options?: string[];
}
export interface FixInfo { name: string; title: string; intro: string; fields: FixField[] }

export default function FixDialog({ fix, onClose, onDone }: { fix: FixInfo; onClose: () => void; onDone: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fix.fields.map((f) => [f.name, f.default ?? ""])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const missing = fix.fields.some((f) => f.required && !values[f.name]?.trim());
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await api.post<{ message: string }>(`/api/admin/fixes/${fix.name}`, { values });
      setDone(r.message);
      setValues((v) => Object.fromEntries(Object.keys(v).map((k) => [k, ""])));   // forget what was typed
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{fix.title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography variant="body2" color="text.secondary">{fix.intro}</Typography>
          {error && <Alert severity="error">{error}</Alert>}
          {done ? <Alert severity="success">{done}</Alert> : fix.fields.map((f) => (
            <TextField key={f.name} size="small" label={f.label} required={f.required} helperText={f.help || undefined}
              value={values[f.name] ?? ""} type={f.secret ? "password" : "text"} select={f.kind === "select"}
              autoComplete={f.secret ? "new-password" : "off"} disabled={busy}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}>
              {(f.options ?? []).map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
            </TextField>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>{done ? "Close" : "Cancel"}</Button>
        {!done && <Button variant="contained" onClick={run} disabled={busy || missing}>{busy ? "Fixing…" : "Fix"}</Button>}
      </DialogActions>
    </Dialog>
  );
}
