// Quotas (design §41): limits on what each user's environments may use. A user's own quota wins,
// else the most generous of their groups', else the default; admins have none.
import { useEffect, useState } from "react";
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { api } from "../api";
import { ActionButton } from "../components/Confirm";

interface Quota {
  id?: number; principal_type: string; principal: string; max_vcpus: number | null; max_memory_mb: number | null;
  max_disk_gb: number | null; max_environments: number | null;
}
const LIMITS: [keyof Quota, string][] = [
  ["max_vcpus", "vCPUs"], ["max_memory_mb", "Memory (MB)"], ["max_disk_gb", "VM disk (GB)"], ["max_environments", "Environments"],
];
const EMPTY: Quota = { principal_type: "user", principal: "", max_vcpus: null, max_memory_mb: null, max_disk_gb: null, max_environments: null };
const show = (v: number | null) => (v === null ? "no limit" : String(v));

export default function Quotas() {
  const [rows, setRows] = useState<Quota[] | null>(null);
  const [error, setError] = useState("");
  const [edit, setEdit] = useState<Quota | null>(null);
  const [formError, setFormError] = useState("");
  const load = () => api.get<Quota[]>("/api/admin/quotas").then(setRows, (e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const save = () => api.post("/api/admin/quotas", edit).then(() => { setEdit(null); load(); }, (e) => setFormError(e.message));

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!rows) return null;
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>Quotas</Typography>
        <Button variant="contained" onClick={() => { setFormError(""); setEdit({ ...EMPTY }); }} title="Set limits for a user or group">Add quota</Button>
      </Stack>
      <Typography variant="body2" color="text.secondary">
        What a user's environments (the ones they created and that aren't destroyed) may ask for, checked when an environment is
        created or deployed again. A user's own quota wins; else the most generous of their groups', limit by limit; else the
        default. Admins have none. Empty is no limit.
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow><TableCell>For</TableCell>{LIMITS.map(([, l]) => <TableCell key={l}>{l}</TableCell>)}<TableCell /></TableRow>
        </TableHead>
        <TableBody>
          {rows.map((q) => (
            <TableRow key={q.id}>
              <TableCell>{q.principal_type === "default" ? "Everyone (default)" : `${q.principal_type} ${q.principal}`}</TableCell>
              {LIMITS.map(([k]) => <TableCell key={k}>{show(q[k] as number | null)}</TableCell>)}
              <TableCell>
                <Stack direction="row" spacing={0.5}>
                  <Button size="small" onClick={() => { setFormError(""); setEdit({ ...q }); }} title="Change this quota">Edit</Button>
                  <ActionButton size="small" color="error" tip="Remove this quota"
                    confirm={{ title: "Remove the quota?", confirmLabel: "Remove", body: "The next quota that applies (group or default), or none, takes over." }}
                    onClick={() => api.del(`/api/admin/quotas/${q.id}`).then(load)}>Remove</ActionButton>
                </Stack>
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && <TableRow><TableCell colSpan={6}>No quotas: nobody is limited.</TableCell></TableRow>}
        </TableBody>
      </Table>
      <Dialog open={!!edit} onClose={() => setEdit(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{edit?.id ? "Edit quota" : "Add quota"}</DialogTitle>
        {edit && <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Stack direction="row" spacing={1}>
              <TextField select label="For" value={edit.principal_type} sx={{ width: 160 }} disabled={!!edit.id}
                onChange={(e) => setEdit({ ...edit, principal_type: e.target.value })}>
                <MenuItem value="user">User</MenuItem><MenuItem value="group">Group</MenuItem><MenuItem value="default">Everyone (default)</MenuItem>
              </TextField>
              {edit.principal_type !== "default" && (
                <TextField label={edit.principal_type === "user" ? "Username" : "Group"} value={edit.principal} disabled={!!edit.id}
                  onChange={(e) => setEdit({ ...edit, principal: e.target.value })} sx={{ flexGrow: 1 }} />
              )}
            </Stack>
            {LIMITS.map(([k, l]) => (
              <TextField key={k} label={l} type="number" value={edit[k] ?? ""} helperText="Empty: no limit"
                onChange={(e) => setEdit({ ...edit, [k]: e.target.value === "" ? null : Number(e.target.value) })} />
            ))}
            {formError && <Alert severity="error">{formError}</Alert>}
          </Stack>
        </DialogContent>}
        <DialogActions>
          <Button onClick={() => setEdit(null)}>Cancel</Button>
          <Button variant="contained" onClick={save}>Save</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
