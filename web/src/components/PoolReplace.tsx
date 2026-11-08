// Guided drive replacement (plan R1): which drive to pull, which new drive to use, what will be written, then the
// rebuild shows on the pool. The new drive's partition table is overwritten, so its serial number is typed.
import { useState } from "react";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, Typography } from "@mui/material";
import { api } from "../api";
import { Field } from "../forms";
import { gb, Pool } from "./HostSections";

export function ReplaceDialog({ pool, hostId, onClose, onDone }: { pool: Pool; hostId: number; onClose: () => void; onDone: (m: string) => void }) {
  const plan = pool.replace!;
  const [device, setDevice] = useState(plan.candidates[0]?.path ?? "");
  const [typed, setTyped] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const cand = plan.candidates.find((c) => c.path === device);
  const want = cand ? cand.serial || cand.path : "";
  const go = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/hosts/${hostId}/pools/replace`, { pool: pool.id, device, confirm: typed });
      onDone(`${device} was prepared and added to ${pool.name}; it is rebuilding.`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Replace a drive of {pool.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <Typography variant="subtitle2">1. The drive to pull</Typography>
          {plan.pull.length ? plan.pull.map((d) => (
            <Typography key={d.disk} variant="body2">
              <b>{d.disk}</b>, serial <b>{d.serial || "unknown"}</b>{d.model ? `, ${d.model}` : ""}{d.size ? `, ${gb(d.size)}` : ""}. Match the serial to the label on the drive before you pull it.
              If it is still in the machine it can stay until the new one is in.
            </Typography>
          )) : <Typography variant="body2">The failed drive is already gone from the machine; the slot is empty.</Typography>}
          {plan.at_risk.length > 0 && <Alert severity="warning">Until the rebuild finishes, {plan.at_risk.join(", ")} holds the only copy of what the missing drive mirrored. Don&apos;t power the machine off, and keep backups current.</Alert>}
          <Typography variant="subtitle2">2. The new drive</Typography>
          {plan.candidates.length === 0 ? (
            <Alert severity="info">No free drive at least as large as {gb(plan.need)} (the drive it must match, {plan.template}). Power down, fit the new drive, power on, then Refresh. Hot-plugged drives show here after Refresh.</Alert>
          ) : (
            <>
              <Field select label="Use this drive" value={device} onChange={(e) => { setDevice(e.target.value); setTyped(""); }}>
                {plan.candidates.map((c) => <MenuItem key={c.path} value={c.path}>{c.path} · {c.model || "unknown model"} · {gb(c.size)} · serial {c.serial || "none"}</MenuItem>)}
              </Field>
              <Typography variant="body2" color="text.secondary">
                Only drives with no partitions, filesystem or RAID label are offered. LABaPe copies the partition layout of {plan.template} (serial {plan.template_serial || "unknown"}) to it,
                with new disk and partition ids, and adds partition {plan.partition} to {pool.name}; the array then rebuilds onto it. The layout of the new drive is overwritten.
              </Typography>
              <Field label={`Type ${want} to confirm`} value={typed} onChange={(e) => setTyped(e.target.value)} />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" color="warning" disabled={busy || !cand || typed.trim() !== want} onClick={go}>Prepare and add</Button>
      </DialogActions>
    </Dialog>
  );
}
