// Admin -> Self-tests (plan: Planning_SMART-Self-Test-Schedule.md): every host's SMART self-test schedule, what its
// smartd.conf really has, when the next test runs, each drive's last result; edit the stack default or one host.
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Chip, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import SelfTestDialog, { Drive, Schedule, SelfTestHost, describe, hostTime } from "../components/SelfTestDialog";

interface Fleet { default: Schedule; hosts: SelfTestHost[] }

/** The newest short and long result of a drive, in days of running ago (the host page's wording). */
export const lastTests = (d: Drive): string => {
  if (!d.tests || d.power_on_hours == null) return "no self-test log (NVMe or not read)";
  const parts = (["short", "long"] as const).map((k) => {
    const t = d.tests?.[k];
    if (!t || t.hours == null) return `${k}: never`;
    const days = Math.floor((d.power_on_hours! - t.hours) / 24);
    return `${k}: ${t.passed === false ? "FAILED, " : ""}${days === 0 ? "today" : `${days} d ago`}`;
  });
  return parts.join(" · ");
};

export const SyncChip = ({ h }: { h: SelfTestHost }) => {
  if (!h.ready) return <Chip size="small" label="no SSH access" />;
  if (h.in_sync === null) return <Chip size="small" label="not read yet" title="The host's smartd.conf hasn't been read yet (the hourly collection, or Refresh on its page)" />;
  return h.in_sync
    ? <Chip size="small" color="success" label="applied" title="The host's smartd.conf has this schedule" />
    : <Chip size="small" color="warning" label="not applied yet" title="The host's smartd.conf differs; LABaPe applies it within a few hours, or save again to apply it now" />;
};

export default function SelfTests() {
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<SelfTestHost | "default" | null>(null);
  const load = useCallback(() => api.get<Fleet>("/api/admin/selftests").then(setFleet, (e) => setError(e.message)), []);
  useEffect(() => { void load(); }, [load]);
  if (!fleet) return error ? <Alert severity="error">{error}</Alert> : null;
  const following = fleet.hosts.filter((h) => h.ready && h.policy.mode === "default").map((h) => h.name);
  return (
    <Stack spacing={2}>
      <Typography variant="h5">Self-tests</Typography>
      <Typography variant="body2" color="text.secondary">
        smartd on each host runs short and long SMART self-tests on its drives. This is the schedule LABaPe keeps on every host (it writes it into the
        host&apos;s smartd.conf and restarts smartd), what the host really has, and each drive&apos;s last result. A drive that never ran one is flagged
        on its host&apos;s page unless its host or the drive is left out here.
      </Typography>
      {msg && <Alert severity="success" onClose={() => setMsg("")}>{msg}</Alert>}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Stack default</Typography>
            <Typography variant="body2">{describe(fleet.default)}{fleet.default.warn ? "" : " · no warnings about missing tests"}</Typography>
            <Typography variant="caption" color="text.secondary">Applies to {following.length} host{following.length === 1 ? "" : "s"} that follow it.</Typography>
          </div>
          <Button variant="outlined" onClick={() => setEditing("default")} title="Change the schedule every host follows unless it has its own">Edit default</Button>
        </Stack>
      </Paper>
      <Paper variant="outlined" sx={{ p: 2, overflowX: "auto" }}>
        <Table size="small">
          <TableHead>
            <TableRow><TableCell>Host</TableCell><TableCell>Schedule</TableCell><TableCell>On the host</TableCell><TableCell>Next run</TableCell><TableCell>Drives</TableCell><TableCell align="right" /></TableRow>
          </TableHead>
          <TableBody>
            {fleet.hosts.map((h) => (
              <TableRow key={h.id} sx={{ verticalAlign: "top" }}>
                <TableCell><RouterLink to={`/hosts/${h.id}?section=drives`}>{h.name}</RouterLink></TableCell>
                <TableCell>
                  {h.described}{" "}
                  <Chip size="small" variant="outlined" label={h.policy.mode === "default" ? "default" : h.policy.mode === "off" ? "off" : "own"} />
                  {!h.policy.warn && <Chip size="small" variant="outlined" sx={{ ml: 0.5 }} label="no warnings" />}
                </TableCell>
                <TableCell>
                  <SyncChip h={h} />
                  {h.smartd && !h.smartd.active && <Chip size="small" color="error" sx={{ ml: 0.5 }} label="smartd not running" />}
                  {h.smartd && h.in_sync === false && <Typography variant="caption" component="div" color="text.secondary">has: {h.smartd.described}</Typography>}
                </TableCell>
                <TableCell sx={{ fontSize: 12 }}>
                  {h.next.short ? <>short {hostTime(h.next.short, h.smartd?.tz)}</> : "—"}
                  {h.next.long && <div>long {hostTime(h.next.long, h.smartd?.tz)}</div>}
                </TableCell>
                <TableCell sx={{ fontSize: 12 }}>
                  {h.drives.length === 0 ? "—" : h.drives.map((d) => (
                    <div key={d.device} title={d.warnings.join("\n")}>
                      {d.device}: {d.left_out ? "left out" : lastTests(d)}{d.warnings.length > 0 && " ⚠"}
                    </div>
                  ))}
                </TableCell>
                <TableCell align="right">
                  <Button size="small" disabled={!h.ready} onClick={() => setEditing(h)} title={h.ready ? "Change this host's schedule, the drives left out, or the warning" : "Set up SSH access first"}>Edit</Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>
      {editing && (
        <SelfTestDialog host={editing === "default" ? null : editing} def={fleet.default} following={following} onClose={() => setEditing(null)}
          onSaved={(note) => { setEditing(null); setMsg(note); void load(); }} />
      )}
    </Stack>
  );
}
