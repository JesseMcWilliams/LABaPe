import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Link, Paper, Stack, Table,
  TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { api, CredentialSection, Environment, fmtTime, Job } from "../api";
import Credentials from "../components/Credentials";
import PasswordRotation from "../components/PasswordRotation";
import { hasRole, useAuth } from "../App";
import { StateChip } from "../components";
import { ActionButton } from "../components/Confirm";

export default function EnvironmentDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [env, setEnv] = useState<Environment | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [creds, setCreds] = useState<CredentialSection[] | null>(null);
  const [promote, setPromote] = useState<{ vm: string; name: string } | null>(null);
  const { me } = useAuth();
  const canPromote = hasRole(me, "template_editor");
  const doPromote = async () => {
    if (!promote) return;
    try {
      const r = await api.post<{ job_id: number }>("/api/templates/promote", { environment_id: Number(id), vm: promote.vm, name: promote.name });
      navigate(`/jobs/${r.job_id}`);
    } catch (e) {
      setError((e as Error).message);
      setPromote(null);
    }
  };
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

  const act = async (action: "deploy" | "destroy" | "refresh-addresses") => {
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
      setCreds((await api.get<{ sections: CredentialSection[] }>(`/api/environments/${id}/credentials`)).sections);
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
          <ActionButton variant="contained" disabled={busy} onClick={() => act("deploy")}
            tip={env.status === "destroyed" || env.status === "new" ? "Create the VMs and configure them" : "Apply the environment again (creates missing VMs, reconfigures existing ones)"}
            confirm={{
              title: `${env.status === "destroyed" || env.status === "new" ? "Deploy" : "Redeploy"} ${env.name}?`,
              body: env.status === "destroyed" || env.status === "new"
                ? `A job creates the ${env.spec.host_groups.reduce((n, g) => n + (g.count ?? 1), 0)} VM(s) on ${env.kvm_host}, then runs Ansible on them. It can take from minutes (templates) to an hour or more (Windows ISO installs).`
                : "A job runs OpenTofu and Ansible again: missing VMs are created and every VM is reconfigured. Existing VMs aren't reinstalled.",
              confirmLabel: env.status === "destroyed" || env.status === "new" ? "Deploy" : "Redeploy",
            }}>
            {env.status === "destroyed" || env.status === "new" ? "Deploy" : "Redeploy"}
          </ActionButton>
          <ActionButton variant="outlined" color="error" disabled={busy || env.status === "destroyed"}
            tip="Delete every VM in this environment"
            confirm={{ title: `Destroy ${env.name}?`, danger: true, confirmLabel: "Destroy",
              body: "Every VM in this environment, its disks and its OpenTofu workspace are deleted, and its addresses are released. This can't be undone." }}
            onClick={() => act("destroy")}>
            Destroy
          </ActionButton>
          <ActionButton onClick={showCreds} disabled={env.status !== "deployed"}
            tip="Show the tester accounts and passwords (each view is audited)">
            Show credentials
          </ActionButton>
          {env.spec.host_groups.some((g) => g.addressing === "dhcp") && (
            <ActionButton onClick={() => act("refresh-addresses")} disabled={busy || env.status !== "deployed"}
              tip="Find DHCP VMs' current addresses and regenerate the inventory"
              confirm={{ title: "Refresh addresses?", confirmLabel: "Refresh",
                body: "A job looks up each DHCP VM's current address and regenerates the inventory and hosts entries. No VM is changed." }}>
              Refresh addresses
            </ActionButton>
          )}
        </Stack>
      )}
      {creds !== null && (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2">Tester credentials (viewing is audited)</Typography>
          <Credentials envId={id!} sections={creds} />
        </Paper>
      )}
      {env.is_owner && env.status === "deployed" && <PasswordRotation envId={id!} />}
      <Typography variant="h6">Host groups</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Group</TableCell><TableCell>Count</TableCell><TableCell>OS</TableCell>
            <TableCell>Roles</TableCell><TableCell>Source</TableCell><TableCell>Size</TableCell>
            <TableCell>Network</TableCell><TableCell>Addresses</TableCell>
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
              <TableCell>
                {g.network ?? "host default"} · {g.addressing ?? "static"}
                {[...(g.services ?? []), ...(g.applications ?? []), ...(g.packages ?? [])].length > 0 && (
                  <Typography variant="caption" component="div" color="text.secondary">
                    + {[...(g.services ?? []), ...(g.applications ?? []), ...(g.packages ?? [])].join(", ")}
                  </Typography>
                )}
              </TableCell>
              <TableCell>
                {Array.from({ length: g.count ?? 1 }, (_, k) => g.names?.[k] || `${g.name}${k + 1}`).map((vm) => (
                  <Box key={vm}>
                    {vm} {env.addresses?.[vm] ?? (g.addressing === "dhcp" ? "(DHCP)" : "")}
                    {env.is_owner && env.status === "deployed" && canPromote && !g.roles.includes("domain_controller") && (
                      <ActionButton size="small" tip="Turn this VM into a reusable template (opens a dialog to confirm)"
                        onClick={() => setPromote({ vm, name: `${vm}-template` })}>Promote to template</ActionButton>
                    )}
                  </Box>
                ))}
              </TableCell>
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
      <Dialog open={Boolean(promote)} onClose={() => setPromote(null)}>
        <DialogTitle>Promote {promote?.vm} to a template</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            The VM is generalized (Windows sysprep, or cloud-init clean on Linux), shut down and copied into the
            template library. It's left shut down; destroy the environment when you're done with it.
          </DialogContentText>
          <TextField label="Template name" fullWidth value={promote?.name ?? ""}
            onChange={(e) => setPromote(promote ? { ...promote, name: e.target.value } : null)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPromote(null)} title="Close without promoting">Cancel</Button>
          <Button variant="contained" onClick={doPromote} title="Start the promote job: generalize, shut down and copy the VM">Promote</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
