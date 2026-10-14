import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, Chip, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { api, Network } from "../api";
import { hasRole, useAuth } from "../App";
import { ListHeader } from "../components/PageParts";
import { FilterBar, SelectFilter, useUrlFilters } from "../components/Filters";
import { Field } from "../forms";
import { fromList } from "./NetworkDetail";

// The network catalog (design §20.1, §22.2): admins see and manage all of
// it; others see the networks they may deploy on, read-only.
const NET_KEYS = ["name", "address", "addressing", "vlan", "state"];
const has = (hay: string, needle: string) => !needle || hay.toLowerCase().includes(needle.toLowerCase());

export default function Networks() {
  const { me } = useAuth();
  const isAdmin = hasRole(me, "admin");
  const [nets, setNets] = useState<Network[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Network[]>("/api/networks").then(setNets, (e) => setError(e.message));
  }, []);
  // Filters (L2), in the browser.
  const flt = useUrlFilters(NET_KEYS);
  const a = flt.applied;
  const shown = nets.filter((n) => (has(n.name, a.name) || has(n.description ?? "", a.name))
    && has([n.cidr, n.gateway, ...n.static_pools, ...n.dhcp_ranges, ...n.dns_servers].join(" "), a.address)
    && (!a.addressing || n.addressing.includes(a.addressing as "static" | "dhcp"))
    && (!a.vlan || String(n.vlan ?? "") === a.vlan)
    && (!a.state || (a.state === "enabled") === n.enabled));
  return (
    <Stack spacing={2}>
      <ListHeader title="Networks" canAdd={isAdmin} addTo="/admin/networks/new" addLabel="Add network"
        intro={isAdmin
          ? "The networks VMs may use. Attach them to KVM hosts on each host's page. Static addresses are handed out from the static pools automatically; DHCP addresses are found after boot."
          : "The networks you may deploy on."} />
      {error && <Alert severity="error">{error}</Alert>}
      <FilterBar onApply={() => flt.apply()} onClear={flt.clear} active={flt.active}>
        <Field label="Name" value={flt.draft.name} sx={{ width: 170 }} help="Contains (name or description)"
          onChange={(e) => flt.set("name", e.target.value)} />
        <Field label="Address" value={flt.draft.address} sx={{ width: 170 }} help="Network, gateway, pool, DNS contains"
          onChange={(e) => flt.set("address", e.target.value)} />
        <SelectFilter label="Addressing" value={flt.draft.addressing} width={140} options={["static", "dhcp"]}
          onChange={(v) => flt.set("addressing", v)} />
        <SelectFilter label="VLAN" value={flt.draft.vlan} width={120}
          options={[...new Set(nets.filter((n) => n.vlan).map((n) => String(n.vlan)))].sort((x, y) => Number(x) - Number(y))}
          onChange={(v) => flt.set("vlan", v)} />
        {isAdmin && <SelectFilter label="State" value={flt.draft.state} width={130} options={["enabled", "disabled"]}
          onChange={(v) => flt.set("state", v)} />}
      </FilterBar>
      {flt.active && <Typography variant="body2" color="text.secondary">{shown.length} of {nets.length} match</Typography>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell><TableCell>Network</TableCell><TableCell>Addressing</TableCell>
            <TableCell>Static pools</TableCell><TableCell>DHCP scope</TableCell>
            {isAdmin && <TableCell>Who</TableCell>}<TableCell>In use</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {shown.map((n) => (
            <TableRow key={n.id} hover sx={{ opacity: n.enabled ? 1 : 0.5 }}>
              <TableCell>
                <Link component={RouterLink} to={`/admin/networks/${n.id}`}>{n.name}</Link>{n.vlan ? ` (VLAN ${n.vlan})` : ""}
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
          {shown.length === 0 && <TableRow><TableCell colSpan={7}>{nets.length ? "No networks match these filters." : "No networks you can use."}</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Stack>
  );
}
