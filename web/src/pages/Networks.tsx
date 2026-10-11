import { FormEvent, useEffect, useState } from "react";
import {
  Alert, Button, Checkbox, Chip, FormControlLabel, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow,
  TextField, Typography,
} from "@mui/material";
import { api, Network } from "../api";

// The central network catalog: what VMs may use (design §20.1). Admin only.
type Form = Omit<Network, "id" | "allocated" | "bridge" | "is_default">;

const blank: Form = {
  name: "", description: "", cidr: "", gateway: "", dns_servers: [], vlan: null, addressing: ["static"],
  static_pools: [], dhcp_ranges: [], reserved: [], allowed_roles: [], allowed_groups: [], enabled: true,
};
const ROLES = ["admin", "template_editor", "deployer", "file_manager", "viewer"];

// Lists are edited as comma- or line-separated text.
const toList = (t: string) => t.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
const fromList = (l: string[]) => l.join(", ");

export default function Networks() {
  const [nets, setNets] = useState<Network[]>([]);
  const [form, setForm] = useState<Form>(blank);
  const [editing, setEditing] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = () => api.get<Network[]>("/api/networks").then(setNets, (e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      if (editing) await api.put(`/api/networks/${editing}`, form);
      else await api.post("/api/networks", form);
      setForm(blank);
      setEditing(null);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const remove = async (n: Network) => {
    if (!confirm(`Delete network ${n.name}?`)) return;
    try {
      await api.del(`/api/networks/${n.id}`);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const listField = (key: "dns_servers" | "static_pools" | "dhcp_ranges" | "reserved" | "allowed_groups",
                     label: string, help: string) => (
    <TextField label={label} size="small" value={fromList(form[key])} helperText={help} sx={{ minWidth: 280 }}
      onChange={(e) => setForm({ ...form, [key]: toList(e.target.value) })} />
  );

  const toggle = <T extends string>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Networks</Typography>
      <Typography variant="body2" color="text.secondary">
        The networks VMs may use. Attach them to KVM hosts under Hosts. Static addresses are handed out from the
        static pools automatically; DHCP addresses are found after boot.
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell><TableCell>Network</TableCell><TableCell>Addressing</TableCell>
            <TableCell>Static pools</TableCell><TableCell>DHCP scope</TableCell><TableCell>Who</TableCell>
            <TableCell>In use</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {nets.map((n) => (
            <TableRow key={n.id} sx={{ opacity: n.enabled ? 1 : 0.5 }}>
              <TableCell>{n.name}{n.vlan ? ` (VLAN ${n.vlan})` : ""}</TableCell>
              <TableCell>{n.cidr} via {n.gateway || "(.1)"}; DNS {fromList(n.dns_servers) || "gateway"}</TableCell>
              <TableCell>{n.addressing.map((a) => <Chip key={a} size="small" label={a} sx={{ mr: 0.5 }} />)}</TableCell>
              <TableCell>{fromList(n.static_pools)}</TableCell>
              <TableCell>{fromList(n.dhcp_ranges)}</TableCell>
              <TableCell>
                {n.allowed_roles.length || n.allowed_groups.length
                  ? fromList([...n.allowed_roles, ...n.allowed_groups.map((g) => `group ${g}`)])
                  : "anyone who deploys"}
              </TableCell>
              <TableCell>{n.allocated}</TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                <Button size="small" onClick={() => { const { id, allocated: _a, ...rest } = n; setEditing(id); setForm(rest); }}>
                  Edit
                </Button>
                <Button size="small" color="error" onClick={() => remove(n)}>Delete</Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Paper variant="outlined" sx={{ p: 2 }} component="form" onSubmit={submit}>
        <Stack spacing={2}>
          <Typography variant="h6">{editing ? `Edit ${form.name}` : "Add a network"}</Typography>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
            <TextField label="Name" size="small" value={form.name} required
              onChange={(e) => setForm({ ...form, name: e.target.value })} helperText="e.g. lab-vlan48" />
            <TextField label="CIDR" size="small" value={form.cidr} required
              onChange={(e) => setForm({ ...form, cidr: e.target.value })} helperText="e.g. 172.21.48.0/22" />
            <TextField label="Gateway" size="small" value={form.gateway}
              onChange={(e) => setForm({ ...form, gateway: e.target.value })} helperText="Empty: the network's .1" />
            <TextField label="VLAN" size="small" type="number" value={form.vlan ?? ""} sx={{ width: 100 }}
              onChange={(e) => setForm({ ...form, vlan: e.target.value ? Number(e.target.value) : null })} />
            <TextField label="Description" size="small" value={form.description} sx={{ minWidth: 280 }}
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap" }}>
            {listField("dns_servers", "DNS servers", "Empty: the gateway")}
            {listField("static_pools", "Static pools", "Ranges LABaPe may assign, e.g. 172.21.50.1-172.21.51.254")}
            {listField("dhcp_ranges", "DHCP scope", "The DHCP server's range; never assigned statically")}
            {listField("reserved", "Reserved", "Never assigned (infrastructure)")}
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "center" }}>
            <Typography variant="body2">Addressing:</Typography>
            {(["static", "dhcp"] as const).map((m) => (
              <FormControlLabel key={m} label={m} control={<Checkbox checked={form.addressing.includes(m)}
                onChange={() => setForm({ ...form, addressing: toggle(form.addressing, m) })} />} />
            ))}
            <FormControlLabel label="Enabled" control={<Checkbox checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />} />
          </Stack>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: "wrap", alignItems: "center" }}>
            <Typography variant="body2">Limit to roles:</Typography>
            {ROLES.map((r) => (
              <FormControlLabel key={r} label={r} control={<Checkbox checked={form.allowed_roles.includes(r)}
                onChange={() => setForm({ ...form, allowed_roles: toggle(form.allowed_roles, r) })} />} />
            ))}
            {listField("allowed_groups", "or groups", "Authentik groups; empty roles and groups = anyone who deploys")}
          </Stack>
          <Stack direction="row" spacing={2}>
            <Button type="submit" variant="contained">{editing ? "Save" : "Add"}</Button>
            {editing && <Button onClick={() => { setEditing(null); setForm(blank); }}>Cancel</Button>}
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}
