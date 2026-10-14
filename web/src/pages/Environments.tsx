import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Button, Chip, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { api, Environment, fmtTime, LEASE_COLOR } from "../api";
import { hasRole, useAuth } from "../App";
import { StateChip } from "../components";

export default function Environments() {
  const { me } = useAuth();
  const [envs, setEnvs] = useState<Environment[]>([]);
  const [error, setError] = useState("");
  const [quota, setQuota] = useState<{ limits: Record<string, number | null | string> | null; usage: Record<string, number> } | null>(null);
  useEffect(() => { api.get<typeof quota>("/api/quotas/me").then(setQuota, () => setQuota(null)); }, [envs.length]);

  useEffect(() => {
    const load = () => api.get<Environment[]>("/api/environments").then(setEnvs, (e) => setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h5">Environments</Typography>
        {quota?.limits && (
          <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1, ml: 2 }} title={`Your quota comes from ${quota.limits.source}`}>
            Your quota: {[["max_environments", "environments", "environments"], ["max_vcpus", "vcpus", "vCPUs"],
              ["max_memory_mb", "memory_mb", "MB memory"], ["max_disk_gb", "disk_gb", "GB disk"]]
              .filter(([k]) => quota.limits![k] !== null)
              .map(([k, u, l]) => `${quota.usage[u]} of ${quota.limits![k]} ${l}`).join(" · ") || "no limits"}
          </Typography>
        )}
        {hasRole(me, "deployer", "template_editor") && (
          <Button variant="contained" component={RouterLink} to="/environments/new"
            title="New environment: opens the form; nothing is created until you confirm there">
            New environment
          </Button>
        )}
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>KVM host</TableCell>
            <TableCell>Hosts</TableCell>
            <TableCell>Created by</TableCell>
            <TableCell>Expires</TableCell>
            <TableCell>Updated</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {envs.map((e) => (
            <TableRow key={e.id} hover>
              <TableCell>
                <Link component={RouterLink} to={`/environments/${e.id}`}>
                  {e.name}
                </Link>
              </TableCell>
              <TableCell>
                <StateChip state={e.status} />
              </TableCell>
              <TableCell>{e.kvm_host}</TableCell>
              <TableCell>
                {e.spec.host_groups.map((g) => `${g.name}×${g.count ?? 1} (${g.os})`).join(", ")}
              </TableCell>
              <TableCell>{e.created_by}</TableCell>
              <TableCell>{e.lease?.expires_at
                ? <Chip size="small" variant="outlined" color={LEASE_COLOR[e.lease.phase]} label={fmtTime(e.lease.expires_at)}
                    title={e.lease.phase === "ended" ? "The lease ended: its VMs were shut down" : `Lease: ${e.lease.phase}`} />
                : "never"}</TableCell>
              <TableCell>{fmtTime(e.updated_at)}</TableCell>
            </TableRow>
          ))}
          {envs.length === 0 && (
            <TableRow>
              <TableCell colSpan={7}>No environments you can see yet.</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Stack>
  );
}
