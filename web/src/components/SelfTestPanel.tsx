// A host's self-test schedule, on its Drives page (plan: Planning_SMART-Self-Test-Schedule.md): what applies, what
// the host has, the next run, and the edit dialog; the same one the Admin -> Self-tests page uses.
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Paper, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import SelfTestDialog, { Schedule, SelfTestHost, hostTime } from "./SelfTestDialog";
import { SyncChip } from "../pages/SelfTests";

type One = SelfTestHost & { default: Schedule };

export default function SelfTestPanel({ hostId }: { hostId: number }) {
  const [one, setOne] = useState<One | null>(null);
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api.get<One>(`/api/admin/selftests/hosts/${hostId}`).then(setOne, () => setOne(null)), [hostId]);
  useEffect(() => { void load(); }, [load]);
  if (!one) return null;
  const infos = one.drives.flatMap((d) => d.warnings.filter((w) => w.includes("no self-test has run yet")));
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            Self-tests <SyncChip h={one} />
          </Typography>
          <Button size="small" variant="outlined" onClick={() => setOpen(true)} title="Change this host's schedule, the drives left out, or the warning">Edit</Button>
        </Stack>
        <Typography variant="body2">
          {one.described}
          {one.policy.mode === "default" ? " (the stack default)" : one.policy.mode === "off" ? " (switched off for this host)" : " (this host's own)"}.
          {one.next.short || one.next.long ? <> Next: {[one.next.short && `short ${hostTime(one.next.short, one.smartd?.tz)}`, one.next.long && `long ${hostTime(one.next.long, one.smartd?.tz)}`].filter(Boolean).join(", ")}.</> : null}
        </Typography>
        {one.smartd && !one.smartd.active && <Alert severity="error">smartd isn&apos;t running on this host, so no scheduled self-test happens.</Alert>}
        {one.in_sync === false && <Alert severity="warning">The host&apos;s smartd.conf has {one.smartd?.described ?? "something else"}; LABaPe applies the schedule within a few hours, or save it again to apply it now.</Alert>}
        {infos.map((m, i) => <Typography key={i} variant="caption" color="text.secondary" component="div">{m}</Typography>)}
        <Typography variant="caption" color="text.secondary">
          All hosts: <RouterLink to="/admin/selftests">Admin → Self-tests</RouterLink>
        </Typography>
        {msg && <Alert severity="success" onClose={() => setMsg("")}>{msg}</Alert>}
      </Stack>
      {open && <SelfTestDialog host={one} def={one.default} following={[]} onClose={() => setOpen(false)}
        onSaved={(note) => { setOpen(false); setMsg(note); void load(); }} />}
    </Paper>
  );
}
