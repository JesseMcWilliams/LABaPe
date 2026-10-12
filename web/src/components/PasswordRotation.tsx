import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert, Checkbox, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { StateChip } from "../components";
import { ActionButton } from "./Confirm";

interface Policy {
  id: number; kind: string; label: string; enabled: boolean; interval_hours: number; status: string;
  last_rotated_at: string | null; next_due_at: string | null; last_error: string; notes: string;
}

/** The environment's Windows Administrator and domain Administrator passwords
 * (design §24, R4): weekly by default, changeable or off per environment.
 * The DSRM password is never rotated. */
export default function PasswordRotation({ envId }: { envId: string }) {
  const navigate = useNavigate();
  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [days, setDays] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ policies: Policy[] }>(`/api/environments/${envId}/rotation`);
      setPolicies(r.policies);
      setDays(Object.fromEntries(r.policies.map((p) => [p.kind, String(p.interval_hours / 24)])));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [envId]);
  useEffect(() => { load(); }, [load]);

  const save = async (p: Policy, enabled: boolean) => {
    setError(""); setSaved("");
    const d = Number(days[p.kind]);
    if (!Number.isInteger(d) || d < 1 || d > 365) { setError("Use a whole number of days from 1 to 365."); return; }
    try {
      await api.put(`/api/environments/${envId}/rotation/${p.kind}`, { enabled, interval_hours: d * 24 });
      setSaved(`${p.label}: saved.`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const rotate = async (p: Policy) => {
    setError("");
    try {
      const r = await api.post<{ job_id: number }>(`/api/environments/${envId}/rotation/${p.kind}/rotate`);
      navigate(`/jobs/${r.job_id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (policies === null) return error ? <Alert severity="error">{error}</Alert> : null;
  if (policies.length === 0) return null;
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2">Password rotation</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        LABaPe changes these passwords on the VMs on a schedule and updates the credentials and its own records.
        The DSRM password is never changed.
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
      {saved && <Alert severity="success" sx={{ mb: 1 }}>{saved}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Account</TableCell><TableCell>On</TableCell><TableCell>Every (days)</TableCell>
            <TableCell>Last changed</TableCell><TableCell>Next</TableCell><TableCell>Status</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {policies.map((p) => (
            <TableRow key={p.kind}>
              <TableCell><Tooltip title={p.notes} arrow><span>{p.label.split(": ").slice(1).join(": ") || p.label}</span></Tooltip></TableCell>
              <TableCell>
                <Tooltip title={p.enabled ? "Turn scheduled changes off (saves at once)" : "Turn scheduled changes on (saves at once)"} arrow>
                  <Checkbox size="small" checked={p.enabled} onChange={(e) => save(p, e.target.checked)} />
                </Tooltip>
              </TableCell>
              <TableCell>
                <TextField size="small" type="number" value={days[p.kind] ?? ""} sx={{ width: 90 }}
                  onChange={(e) => setDays({ ...days, [p.kind]: e.target.value })} />
              </TableCell>
              <TableCell>{p.last_rotated_at ? fmtTime(p.last_rotated_at) : "never"}</TableCell>
              <TableCell>{p.enabled && p.next_due_at ? fmtTime(p.next_due_at) : "off"}</TableCell>
              <TableCell>
                <Tooltip title={p.last_error || ""} arrow><span><StateChip state={p.status} /></span></Tooltip>
              </TableCell>
              <TableCell>
                <Stack direction="row" spacing={1}>
                  <ActionButton size="small" onClick={() => save(p, p.enabled)} tip="Save the interval (no confirmation)">
                    Save
                  </ActionButton>
                  <ActionButton size="small" onClick={() => rotate(p)} tip="Change this password now"
                    confirm={{ title: `Change the ${p.label} password now?`, confirmLabel: "Change now",
                      body: "A job sets a new password on the VMs, checks it signs in, and updates the credentials. Anyone signed in with the old password keeps their session but needs the new one next time." }}>
                    Change now
                  </ActionButton>
                </Stack>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}
