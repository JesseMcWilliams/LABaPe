import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Chip, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
import { api, KvmHost } from "../api";
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
  useEffect(() => {
    api.get<KvmHost[]>("/api/hosts").then(setHosts, (e) => setError(e.message));
  }, []);
  return (
    <Stack spacing={2}>
      <ListHeader title="KVM hosts" canAdd={isAdmin} addTo="/hosts/new" addLabel="Register host"
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
                <TableCell><Link component={RouterLink} to={`/hosts/${h.id}`}>{h.name}</Link></TableCell>
                <TableCell>{h.libvirt_uri}</TableCell>
                <TableCell>{s ? `${s.vms_running} of ${s.vms_defined}` : "–"}</TableCell>
                <TableCell>{s ? pct(s.memory_committed_mb, s.memory_total_mb) : "–"}</TableCell>
                <TableCell>{s ? `${s.storage.vm.free_gb} of ${s.storage.vm.total_gb} GB` : "–"}</TableCell>
                {isAdmin && <TableCell>{h.concurrency_limit}</TableCell>}
                <TableCell>
                  <Chip size="small" label={h.enabled ? "enabled" : "disabled"} color={h.enabled ? "success" : "default"} />
                </TableCell>
              </TableRow>
            );
          })}
          {hosts.length === 0 && <TableRow><TableCell colSpan={7}>No KVM hosts you can use.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Stack>
  );
}
