import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Chip, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { api, fmtTime, KvmHost } from "../api";
import { ActionButton } from "../components/Confirm";
import { hasRole, useAuth } from "../App";
import { ListHeader } from "../components/PageParts";

const pct = (n: number, d: number) => (d > 0 ? `${Math.round((n / d) * 100)}%` : "–");

// KVM hosts (design §22.2): admins register and edit them; others see the
// hosts they can use, read-only. Each name opens the host's page.
export default function Hosts() {
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [hosts, setHosts] = useState<KvmHost[]>([]);
  const [error, setError] = useState("");
  const [deleted, setDeleted] = useState<{ id: number; name: string; deleted_at: string; deleted_by: string; purge_on: string }[]>([]);
  const load = () => {
    api.get<KvmHost[]>("/api/hosts").then(setHosts, (e) => setError(e.message));
    if (isAdmin) api.get<typeof deleted>("/api/hosts/deleted").then(setDeleted, () => setDeleted([]));
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Stack spacing={2}>
      <ListHeader title="KVM hosts" canAdd={isAdmin} addTo="/admin/hosts/new" addLabel="Register host"
        intro={isAdmin ? undefined : "The KVM hosts you can deploy to."} />
      {error && <Alert severity="error">{error}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell><TableCell>libvirt URI</TableCell><TableCell>VMs running</TableCell>
            <TableCell>RAM committed</TableCell><TableCell>VM storage free</TableCell>
            {isAdmin && <TableCell>Concurrent jobs</TableCell>}<TableCell>State</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {hosts.map((h) => {
            const s = h.resources?.stats;
            return (
              <TableRow key={h.id} hover sx={{ opacity: h.enabled ? 1 : 0.5 }}>
                <TableCell><Link component={RouterLink} to={`/admin/hosts/${h.id}`}>{h.name}</Link></TableCell>
                <TableCell>{h.libvirt_uri}</TableCell>
                <TableCell>{s ? `${s.vms_running} of ${s.vms_defined}` : "–"}</TableCell>
                <TableCell>{s ? pct(s.memory_committed_mb, s.memory_total_mb) : "–"}</TableCell>
                <TableCell>{s ? `${s.storage.vm.free_gb} of ${s.storage.vm.total_gb} GB` : "–"}</TableCell>
                {isAdmin && <TableCell>{h.concurrency_limit}</TableCell>}
                <TableCell>
                  <Chip size="small" label={h.enabled ? "enabled" : "disabled"} color={h.enabled ? "success" : "default"} />
                  {h.role && h.role !== "kvm" && <Chip size="small" label={h.role === "stack" ? "stack" : "KVM + stack"} sx={{ ml: 0.5 }} title={h.stack_url || "Runs a LABaPe stack"} />}
                  {h.is_primary && <Chip size="small" label="primary" color="primary" sx={{ ml: 0.5 }} title="Holds every file; other hosts get copies when a job needs them" />}
                  {h.files_from_share && <Chip size="small" label="share" sx={{ ml: 0.5 }} title="Reads the primary's files through a share" />}
                </TableCell>
              </TableRow>
            );
          })}
          {hosts.length === 0 && <TableRow><TableCell colSpan={7}>No KVM hosts you can use.</TableCell></TableRow>}
        </TableBody>
      </Table>
      {isAdmin && deleted.length > 0 && (
        <Stack spacing={1}>
          <Typography variant="h6">Deleted hosts</Typography>
          <Typography variant="body2" color="text.secondary">
            Hidden everywhere. A deleted host can be restored until its day below (Settings → History sets how long); then it is removed for good.
            Nothing was changed on the hosts themselves.
          </Typography>
          <Table size="small">
            <TableHead><TableRow><TableCell>Name</TableCell><TableCell>Deleted</TableCell><TableCell>By</TableCell><TableCell>Removed for good</TableCell><TableCell /></TableRow></TableHead>
            <TableBody>
              {deleted.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>{d.name}</TableCell><TableCell>{fmtTime(d.deleted_at)}</TableCell><TableCell>{d.deleted_by}</TableCell>
                  <TableCell>{fmtTime(d.purge_on)}</TableCell>
                  <TableCell align="right">
                    <ActionButton size="small" tip="Bring the host back (turned off until you enable it)"
                      confirm={{ title: `Restore ${d.name}?`, confirmLabel: "Restore",
                        body: "The host comes back in the list, turned off. Check its access, then enable it in its settings." }}
                      onClick={() => api.post(`/api/hosts/${d.id}/restore`).then(load, (e) => setError(e.message))}>Restore</ActionButton>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Stack>
      )}
    </Stack>
  );
}
