import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Chip, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
import { api, Network } from "../api";
import { hasRole, useAuth } from "../App";
import { ListHeader } from "../components/PageParts";
import { fromList } from "./NetworkDetail";

// The network catalog (design §20.1, §22.2): admins see and manage all of
// it; others see the networks they may deploy on, read-only.
export default function Networks() {
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [nets, setNets] = useState<Network[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Network[]>("/api/networks").then(setNets, (e) => setError(e.message));
  }, []);
  return (
    <Stack spacing={2}>
      <ListHeader title="Networks" canAdd={isAdmin} addTo="/networks/new" addLabel="Add network"
        intro={isAdmin
          ? "The networks VMs may use. Attach them to KVM hosts on each host's page. Static addresses are handed out from the static pools automatically; DHCP addresses are found after boot."
          : "The networks you may deploy on."} />
      {error && <Alert severity="error">{error}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell><TableCell>Network</TableCell><TableCell>Addressing</TableCell>
            <TableCell>Static pools</TableCell><TableCell>DHCP scope</TableCell>
            {isAdmin && <TableCell>Who</TableCell>}<TableCell>In use</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {nets.map((n) => (
            <TableRow key={n.id} hover sx={{ opacity: n.enabled ? 1 : 0.5 }}>
              <TableCell>
                <Link component={RouterLink} to={`/networks/${n.id}`}>{n.name}</Link>{n.vlan ? ` (VLAN ${n.vlan})` : ""}
              </TableCell>
              <TableCell>{n.cidr} via {n.gateway || "(.1)"}; DNS {fromList(n.dns_servers) || "gateway"}</TableCell>
              <TableCell>{n.addressing.map((a) => <Chip key={a} size="small" label={a === "dhcp" ? "DHCP" : "Static"} sx={{ mr: 0.5 }} />)}</TableCell>
              <TableCell>{fromList(n.static_pools)}</TableCell>
              <TableCell>{fromList(n.dhcp_ranges)}</TableCell>
              {isAdmin && (
                <TableCell>
                  {n.allowed_roles.length || n.allowed_groups.length
                    ? fromList([...n.allowed_roles, ...n.allowed_groups.map((g) => `group ${g}`)])
                    : "anyone who deploys"}
                </TableCell>
              )}
              <TableCell>{n.allocated}</TableCell>
            </TableRow>
          ))}
          {nets.length === 0 && <TableRow><TableCell colSpan={7}>No networks you can use.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Stack>
  );
}
