import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, LinearProgress, Link, Radio,
  RadioGroup, Typography,
} from "@mui/material";
import { api, fmtTime } from "../api";
import { fromApiError } from "../forms";

// Check now (design §23.2): the newest release, a newer pre-release, or the
// previous version on standby, each with its notes; Continue hands the
// choice to the host's update agent and shows its progress.
interface Release { tag: string; name: string; prerelease: boolean; url: string; published_at: string }
interface AgentStatus {
  installed: boolean; state: string; step?: string; message?: string; from?: string; to?: string; action?: string;
  started_at?: string; finished_at?: string | null; pending: boolean;
}
export interface UpdateInfo {
  current: string; release: Release | null; prerelease: Release | null; error: string;
  db_revision: string | null; code_revision: string;
  previous: { version: string; db_revision: string; backup: string; kept_at: string } | null;
  agent: AgentStatus;
}

export default function UpdateDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [choice, setChoice] = useState("");
  const [error, setError] = useState("");
  const [started, setStarted] = useState(false);
  // What the agent reported when Continue was pressed: until it differs (or says running or pending), the status on
  // screen is the last run's, not this one's, so it is not shown.
  const [before, setBefore] = useState("");
  const stamp = (x?: AgentStatus) => (x ? `${x.state}|${x.started_at ?? ""}|${x.finished_at ?? ""}|${x.message ?? ""}` : "");

  const load = () => api.get<UpdateInfo>("/api/admin/update").then((i) => {
    setInfo(i);
    setError("");                      // an error from the API restarting mid-update is not news once it answers again
    setChoice((c) => c || i.release?.tag || i.prerelease?.tag || (i.previous ? "rollback" : ""));
  }, (e) => setError(e.message));
  useEffect(() => {
    if (!open) return;
    setStarted(false);
    setBefore("");
    setError("");
    load();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const running = Boolean(info && (info.agent.state === "running" || info.agent.pending));
  useEffect(() => {
    if (!open || !(started || running)) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [open, started, running]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = async () => {
    setError("");
    try {
      if (choice === "rollback") await api.post("/api/admin/update/rollback");
      else await api.post("/api/admin/update", { target: choice });
      setBefore(stamp(info?.agent));
      setStarted(true);
      setInfo(null);                    // the old status goes; the new one is read as it arrives
      load();
    } catch (e) { setError(fromApiError(e).summary); }
  };

  const options = info ? [
    info.release && { value: info.release.tag, label: `Update to ${info.release.tag}`, notes: info.release.url, note: "Release" },
    info.prerelease && { value: info.prerelease.tag, label: `Update to ${info.prerelease.tag}`, notes: info.prerelease.url,
      note: "Pre-release (preview): newer, less tested" },
    info.previous && { value: "rollback", label: `Roll back to ${info.previous.version}`, notes: "",
      note: info.previous.db_revision && info.previous.db_revision !== info.db_revision
        ? `The database layout changed since then: its pre-update backup is restored, and changes made since the update are lost.`
        : "The previous version is on standby: no rebuild, no data lost." },
  ].filter(Boolean) as { value: string; label: string; notes: string; note: string }[] : [];
  const a = info?.agent;
  const stale = started && !running && a !== undefined && stamp(a) === before;   // still the last run's status

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Update LABaPe</DialogTitle>
      <DialogContent>
        {!info && !error && <LinearProgress />}
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        {info && (
          <>
            <Typography variant="body2" gutterBottom>
              Running <b>{info.current}</b> · database layout {info.db_revision ?? "not stamped"}
            </Typography>
            {info.error && <Alert severity="warning" sx={{ my: 1 }}>{info.error}</Alert>}
            {stale && <Alert severity="info" sx={{ my: 1 }}>Waiting for the update agent to start…<LinearProgress sx={{ mt: 1 }} /></Alert>}
            {!stale && (started || running || (a && a.state !== "idle")) && a && (
              <Alert severity={a.state === "succeeded" ? "success" : a.state === "running" || a.pending ? "info" : "error"} sx={{ my: 1 }}>
                {a.pending && a.state !== "running" ? "Waiting for the update agent to start…" : <>
                  <b>{a.state}</b>{a.step ? ` (${a.step})` : ""}: {a.message}
                  {a.finished_at && <Typography variant="caption" component="div">Finished {fmtTime(a.finished_at)}</Typography>}
                </>}
                {(a.state === "running" || a.pending) && <LinearProgress sx={{ mt: 1 }} />}
              </Alert>
            )}
            {!running && !started && (options.length === 0 ? (
              <Alert severity="success" sx={{ my: 1 }}>You're on the newest release; nothing newer is published.</Alert>
            ) : (
              <RadioGroup value={choice} onChange={(e) => setChoice(e.target.value)}>
                {options.map((o) => (
                  <Box key={o.value} sx={{ mb: 1 }}>
                    <FormControlLabel value={o.value} control={<Radio />} label={o.label} />
                    <Typography variant="caption" color="text.secondary" component="div" sx={{ ml: 4 }}>
                      {o.note}{o.notes && <> · <Link href={o.notes} target="_blank" rel="noreferrer">Release notes</Link></>}
                    </Typography>
                  </Box>
                ))}
              </RadioGroup>
            ))}
            {!running && !started && options.length > 0 && (info.agent.installed ? (
              <Typography variant="body2" color="text.secondary">
                Continue turns maintenance mode on, waits for running jobs, backs up the database, keeps the current
                version on standby, then switches. If the health checks fail it goes back automatically. Only releases
                signed by an allowed key are applied.
              </Typography>
            ) : (
              <Alert severity="info">
                The update agent isn't installed on this host. Run these as the service user:
                <Box component="pre" sx={{ m: 0, mt: 1, fontSize: 12, whiteSpace: "pre-wrap" }}>
                  {["cd ~/LABaPe-web", "git fetch --tags origin", `git verify-tag ${choice}`, `git checkout ${choice}`,
                    "cd container && export LABAPE_VERSION=$(git describe --tags --always)", "docker compose up -d --build"].join("\n")}
                </Box>
              </Alert>
            ))}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} title="Close this dialog (an update already started carries on)">Close</Button>
        {info?.agent.installed && !running && !started && options.length > 0 && (
          <Button variant="contained" color={choice === "rollback" ? "warning" : "primary"} disabled={!choice} onClick={go}
            title="Hand the choice to the update agent on the host">
            Continue
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
