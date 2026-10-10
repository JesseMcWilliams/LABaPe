import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Link, Paper, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, Typography,
} from "@mui/material";
import { api, Environment, fmtTime, Job } from "../api";
import { StateChip } from "../components";

export default function EnvironmentDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [env, setEnv] = useState<Environment | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [creds, setCreds] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setEnv(await api.get<Environment>(`/api/environments/${id}`));
      const all = await api.get<Job[]>("/api/jobs");
      setJobs(all.filter((j) => j.environment_id === Number(id)));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (action: "deploy" | "destroy") => {
    setConfirm(false);
    setError("");
    try {
      const r = await api.post<{ job_id: number }>(`/api/environments/${id}/${action}`);
      navigate(`/jobs/${r.job_id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const showCreds = async () => {
    try {
      setCreds((await api.get<{ credentials: string }>(`/api/environments/${id}/credentials`)).credentials);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!env) return error ? <Alert severity="error">{error}</Alert> : null;
  const busy = env.status === "deploying" || env.status === "destroying";

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <Typography variant="h5">{env.name}</Typography>
        <StateChip state={env.status} />
        <Typography variant="body2" color="text.secondary">
          on {env.kvm_host} · created by {env.created_by} {fmtTime(env.created_at)}
        </Typography>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {env.is_owner && (
        <Stack direction="row" spacing={2}>
          <Button variant="contained" disabled={busy} onClick={() => act("deploy")}>
            {env.status === "destroyed" || env.status === "new" ? "Deploy" : "Redeploy"}
          </Button>
          <Button variant="outlined" color="error" disabled={busy || env.status === "destroyed"}
            onClick={() => setConfirm(true)}>
            Destroy
          </Button>
          <Button onClick={showCreds} disabled={env.status !== "deployed"}>
            Show credentials
          </Button>
        </Stack>
      )}
      {creds !== null && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2">Tester credentials (viewing is audited)</Typography>
          <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{creds || "None stored."}</pre>
        </Paper>
      )}
      <Typography variant="h6">Host groups</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Group</TableCell><TableCell>Count</TableCell><TableCell>OS</TableCell>
            <TableCell>Roles</TableCell><TableCell>Source</TableCell><TableCell>Size</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {env.spec.host_groups.map((g) => (
            <TableRow key={g.name}>
              <TableCell>{g.name}</TableCell>
              <TableCell>{g.count ?? 1}</TableCell>
              <TableCell>{g.os}{g.windows_core ? " (Core)" : ""}</TableCell>
              <TableCell>{g.roles.join(", ")}</TableCell>
              <TableCell>{g.image_source === "packer_template" ? `template ${g.template}` : g.image_source ?? "default"}</TableCell>
              <TableCell>{g.cpu_count} vCPU · {g.memory_mb} MB · {g.disk_gb} GB</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {env.inventory && (
        <>
          <Typography variant="h6">Hosts file entries</Typography>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <pre style={{ margin: 0 }}>{env.inventory}</pre>
          </Paper>
        </>
      )}
      <Typography variant="h6">Jobs</Typography>
      <Table size="small">
        <TableBody>
          {jobs.map((j) => (
            <TableRow key={j.id}>
              <TableCell><Link component={RouterLink} to={`/jobs/${j.id}`}>#{j.id}</Link></TableCell>
              <TableCell>{j.type}</TableCell>
              <TableCell><StateChip state={j.state} /></TableCell>
              <TableCell>{j.requested_by}</TableCell>
              <TableCell>{fmtTime(j.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Dialog open={confirm} onClose={() => setConfirm(false)}>
        <DialogTitle>Destroy {env.name}?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Every VM in this environment and its OpenTofu workspace will be deleted. This can't be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(false)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={() => act("destroy")}>Destroy</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
