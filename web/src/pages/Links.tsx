// Linked stacks (design §34): other LABaPe stacks this one reads a summary of, linked with a
// one-time code. Admins link and unlink; auditors see the list.
import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { hasRole, useAuth } from "../App";
import { ActionButton } from "../components/Confirm";

interface Summary {
  name: string; version: string; maintenance: { active: boolean; reason: string };
  hosts: unknown[]; environments: unknown[]; jobs: { running: number; queued: number; failed_24h: number };
  alerts: { level: string; message: string }[];
}
interface Link {
  id: number; name: string; url: string; ca_fingerprint: string; created_by: string; created_at: string;
  last_ok_at: string | null; last_try_at: string | null; failures: number; error: string; stale: boolean;
  summary: Summary | null;
}
interface LinkList { self: { url: string; ca_fingerprint: string }; links: Link[] }

const fp = (f: string) => f.slice(0, 32).match(/.{2}/g)?.join(":") ?? f;

function status(l: Link) {
  if (!l.last_ok_at) return <Chip size="small" color="warning" label="not reached yet" />;
  if (l.failures > 0) return <Chip size="small" color="error" label={`not answering (${l.failures})`} title={l.error} />;
  if (l.stale) return <Chip size="small" color="warning" label="stale" />;
  return <Chip size="small" color="success" label="ok" />;
}

export default function Links() {
  const { me } = useAuth();
  const admin = hasRole(me, "admin");
  const [data, setData] = useState<LinkList | null>(null);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState<{ code: string; expires_at: string } | null>(null);
  const [joining, setJoining] = useState(false);
  const [code, setCode] = useState("");
  const [joinError, setJoinError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.get<LinkList>("/api/admin/links").then(setData, (e) => setError(e.message));
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const join = async () => {
    setBusy(true);
    setJoinError("");
    try {
      await api.post("/api/admin/links", { code });
      setJoining(false);
      setCode("");
      load();
    } catch (e) {
      setJoinError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return null;
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="h5">Linked stacks</Typography>
        <Box sx={{ flexGrow: 1 }} />
        {admin && <Button variant="outlined" title="Make a one-time code for another stack's admin to link with this one"
          onClick={() => api.post<{ code: string; expires_at: string }>("/api/admin/links/invite").then(setInvite, (e) => setError(e.message))}>
          Create link code</Button>}
        {admin && <Button variant="contained" title="Paste the code another stack made" onClick={() => setJoining(true)}>Link with a stack</Button>}
      </Stack>
      <Typography variant="body2" color="text.secondary">
        Linked stacks read each other's summary (hosts, environments, jobs, alerts) every minute, read-only. This stack is{" "}
        <b>{data.self.url}</b>; its platform CA fingerprint starts {fp(data.self.ca_fingerprint)}.
      </Typography>

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Stack</TableCell><TableCell>Status</TableCell><TableCell>Last answer</TableCell>
            <TableCell>Summary</TableCell><TableCell>Linked</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {data.links.map((l) => (
            <TableRow key={l.id}>
              <TableCell>
                <a href={l.url} target="_blank" rel="noreferrer">{l.name}</a>
                <Typography variant="caption" color="text.secondary" component="div" title="The other stack's platform CA, pinned when linked">
                  CA {fp(l.ca_fingerprint)}
                </Typography>
              </TableCell>
              <TableCell>{status(l)}{l.error && <Typography variant="caption" color="error" component="div">{l.error}</Typography>}</TableCell>
              <TableCell>{l.last_ok_at ? fmtTime(l.last_ok_at) : "never"}</TableCell>
              <TableCell>
                {l.summary ? <>
                  {l.summary.version} · {l.summary.hosts.length} hosts · {l.summary.environments.length} environments ·{" "}
                  {l.summary.jobs.running} running jobs · {l.summary.alerts.length} open alerts
                  {l.summary.maintenance.active && <Chip size="small" sx={{ ml: 1, height: 18 }} label="maintenance" title={l.summary.maintenance.reason} />}
                </> : "—"}
              </TableCell>
              <TableCell>{fmtTime(l.created_at)}<Typography variant="caption" color="text.secondary" component="div">{l.created_by}</Typography></TableCell>
              <TableCell>
                {admin && <Stack direction="row" spacing={0.5}>
                  <Button size="small" title="Read its summary now" onClick={() => api.post(`/api/admin/links/${l.id}/poll`).then(load)}>Check</Button>
                  <ActionButton size="small" color="error" tip="Unlink: both stacks forget the link"
                    confirm={{ title: `Unlink ${l.name}?`, confirmLabel: "Unlink",
                      body: `${l.name} is told to drop its side too. Linking again takes a new code.` }}
                    onClick={() => api.del(`/api/admin/links/${l.id}`).then(load)}>Unlink</ActionButton>
                </Stack>}
              </TableCell>
            </TableRow>
          ))}
          {data.links.length === 0 && <TableRow><TableCell colSpan={6}>No linked stacks yet.</TableCell></TableRow>}
        </TableBody>
      </Table>

      <Dialog open={!!invite} onClose={() => setInvite(null)} maxWidth="md" fullWidth>
        <DialogTitle>Link code</DialogTitle>
        <DialogContent>
          <Stack spacing={2}>
            <Typography variant="body2">
              Give this code to the other stack's admin (Admin → Linked stacks → Link with a stack). It works once, until{" "}
              {invite && fmtTime(invite.expires_at)}, and isn't shown again.
            </Typography>
            <TextField value={invite?.code ?? ""} multiline minRows={3} slotProps={{ input: { readOnly: true } }} onFocus={(e) => e.target.select()} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => invite && navigator.clipboard?.writeText(invite.code)}>Copy</Button>
          <Button variant="contained" onClick={() => setInvite(null)}>Done</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={joining} onClose={() => setJoining(false)} maxWidth="md" fullWidth>
        <DialogTitle>Link with a stack</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Typography variant="body2">
              Paste the link code the other stack made. This stack checks the other's platform CA against the code before it
              sends anything.
            </Typography>
            <TextField label="Link code" value={code} onChange={(e) => setCode(e.target.value)} multiline minRows={3} />
            {joinError && <Alert severity="error">{joinError}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setJoining(false)}>Cancel</Button>
          <Button variant="contained" disabled={busy || !code.trim()} onClick={join}>Link</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
