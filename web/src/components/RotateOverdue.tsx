// Rotate every overdue secret now, one at a time (design §62.1, batch R2). The button asks first and says
// plainly which rotations stop services; the banner follows the run and survives the API restarting.
import { useEffect, useRef, useState } from "react";
import {
  Alert, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, LinearProgress, List,
  ListItem, ListItemText, Stack, Typography,
} from "@mui/material";
import { api } from "../api";
import { fromApiError } from "../forms";

interface PlanItem { id: number; label: string; restart: boolean; late_hours: number }
interface Queue {
  state: "running" | "done" | "stopped" | "cancelled"; by: string; started: string; note: string; total: number;
  done: string[]; current: string | null; left: string[];
}
const lateText = (h: number) => (h >= 48 ? `${Math.floor(h / 24)} days late` : `${h} hours late`);
const SEEN = "labape-rotation-queue-seen";

export function RotateOverdueButton({ onStarted }: { onStarted: () => void }) {
  const [plan, setPlan] = useState<PlanItem[] | null>(null);
  const [open, setOpen] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const restarting = (plan ?? []).filter((p) => p.restart);

  const show = async () => {
    setError(""); setUnderstood(false); setOpen(true); setPlan(null);
    try { setPlan((await api.get<{ plan: PlanItem[] }>("/api/admin/secrets/rotate-overdue")).plan); }
    catch (e) { setError((e as Error).message); }
  };
  const start = async () => {
    setBusy(true); setError("");
    try {
      await api.post("/api/admin/secrets/rotate-overdue", { acknowledge_restart: understood });
      setOpen(false);
      onStarted();
    } catch (e) { setError(fromApiError(e).summary || (e as Error).message); } finally { setBusy(false); }
  };
  return (
    <>
      <Button size="small" color="inherit" variant="outlined" onClick={show}
        title="Rotate every overdue secret now, one at a time (you are asked first; some restart services)">Rotate overdue now</Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Rotate the overdue secrets now?</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5}>
            {error && <Alert severity="error">{error}</Alert>}
            {plan && plan.length === 0 && <Alert severity="info">Nothing is overdue any more.</Alert>}
            {plan && plan.length > 0 && (
              <>
                <Typography variant="body2">
                  LABaPe rotates these {plan.length} secret{plan.length === 1 ? "" : "s"} one at a time, in this order, and
                  stops at the first one that fails (the ones after it are left for you to look at).
                </Typography>
                <List dense disablePadding>
                  {plan.map((p, i) => (
                    <ListItem key={p.id} disableGutters secondaryAction={p.restart ? <Chip size="small" color="warning" label="stops services" /> : undefined}>
                      <ListItemText primary={`${i + 1}. ${p.label}`} secondary={lateText(p.late_hours)} />
                    </ListItem>
                  ))}
                </List>
                {restarting.length > 0 && (
                  <Alert severity="warning">
                    {restarting.length} of these ({restarting.map((p) => p.label.split(" (")[0]).join(", ")}) restart LABaPe's services:
                    the API and the worker, and for some the CA or the database. The web interface is unavailable for a short
                    time each time, running jobs are interrupted, everyone signs in again, and maintenance mode (no changes) is
                    on meanwhile. Each is rolled back if its health checks fail.
                  </Alert>
                )}
                {restarting.length > 0 && (
                  <FormControlLabel label="I understand that services will stop" control={<Checkbox checked={understood}
                    onChange={(e) => setUnderstood(e.target.checked)} />} />
                )}
              </>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="contained" color={restarting.length ? "warning" : "primary"} disabled={busy || !plan || plan.length === 0 || (restarting.length > 0 && !understood)}
            onClick={start}>Rotate {plan?.length ?? ""} now</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

/** The run in progress, or how the last one ended. Polls while it runs, and keeps polling through API restarts. */
export function RotateOverdueStatus({ refreshKey, onChange }: { refreshKey: number; onChange: () => void }) {
  const [q, setQ] = useState<Queue | null>(null);
  const [away, setAway] = useState(false);                 // the API did not answer (it is restarting)
  const last = useRef<string>("");
  const [seen, setSeen] = useState(() => { try { return localStorage.getItem(SEEN) ?? ""; } catch { return ""; } });

  const poll = () => api.get<{ queue: Queue | null }>("/api/admin/secrets/rotate-overdue").then((r) => {
    setAway(false);
    setQ(r.queue);
    const sig = r.queue ? `${r.queue.state}:${r.queue.done.length}` : "";
    if (sig !== last.current) { if (last.current) onChange(); last.current = sig; }
  }, () => setAway(true));
  useEffect(() => { poll(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (q?.state !== "running") return;
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, [q?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!q) return null;
  const dismiss = () => { try { localStorage.setItem(SEEN, q.started); } catch { /* storage unavailable */ } setSeen(q.started); };
  if (q.state === "running") {
    const n = q.done.length;
    return (
      <Alert severity="info" action={<Button size="small" color="inherit" onClick={() => api.del("/api/admin/secrets/rotate-overdue").then(poll)}
        title="Stop after the rotation in progress">Stop</Button>}>
        <Stack spacing={0.5}>
          <span>Rotating the overdue secrets ({n} of {q.total} done{q.current ? `; now: ${q.current}` : ""}).
            {away ? " The API is restarting; this page keeps checking." : ""}</span>
          <LinearProgress variant="determinate" value={q.total ? (n / q.total) * 100 : 0} />
        </Stack>
      </Alert>
    );
  }
  if (seen === q.started) return null;
  const done = q.state === "done";
  return (
    <Alert severity={done ? "success" : "warning"} onClose={dismiss}>
      {done ? `All ${q.total} overdue secrets were rotated.`
        : `${q.state === "cancelled" ? "Stopped" : "Stopped early"}: ${q.done.length} of ${q.total} rotated. ${q.note}`}
      {!done && q.left.length > 0 ? ` Not rotated: ${q.left.join("; ")}.` : ""}
    </Alert>
  );
}
